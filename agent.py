#!/usr/bin/env python3
"""
my-agent — 我自己的 coding agent。

这个文件的起点 = 你已经用五步法学透的 s01~s06：
  s01 agent loop / s02 多工具 dispatch / s03 权限 / s04 hooks
  s05 todo_write / s06 subagent

从 s07 开始，每学一个机制，你就往这个文件（或拆分的模块）里加。
目标：学到 s20 时，这就是一个完整的、属于你的 agent —— 你的 capstone 作品集。

设计约定：
  - 本模块「导入安全」：仅 import 不会创建网络客户端、不要求 API key。
    （client 懒加载，MODEL 用 getenv 带默认）这样测试可以直接 import 而不触网。
  - 运行：python my-agent/agent.py   （需要同目录或父目录 .env 里有 ANTHROPIC_API_KEY）

运行起来后是一个终端 coding agent，输入问题回车，输入 q 退出。
"""

import os
import pathlib
import subprocess
from pathlib import Path

try:
    import readline  # noqa: F401  (修复 macOS 中文输入退格)

    readline.parse_and_bind("set bind-tty-special-chars off")
except ImportError:
    pass

from dotenv import load_dotenv

# 加载本仓库自己的 .env（无论从哪个目录运行都能找到），再退回当前目录
_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env", override=True)
load_dotenv(override=False)

if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
MODEL = os.getenv("MODEL_ID", "deepseek-v4-flash")  # getenv 带默认 → 导入不崩
CURRENT_TODOS: list[dict] = []

SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "For complex sub-problems, use the task tool to spawn a subagent. "
    "Before starting any multi-step task, use todo_write to plan."
)
SUB_SYSTEM = (
    f"You are a coding agent at {WORKDIR}. "
    "Complete the task you were given, then return a concise summary. "
    "Do not delegate further."
)


# ── client 懒加载：只在真正调用 LLM 时才创建 ────────────────
_client = None


def get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic

        _client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
    return _client


# ═══════════════════════════════════════════════════════════
#  工具实现（s02~s05）
# ═══════════════════════════════════════════════════════════


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    try:
        r = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        text = fp.read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str) -> str:
    import glob as g

    try:
        results = [
            m
            for m in g.glob(pattern, root_dir=WORKDIR)
            if (WORKDIR / m).resolve().is_relative_to(WORKDIR)
        ]
        return "\n".join(results) if results else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


def run_todo_write(todos: list) -> str:
    global CURRENT_TODOS
    for i, t in enumerate(todos):
        if "content" not in t or "status" not in t:
            return f"Error: todos[{i}] missing 'content' or 'status'"
        if t["status"] not in ("pending", "in_progress", "completed"):
            return f"Error: todos[{i}] invalid status '{t['status']}'"
    CURRENT_TODOS = todos
    lines = ["\n\033[33m## Current Tasks\033[0m"]
    for t in CURRENT_TODOS:
        icon = {
            "pending": " ",
            "in_progress": "\033[36m▸\033[0m",
            "completed": "\033[32m✓\033[0m",
        }[t["status"]]
        lines.append(f"  [{icon}] {t['content']}")
    print("\n".join(lines))
    return f"Updated {len(CURRENT_TODOS)} tasks"


def extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content)
    return "\n".join(
        getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text"
    )


# ═══════════════════════════════════════════════════════════
#  subagent（s06）
# ═══════════════════════════════════════════════════════════

SUB_TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
]
SUB_HANDLERS = {"bash": run_bash, "read_file": run_read, "write_file": run_write}


def spawn_subagent(description: str) -> str:
    print("\n\033[35m[Subagent spawned]\033[0m")
    messages = [{"role": "user", "content": description}]
    for _ in range(30):
        response = get_client().messages.create(
            model=MODEL,
            system=SUB_SYSTEM,
            messages=messages,
            tools=SUB_TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                blocked = trigger_hooks("PreToolUse", block)
                if blocked:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(blocked),
                        }
                    )
                    continue
                handler = SUB_HANDLERS.get(block.name)
                output = handler(**block.input) if handler else f"Unknown: {block.name}"
                trigger_hooks("PostToolUse", block, output)
                print(f"  \033[90m[sub] {block.name}: {str(output)[:100]}\033[0m")
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output}
                )
        messages.append({"role": "user", "content": results})
    result = extract_text(messages[-1]["content"])
    if not result:
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                result = extract_text(msg["content"])
                if result:
                    break
        result = result or "Subagent stopped after 30 turns without final answer."
    print("\033[35m[Subagent done]\033[0m")
    return result


