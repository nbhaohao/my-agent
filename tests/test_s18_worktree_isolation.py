#!/usr/bin/env python3
"""
s18 验收测试 —— Worktree Isolation（任务↔目录绑定 + 名字安全 + 生命周期审计）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s18_worktree_isolation.py

全绿 = 达标。0 API、0 真 git：git 边界（run_git / _count_worktree_changes）
被测试 monkeypatch 成假函数，只测【纯逻辑】——名字校验、绑定、keep/remove 决策、事件日志。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py）：

  0. WORKTREES_DIR：模块级 Path，worktree 目录与 events.jsonl 的根（导入时不要 mkdir）。
     Task 增加字段 worktree: str = ""（同步改 save_task / load_task 的字段，向后兼容 s12）。

  1. validate_worktree_name(name) -> str | None
        合法（仅 [A-Za-z0-9._-] 1-64 字符，且不是 "." / ".."）→ 返回 None；
        非法 → 返回错误信息字符串。含 "/" 的路径穿越、空串、超长都算非法。

  2. run_git(args: list[str]) -> tuple[bool, str]
        git 边界（真实跑 subprocess）。测试会把它换成假函数 —— 实现里务必
        以模块级名字调用（bare run_git(...)），别把它捕获进闭包，否则没法 monkeypatch。

  3. log_event(event_type, worktree_name, task_id="")
        往 WORKTREES_DIR/events.jsonl append 一行 JSON（含 type/worktree/task_id）。
        目录不存在时自动创建。

  4. create_worktree(name, task_id="") -> str
        ① validate_worktree_name，非法→返回 "Error: ..."（不调 run_git）
        ② 目录已存在→返回 "already exists"（不调 run_git）
        ③ run_git(["worktree","add",...]) 成功后：task_id 非空则 bind_task_to_worktree，
           并 log_event("create", name, task_id)。

  5. bind_task_to_worktree(task_id, worktree_name, tasks_dir=None)
        只写 task.worktree 字段并 save_task；【不改 status】（保持 pending 等队友认领）。

  6. _count_worktree_changes(path) -> tuple[int, int]
        (未提交文件数, 未推送提交数)。git 边界，测试会 monkeypatch。

  7. remove_worktree(name, discard_changes=False) -> str
        非法名→错误；目录不存在→ "not found"；
        not discard_changes 且 _count_worktree_changes 有改动（>0）→ 拒绝（不调 run_git remove）；
        否则 run_git(["worktree","remove",...]) + 删分支 + log_event("remove", name)。

  8. keep_worktree(name) -> str
        log_event("keep", name)，返回保留提示（分支留着等 review）。

  9. TOOL_HANDLERS 注册 "create_worktree" / "remove_worktree" / "keep_worktree"。

设计：本章测纯逻辑（选项 1），git 命令 mock 掉。真隔离效果留给你跑真 agent 时体会。
参考 learn-claude-code-main/s18_worktree_isolation/code.py 同名函数。
══════════════════════════════════════════════════════════════
"""
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PASS, FAIL = "\033[32m✅\033[0m", "\033[31m❌\033[0m"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print((PASS if cond else FAIL) + f" {name}" + (f"  — {detail}" if detail and not cond else ""))


def _read_events(wt_dir):
    f = wt_dir / "events.jsonl"
    if not f.is_file():
        return []
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]


# ── 集成块用的假 client ──────────────────────────────────────
class _TextBlock:
    def __init__(self, text):
        self.type, self.text = "text", text


class _ToolUseBlock:
    def __init__(self, name, input_data):
        self.type = "tool_use"
        self.id = "toolu_s18_01"
        self.name = name
        self.input = input_data


class _Resp:
    def __init__(self, stop_reason, content):
        self.stop_reason, self.content = stop_reason, content


class FakeClient:
    """第一轮返回 create_worktree 工具调用，第二轮 end_turn。
    （run_git 已被换成假成功，验证 handler 接进 TOOL_HANDLERS 且事件落盘。）"""
    def __init__(self):
        self.calls = 0

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _Resp("tool_use", [_ToolUseBlock("create_worktree", {"name": "feat-x"})])
        return _Resp("end_turn", [_TextBlock("done")])


