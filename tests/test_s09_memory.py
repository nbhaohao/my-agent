#!/usr/bin/env python3
"""
s09 验收测试 —— Memory（跨压缩、跨会话的持久记忆层）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s09_memory.py

全绿（✅）= 今天这个机制达标。红（❌）= 还没做完。

设计原则：0 API、不碰网络、确定性。
记忆的「存储 / 索引 / 注入」都是纯文件操作；唯一要调 LLM 的「选相关记忆」
我们让它接受一个可注入的 selector，测试里传个假的；不传时走关键词兜底——
两条路都不碰网络。所有读写都在临时目录里，不污染真实 .memory/。

══════════════════════════════════════════════════════════════
你需要在 my-agent/agent.py 里实现以下契约（函数名/参数名要对上）：

  0. MEMORY_DIR
        模块级 Path，记忆文件的默认存放目录。下面的函数 memory_dir=None 时用它。

  1. write_memory_file(name, mem_type, description, body, memory_dir=None) -> Path
        把 name 转 slug（小写、空格转 -）写成 <slug>.md，带 YAML frontmatter
        （name / description / type 三个字段），body 接在 --- 之后。写完重建索引。

  2. list_memory_files(memory_dir=None) -> list[dict]
        遍历目录里的 *.md（排除 MEMORY.md 索引本身），解析 frontmatter，
        每条返回含 name / description / type / filename 的 dict。

  3. rebuild_memory_index(memory_dir=None) -> Path
        写出 MEMORY.md 索引：一行一个记忆，形如
        "- [name](slug.md) — description"。

  4. build_system() -> str
        在 s07 技能目录之外，再把「记忆索引」（name + description）内联进 SYSTEM，
        但【不】内联 body（保持便宜，和技能两层加载同理）。

  5. select_relevant_memories(messages, max_items=5, selector=None, memory_dir=None) -> list[str]
        selector 是 callable(catalog:str, recent:str) -> list[int]（默认走 LLM）。
        返回相关记忆的 filename 列表，最多 max_items 条。
        selector 为 None 或抛异常时，降级为【关键词匹配】：用最近对话里的词
        去匹配每条记忆的 name + description。

  6. consolidate_memories(memory_dir=None, threshold=10, consolidator=None) -> bool
        记忆文件数 < threshold：直接返回 False，【不】调用 consolidator（太少不值得整理）。
        达到阈值才调 consolidator 去重合并。本测试只验证「未达阈值时是 no-op」。

  附加（s02 三步仪式）：把 "remember" 注册进 TOOL_HANDLERS。

提示：参考 learn-claude-code-main/s09_memory/code.py 的
      write_memory_file / list_memory_files / _rebuild_index /
      select_relevant_memories / consolidate_memories，但写进你自己的 agent.py，
      自己理解「索引常驻 SYSTEM（便宜、可缓存）+ 正文按需注入」为什么这样分。
══════════════════════════════════════════════════════════════
"""

import sys
import tempfile
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

