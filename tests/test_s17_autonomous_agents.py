#!/usr/bin/env python3
"""
s17 验收测试 —— Autonomous Agents（自己看板、自己认领 + WORK/IDLE 生命周期）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s17_autonomous_agents.py

全绿 = 达标。0 API、临时目录、可注入 sleep（不真等）。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py）：

  1. scan_unclaimed_tasks(tasks_dir=None) -> list[Task]
        扫描任务看板，返回【可认领】的任务：status=="pending" 且 owner 为空
        且 can_start(...) 为 True（依赖全完成）。按文件名排序。

  2. claim_task 升级（改现有函数，保持 s12 测试向后兼容）：
        - task.owner 非空 → 返回含 "owned" 的提示，不改状态（防并发抢占）
        - task.status != "pending" → 返回提示，不改
        - 被未完成依赖阻塞（can_start False）→ 返回 blocked 提示
        - 否则 → status=in_progress + owner，落盘

  3. idle_poll(agent_name, messages, poll_interval=5, timeout=60, sleep=time.sleep) -> str
        队友空闲时的轮询循环，返回 "work" / "shutdown" / "timeout"。
        循环 timeout // poll_interval 次，每次先 sleep(poll_interval)，再：
          ① 读自己的收件箱（MessageBus().read_inbox(agent_name)）：
             - 有 type=="shutdown_request" 的消息 → 回 shutdown_response 给 "lead"
               （metadata 含 {request_id, approve:True}），返回 "shutdown"
             - 有其它消息 → 注入 messages（<inbox>...</inbox>），返回 "work"
          ② 否则扫看板 scan_unclaimed_tasks()：有可认领 → claim_task(task.id, agent_name)，
             成功（结果含 "claimed"）→ 注入 messages，返回 "work"
        全程没活也没消息 → 返回 "timeout"。sleep 可注入（测试传 no-op）。

  4. TOOL_HANDLERS 注册 "scan_unclaimed_tasks"（Lead 可查看板上谁都没领的活）。

设计说明：idle_poll 是【队友外层循环】，不在 Lead 的 agent_loop 里——和 s15/s16
团队机制一样属于"团队工具箱"。集成块因此验证「自治循环」本身（idle_poll 把
scan + claim 串起来、读活的 MAILBOX_DIR/TASKS_DIR），而非走 agent_loop。
参考 learn-claude-code-main/s17_autonomous_agents/code.py 的
scan_unclaimed_tasks / claim_task / idle_poll。
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


def integration_checks(agent):
    """── 集成：自治循环把 scan + claim + idle_poll 串起来（读活的全局目录） ──
    在临时目录放一个可认领任务，调 idle_poll（no-op sleep）：
      1. 返回 "work"（找到活了）
      2. 任务被该 agent 认领（in_progress + owner）
      3. messages 注入了认领记录
    再放一个 shutdown_request，验证 idle 阶段能直接响应关机。0 API、不真 sleep。"""
    needed = ("scan_unclaimed_tasks", "idle_poll", "claim_task", "create_task",
              "MessageBus", "MAILBOX_DIR", "TASKS_DIR")
    if not all(hasattr(agent, a) for a in needed):
        check("集成：agent 具备所需符号", False,
              f"缺: {[a for a in needed if not hasattr(agent, a)]}")
        return

    saved = {"mailbox": agent.MAILBOX_DIR, "tasks": agent.TASKS_DIR}
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            agent.MAILBOX_DIR = tmp
            agent.TASKS_DIR = tmp

            # 看板放一个可认领任务（无依赖、无 owner）
            t = agent.create_task("自治任务", tasks_dir=tmp)
            msgs = []
            r = agent.idle_poll("alice", msgs, poll_interval=1, timeout=2,
                                sleep=lambda s: None)
            claimed = agent.load_task(t.id, tasks_dir=tmp)
            check("集成：idle_poll 发现看板任务 → 返回 work",
                  r == "work", f"return={r!r}")
            check("集成：任务被 alice 自动认领（in_progress + owner）",
                  claimed.status == "in_progress" and claimed.owner == "alice",
                  f"status={claimed.status} owner={claimed.owner!r}")
            check("集成：认领记录注入了 messages", len(msgs) >= 1, f"messages={msgs}")

            # IDLE 阶段收到 shutdown_request → 直接响应关机
            agent.MessageBus(mailbox_dir=tmp).send(
                "lead", "bob", "请关机", "shutdown_request", {"request_id": "req_idle"})
            msgs2 = []
            r2 = agent.idle_poll("bob", msgs2, poll_interval=1, timeout=2,
                                 sleep=lambda s: None)
            lead_inbox = agent.MessageBus(mailbox_dir=tmp).read_inbox("lead")
            check("集成：IDLE 收到 shutdown_request → 返回 shutdown", r2 == "shutdown",
                  f"return={r2!r}")
            check("集成：idle 阶段回了 shutdown_response 给 lead（带 approve）",
                  any(m.get("type") == "shutdown_response"
                      and m.get("metadata", {}).get("approve") is True
                      for m in lead_inbox), str(lead_inbox))
    finally:
        agent.MAILBOX_DIR = saved["mailbox"]
        agent.TASKS_DIR = saved["tasks"]


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    needed = ("scan_unclaimed_tasks", "idle_poll")
    for sym in needed:
        check(f"agent 暴露 {sym}", hasattr(agent, sym))
    if not all(hasattr(agent, s) for s in needed):
        return summarize()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ── scan_unclaimed_tasks ──
        a = agent.create_task("打地基", tasks_dir=tmp)
        b = agent.create_task("盖屋顶", blockedBy=[a.id], tasks_dir=tmp)
        unclaimed = agent.scan_unclaimed_tasks(tasks_dir=tmp)
        ids = {t.id for t in unclaimed}
        check("scan：无依赖的 A 在可认领列表", a.id in ids)
        check("scan：被未完成依赖挡住的 B 不在列表", b.id not in ids, f"ids={ids}")

        # 认领 A → A 有 owner，不再可认领
        agent.claim_task(a.id, owner="alice", tasks_dir=tmp)
        ids2 = {t.id for t in agent.scan_unclaimed_tasks(tasks_dir=tmp)}
        check("scan：已认领（有 owner）的 A 不再出现", a.id not in ids2, f"ids={ids2}")
        check("scan：B 仍被挡（A 还没 complete）", b.id not in ids2)

        # 完成 A → B 解锁，进入可认领
        agent.complete_task(a.id, tasks_dir=tmp)
        ids3 = {t.id for t in agent.scan_unclaimed_tasks(tasks_dir=tmp)}
        check("scan：A 完成后 B 解锁、进入可认领", b.id in ids3, f"ids={ids3}")

        # ── claim_task 升级：owner / status 检查 ──
        c = agent.create_task("独立任务", tasks_dir=tmp)
        r1 = agent.claim_task(c.id, owner="alice", tasks_dir=tmp)
        check("claim：首次认领成功", "claimed" in r1.lower(),
              f"r1={r1!r}")
        c_after = agent.load_task(c.id, tasks_dir=tmp)
        check("claim：认领后 in_progress + owner=alice",
              c_after.status == "in_progress" and c_after.owner == "alice")

        # bob 来抢已被 alice 认领的任务：必须被拒（status!=pending 或 owner 检查都行）
        r2 = agent.claim_task(c.id, owner="bob", tasks_dir=tmp)
        check("claim：已被 alice 认领 → 拒绝 bob（防并发抢占）",
              "claimed by bob" not in r2.lower(), f"r2={r2!r}")
        c_still = agent.load_task(c.id, tasks_dir=tmp)
        check("claim：被拒后 owner 仍是 alice（没被覆盖）", c_still.owner == "alice")

        # 已完成的任务不能再认领
        agent.complete_task(c.id, tasks_dir=tmp)
        r3 = agent.claim_task(c.id, owner="carol", tasks_dir=tmp)
        check("claim：completed 任务不可认领", "claimed" not in r3.lower(), f"r3={r3!r}")

    # ── idle_poll：三种结局 ──
    saved = {"mailbox": agent.MAILBOX_DIR, "tasks": agent.TASKS_DIR}
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            agent.MAILBOX_DIR = tmp
            agent.TASKS_DIR = tmp
            noop = lambda s: None

            # (a) 空看板 + 空收件箱 → timeout
            msgs = []
            r = agent.idle_poll("alice", msgs, poll_interval=1, timeout=2, sleep=noop)
            check("idle_poll：无活无消息 → timeout", r == "timeout", f"r={r!r}")

            # (b) 看板有可认领任务 → work + 自动认领
            t = agent.create_task("待认领", tasks_dir=tmp)
            msgs_b = []
            rb = agent.idle_poll("alice", msgs_b, poll_interval=1, timeout=2, sleep=noop)
            tb = agent.load_task(t.id, tasks_dir=tmp)
            check("idle_poll：发现看板任务 → work", rb == "work", f"r={rb!r}")
            check("idle_poll：自动认领（in_progress + owner=alice）",
                  tb.status == "in_progress" and tb.owner == "alice")

            # (c) 收件箱有普通消息 → work + 注入
            agent.MessageBus(mailbox_dir=tmp).send("lead", "alice", "新指示", "message")
            msgs_c = []
            rc = agent.idle_poll("alice", msgs_c, poll_interval=1, timeout=2, sleep=noop)
            check("idle_poll：收件箱普通消息 → work", rc == "work", f"r={rc!r}")
            check("idle_poll：普通消息注入了 messages", len(msgs_c) >= 1)

            # (d) 收件箱有 shutdown_request → shutdown + 回响应
            agent.MessageBus(mailbox_dir=tmp).send(
                "lead", "alice", "关机", "shutdown_request", {"request_id": "req_q"})
            msgs_d = []
            rd = agent.idle_poll("alice", msgs_d, poll_interval=1, timeout=2, sleep=noop)
            lead_box = agent.MessageBus(mailbox_dir=tmp).read_inbox("lead")
            check("idle_poll：收到 shutdown_request → shutdown", rd == "shutdown", f"r={rd!r}")
            check("idle_poll：回了 shutdown_response 给 lead（request_id 一致）",
                  any(m.get("type") == "shutdown_response"
                      and m.get("metadata", {}).get("request_id") == "req_q"
                      for m in lead_box), str(lead_box))
    finally:
        agent.MAILBOX_DIR = saved["mailbox"]
        agent.TASKS_DIR = saved["tasks"]

    # TOOL_HANDLERS 注册
    handlers = getattr(agent, "TOOL_HANDLERS", {})
    check("TOOL_HANDLERS 注册了 'scan_unclaimed_tasks'", "scan_unclaimed_tasks" in handlers)

    # ── 集成：自治循环（idle_poll 串起 scan + claim，0 API）─────────────
    integration_checks(agent)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
