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
import time
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

    summarize()


def summarize():
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} 通过", "🎉 全绿，今天达标！" if ok == total and total else "")
    sys.exit(0 if ok == total and total else 1)


if __name__ == "__main__":
    main()
