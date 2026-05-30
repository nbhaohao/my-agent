#!/usr/bin/env python3
"""
s07 验收测试 —— Skill Loading（两层按需加载）

跑法（在项目根目录 fullstack-roadmap/ 下）：
    python my-agent/tests/test_s07_skills.py

全绿（✅）= 今天这个机制达标。红（❌）= 还没做完。

测试只针对 harness 机制本身，不调用 LLM —— 所以又快又稳，不花钱。

══════════════════════════════════════════════════════════════
你需要在 my-agent/agent.py 里实现以下契约（函数名/变量名要对上）：

  1. SKILL_REGISTRY: dict        # name -> {"name","description","content"}
  2. scan_skills(skills_dir=None) # 扫描 skills/ 目录，填充 SKILL_REGISTRY
                                  #   解析每个 <skill>/SKILL.md 的 frontmatter
                                  #   （--- 之间的 name: / description:）
  3. load_skill(name) -> str      # 返回该 skill 的完整正文；找不到返回含
                                  #   "not found" 或 "未找到" 的字符串
  4. build_system() -> str        # 返回 SYSTEM 字符串，里面要「内联」每个
                                  #   skill 的 name + description（便宜的目录层）
                                  #   但不内联正文

提示：参考 learn-claude-code-main/s07_skill_loading/code.py 的
      _parse_frontmatter / _scan_skills / list_skills / build_system / load_skill，
      但要写进你自己的 agent.py，自己理解每一行。
══════════════════════════════════════════════════════════════
"""

import sys
from pathlib import Path

# 让测试能 import 到 my-agent/agent.py
AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

PASS, FAIL = "\033[32m✅\033[0m", "\033[31m❌\033[0m"
results = []


def check(name, cond, detail=""):
    results.append(cond)
    mark = PASS if cond else FAIL
    print(f"{mark} {name}" + (f"  — {detail}" if detail and not cond else ""))


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    # 1. 契约存在性
    has_registry = hasattr(agent, "SKILL_REGISTRY")
    has_scan = hasattr(agent, "scan_skills")
    has_load = hasattr(agent, "load_skill")
    has_build = hasattr(agent, "build_system")
    check("agent 暴露 SKILL_REGISTRY", has_registry)
    check("agent 暴露 scan_skills()", has_scan)
    check("agent 暴露 load_skill()", has_load)
    check("agent 暴露 build_system()", has_build)
    if not (has_registry and has_scan and has_load and has_build):
        summarize()
        return

    # 2. 扫描 my-agent/skills/，应找到 hello
    skills_dir = AGENT_DIR / "skills"
    try:
        agent.scan_skills(skills_dir)
    except TypeError:
        agent.scan_skills()  # 兼容无参实现（依赖默认 skills 目录）
    check(
        "扫描后 SKILL_REGISTRY 含 'hello'",
        "hello" in agent.SKILL_REGISTRY,
        f"registry keys = {list(agent.SKILL_REGISTRY)}",
    )

    hello = agent.SKILL_REGISTRY.get("hello", {})
    check(
        "hello 的 description 被正确解析",
        "示例技能" in hello.get("description", ""),
        f"description = {hello.get('description')!r}",
    )

    # 3. load_skill 返回完整正文（含正文标记），目录层不含正文标记
    body = agent.load_skill("hello")
    check("load_skill('hello') 返回完整正文", "SKILL_BODY_MARKER_42" in body)
    check(
        "load_skill 对未知技能优雅处理",
        (
            "not found" in agent.load_skill("nope").lower()
            or "未找到" in agent.load_skill("nope")
        ),
    )

    # 4. build_system 内联目录（name+desc），但不内联正文 → 这就是「便宜」的意义
    sys_prompt = agent.build_system()
    check("build_system() 内联了 skill 名 'hello'", "hello" in sys_prompt)
    check("build_system() 内联了 description", "示例技能" in sys_prompt)
    check(
        "build_system() 没把正文塞进 SYSTEM（保持便宜）",
        "SKILL_BODY_MARKER_42" not in sys_prompt,
    )

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(
        f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else ""
    )
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
