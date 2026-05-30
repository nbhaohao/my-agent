#!/usr/bin/env python3
"""
s08 验收测试 —— Context Compact（上下文四层压缩管线）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s08_compact.py

全绿（✅）= 今天这个机制达标。红（❌）= 还没做完。

设计原则：便宜的先跑，贵的后跑。
前三层（snip / micro / budget）是纯文本/结构操作，0 API 调用 —— 所以本测试
又快又稳、不花钱。第四层 compact_history 真实场景要调 LLM，但我们让它接受一个
可注入的 summarizer，测试里传个假摘要器，于是整套测试完全不碰网络。

══════════════════════════════════════════════════════════════
你需要在 my-agent/agent.py 里实现以下契约（函数名/参数名要对上）：

  1. snip_compact(messages, max_messages=50) -> list
        消息数 <= max_messages：原样返回。
        超了：保留头 3 条 + 一条占位（含 "snipped"）+ 尾 (max_messages-3) 条。

  2. micro_compact(messages, keep_recent=3) -> list
        扫描所有 tool_result 块。最近 keep_recent 条保留完整内容；
        更旧的、且内容较长（>120 字符）的，content 换成占位串（含 "compacted"）。

  3. tool_result_budget(messages, max_bytes=200_000) -> list
        看「最后一条消息」里所有 tool_result 的总字节。没超：原样返回。
        超了：从最大的开始落盘/截断，直到总量回到预算内；被处理的块 content
        里要留一个标记（含 "persisted" 或 "落盘"）。完整内容不再内联。

  4. compact_history(messages, summarizer=None) -> list
        summarizer 是 callable(messages)->str（默认走 LLM，测试里注入假的）。
        返回「单条」消息列表：[{"role":"user","content": 含摘要文本}]。

  附加（s02 三步仪式）：把 "compact" 注册进 TOOL_HANDLERS。

提示：参考 learn-claude-code-main/s08_context_compact/code.py 的
      snip_compact / micro_compact / tool_result_budget / compact_history，
      但要写进你自己的 agent.py，自己理解每一行 + 顺序为什么不能换。
══════════════════════════════════════════════════════════════
"""

import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

PASS, FAIL = "\033[32m✅\033[0m", "\033[31m❌\033[0m"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    mark = PASS if cond else FAIL
    print(f"{mark} {name}" + (f"  — {detail}" if detail and not cond else ""))


def _tool_result(tool_id, text):
    """构造一条带 tool_result 的 user 消息。"""
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}],
    }


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    for fn in (
        "snip_compact",
        "micro_compact",
        "tool_result_budget",
        "compact_history",
    ):
        check(f"agent 暴露 {fn}()", hasattr(agent, fn))
    if not all(
        hasattr(agent, fn)
        for fn in (
            "snip_compact",
            "micro_compact",
            "tool_result_budget",
            "compact_history",
        )
    ):
        summarize()
        return

    # ── L1: snip_compact ───────────────────────────────────────
    short = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    check(
        "snip_compact：未超阈值时原样返回",
        agent.snip_compact(short, max_messages=50) == short,
    )

    long_msgs = [{"role": "user", "content": f"m{i}"} for i in range(120)]
    snipped = agent.snip_compact(long_msgs, max_messages=50)
    check(
        "snip_compact：超阈值后变短",
        len(snipped) < len(long_msgs),
        f"len = {len(snipped)}",
    )
    check("snip_compact：保留了头部首条（m0）", snipped[0].get("content") == "m0")
    check("snip_compact：保留了尾部末条（m119）", snipped[-1].get("content") == "m119")
    check(
        "snip_compact：中间插入了 snipped 占位",
        any("snipped" in str(m.get("content", "")) for m in snipped),
    )

    # ── L2: micro_compact ──────────────────────────────────────
    big = "X" * 500
    msgs = [_tool_result(f"t{i}", big) for i in range(6)]  # 6 条工具结果
    out = agent.micro_compact(msgs, keep_recent=3)
    recent_full = [m for m in out[-3:] if m["content"][0]["content"] == big]
    check("micro_compact：最近 3 条 tool_result 内容保留完整", len(recent_full) == 3)
    old_blocks = [m["content"][0]["content"] for m in out[:3]]
    check(
        "micro_compact：更旧的 tool_result 被占位（含 compacted）",
        all("compacted" in str(c).lower() for c in old_blocks),
        f"old = {old_blocks!r}",
    )

    few = [_tool_result(f"t{i}", big) for i in range(3)]
    out_few = agent.micro_compact(few, keep_recent=3)
    check(
        "micro_compact：不超过 keep_recent 时不动",
        all(m["content"][0]["content"] == big for m in out_few),
    )

    # ── L3: tool_result_budget ─────────────────────────────────
    small_msgs = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": "tiny"}],
        }
    ]
    check(
        "tool_result_budget：未超预算原样返回",
        agent.tool_result_budget(small_msgs, max_bytes=200_000) == small_msgs,
    )

    huge_last = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "big1", "content": "A" * 5000},
            {"type": "tool_result", "tool_use_id": "big2", "content": "B" * 5000},
        ],
    }
    budgeted = agent.tool_result_budget(
        [{"role": "user", "content": "hi"}, huge_last], max_bytes=2000
    )
    total_after = sum(
        len(str(b.get("content", "")))
        for b in budgeted[-1]["content"]
        if b.get("type") == "tool_result"
    )
    check(
        "tool_result_budget：超预算后总量被压到预算内",
        total_after <= 2000,
        f"total_after = {total_after}",
    )
    check(
        "tool_result_budget：被处理的块留下落盘标记",
        any(
            "persisted" in str(b.get("content", "")).lower()
            or "落盘" in str(b.get("content", ""))
            for b in budgeted[-1]["content"]
        ),
    )

    # ── L4: compact_history（注入假摘要器，0 API）──────────────
    history = [
        {"role": "user", "content": "做个网站"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "再加登录"},
    ]
    fake = lambda msgs: "FAKE_SUMMARY_目标=做网站+登录"
    compacted = agent.compact_history(history, summarizer=fake)
    check(
        "compact_history：压成单条消息", len(compacted) == 1, f"len = {len(compacted)}"
    )
    check(
        "compact_history：摘要内容进入了那条消息",
        "FAKE_SUMMARY" in str(compacted[0].get("content", "")),
    )
    check("compact_history：那条是 user 角色", compacted[0].get("role") == "user")

    # ── 附加：compact 工具注册（s02 三步仪式）──────────────────
    handlers = getattr(agent, "TOOL_HANDLERS", {})
    check("TOOL_HANDLERS 注册了 'compact'", "compact" in handlers)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(
        f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else ""
    )
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
