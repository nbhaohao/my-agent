# my-agent 构建进度

> 这是**我自己的 agent**。起点 = 已学透的 s01~s06，从 s07 起每学一个机制就加上来。
> 学到 s20 时，这就是我的 capstone 作品集。

## 每日规则

- **今日最低达标** = 给 my-agent 加 **1 个机制** + 对应测试**变绿** + 能向 AI **解释**它。
  做到即可心安理得休息。状态好就多加一个，不勉强。
- **最低启动量** = 打开终端写 15 分钟。状态差也先坐下，门槛要低。
- **两档速度**：硬机制（压缩 / 任务系统 / 团队）走深；简单机制允许快速抄进来 + 解释即可。

## 三拍循环（取代旧五步法）

1. **预测**：动手前口头预测测试会怎么过（AI 替不了，30 秒）
2. **变绿**：实现机制让测试通过。核心逻辑自己写，样板可让 AI 生成。绿 = 赢
3. **解释**：回答 AI 的 1-2 个尖问题 = 机制真正是我的了

## 跑测试

```sh
# 在 my-agent 目录下（用本仓库自带 .venv）
.venv/bin/python tests/test_s09_memory.py
```

## 机制清单（绿 = 已装进 my-agent）

| 机制 | 来源 | 测试 | 状态 |
|---|---|---|---|
| agent loop + 多工具 + 权限 + hooks + todo + subagent | s01-s06 | （已学透，固化为起点） | ✅ 起点 |
| Skill 两层加载 | s07 | `test_s07_skills.py` | ✅ 已装进（11/11 绿） |
| 上下文压缩（四层管线） | s08 | `test_s08_compact.py` | ✅ 已装进（19/19 绿） |
| Memory（文件仓库+索引+按需） | s09 | `test_s09_memory.py` | 🔴 进行中 |
| System Prompt 运行时组装 | s10 | `test_s10_system_prompt.py` | 🟡 测试就绪·待做 |
| 错误恢复 / fallback | s11 | `test_s11_error_recovery.py` | 🟡 测试就绪·待做 |
| 任务系统（依赖图 + 持久化） | s12 | `test_s12_task_system.py` | 🟡 测试就绪·待做 |
| 后台任务 | s13 | `test_s13_background_tasks.py` | 🟡 测试就绪·待做 |
| Cron 调度 | s14 | `test_s14_cron_scheduler.py` | 🟡 测试就绪·待做 |
| Agent Teams（消息总线） | s15 | `test_s15_agent_teams.py` | 🟡 测试就绪·待做 |
| Team Protocols | s16 | 待加 | ⬜ |
| 自主认领 | s17 | 待加 | ⬜ |
| Worktree 隔离 | s18 | 待加 | ⬜ |
| MCP | s19 | 待加 | ⬜ |
| 综合收口 | s20 | 待加 | ⬜ |

## 当前位置

🎯 s09：让 my-agent 的测试 `test_s09_memory.py` 变绿 —— 实现跨会话记忆层
（`write_memory_file` / `list_memory_files` / `rebuild_memory_index` /
`select_relevant_memories` / `consolidate_memories`），扩展 `build_system` 内联记忆索引，
并把 `remember` 注册进 `TOOL_HANDLERS`。

✅ s08 已完成：Context Compact 四层压缩，`test_s08_compact.py` 19/19 全绿。

📦 **s10–s15 已预生成**（测试 + learn-web 教学内容都就绪，全是红的）：按顺序一章一章做绿即可，
无需再消耗对话额度生成。每章测试都 0 API、确定性。s16–s20 仍待生成。
