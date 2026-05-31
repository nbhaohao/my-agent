#!/usr/bin/env python3
"""
s13 验收测试 —— Background Tasks（慢操作丢后台线程 + 通知回收）

跑法（在 my-agent/ 目录下）：
    .venv/bin/python tests/test_s13_background_tasks.py

全绿 = 达标。0 API：后台跑的是注入的纯函数，不调 LLM；用「轮询+超时」等线程完成。

══════════════════════════════════════════════════════════════
契约（写进 my-agent/agent.py）：

  1. is_slow_operation(tool_name, tool_input) -> bool
        仅 bash；command 含 install/build/test/deploy/compile/pytest/make 等
        慢关键词 → True；否则 False。

  2. should_run_background(tool_name, tool_input) -> bool
        tool_input["run_in_background"] 为真 → True（模型显式优先）；
        否则回退到 is_slow_operation。

  3. background_tasks: dict / background_results: dict（模块级，追踪状态与结果）

  4. start_background_task(fn, command="") -> str
        在 daemon 线程里跑 fn()（无参，返回字符串）；立刻返回 bg_id（形如 "bg_0001"）；
        注册 status=running，完成后置 completed 并把返回值存进 background_results[bg_id]。

  5. collect_background_results() -> list | str
        把已完成的后台结果收集出来（含其输出文本，便于注入对话通知）。

参考 learn-claude-code-main/s13_background_tasks/code.py 的
should_run_background / start_background_task / collect_background_results。
══════════════════════════════════════════════════════════════
"""
import sys
import tempfile
import time
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
    """── 集成：后台线程与 agent_loop 并行不互相阻塞 ──
    先发射一个即时完成的后台任务，再跑 agent_loop，断言：
      1. loop 正常完成（后台线程没有阻塞主线程）
      2. 后台任务结果写回 background_results（daemon 线程跑通了）
      3. collect_background_results 能收到输出（不重复收集）。0 API。"""
    needed = ("agent_loop", "get_client", "HOOKS", "start_background_task",
              "collect_background_results", "background_results")
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

            bg_id = agent.start_background_task(lambda: "BG_INTEG_DONE", command="test")

            try:
                agent.agent_loop([{"role": "user", "content": "hi"}])
                completed["ok"] = True
            except Exception as e:
                completed["err"] = str(e)

            # 等后台线程完成（最多 2s，fn 是即时的）
            for _ in range(200):
                if agent.background_results.get(bg_id) is not None:
                    break
                time.sleep(0.01)

            collected = agent.collect_background_results()

            check("集成：agent_loop 与后台线程并行，loop 正常完成",
                  completed["ok"], completed.get("err", ""))
            check("集成：后台任务结果写回 background_results（线程跑通）",
                  agent.background_results.get(bg_id) == "BG_INTEG_DONE",
                  f"got={agent.background_results.get(bg_id)!r}")
            check("集成：collect_background_results 包含后台输出",
                  "BG_INTEG_DONE" in str(collected), f"collected={collected!r}")
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

    needed = ("is_slow_operation", "should_run_background", "start_background_task", "collect_background_results")
    for fn in needed:
        check(f"agent 暴露 {fn}()", hasattr(agent, fn))
    if not all(hasattr(agent, fn) for fn in needed):
        return summarize()

    # 启发式
    check("is_slow：pip install 算慢", agent.is_slow_operation("bash", {"command": "pip install torch"}))
    check("is_slow：ls 不算慢", not agent.is_slow_operation("bash", {"command": "ls -la"}))
    check("is_slow：非 bash 不算", not agent.is_slow_operation("read_file", {"command": "pip install x"}))

    # 显式优先
    check("should_bg：显式 run_in_background 即便是 ls", agent.should_run_background("bash", {"command": "ls", "run_in_background": True}))
    check("should_bg：无显式时走启发式(npm install)", agent.should_run_background("bash", {"command": "npm install"}))
    check("should_bg：无显式且快命令 → False", not agent.should_run_background("bash", {"command": "echo hi"}))

    # 真后台执行（注入纯函数）
    bg_id = agent.start_background_task(lambda: "DONE_42", command="sleep 0")
    check("start_background_task：返回 bg_id 形如 bg_xxxx", isinstance(bg_id, str) and bg_id.startswith("bg_"), str(bg_id))

    # 轮询等待完成（最多 ~3s）
    done = False
    for _ in range(300):
        if agent.background_results.get(bg_id) is not None:
            done = True
            break
        time.sleep(0.01)
    check("后台线程完成并写回结果", done and agent.background_results.get(bg_id) == "DONE_42", str(agent.background_results.get(bg_id)))
    check("后台任务状态置 completed", agent.background_tasks.get(bg_id, {}).get("status") == "completed")

    collected = agent.collect_background_results()
    check("collect_background_results：包含该任务输出", "DONE_42" in str(collected))

    # ── 集成：agent_loop 接线（用假 client，0 API）─────────────
    integration_checks(agent)

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
