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
from datetime import datetime
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

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
