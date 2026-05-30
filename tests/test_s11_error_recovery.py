#!/usr/bin/env python3
"""
s11 验收测试 —— Error Recovery（重试 / 退避 / 兜底 / reactive 压缩）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s11_error_recovery.py

全绿 = 达标。0 API、不真 sleep：fn 与 sleep 都可注入，测试里传假的。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py）：

  1. retry_delay(attempt, retry_after=None) -> float（秒）
        retry_after 非空 → 直接返回它（尊重服务端 Retry-After）。
        否则指数退避：base = min(500*2**attempt, 32000)/1000，
        再加 [0, base*0.25] 的随机抖动。

  2. is_retryable_error(e) -> bool
        str(e) 含 429 / 529 / overloaded / rate_limit 之一 → True。

  3. is_prompt_too_long_error(e) -> bool
        str(e) 含 prompt_too_long / "prompt is too long" / 413 之一 → True。

  4. with_retry(fn, max_retries=10, sleep=time.sleep) -> Any
        调用 fn()；抛可重试错误 → sleep(retry_delay(...)) 后重试；
        成功就返回；超过 max_retries 仍失败 → 把最后异常抛出。
        sleep 可注入（测试传 no-op，不真等）。

  5. reactive_compact(messages, summarizer=None, keep_recent=5) -> list
        比 s08 更激进：保留最后 keep_recent 条 + 一条摘要消息（summarizer 可注入）。

参考 learn-claude-code-main/s11_error_recovery/code.py 的
retry_delay / with_retry / is_prompt_too_long_error / reactive_compact。
══════════════════════════════════════════════════════════════
"""
import sys
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

    needed = ("retry_delay", "is_retryable_error", "is_prompt_too_long_error", "with_retry", "reactive_compact")
    for fn in needed:
        check(f"agent 暴露 {fn}()", hasattr(agent, fn))
    if not all(hasattr(agent, fn) for fn in needed):
        return summarize()

    # retry_delay：退避 + 尊重 retry_after + 封顶
    d0 = agent.retry_delay(0)
    check("retry_delay(0) 在 [0.5, 0.625]", 0.5 <= d0 <= 0.625, f"d0={d0}")
    check("retry_delay 尊重 retry_after", agent.retry_delay(3, retry_after=7) == 7)
    dbig = agent.retry_delay(20)
    check("retry_delay 封顶 ~32s(+抖动)", dbig <= 32 * 1.25 + 1e-6, f"dbig={dbig}")

    # 错误分类
    check("is_retryable_error 命中 529", agent.is_retryable_error(Exception("Error: 529 overloaded")))
    check("is_retryable_error 不误伤普通错误", not agent.is_retryable_error(Exception("ValueError: bad arg")))
    check("is_prompt_too_long_error 命中", agent.is_prompt_too_long_error(Exception("prompt_too_long: 413")))
    check("is_prompt_too_long_error 不误伤", not agent.is_prompt_too_long_error(Exception("529 overloaded")))

    # with_retry：失败 2 次后成功
    calls = {"n": 0}
    sleeps = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("429 rate_limit")
        return "ok"

    out = agent.with_retry(flaky, max_retries=10, sleep=lambda s: sleeps.__setitem__("n", sleeps["n"] + 1))
    check("with_retry：最终成功返回", out == "ok", f"out={out!r}")
    check("with_retry：共调用 fn 3 次", calls["n"] == 3, f"calls={calls['n']}")
    check("with_retry：sleep 退避 2 次", sleeps["n"] == 2, f"sleeps={sleeps['n']}")

    # with_retry：始终失败 → 抛出
    def always():
        raise Exception("529 overloaded")

    raised = False
    try:
        agent.with_retry(always, max_retries=3, sleep=lambda s: None)
    except Exception:
        raised = True
    check("with_retry：耗尽重试后抛出异常", raised)

    # reactive_compact
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    rc = agent.reactive_compact(msgs, summarizer=lambda m: "FAKE_SUM", keep_recent=5)
    check("reactive_compact：压到 <= keep_recent+1", len(rc) <= 6, f"len={len(rc)}")
    check("reactive_compact：含摘要", any("FAKE_SUM" in str(x.get("content", "")) for x in rc))
    check("reactive_compact：保留了最近的 m29", any("m29" == x.get("content") for x in rc))

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
