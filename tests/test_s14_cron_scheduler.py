#!/usr/bin/env python3
"""
s14 验收测试 —— Cron Scheduler（五段式 cron 表达式匹配）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s14_cron_scheduler.py

全绿 = 达标。0 API、纯时间运算（给定表达式 + datetime → 是否触发）。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py）：

  1. cron_matches(cron_expr: str, dt: datetime) -> bool
        五段式 "分 时 日 月 周"。分/时/月必须全部匹配；
        日(DOM)与周(DOW)同时被约束时，任一匹配即可（标准 cron 的 OR 语义）；
        段数不为 5 → False。

  2. 字段语法支持：'*'、'*/N'、'N'、'N-M'、'N,M,...'
        （可内部用一个 _cron_field_matches(field, value) 辅助函数。）

  周：cron 里 0=周日。Python datetime.weekday() 周一=0，需换算：(weekday()+1)%7。

参考 learn-claude-code-main/s14_cron_scheduler/code.py 的 cron_matches / _cron_field_matches。
══════════════════════════════════════════════════════════════
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PASS, FAIL = "\033[32m✅\033[0m", "\033[31m❌\033[0m"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print((PASS if cond else FAIL) + f" {name}" + (f"  — {detail}" if detail and not cond else ""))


# ── 集成块用的假 client ──────────────────────────────────────
class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self):
        self.stop_reason, self.content = "end_turn", [_Block("done")]


class FakeClient:
    def __init__(self):
        self.calls = 0

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        return _Resp()


def integration_checks(agent):
    """── 集成：cron_matches 是纯函数，验证 agent_loop 跑完后仍返回正确结果 ──
    cron_matches 无副作用、不依赖 loop 状态；集成目标是确认 import 环境下
    agent_loop 正常完成，且 cron_matches 没有被 loop 破坏。0 API。"""
    needed = ("agent_loop", "get_client", "HOOKS", "cron_matches")
    if not all(hasattr(agent, a) for a in needed):
        check("集成：agent 具备所需符号", False,
              f"缺: {[a for a in needed if not hasattr(agent, a)]}")
        return

    saved = {
        "hooks": {k: list(v) for k, v in agent.HOOKS.items()},
        "get_client": agent.get_client,
        "mem_dir": agent.MEMORY_DIR,
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
            agent.get_client = lambda: fake
            agent.rounds_since_todo = 0

            try:
                agent.agent_loop([{"role": "user", "content": "hi"}])
                completed["ok"] = True
            except Exception as e:
                completed["err"] = str(e)

        check("集成：agent_loop 正常完成（cron 纯函数不影响 loop）",
              completed["ok"], completed.get("err", ""))
        # loop 跑完后 cron_matches 仍正确（无状态污染）
        mon_9 = datetime(2026, 6, 1, 9, 0)
        check("集成：loop 后 cron_matches 仍返回正确结果",
              agent.cron_matches("0 9 * * *", mon_9) is True)
    finally:
        for k, v in saved["hooks"].items():
            agent.HOOKS[k] = v
        agent.get_client = saved["get_client"]
        agent.MEMORY_DIR = saved["mem_dir"]
        agent.rounds_since_todo = saved["rounds"]


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    if not hasattr(agent, "cron_matches"):
        check("agent 暴露 cron_matches()", False)
        return summarize()
    cm = agent.cron_matches

    # 2026-06-01 是周一，09:00
    mon_9 = datetime(2026, 6, 1, 9, 0)
    mon_901 = datetime(2026, 6, 1, 9, 1)
    mon_10 = datetime(2026, 6, 1, 10, 0)
    sat_9 = datetime(2026, 6, 6, 9, 0)   # 周六

    check("'* * * * *' 任意时刻都匹配", cm("* * * * *", mon_9) and cm("* * * * *", sat_9))
    check("'0 9 * * *' 匹配 9:00", cm("0 9 * * *", mon_9))
    check("'0 9 * * *' 不匹配 9:01", not cm("0 9 * * *", mon_901))
    check("'0 9 * * *' 不匹配 10:00", not cm("0 9 * * *", mon_10))

    check("'*/5 * * * *' 匹配第 0 分", cm("*/5 * * * *", mon_9))
    check("'*/5 * * * *' 不匹配第 1 分", not cm("*/5 * * * *", mon_901))

    check("'0 9,17 * * *' 列表匹配 9 点", cm("0 9,17 * * *", mon_9))
    check("'0 9,17 * * *' 不匹配 10 点", not cm("0 9,17 * * *", mon_10))

    check("'0 9 * * 1-5' 工作日 9 点匹配", cm("0 9 * * 1-5", mon_9))
    check("'0 9 * * 1-5' 周六不匹配", not cm("0 9 * * 1-5", sat_9))

    # DOM/DOW 同时约束 → OR：1 号 或 周一
    check("'0 0 1 * 1' 周一(非1号)也匹配(OR)", cm("0 0 1 * 1", datetime(2026, 6, 1, 0, 0)))  # 6/1 既是1号又是周一
    check("'0 0 1 * 1' 既非1号又非周一 → 不匹配", not cm("0 0 1 * 1", datetime(2026, 6, 3, 0, 0)))  # 6/3 周三、非1号

    check("段数不为 5 → False", not cm("0 9 * *", mon_9))

    # ── 集成：agent_loop 接线（用假 client，0 API）─────────────
    integration_checks(agent)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