# ═══════════════════════════════════════════════════════════
#  Context Compact 四层压缩管线（s08）
#
#  设计原则：便宜的先跑，贵的后跑。
#  snip → micro → budget → compact_history
#
#  顺序不能换的原因：
#    L1 snip     — 纯 O(n) 扫描，先砍掉整条多余消息（外壳层）。
#                   500→50 条后，L2/L3 要处理的文本量就少了 10 倍。
#    L2 micro    — 在剩下的消息里，把旧 tool_result 正文换成占位。
#                   仍然是纯文本操作，0 API 调用。
#    L3 budget   — 精准看最后一条消息的字节预算，超了就落盘最大的块。
#                   L1+L2 已经削减了大量内容，这层只处理最后的残余。
#    L4 history  — LLM 摘要，最贵（API 调用）。前三层都压不住才到这里。
#                   如果把它放在前面，每次都要烧钱；放最后是最经济的。
# ═══════════════════════════════════════════════════════════


def snip_compact(messages: list, max_messages: int = 50) -> list:
    """L1: 消息条数超过阈值时，保留头尾 + snipped 占位。"""
    if len(messages) <= max_messages:
        return messages
    tail_count = max_messages - 3
    snipped_count = len(messages) - 3 - tail_count
    placeholder = {
        "role": "user",
        "content": f"[... {snipped_count} messages snipped ...]",
    }
    return messages[:3] + [placeholder] + messages[-tail_count:]


def micro_compact(messages: list, keep_recent: int = 3) -> list:
    """L2: 扫描所有 tool_result 块；最近 keep_recent 条保留完整，
    更旧且内容 >120 字符的换成含 "compacted" 的占位。"""
    # 收集所有 tool_result 块的位置 (msg_index, block_index)
    locations = []
    for mi, msg in enumerate(messages):
        content = msg.get("content", "")
        if isinstance(content, list):
            for bi, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    locations.append((mi, bi))

    # 确定哪些块要压缩：全部靠后的 keep_recent 条不动，前面的动
    if len(locations) <= keep_recent:
        return messages
    to_compact = set(locations[:-keep_recent])

    # 构建输出（只修改被标记的块）
    result = []
    for mi, msg in enumerate(messages):
        content = msg.get("content", "")
        if isinstance(content, list):
            new_blocks = []
            for bi, block in enumerate(content):
                if (mi, bi) in to_compact:
                    text = str(block.get("content", ""))
                    if len(text) > 120:
                        new_blocks.append(
                            {
                                **block,
                                "content": f"[content compacted: {len(text)} chars]",
                            }
                        )
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)
            result.append({**msg, "content": new_blocks})
        else:
            result.append(msg)
    return result


def tool_result_budget(messages: list, max_bytes: int = 200_000) -> list:
    """L3: 看最后一条消息里 tool_result 的总字节数；
    超预算时从最大的块开始落盘/截断到预算内，留下 persisted 标记。"""
    last = messages[-1]
    content = last.get("content", "")
    if not isinstance(content, list):
        return messages

    # 收集 tool_result 块信息
    tool_blocks = []
    total_bytes = 0
    for bi, block in enumerate(content):
        if isinstance(block, dict) and block.get("type") == "tool_result":
            text = str(block.get("content", ""))
            size = len(text.encode("utf-8"))
            tool_blocks.append((bi, size))
            total_bytes += size

    if total_bytes <= max_bytes:
        return messages

    # 从大到小排序，优先处理最大的块
    tool_blocks.sort(key=lambda x: x[1], reverse=True)

    for bi, size in tool_blocks:
        if total_bytes <= max_bytes:
            break
        block = content[bi]
        placeholder = f"[content persisted ({size} bytes)]"
        content[bi] = {**block, "content": placeholder}
        total_bytes -= size
        total_bytes += len(placeholder.encode("utf-8"))

    return messages


def compact_history(messages: list, summarizer=None) -> list:
    """L4: 用摘要器把整个对话历史压成一条 user 消息。
    summarizer 是 callable(messages)->str；默认走 LLM，测试可注入假的。"""
    if summarizer is None:
        # 默认摘要器：调 LLM 生成摘要
        def default_summarizer(msgs):
            prompt = (
                "Summarize the following conversation as concisely as possible. "
                "Include key decisions, facts learned, and work done.\n\n"
            )
            for m in msgs:
                prompt += (
                    f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:1000]}\n"
                )
            client = get_client()
            response = client.messages.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1000,
            )
            return extract_text(response.content)

        summarizer = default_summarizer

    summary = summarizer(messages)
    return [{"role": "user", "content": summary}]


def run_compact(
    messages: list = None,
    max_messages: int = 50,
    keep_recent: int = 3,
    max_bytes: int = 200_000,
) -> str:
    """compact 工具 handler：跑完整四层压缩管线。"""
    if messages is None:
        return "Error: messages is required"
    messages = snip_compact(messages, max_messages)
    messages = micro_compact(messages, keep_recent)
    messages = tool_result_budget(messages, max_bytes)
    return f"Compacted to {len(messages)} messages"