PASS, FAIL = "\033[32m✅\033[0m", "\033[31m❌\033[0m"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    mark = PASS if cond else FAIL
    print(f"{mark} {name}" + (f"  — {detail}" if detail and not cond else ""))


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    needed = (
        "write_memory_file",
        "list_memory_files",
        "rebuild_memory_index",
        "select_relevant_memories",
        "consolidate_memories",
    )
    for fn in needed:
        check(f"agent 暴露 {fn}()", hasattr(agent, fn))
    check("agent 暴露 MEMORY_DIR", hasattr(agent, "MEMORY_DIR"))
    if not all(hasattr(agent, fn) for fn in needed) or not hasattr(agent, "MEMORY_DIR"):
        summarize()
        return

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # 让 build_system() 这种无参函数也指向临时目录
        agent.MEMORY_DIR = tmp

        # ── 写入 + 索引 ───────────────────────────────────────
        BODY_SENTINEL = "ZZ_BODY_ONLY_应当只在文件里_不进SYSTEM_ZZ"
        p = agent.write_memory_file(
            "User Tabs",
            "user",
            "prefers tabs over spaces",
            f"用户偏好用 tab 缩进。{BODY_SENTINEL}",
            memory_dir=tmp,
        )
        check("write_memory_file 落了一个 .md 文件", Path(p).exists() and Path(p).suffix == ".md")
        text = Path(p).read_text(encoding="utf-8")
        check(
            "记忆文件含 frontmatter（name/description/type）",
            "name:" in text and "description:" in text and "type:" in text,
        )
        check("slug 正确（User Tabs -> user-tabs.md）", Path(p).name == "user-tabs.md", Path(p).name)

        agent.write_memory_file(
            "DB No Mock", "feedback", "never mock the database", "写测试别 mock 数据库。", memory_dir=tmp
        )

        # ── list 解析 ─────────────────────────────────────────
        files = agent.list_memory_files(memory_dir=tmp)
        check("list_memory_files 读到 2 条", len(files) == 2, f"got {len(files)}")
        names = {f.get("name") for f in files}
        check("list 解析出 name", {"User Tabs", "DB No Mock"} <= names, str(names))
        first = next((f for f in files if f.get("name") == "User Tabs"), {})
        check(
            "list 解析出 description 和 type",
            first.get("description") == "prefers tabs over spaces" and first.get("type") == "user",
            str(first),
        )

        # ── 索引文件 ──────────────────────────────────────────
        idx = tmp / "MEMORY.md"
        check("rebuild 后 MEMORY.md 存在", idx.exists())
        if idx.exists():
            itext = idx.read_text(encoding="utf-8")
            check(
                "索引一行含 name + description + 文件链接",
                "prefers tabs over spaces" in itext and "user-tabs.md" in itext,
            )
            check("索引【不】内联 body（保持便宜）", BODY_SENTINEL not in itext)

        # ── build_system：索引进 SYSTEM，正文不进 ──────────────
        sys_prompt = agent.build_system()
        check("build_system 内联了记忆 description", "prefers tabs over spaces" in sys_prompt)
        check("build_system 【没】把 body 塞进 SYSTEM", BODY_SENTINEL not in sys_prompt)

        # ── 选相关记忆：注入 selector（0 API）─────────────────
        msgs = [{"role": "user", "content": "please use tabs here"}]
        picked = agent.select_relevant_memories(
            msgs, max_items=5, selector=lambda catalog, recent: [0], memory_dir=tmp
        )
        check(
            "select：注入 selector 返回索引 -> 拿到对应 filename",
            len(picked) == 1 and picked[0].endswith(".md"),
            str(picked),
        )

        # ── 选相关记忆：关键词兜底（selector=None）─────────────
        fb = agent.select_relevant_memories(msgs, max_items=5, selector=None, memory_dir=tmp)
        check(
            "select：无 selector 时关键词兜底命中 'tabs' 记忆",
            any("user-tabs.md" == Path(f).name for f in fb),
            str(fb),
        )

        # ── 选相关记忆：max_items 截断 ────────────────────────
        capped = agent.select_relevant_memories(
            msgs, max_items=1, selector=lambda catalog, recent: [0, 1], memory_dir=tmp
        )
        check("select：尊重 max_items 上限", len(capped) <= 1, f"len={len(capped)}")

        # ── 整理：未达阈值是 no-op，不调 consolidator ─────────
        called = {"hit": False}

        def spy(mems):
            called["hit"] = True
            return mems

        ret = agent.consolidate_memories(memory_dir=tmp, threshold=10, consolidator=spy)
        check("consolidate：2 条 < 阈值 10 -> 返回 False", ret is False, f"ret={ret!r}")
        check("consolidate：未达阈值时没调用 consolidator", called["hit"] is False)

    # ── 附加：remember 工具注册 ───────────────────────────────
    handlers = getattr(agent, "TOOL_HANDLERS", {})
    check("TOOL_HANDLERS 注册了 'remember'", "remember" in handlers)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
