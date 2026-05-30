#!/usr/bin/env python3
"""
s12 验收测试 —— Task System（文件持久化的任务图 + blockedBy 依赖）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s12_task_system.py

全绿 = 达标。0 API、临时目录（不碰网络、不污染 .tasks）。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py）：

  0. TASKS_DIR：模块级 Path，任务 JSON 的默认目录。

  1. Task（dataclass 或等价）：字段 id / subject / description /
     status / owner / blockedBy(list)。status ∈ pending|in_progress|completed。

  2. create_task(subject, description="", blockedBy=None, tasks_dir=None) -> Task
        生成唯一 id，status=pending，owner=None，自动存成 .tasks/{id}.json。

  3. save_task(task, tasks_dir=None) / load_task(id, tasks_dir=None) -> Task
     / list_tasks(tasks_dir=None) -> list[Task]
        落盘 / 读盘往返；list 读取目录下全部任务。

  4. can_start(task_id, tasks_dir=None) -> bool
        blockedBy 里【全部 completed】才 True；依赖缺失（文件不存在）视为 False。

  5. claim_task(task_id, owner="agent", tasks_dir=None) -> str
        仅当 can_start 时认领成功：设 owner + status=in_progress；
        否则不改状态（返回含 blocked/未就绪 的提示）。

  6. complete_task(task_id, tasks_dir=None) -> str
        status=completed。

参考 learn-claude-code-main/s12_task_system/code.py 同名函数。
══════════════════════════════════════════════════════════════
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PASS, FAIL = "\033[32m✅\033[0m", "\033[31m❌\033[0m"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print((PASS if cond else FAIL) + f" {name}" + (f"  — {detail}" if detail and not cond else ""))


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    needed = ("Task", "create_task", "save_task", "load_task", "list_tasks", "can_start", "claim_task", "complete_task")
    for fn in needed:
        check(f"agent 暴露 {fn}", hasattr(agent, fn))
    check("agent 暴露 TASKS_DIR", hasattr(agent, "TASKS_DIR"))
    if not (all(hasattr(agent, fn) for fn in needed) and hasattr(agent, "TASKS_DIR")):
        return summarize()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        agent.TASKS_DIR = tmp

        a = agent.create_task("打地基", tasks_dir=tmp)
        b = agent.create_task("盖屋顶", blockedBy=[a.id], tasks_dir=tmp)
        check("create_task：落了 JSON 文件", any(tmp.glob("*.json")))
        check("create_task：初始 pending / 无 owner", a.status == "pending" and a.owner in (None, ""))

        # 持久化往返
        a2 = agent.load_task(a.id, tasks_dir=tmp)
        check("load_task：往返字段一致", a2.id == a.id and a2.subject == "打地基")
        check("list_tasks：读到 2 个", len(agent.list_tasks(tasks_dir=tmp)) == 2)

        # 依赖门控
        check("can_start：无依赖的 A 可开始", agent.can_start(a.id, tasks_dir=tmp) is True)
        check("can_start：A 未完成时 B 被挡", agent.can_start(b.id, tasks_dir=tmp) is False)

        # 认领被挡
        agent.claim_task(b.id, tasks_dir=tmp)
        check("claim_task：被挡的 B 不应变 in_progress", agent.load_task(b.id, tasks_dir=tmp).status != "in_progress")

        # 认领 A → 完成 A → 解锁 B
        agent.claim_task(a.id, owner="zhang", tasks_dir=tmp)
        a3 = agent.load_task(a.id, tasks_dir=tmp)
        check("claim_task：A 变 in_progress + 有 owner", a3.status == "in_progress" and a3.owner == "zhang")
        agent.complete_task(a.id, tasks_dir=tmp)
        check("complete_task：A 变 completed", agent.load_task(a.id, tasks_dir=tmp).status == "completed")
        check("依赖解锁：A 完成后 B 可开始", agent.can_start(b.id, tasks_dir=tmp) is True)

        # 缺失依赖视为 blocked
        c = agent.create_task("引用了不存在的依赖", blockedBy=["task_does_not_exist"], tasks_dir=tmp)
        check("can_start：缺失依赖视为 blocked", agent.can_start(c.id, tasks_dir=tmp) is False)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
