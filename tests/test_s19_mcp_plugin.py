#!/usr/bin/env python3
"""
s19 验收测试 —— MCP Plugin（标准协议接外部工具：发现 → 组装 → 调用）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s19_mcp_plugin.py

全绿 = 达标。0 API、0 外部进程：用 mock server（Python 函数模拟 tools/list + tools/call）。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py）：

  1. MCPClient 类
        __init__(self, name)；属性 tools(list) / _handlers(dict)。
        register(tool_defs, handlers)：模拟 tools/list 发现（存下工具定义 + 实现）。
        call_tool(tool_name, args: dict) -> str：模拟 tools/call；
          未知工具 → "MCP error: unknown tool ..."；handler 抛错 → "MCP error: ..."。

  2. mcp_clients: dict（模块级）—— server_name -> MCPClient（已连接的 server）。

  3. normalize_mcp_name(name) -> str
        把非 [a-zA-Z0-9_-] 的字符全替换成 "_"（防命名冲突/注入）。

  4. MOCK_SERVERS: dict —— 至少含 "docs" 和 "deploy" 两个工厂函数；
     connect_mcp(name) -> str
        已连接 → 返回 "already connected"；未知 server → 返回 "Unknown server"（含可用列表）；
        否则 factory() 建 client 存进 mcp_clients，返回含发现工具名的提示。

  5. assemble_tool_pool() -> tuple[list[dict], dict]
        以内置 TOOLS / TOOL_HANDLERS 为基底；对每个已连接 server 的每个工具，
        生成前缀名 mcp__{normalize(server)}__{normalize(tool)}，加入 tools 列表
        （input_schema 取 tool_def["inputSchema"]）和 handlers（调 client.call_tool）。

  6. connect_mcp 注册进 TOOL_HANDLERS（+ TOOLS 里有对应工具定义）。

  7. agent_loop 接线：每轮用 assemble_tool_pool() 取 (tools, handlers)，
     create 传 tools=tools、分发用 handlers.get(...)。这样 connect_mcp 之后
     下一轮工具池重建，新 mcp__ 工具才会出现在发给模型的 tools 里（s19 去掉了缓存）。

设计：mock server 模拟外部服务，0 网络/0 子进程。真实 stdio/JSON-RPC 留给跑真 agent 体会。
参考 learn-claude-code-main/s19_mcp_plugin/code.py 同名符号。
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


# ── 集成块用的假 client：记录每次 create 的 tools ──
class _TextBlock:
    def __init__(self, text):
        self.type, self.text = "text", text


class _ToolUseBlock:
    def __init__(self, name, input_data):
        self.type = "tool_use"
        self.id = "toolu_s19_01"
        self.name = name
        self.input = input_data


class _Resp:
    def __init__(self, stop_reason, content):
        self.stop_reason, self.content = stop_reason, content


class FakeClient:
    """第一轮返回 connect_mcp('docs')，第二轮 end_turn。
    记录每次 create 的 tools 列表，用于验证 loop 在连接后重建了工具池。"""
    def __init__(self):
        self.calls = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return _Resp("tool_use", [_ToolUseBlock("connect_mcp", {"name": "docs"})])
        return _Resp("end_turn", [_TextBlock("done")])


def _tool_names(tools):
    out = []
    for t in tools or []:
        out.append(t["name"] if isinstance(t, dict) else getattr(t, "name", ""))
    return out


def integration_checks(agent):
    """── 集成：agent_loop 连接 MCP 后重建工具池，新 mcp__ 工具进入下一轮 tools ──
    FakeClient 第一轮 connect_mcp('docs')、第二轮 end_turn。断言：
      1. loop 正常完成
      2. mcp_clients 里出现 'docs'
      3. 第二次 create 的 tools 含 mcp__docs__search（证明 loop 用 assemble_tool_pool 重建了池）。0 API。"""
    needed = ("agent_loop", "get_client", "HOOKS", "mcp_clients",
              "connect_mcp", "assemble_tool_pool")
    if not all(hasattr(agent, a) for a in needed):
        check("集成：agent 具备所需符号", False,
              f"缺: {[a for a in needed if not hasattr(agent, a)]}")
        return

    saved = {
        "hooks": {k: list(v) for k, v in agent.HOOKS.items()},
        "get_client": agent.get_client,
        "mem_dir": agent.MEMORY_DIR,
        "rounds": getattr(agent, "rounds_since_todo", 0),
        "mcp": dict(agent.mcp_clients),
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
            agent.mcp_clients.clear()

            try:
                agent.agent_loop([{"role": "user", "content": "连 docs 这个 MCP"}])
                completed["ok"] = True
            except Exception as e:
                completed["err"] = str(e)

            check("集成：agent_loop 收到 connect_mcp 后正常完成",
                  completed["ok"], completed.get("err", ""))
            check("集成：mcp_clients 里出现 'docs'", "docs" in agent.mcp_clients,
                  f"clients={list(agent.mcp_clients)}")
            second = fake.calls[1] if len(fake.calls) >= 2 else {}
            names = _tool_names(second.get("tools"))
            check("集成：连接后下一轮 tools 含 mcp__docs__search（工具池已重建）",
                  any(n == "mcp__docs__search" for n in names),
                  f"第二轮 tools={names}")
    finally:
        for k, v in saved["hooks"].items():
            agent.HOOKS[k] = v
        agent.get_client = saved["get_client"]
        agent.MEMORY_DIR = saved["mem_dir"]
        agent.rounds_since_todo = saved["rounds"]
        agent.mcp_clients.clear()
        agent.mcp_clients.update(saved["mcp"])


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    needed = ("MCPClient", "mcp_clients", "normalize_mcp_name",
              "connect_mcp", "assemble_tool_pool", "MOCK_SERVERS")
    for sym in needed:
        check(f"agent 暴露 {sym}", hasattr(agent, sym))
    if not all(hasattr(agent, s) for s in needed):
        return summarize()

    # ── normalize_mcp_name：纯规范化 ──
    n = agent.normalize_mcp_name
    check("normalize：合法名不变", n("docs-v2_1") == "docs-v2_1")
    check("normalize：特殊字符 → _", n("my.server/x!") == "my_server_x_", f"got={n('my.server/x!')!r}")

    # ── MCPClient：register + call_tool ──
    c = agent.MCPClient("t")
    c.register(
        tool_defs=[{"name": "echo", "description": "echo",
                    "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}}}],
        handlers={"echo": lambda x: f"echo:{x}"})
    check("MCPClient.register：存下工具定义", len(c.tools) == 1 and c.tools[0]["name"] == "echo")
    check("MCPClient.call_tool：分发到 handler", c.call_tool("echo", {"x": "hi"}) == "echo:hi")
    check("MCPClient.call_tool：未知工具 → MCP error",
          "error" in c.call_tool("nope", {}).lower())
    c.register(tool_defs=[], handlers={"boom": lambda: (_ for _ in ()).throw(ValueError("x"))})
    check("MCPClient.call_tool：handler 抛错被兜住",
          "error" in c.call_tool("boom", {}).lower())

    # ── connect_mcp + assemble_tool_pool（用模块级 mcp_clients，注意清理）──
    saved_mcp = dict(agent.mcp_clients)
    try:
        agent.mcp_clients.clear()

        # 未知 server
        r_unknown = agent.connect_mcp("nonexistent-xyz")
        check("connect_mcp：未知 server → 提示 Unknown", "unknown" in r_unknown.lower(),
              f"r={r_unknown!r}")

        # 连接 docs
        r_docs = agent.connect_mcp("docs")
        check("connect_mcp：docs 连接成功并发现工具", "docs" in agent.mcp_clients
              and ("search" in r_docs), f"r={r_docs!r}")
        # 重复连接
        r_again = agent.connect_mcp("docs")
        check("connect_mcp：重复连接 → already connected", "already" in r_again.lower())

        # assemble_tool_pool：含前缀工具 + 内置仍在
        tools, handlers = agent.assemble_tool_pool()
        names = _tool_names(tools)
        check("assemble：内置工具仍在池里（如 bash）", "bash" in names, f"names sample={names[:6]}")
        check("assemble：docs 工具带 mcp__docs__ 前缀", "mcp__docs__search" in names,
              f"names={[x for x in names if x.startswith('mcp__')]}")
        check("assemble：前缀工具有对应 handler", "mcp__docs__search" in handlers)
        # 前缀 handler 真的能调到 mock server
        out = handlers["mcp__docs__search"](query="agent")
        check("assemble：调前缀 handler → 命中 mock server", "docs" in out and "agent" in out,
              f"out={out!r}")

        # 连第二个 server，两边工具不冲突
        agent.connect_mcp("deploy")
        tools2, handlers2 = agent.assemble_tool_pool()
        names2 = _tool_names(tools2)
        check("assemble：两个 server 工具共存（docs + deploy 前缀都在）",
              "mcp__docs__search" in names2 and "mcp__deploy__trigger" in names2,
              f"mcp tools={[x for x in names2 if x.startswith('mcp__')]}")
        # 危险标注
        deploy_trigger = next((t for t in tools2 if (t["name"] if isinstance(t, dict)
                               else getattr(t, "name", "")) == "mcp__deploy__trigger"), None)
        desc = (deploy_trigger.get("description", "") if isinstance(deploy_trigger, dict) else "")
        check("assemble：deploy.trigger 描述带 destructive 标注", "destructive" in desc.lower(),
              f"desc={desc!r}")
    finally:
        agent.mcp_clients.clear()
        agent.mcp_clients.update(saved_mcp)

    # TOOL_HANDLERS 注册 connect_mcp
    handlers0 = getattr(agent, "TOOL_HANDLERS", {})
    check("TOOL_HANDLERS 注册了 'connect_mcp'", "connect_mcp" in handlers0)

    # ── 集成：agent_loop 连 MCP 后重建工具池（假 client，0 API）──
    integration_checks(agent)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