# ═══════════════════════════════════════════════════════════
#  工具注册表（s02 dispatch map）
#  ⬇️ s07 起，新机制的工具往这里加
# ═══════════════════════════════════════════════════════════

TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exact text in a file once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "glob",
        "description": "Find files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "todo_write",
        "description": "Create and manage a task list for the current session.",
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["content", "status"],
                    },
                }
            },
            "required": ["todos"],
        },
    },
    {
        "name": "task",
        "description": "Launch a subagent for a complex subtask. Returns only the final conclusion.",
        "input_schema": {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"],
        },
    },
    {
        "name": "compact",
        "description": "Compact conversation history using the four-layer pipeline (snip → micro → budget).",
        "input_schema": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "description": "Conversation messages to compact.",
                },
                "max_messages": {
                    "type": "integer",
                    "description": "Max messages for snip layer. Default 50.",
                },
                "keep_recent": {
                    "type": "integer",
                    "description": "Recent tool_results to keep full. Default 3.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Max bytes for tool_result_budget layer. Default 200000.",
                },
            },
            "required": ["messages"],
        },
    },
]

TOOL_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
    "todo_write": run_todo_write,
    "task": spawn_subagent,
    "compact": run_compact,
}


# ═══════════════════════════════════════════════════════════
#  Hook 系统（s04）
# ═══════════════════════════════════════════════════════════

HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": []}


def register_hook(event: str, callback):
    HOOKS[event].append(callback)


def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None


DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]


def permission_hook(block):
    if block.name == "bash":
        for p in DENY_LIST:
            if p in block.input.get("command", ""):
                print(f"\n\033[31m⛔ Blocked: '{p}'\033[0m")
                return "Permission denied"
    return None


def log_hook(block):
    print(f"\033[90m[HOOK] {block.name}\033[0m")
    return None


def summary_hook(messages: list):
    n = sum(
        1
        for m in messages
        for b in (m.get("content") if isinstance(m.get("content"), list) else [])
        if isinstance(b, dict) and b.get("type") == "tool_result"
    )
    print(f"\033[90m[HOOK] Stop: session used {n} tool calls\033[0m")
    return None


register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("Stop", summary_hook)


# ═══════════════════════════════════════════════════════════
#  agent loop（s01 内核 + s05 nag）
# ═══════════════════════════════════════════════════════════

rounds_since_todo = 0

SKILL_REGISTRY: dict = {}


def scan_skills(skills_dir=None):
    """Scan skills/ subdirs, parse SKILL.md frontmatter, fill SKILL_REGISTRY."""
    global SKILL_REGISTRY
    if skills_dir is None:
        skills_dir = Path(__file__).resolve().parent / "skills"
    else:
        skills_dir = Path(skills_dir)
    if not skills_dir.is_dir():
        return
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        md_path = child / "SKILL.md"
        if not md_path.is_file():
            continue
        text = md_path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        fm = {}
        for line in parts[1].strip().splitlines():
            line = line.strip()
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
        name = fm.get("name")
        if not name:
            continue
        SKILL_REGISTRY[name] = {
            "path": str(child),
            "description": fm.get("description", ""),
        }


def load_skill(name: str) -> str:
    """Load full body of a skill by name (from SKILL_REGISTRY)."""
    entry = SKILL_REGISTRY.get(name)
    if entry is None:
        return f"Skill '{name}' not found"
    md_path = Path(entry["path"]) / "SKILL.md"
    text = md_path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        return f"Skill '{name}' has no body"
    return parts[2].strip()


def build_system() -> str:
    """Build full system prompt with skill catalog (name+description only)."""
    base = SYSTEM
    if not SKILL_REGISTRY:
        return base
    catalog_lines = ["\n## Available Skills"]
    for name, info in SKILL_REGISTRY.items():
        catalog_lines.append(f"- **{name}**: {info['description']}")
    return base + "\n" + "\n".join(catalog_lines)


def agent_loop(messages: list):
    global rounds_since_todo
    while True:
        if rounds_since_todo >= 3 and messages:
            messages.append(
                {"role": "user", "content": "<reminder>Update your todos.</reminder>"}
            )
            rounds_since_todo = 0

        response = get_client().messages.create(
            model=MODEL, system=SYSTEM, messages=messages, tools=TOOLS, max_tokens=8000
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue
            return

        rounds_since_todo += 1
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(blocked),
                    }
                )
                continue
            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
            trigger_hooks("PostToolUse", block, output)
            if block.name == "todo_write":
                rounds_since_todo = 0
            results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": output}
            )

        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("my-agent  (输入问题回车，q 退出)\n")
    history = []
    while True:
        try:
            query = input("\033[36mmy-agent >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
