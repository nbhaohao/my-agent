#!/usr/bin/env python3
"""
s10 验收测试 —— System Prompt 运行时组装（分段 + 按需拼接）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s10_system_prompt.py

全绿 = 达标。0 API、确定性（纯字符串拼接，不碰网络）。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py，函数名/参数对上）：

  1. PROMPT_SECTIONS: dict
        把硬编码 SYSTEM 拆成分段字典，至少含 "identity" / "tools" / "workspace" 三个 key。

  2. assemble_system_prompt(context: dict) -> str
        始终拼接 identity / tools / workspace 三段；
        按【真实状态】（不是关键词）决定可选段：
          - context.get("memories") 非空 → 追加含该记忆内容的一段
          - context.get("skills") 非空 → 追加含技能目录的一段
        identity 永远在最前。

思路：这是把 s07/s09 的 build_system 思想推广成「按 context 组装」。
参考 learn-claude-code-main/s10_system_prompt/code.py 的
PROMPT_SECTIONS / assemble_system_prompt。
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

    check("agent 暴露 PROMPT_SECTIONS", hasattr(agent, "PROMPT_SECTIONS"))
    check("agent 暴露 assemble_system_prompt()", hasattr(agent, "assemble_system_prompt"))
    if not (hasattr(agent, "PROMPT_SECTIONS") and hasattr(agent, "assemble_system_prompt")):
        return summarize()

    ps = agent.PROMPT_SECTIONS
    check("PROMPT_SECTIONS 含 identity/tools/workspace", all(k in ps for k in ("identity", "tools", "workspace")), str(list(ps)))

    # 空 context：只有三段必选，不应出现记忆内容
    base = agent.assemble_system_prompt({})
    check("空 context：包含 identity 段", ps["identity"] in base)
    check("空 context：包含 tools 段", ps["tools"] in base)
    check("空 context：不注入记忆", "ZZ_MEM_ZZ" not in base)
    check("identity 排在最前", base.strip().startswith(ps["identity"][:10]))

    # 有记忆：按需追加
    withmem = agent.assemble_system_prompt({"memories": "ZZ_MEM_ZZ 用户偏好 tab"})
    check("有 memories：把记忆内容拼进来", "ZZ_MEM_ZZ" in withmem)
    check("有 memories：比空 context 更长", len(withmem) > len(base))

    # 有技能：按需追加
    withskill = agent.assemble_system_prompt({"skills": "ZZ_SKILL_ZZ hello: 打招呼"})
    check("有 skills：把技能目录拼进来", "ZZ_SKILL_ZZ" in withskill)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
