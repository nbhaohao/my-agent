#!/usr/bin/env python3
"""
s15 验收测试 —— Agent Teams（文件收件箱 MessageBus）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s15_agent_teams.py

全绿 = 达标。0 API、临时目录。只测团队的核心机制——消息总线（收发/消费）；
队友线程要调 LLM，留给你跑真 agent 时体会。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py）：

  1. MAILBOX_DIR：模块级 Path，邮箱 .jsonl 的默认目录。

  2. MessageBus 类（构造可接受 mailbox_dir=None，默认用 MAILBOX_DIR）：
       send(from_agent, to_agent, content, msg_type="message")
           往 {to_agent}.jsonl append 一行 JSON（含 from/to/content/type）。
       read_inbox(agent) -> list[dict]
           读出该 agent 的全部消息（按到达顺序），并【消费式删除】收件箱；
           收件箱不存在 → 返回 []。

  附加（s02 三步仪式）：把 "send_message" 注册进 TOOL_HANDLERS。

参考 learn-claude-code-main/s15_agent_teams/code.py 的 MessageBus.send / read_inbox。
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


# ── 集成块用的假 client ──────────────────────────────────────
class _TextBlock:
    def __init__(self, text):
        self.type, self.text = "text", text


class _ToolUseBlock:
    def __init__(self, name, input_data):
        self.type = "tool_use"
        self.id = "toolu_s15_01"
        self.name = name
        self.input = input_data


class _Resp:
    def __init__(self, stop_reason, content):
        self.stop_reason, self.content = stop_reason, content


class FakeClient:
    """第一轮返回 send_message 工具调用，第二轮 end_turn。
    验证 send_message handler 接进了 TOOL_HANDLERS，消息文件落盘。"""
    def __init__(self):
        self.calls = 0

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _Resp("tool_use", [_ToolUseBlock("send_message", {
                "to": "bob", "content": "集成测试消息", "msg_type": "message"
            })])
        return _Resp("end_turn", [_TextBlock("done")])


def integration_checks(agent):
    """── 集成：agent_loop 收到 send_message 调用时消息文件真的落盘 ──
    FakeClient 第一轮返回 send_message tool_use，断言：
      1. loop 正常完成
      2. MAILBOX_DIR 里出现了 bob.jsonl（消息持久化接通）。0 API。"""
    needed = ("agent_loop", "get_client", "HOOKS", "MAILBOX_DIR", "MessageBus")
    if not all(hasattr(agent, a) for a in needed):
        check("集成：agent 具备所需符号", False,
              f"缺: {[a for a in needed if not hasattr(agent, a)]}")
        return

    saved = {
        "hooks": {k: list(v) for k, v in agent.HOOKS.items()},
        "get_client": agent.get_client,
        "mem_dir": agent.MEMORY_DIR,
        "mailbox_dir": agent.MAILBOX_DIR,
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
            agent.MAILBOX_DIR = tmp
            agent.get_client = lambda: fake
            agent.rounds_since_todo = 0

            try:
                agent.agent_loop([{"role": "user", "content": "发一条消息给 bob"}])
                completed["ok"] = True
            except Exception as e:
                completed["err"] = str(e)

            mailbox_files = list(tmp.glob("*.jsonl"))
            check("集成：agent_loop 收到 send_message 调用后正常完成",
                  completed["ok"], completed.get("err", ""))
            check("集成：MAILBOX_DIR 里出现消息文件（持久化接通）",
                  len(mailbox_files) >= 1, f"jsonl 文件数={len(mailbox_files)}")
    finally:
        for k, v in saved["hooks"].items():
            agent.HOOKS[k] = v
        agent.get_client = saved["get_client"]
        agent.MEMORY_DIR = saved["mem_dir"]
        agent.MAILBOX_DIR = saved["mailbox_dir"]
        agent.rounds_since_todo = saved["rounds"]


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    check("agent 暴露 MessageBus", hasattr(agent, "MessageBus"))
    check("agent 暴露 MAILBOX_DIR", hasattr(agent, "MAILBOX_DIR"))
    if not (hasattr(agent, "MessageBus") and hasattr(agent, "MAILBOX_DIR")):
        return summarize()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        try:
            bus = agent.MessageBus(mailbox_dir=tmp)
        except TypeError:
            agent.MAILBOX_DIR = tmp
            bus = agent.MessageBus()

        bus.send("lead", "alice", "去写 API", "task")
        check("send：生成收件箱文件", any(tmp.glob("alice*.jsonl")) or (tmp / "alice.jsonl").exists())

        msgs = bus.read_inbox("alice")
        check("read_inbox：读到 1 条", len(msgs) == 1, f"got {len(msgs)}")
        if msgs:
            m = msgs[0]
            check("消息含 from/content/type", m.get("from") == "lead" and m.get("content") == "去写 API" and m.get("type") == "task", str(m))

        check("read_inbox：消费式——再读为空", bus.read_inbox("alice") == [])

        # 多条按序
        bus.send("alice", "lead", "API 写好了", "result")
        bus.send("bob", "lead", "测试通过", "result")
        lead_msgs = bus.read_inbox("lead")
        check("多条消息按到达顺序", [x.get("content") for x in lead_msgs] == ["API 写好了", "测试通过"], str([x.get("content") for x in lead_msgs]))

        check("read_inbox：空 agent 返回 []", bus.read_inbox("nobody") == [])

    handlers = getattr(agent, "TOOL_HANDLERS", {})
    check("TOOL_HANDLERS 注册了 'send_message'", "send_message" in handlers)

    # ── 集成：agent_loop 接线（用假 client，0 API）─────────────
    integration_checks(agent)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