def integration_checks(agent):
    """── 集成：agent_loop 收到 create_worktree 调用时事件落盘（git 已 mock） ──
    断言：loop 正常完成 + WORKTREES_DIR/events.jsonl 出现 create 事件。0 API、0 真 git。"""
    needed = ("agent_loop", "get_client", "HOOKS", "WORKTREES_DIR",
              "create_worktree", "run_git")
    if not all(hasattr(agent, a) for a in needed):
        check("集成：agent 具备所需符号", False,
              f"缺: {[a for a in needed if not hasattr(agent, a)]}")
        return

    saved = {
        "hooks": {k: list(v) for k, v in agent.HOOKS.items()},
        "get_client": agent.get_client,
        "mem_dir": agent.MEMORY_DIR,
        "wt_dir": agent.WORKTREES_DIR,
        "run_git": agent.run_git,
        "rounds": getattr(agent, "rounds_since_todo", 0),
    }
    fake = FakeClient()
    completed = {"ok": False}
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for k in agent.HOOKS:
                agent.HOOKS[k] = []
            agent.MEMORY_DIR = tmp
            agent.WORKTREES_DIR = tmp
            agent.run_git = lambda args: (True, "(fake ok)")
            agent.get_client = lambda: fake
            agent.rounds_since_todo = 0

            try:
                agent.agent_loop([{"role": "user", "content": "建个 worktree"}])
                completed["ok"] = True
            except Exception as e:
                completed["err"] = str(e)

            evs = _read_events(tmp)
            check("集成：agent_loop 收到 create_worktree 后正常完成",
                  completed["ok"], completed.get("err", ""))
            check("集成：events.jsonl 出现 create 事件（handler 接进 loop）",
                  any(e.get("type") == "create" and e.get("worktree") == "feat-x" for e in evs),
                  f"events={evs}")
    finally:
        for k, v in saved["hooks"].items():
            agent.HOOKS[k] = v
        agent.get_client = saved["get_client"]
        agent.MEMORY_DIR = saved["mem_dir"]
        agent.WORKTREES_DIR = saved["wt_dir"]
        agent.run_git = saved["run_git"]
        agent.rounds_since_todo = saved["rounds"]


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    needed = ("validate_worktree_name", "create_worktree", "bind_task_to_worktree",
              "remove_worktree", "keep_worktree", "log_event", "run_git",
              "_count_worktree_changes", "WORKTREES_DIR")
    for sym in needed:
        check(f"agent 暴露 {sym}", hasattr(agent, sym))
    if not all(hasattr(agent, s) for s in needed):
        return summarize()

    # ── validate_worktree_name：纯校验 ──
    v = agent.validate_worktree_name
    check("validate：合法名 → None", v("auth-refactor") is None)
    check("validate：带点下划线合法", v("feat_1.2-x") is None)
    check("validate：空串非法", v("") is not None)
    check("validate：'..' 非法（防穿越）", v("..") is not None)
    check("validate：含 '/' 非法（防穿越）", v("../etc/passwd") is not None)
    check("validate：超长(>64)非法", v("a" * 65) is not None)

    saved = {
        "wt_dir": agent.WORKTREES_DIR,
        "tasks": agent.TASKS_DIR,
        "run_git": agent.run_git,
        "count": agent._count_worktree_changes,
    }
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            agent.WORKTREES_DIR = tmp
            agent.TASKS_DIR = tmp

            # 记录 run_git 调用
            git_calls = []
            agent.run_git = lambda args: (git_calls.append(list(args)), (True, "(ok)"))[1]

            # ── create_worktree：非法名不调 git ──
            git_calls.clear()
            r_bad = agent.create_worktree("../evil")
            check("create：非法名返回 Error", "error" in r_bad.lower(), f"r={r_bad!r}")
            check("create：非法名不触发 git", len(git_calls) == 0, f"calls={git_calls}")

            # ── create_worktree + bind task：成功路径 ──
            git_calls.clear()
            t = agent.create_task("重构认证", tasks_dir=tmp)
            r_ok = agent.create_worktree("auth", task_id=t.id)
            check("create：成功路径调了 git worktree add",
                  any("worktree" in c and "add" in c for c in git_calls), f"calls={git_calls}")
            bound = agent.load_task(t.id, tasks_dir=tmp)
            check("create：task 绑定了 worktree 字段", bound.worktree == "auth",
                  f"worktree={bound.worktree!r}")
            check("create：绑定不改 status（仍 pending 等认领）",
                  bound.status == "pending", f"status={bound.status}")
            evs = _read_events(tmp)
            check("create：写了 create 事件", any(e.get("type") == "create" for e in evs))

            # ── 目录已存在 → 不调 git ──
            git_calls.clear()
            (tmp / "dup").mkdir()
            r_dup = agent.create_worktree("dup")
            check("create：目录已存在则不调 git", len(git_calls) == 0, f"calls={git_calls}")

            # ── bind_task_to_worktree：只写字段、不改状态 ──
            t2 = agent.create_task("独立任务", tasks_dir=tmp)
            agent.claim_task(t2.id, owner="alice", tasks_dir=tmp)  # 先变 in_progress
            agent.bind_task_to_worktree(t2.id, "ui-login", tasks_dir=tmp)
            b2 = agent.load_task(t2.id, tasks_dir=tmp)
            check("bind：写入 worktree 字段", b2.worktree == "ui-login")
            check("bind：不改 status（保持 in_progress 不被重置）",
                  b2.status == "in_progress", f"status={b2.status}")

            # ── remove_worktree：有改动 → 拒绝 ──
            (tmp / "auth").mkdir(exist_ok=True)
            git_calls.clear()
            agent._count_worktree_changes = lambda p: (2, 0)  # 2 个未提交文件
            r_refuse = agent.remove_worktree("auth", discard_changes=False)
            check("remove：有改动且未 discard → 返回拒绝信息",
                  "discard" in r_refuse.lower() or "uncommitted" in r_refuse.lower()
                  or "改动" in r_refuse,
                  f"r={r_refuse!r}")
            check("remove：拒绝时不调 git remove",
                  not any("remove" in c for c in git_calls), f"calls={git_calls}")

            # ── remove_worktree：无改动 → 删除 + 事件 ──
            git_calls.clear()
            agent._count_worktree_changes = lambda p: (0, 0)
            r_rm = agent.remove_worktree("auth", discard_changes=False)
            check("remove：无改动 → 调 git worktree remove",
                  any("worktree" in c and "remove" in c for c in git_calls), f"calls={git_calls}")
            evs2 = _read_events(tmp)
            check("remove：写了 remove 事件", any(e.get("type") == "remove" for e in evs2))

            # ── remove_worktree：discard_changes 强制删 ──
            (tmp / "force").mkdir()
            git_calls.clear()
            agent._count_worktree_changes = lambda p: (5, 2)  # 一堆改动
            r_force = agent.remove_worktree("force", discard_changes=True)
            check("remove：discard_changes=True 跳过改动检查、强制删",
                  any("remove" in c for c in git_calls), f"calls={git_calls}")

            # ── keep_worktree：记 keep 事件 ──
            agent.keep_worktree("auth")
            evs3 = _read_events(tmp)
            check("keep：写了 keep 事件", any(e.get("type") == "keep" for e in evs3))

            # ── 事件按时间顺序累积 ──
            types = [e.get("type") for e in _read_events(tmp)]
            check("events.jsonl：按发生顺序累积多条", types[0] == "create" and "keep" in types,
                  f"types={types}")
    finally:
        agent.WORKTREES_DIR = saved["wt_dir"]
        agent.TASKS_DIR = saved["tasks"]
        agent.run_git = saved["run_git"]
        agent._count_worktree_changes = saved["count"]

    # TOOL_HANDLERS 注册
    handlers = getattr(agent, "TOOL_HANDLERS", {})
    for name in ("create_worktree", "remove_worktree", "keep_worktree"):
        check(f"TOOL_HANDLERS 注册了 '{name}'", name in handlers)

    # ── 集成：agent_loop 接线（假 client + 假 git，0 API）─────────────
    integration_checks(agent)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
