#!/usr/bin/env python3
"""
s16 验收测试 —— Team Protocols（request-response 协议 + request_id 关联 + 状态机）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s16_team_protocols.py

全绿 = 达标。0 API、临时邮箱目录（不碰网络、不污染 .mailboxes）。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py）：

  0. MessageBus.send 增加可选参数 metadata=None（向后兼容 s15）：
        消息 JSON 里多带一个 "metadata" 字段（默认 {}）。read_inbox 原样读出。

  1. ProtocolState（dataclass）：字段
        request_id / type / sender / target / status / payload
        type ∈ {"shutdown", "plan_approval"}；status ∈ {pending, approved, rejected}。
        （created_at 可选，用 field(default_factory=time.time)。）

  2. pending_requests: dict（模块级）—— request_id -> ProtocolState，追踪在途请求。

  3. new_request_id() -> str
        返回形如 "req_xxxxxx" 的字符串；两次调用结果不同。

  4. match_response(response_type, request_id, approve) -> None
        按 request_id 找到 pending_requests 里的 ProtocolState，并：
          - request_id 不存在 → 静默返回（不抛异常）
          - 类型不匹配（state.type=="shutdown" 但 response_type!="shutdown_response"，
            plan_approval 同理）→ 不改状态
          - state.status 已非 pending（重复响应）→ 不改状态（幂等）
          - 校验通过：approve=True → status="approved"；False → "rejected"

  5. run_request_shutdown(teammate) -> str
        新建一条 type="shutdown" / sender="lead" / target=teammate / status="pending"
        的 ProtocolState 存进 pending_requests；
        通过 MessageBus 往 teammate 发一条 type="shutdown_request" 的消息，
        metadata 含 {"request_id": ...}；返回含该 request_id 的提示串。

  6. run_review_plan(request_id, approve, feedback="") -> str
        针对一条已存在的 plan_approval pending 请求：
          - 不存在 / 已非 pending → 返回提示，不再改
          - 否则设 status approved/rejected，并往该请求的 sender 发
            type="plan_approval_response" 的消息，metadata 含 {request_id, approve}。

  7. consume_lead_inbox(route_protocol=True) -> list[dict]
        读 "lead" 的收件箱（消费式）；route_protocol 时，对每条
        type 以 "_response" 结尾且 metadata 带 request_id 的消息调用 match_response；
        返回读到的全部消息。

  8. TOOL_HANDLERS 注册 "request_shutdown" / "request_plan" / "review_plan"。

协议函数发消息时用 MessageBus()（构造时读取当前 MAILBOX_DIR），便于测试重定向到临时目录。
参考 learn-claude-code-main/s16_team_protocols/code.py 的 match_response /
run_request_shutdown / run_review_plan / consume_lead_inbox。
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


# ── 集成块用的假 client ──────────────────────────────────────
class _TextBlock:
    def __init__(self, text):
        self.type, self.text = "text", text


class _ToolUseBlock:
    def __init__(self, name, input_data):
        self.type = "tool_use"
        self.id = "toolu_s16_01"
        self.name = name
        self.input = input_data


class _Resp:
    def __init__(self, stop_reason, content):
        self.stop_reason, self.content = stop_reason, content


class FakeClient:
    """第一轮返回 request_shutdown 工具调用，第二轮 end_turn。
    验证 request_shutdown handler 接进了 TOOL_HANDLERS，协议请求被登记、消息落盘。"""
    def __init__(self):
        self.calls = 0

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _Resp("tool_use", [_ToolUseBlock("request_shutdown", {"teammate": "alice"})])
        return _Resp("end_turn", [_TextBlock("done")])


def integration_checks(agent):
    """── 集成：agent_loop 收到 request_shutdown 调用时协议请求被登记 + 消息落盘 ──
    FakeClient 第一轮返回 request_shutdown tool_use，断言：
      1. loop 正常完成（handler 已接进 TOOL_HANDLERS）
      2. pending_requests 新增了一条 shutdown 请求
      3. teammate(alice) 的收件箱里出现 shutdown_request 消息。0 API。"""
    needed = ("agent_loop", "get_client", "HOOKS", "MAILBOX_DIR",
              "pending_requests", "MessageBus", "run_request_shutdown")
    if not all(hasattr(agent, a) for a in needed):
        check("集成：agent 具备所需符号", False,
              f"缺: {[a for a in needed if not hasattr(agent, a)]}")
        return

    saved = {
        "hooks": {k: list(v) for k, v in agent.HOOKS.items()},
        "get_client": agent.get_client,
        "mem_dir": agent.MEMORY_DIR,
        "mailbox_dir": agent.MAILBOX_DIR,
        "rounds": getattr(agent, "rounds_since_todo", 0),
        "pending": dict(agent.pending_requests),
    }
    fake = FakeClient()
    completed = {"ok": False}

    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for k in agent.HOOKS:
                agent.HOOKS[k] = []
            agent.MEMORY_DIR = tmp
            agent.MAILBOX_DIR = tmp
            agent.get_client = lambda: fake
            agent.rounds_since_todo = 0
            agent.pending_requests.clear()

            try:
                agent.agent_loop([{"role": "user", "content": "让 alice 关机"}])
                completed["ok"] = True
            except Exception as e:
                completed["err"] = str(e)

            shutdown_reqs = [s for s in agent.pending_requests.values()
                             if getattr(s, "type", "") == "shutdown"]
            alice_inbox = agent.MessageBus(mailbox_dir=tmp).read_inbox("alice")
            has_shutdown_msg = any(m.get("type") == "shutdown_request" for m in alice_inbox)

            check("集成：agent_loop 收到 request_shutdown 调用后正常完成",
                  completed["ok"], completed.get("err", ""))
            check("集成：pending_requests 登记了 shutdown 请求",
                  len(shutdown_reqs) >= 1, f"shutdown 请求数={len(shutdown_reqs)}")
            check("集成：teammate 收件箱出现 shutdown_request 消息",
                  has_shutdown_msg, f"alice inbox={alice_inbox}")
    finally:
        for k, v in saved["hooks"].items():
            agent.HOOKS[k] = v
        agent.get_client = saved["get_client"]
        agent.MEMORY_DIR = saved["mem_dir"]
        agent.MAILBOX_DIR = saved["mailbox_dir"]
        agent.rounds_since_todo = saved["rounds"]
        agent.pending_requests.clear()
        agent.pending_requests.update(saved["pending"])


def main():
    try:
        import agent
    except Exception as e:
        print(f"{FAIL} import agent.py 失败: {e}")
        sys.exit(1)

    needed = ("ProtocolState", "pending_requests", "new_request_id",
              "match_response", "run_request_shutdown", "run_review_plan",
              "consume_lead_inbox")
    for sym in needed:
        check(f"agent 暴露 {sym}", hasattr(agent, sym))
    if not all(hasattr(agent, s) for s in needed):
        return summarize()

    # metadata 向后兼容
    saved_mailbox = agent.MAILBOX_DIR
    saved_pending = dict(agent.pending_requests)
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            agent.MAILBOX_DIR = tmp
            agent.pending_requests.clear()
            bus = agent.MessageBus(mailbox_dir=tmp)

            bus.send("lead", "alice", "hi", "shutdown_request", {"request_id": "req_x"})
            inbox = bus.read_inbox("alice")
            check("MessageBus.send 支持 metadata 且 read_inbox 读得到",
                  len(inbox) == 1 and inbox[0].get("metadata", {}).get("request_id") == "req_x",
                  str(inbox))

            # new_request_id 唯一性
            r1, r2 = agent.new_request_id(), agent.new_request_id()
            check("new_request_id 形如 req_xxx 且两次不同",
                  r1.startswith("req_") and r2.startswith("req_") and r1 != r2, f"{r1} / {r2}")

            # ProtocolState 字段
            ps = agent.ProtocolState(request_id="req_t", type="shutdown",
                                     sender="lead", target="alice",
                                     status="pending", payload="")
            check("ProtocolState 有 request_id/type/status 等字段",
                  ps.request_id == "req_t" and ps.type == "shutdown" and ps.status == "pending")

            # ── match_response 核心：关联 + 类型校验 + 幂等 ──
            agent.pending_requests.clear()
            agent.pending_requests["req_sd"] = agent.ProtocolState(
                request_id="req_sd", type="shutdown", sender="lead",
                target="alice", status="pending", payload="")

            agent.match_response("shutdown_response", "req_unknown", True)
            check("match_response：未知 request_id 不抛异常、不误改",
                  agent.pending_requests["req_sd"].status == "pending")

            agent.match_response("plan_approval_response", "req_sd", True)
            check("match_response：类型不匹配 → 不改状态",
                  agent.pending_requests["req_sd"].status == "pending")

            agent.match_response("shutdown_response", "req_sd", True)
            check("match_response：类型匹配 + approve → approved",
                  agent.pending_requests["req_sd"].status == "approved")

            agent.match_response("shutdown_response", "req_sd", False)
            check("match_response：已 resolved 的请求幂等（不被二次响应翻转）",
                  agent.pending_requests["req_sd"].status == "approved")

            # reject 路径
            agent.pending_requests["req_pa"] = agent.ProtocolState(
                request_id="req_pa", type="plan_approval", sender="bob",
                target="lead", status="pending", payload="重构计划")
            agent.match_response("plan_approval_response", "req_pa", False)
            check("match_response：approve=False → rejected",
                  agent.pending_requests["req_pa"].status == "rejected")

            # ── run_request_shutdown：登记 + 发消息 ──
            agent.pending_requests.clear()
            out = agent.run_request_shutdown("alice")
            sd = [s for s in agent.pending_requests.values() if s.type == "shutdown"]
            check("run_request_shutdown：登记 pending shutdown 请求",
                  len(sd) == 1 and sd[0].status == "pending" and sd[0].target == "alice")
            alice_msgs = agent.MessageBus(mailbox_dir=tmp).read_inbox("alice")
            check("run_request_shutdown：往 teammate 发 shutdown_request（带 request_id）",
                  any(m.get("type") == "shutdown_request"
                      and m.get("metadata", {}).get("request_id") for m in alice_msgs),
                  str(alice_msgs))

            # ── 端到端：请求 → 队友响应 → consume_lead_inbox 路由 → approved ──
            agent.pending_requests.clear()
            agent.run_request_shutdown("alice")
            req_id = next(iter(agent.pending_requests))
            # 模拟 alice 回复
            agent.MessageBus(mailbox_dir=tmp).send(
                "alice", "lead", "ok", "shutdown_response",
                {"request_id": req_id, "approve": True})
            returned = agent.consume_lead_inbox(route_protocol=True)
            check("consume_lead_inbox：路由 shutdown_response → 请求变 approved",
                  agent.pending_requests[req_id].status == "approved")
            check("consume_lead_inbox：仍返回读到的消息",
                  any(m.get("type") == "shutdown_response" for m in returned), str(returned))

            # ── run_review_plan：Lead 审批队友的计划 ──
            agent.pending_requests.clear()
            agent.pending_requests["req_plan"] = agent.ProtocolState(
                request_id="req_plan", type="plan_approval", sender="bob",
                target="lead", status="pending", payload="重构 auth")
            r = agent.run_review_plan("req_plan", approve=True, feedback="LGTM")
            check("run_review_plan：approve → 状态 approved",
                  agent.pending_requests["req_plan"].status == "approved")
            bob_msgs = agent.MessageBus(mailbox_dir=tmp).read_inbox("bob")
            check("run_review_plan：往 sender 发 plan_approval_response（带 approve）",
                  any(m.get("type") == "plan_approval_response"
                      and m.get("metadata", {}).get("approve") is True for m in bob_msgs),
                  str(bob_msgs))
    finally:
        agent.MAILBOX_DIR = saved_mailbox
        agent.pending_requests.clear()
        agent.pending_requests.update(saved_pending)

    # TOOL_HANDLERS 注册
    handlers = getattr(agent, "TOOL_HANDLERS", {})
    for name in ("request_shutdown", "request_plan", "review_plan"):
        check(f"TOOL_HANDLERS 注册了 '{name}'", name in handlers)

    # ── 集成：agent_loop 接线（用假 client，0 API）─────────────
    integration_checks(agent)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
