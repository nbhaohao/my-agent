#!/usr/bin/env python3
"""
s20 验收测试 —— Comprehensive Agent（机制很多，循环一个）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s20_comprehensive.py

全绿 = 达标。0 API。这是收口章：不发明新机制，而是验证 s07–s19 的机制
【真的挂在同一个 agent_loop 上】。重点补齐三处之前只写了函数、loop 却没接的缺口。

══════════════════════════════════════════════════════════════
契约（改 my-agent/agent.py 的 agent_loop，把三样接进去）：

  A. 后台派发：工具执行时，若 should_run_background(name, input) 为真，
     用 start_background_task 把它丢后台（fn 用默认参数绑定 handler/input 避免闭包晚绑定），
     当轮 tool_result 返回占位串（含 bg id / "background"），不同步阻塞。

  B. 后台通知注入：每轮调用 LLM 前，notif = collect_background_results()，
     非空则把它作为一条 user 消息（task_notification）追加进 messages，
     让模型在下一轮看到后台任务的结果。

  C. prompt-too-long 恢复：with_retry 只兜可重试错误（429/529 等）；
     prompt 过长不可重试。agent_loop 要在外层捕获 is_prompt_too_long_error，
     调 reactive_compact(messages) 压缩后重试，而不是直接崩。

  完整性：s07–s19 的关键符号都应已存在（本测试做一次"点名"）。

  实现提示：should_run_background / start_background_task / collect_background_results /
  reactive_compact / is_prompt_too_long_error 都以【模块级名字】调用（便于本测试 monkeypatch）。
参考 learn-claude-code-main/s20_comprehensive/code.py 的 agent_loop 组织方式。
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


# ── 假 client 基件 ──────────────────────────────────────────
class _TextBlock:
    def __init__(self, text):
        self.type, self.text = "text", text


class _ToolUseBlock:
    def __init__(self, name, input_data, id="toolu_s20"):
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = input_data


class _Resp:
    def __init__(self, stop_reason, content):
        self.stop_reason, self.content = stop_reason, content


def _messages_text(messages):
    """把 messages 拍平成可搜索的字符串。"""
    return str(messages)


def _save_env(agent):
    return {
        "hooks": {k: list(v) for k, v in agent.HOOKS.items()},
        "get_client": agent.get_client,
        "mem_dir": agent.MEMORY_DIR,
        "rounds": getattr(agent, "rounds_since_todo", 0),
        "should_bg": agent.should_run_background,
        "start_bg": agent.start_background_task,
        "collect_bg": agent.collect_background_results,
        "reactive": agent.reactive_compact,
    }


def _restore_env(agent, saved):
    for k, v in saved["hooks"].items():
        agent.HOOKS[k] = v
    agent.get_client = saved["get_client"]
    agent.MEMORY_DIR = saved["mem_dir"]
    agent.rounds_since_todo = saved["rounds"]
    agent.should_run_background = saved["should_bg"]
    agent.start_background_task = saved["start_bg"]
    agent.collect_background_results = saved["collect_bg"]
    agent.reactive_compact = saved["reactive"]


def check_background_dispatch(agent):
    """A. 慢/显式后台工具 → loop 走 start_background_task、返回占位、不同步执行。"""
    saved = _save_env(agent)
    bg = {"started": 0, "fn_ran": False}

    class FC:
        def __init__(self): self.calls = 0
        @property
        def messages(self): return self
        def create(self, **kw):
            self.calls += 1
            if self.calls == 1:
                # "echo install" 含慢关键词，但即便红态下同步执行也无害（瞬间 echo，不联网）
                return _Resp("tool_use", [_ToolUseBlock("bash", {"command": "echo install"})])
            return _Resp("end_turn", [_TextBlock("done")])

    fc = FC()  # 共享同一实例（lambda: FC() 会每次新建、calls 永远归零 → 死循环）

    def fake_start(fn, command=""):
        bg["started"] += 1
        return "bg_0001"  # 不执行 fn → 0 副作用

    # 把 bash handler 换成假的：即使红态下 loop 同步执行，也不真跑 subprocess（沙箱里会挂）
    saved_bash = agent.TOOL_HANDLERS.get("bash")
    try:
        with tempfile.TemporaryDirectory() as td:
            for k in agent.HOOKS: agent.HOOKS[k] = []
            agent.MEMORY_DIR = Path(td)
            agent.rounds_since_todo = 0
            agent.get_client = lambda: fc
            agent.should_run_background = lambda name, inp: True   # 强制判定为后台
            agent.start_background_task = fake_start
            agent.collect_background_results = lambda: ""
            agent.TOOL_HANDLERS["bash"] = lambda **_: "FAKE_BASH"  # 不跑真 subprocess

            msgs = [{"role": "user", "content": "装个包"}]
            agent.agent_loop(msgs)

            check("A 后台派发：loop 调用了 start_background_task", bg["started"] >= 1,
                  f"started={bg['started']}（loop 可能没接后台派发）")
            check("A 后台派发：tool_result 是占位（含 bg id / background）",
                  "bg_0001" in _messages_text(msgs) or "background" in _messages_text(msgs).lower(),
                  "占位结果没回到 messages")
    finally:
        if saved_bash is not None:
            agent.TOOL_HANDLERS["bash"] = saved_bash
        _restore_env(agent, saved)


def check_notification_injection(agent):
    """B. LLM 前 collect_background_results 的输出被注入 messages。"""
    saved = _save_env(agent)

    class FC:
        @property
        def messages(self): return self
        def create(self, **kw):
            return _Resp("end_turn", [_TextBlock("done")])

    try:
        with tempfile.TemporaryDirectory() as td:
            for k in agent.HOOKS: agent.HOOKS[k] = []
            agent.MEMORY_DIR = Path(td)
            agent.rounds_since_todo = 0
            agent.get_client = lambda: FC()
            agent.collect_background_results = lambda: "ZZ_NOTIF_ZZ 后台任务完成"

            msgs = [{"role": "user", "content": "hi"}]
            agent.agent_loop(msgs)

            check("B 通知注入：collect_background_results 的输出进了 messages",
                  "ZZ_NOTIF_ZZ" in _messages_text(msgs),
                  "loop 没在 LLM 前注入后台通知")
    finally:
        _restore_env(agent, saved)


def check_prompt_too_long_recovery(agent):
    """C. prompt 过长 → reactive_compact 后重试，而不是崩。"""
    saved = _save_env(agent)
    spy = {"reactive": 0}
    orig_reactive = agent.reactive_compact

    def spy_reactive(messages, summarizer=None, keep_recent=5):
        spy["reactive"] += 1
        return orig_reactive(messages, summarizer=summarizer, keep_recent=keep_recent)

    class FC:
        def __init__(self): self.calls = 0
        @property
        def messages(self): return self
        def create(self, **kw):
            self.calls += 1
            if self.calls == 1:
                raise Exception("prompt is too long: 413")
            return _Resp("end_turn", [_TextBlock("recovered")])

    fc = FC()
    completed = {"ok": False}
    try:
        with tempfile.TemporaryDirectory() as td:
            for k in agent.HOOKS: agent.HOOKS[k] = []
            agent.MEMORY_DIR = Path(td)
            agent.rounds_since_todo = 0
            agent.get_client = lambda: fc
            agent.reactive_compact = spy_reactive

            msgs = [{"role": "user", "content": "x" * 100}]
            try:
                agent.agent_loop(msgs)
                completed["ok"] = True
            except Exception as e:
                completed["err"] = str(e)

            check("C prompt 过长：loop 没崩、最终完成", completed["ok"],
                  completed.get("err", "loop 没接 prompt-too-long 恢复"))
            check("C prompt 过长：调用了 reactive_compact", spy["reactive"] >= 1,
                  f"reactive 调用次数={spy['reactive']}")
            check("C prompt 过长：恢复后重试了 create", fc.calls >= 2, f"create 次数={fc.calls}")
    finally:
        _restore_env(agent, saved)


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    # ── 完整性点名：s07–s19 的关键符号都在 ──
    roll_call = [
        # s08 压缩
        "tool_result_budget", "snip_compact", "micro_compact", "compact_history",
        # s09 记忆 / s07 技能
        "MEMORY_DIR", "_load_relevant_memories", "SKILL_REGISTRY",
        # s10 system prompt
        "assemble_system_prompt", "PROMPT_SECTIONS",
        # s11 错误恢复
        "with_retry", "retry_delay", "is_retryable_error",
        "is_prompt_too_long_error", "reactive_compact",
        # s12 任务 / s17 自治
        "Task", "create_task", "claim_task", "can_start", "TASKS_DIR",
        "scan_unclaimed_tasks", "idle_poll",
        # s13 后台 / s14 cron
        "should_run_background", "start_background_task", "collect_background_results",
        "cron_matches",
        # s15 团队 / s16 协议
        "MessageBus", "MAILBOX_DIR", "ProtocolState", "match_response", "consume_lead_inbox",
        # s18 worktree / s19 MCP
        "create_worktree", "validate_worktree_name", "bind_task_to_worktree",
        "MCPClient", "connect_mcp", "assemble_tool_pool", "mcp_clients",
        # 核心
        "agent_loop", "TOOLS", "TOOL_HANDLERS", "HOOKS", "trigger_hooks",
    ]
    missing = [s for s in roll_call if not hasattr(agent, s)]
    check(f"完整性：s07–s19 关键符号全部就位（{len(roll_call)} 个）", not missing,
          f"缺: {missing}")

    needed = ("agent_loop", "should_run_background", "start_background_task",
              "collect_background_results", "reactive_compact", "is_prompt_too_long_error",
              "get_client", "HOOKS")
    if not all(hasattr(agent, a) for a in needed):
        check("接线前置符号齐备", False, f"缺: {[a for a in needed if not hasattr(agent, a)]}")
        return summarize()

    # ── 三处收口接线（用假 client + monkeypatch，0 API、0 副作用）──
    check_background_dispatch(agent)
    check_notification_injection(agent)
    check_prompt_too_long_recovery(agent)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
