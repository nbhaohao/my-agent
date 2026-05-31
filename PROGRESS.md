# my-agent 构建进度

> 这是**我自己的 agent**。起点 = 已学透的 s01~s06，从 s07 起每学一个机制就加上来。
> 学到 s20 时，这就是我的 capstone 作品集。

## 每日规则

- **今日最低达标** = 给 my-agent 加 **1 个机制** + **单元测试 + 集成测试都变绿** + 能向 AI **解释**它。
  做到即可心安理得休息。状态好就多加一个，不勉强。
- **两种测试**：单元测试（孤立测函数本身）+ 集成测试（`test_sXX_integration.py`，断言 `agent_loop` 真把机制接通了）。**只绿单元不算装进**——函数写好但没接进主 loop，跑起来等于没生效。
- **最低启动量** = 打开终端写 15 分钟。状态差也先坐下，门槛要低。
- **两档速度**：硬机制（压缩 / 任务系统 / 团队）走深；简单机制允许快速抄进来 + 解释即可。

## 三拍循环（取代旧五步法）

1. **预测**：动手前口头预测测试会怎么过（AI 替不了，30 秒）
2. **变绿**：**不亲手写实现**——把验收测试贴给 AI（用本章 `aiPrompts.build`）生成实现，我**认真 review**。单元测试 + 集成测试**都绿 = 赢**。
3. **解释**：回答 AI 的 1-2 个尖问题 = 机制真正是我的了

> review + 解释是"懂没懂"的闸门（AI 替不了），不是手写代码。

## 跑测试

```sh
# 在 my-agent 目录下（用本仓库自带 .venv）
.venv/bin/python tests/test_s09_memory.py
```

## 机制清单（✅ 已装进 = 单元绿 **且** 接进 agent_loop）

> **测试约定（2026-05-30 定）**：一章一个 `test_sXX.py`，**末尾加「集成块」**（用假 client 跑 agent_loop、0 API）。单元块测函数本身、集成块测接线，同一文件一条命令、一个"全绿=达标"。

| 机制 | 来源 | 单元块 | 接线（同文件集成块） | 状态 |
|---|---|---|---|---|
| agent loop + 多工具 + 权限 + hooks + todo + subagent | s01-s06 | （已学透，固化为起点） | —（本就是 loop 本体） | ✅ 起点 |
| Skill 两层加载 | s07 | `test_s07_skills.py`（11/11） | 目录随 `build_system` 进 system（随 s09 接线一并接通） | 🟡 单元绿·接线随 build_system |
| 上下文压缩（四层管线） | s08 | `test_s08_compact.py`（19/19） | **代码已接**：`agent_loop` 调 budget/snip/micro + 超阈值 L4；集成测试免（用户通过） | ✅ 已装进（接线已补·免集成测试） |
| Memory（文件仓库+索引+按需） | s09 | `test_s09_memory.py` 单元 23 | 同文件集成块 3（build_system 进 system + body 注入）= 26/26 | ✅ 已装进（单元+接线绿） |
| System Prompt 运行时组装 | s10 | `test_s10_system_prompt.py` | 待加集成块 | 🟡 测试就绪·待做 |
| 错误恢复 / fallback | s11 | `test_s11_error_recovery.py` | 待加集成块 | 🟡 测试就绪·待做 |
| 任务系统（依赖图 + 持久化） | s12 | `test_s12_task_system.py` | 同文件集成块 2（create_task 落盘）= 22/22 | ✅ 已装进（单元+接线绿） |
| 后台任务 | s13 | `test_s13_background_tasks.py` | 同文件集成块 3（并行不阻塞+结果落 dict）= 17/17 | ✅ 已装进（单元+接线绿） |
| Cron 调度 | s14 | `test_s14_cron_scheduler.py` | 同文件集成块 2（纯函数无副作用验证）= 15/15 | ✅ 已装进（单元+接线绿） |
| Agent Teams（消息总线） | s15 | `test_s15_agent_teams.py` | 同文件集成块 2（send_message 落盘）= 11/11 | ✅ 已装进（单元+接线绿） |
| Team Protocols | s16 | `test_s16_team_protocols.py` | 同文件集成块 3（request_shutdown 落盘 + 登记）= 27/27 | ✅ 已装进（单元+接线绿） |
| 自主认领 | s17 | `test_s17_autonomous_agents.py` | 同文件集成块 5（自治循环 idle_poll 串 scan+claim）= 25/25 | ✅ 已装进（单元+接线绿） |
| Worktree 隔离 | s18 | 待加 | 待加 | ⬜ |
| MCP | s19 | 待加 | 待加 | ⬜ |
| 综合收口 | s20 | 待加 | 待加 | ⬜ |

> 📌 从 s10 起每章都要带集成块——只绿单元 = 函数写好但没接进主 loop，跑起来等于没生效（s08/s09 都验证过这个坑）。

## 当前位置

🎯 下一步：s18「Worktree Isolation」（待生成测试；开工前需定测纯逻辑 vs 引真 git 的取舍）。

✅ s17 已完成：Autonomous Agents —— `test_s17_autonomous_agents.py` 25/25（20 单元 + 5 集成）。
   scan_unclaimed_tasks 三条件；claim_task 加 owner 检查（向后兼容 s12）；idle_poll 的 WORK→IDLE→SHUTDOWN，inbox 优先于看板。

✅ s16 已完成：Team Protocols —— `test_s16_team_protocols.py` 27/27（24 单元 + 3 集成）。
   ProtocolState + pending_requests；request_id 关联；match_response 类型校验+幂等；consume_lead_inbox 统一路由。

✅ s15 已完成：Agent Teams —— `test_s15_agent_teams.py` 11/11（9 单元 + 2 集成）。
   文件收件箱 MessageBus；消费式 read_inbox；send_message 接入 TOOL_HANDLERS。

✅ s14 已完成：Cron 调度 —— `test_s14_cron_scheduler.py` 15/15（13 单元 + 2 集成）。
   五段式 cron 匹配；DOM/DOW OR 语义；weekday 换算 (weekday+1)%7。

✅ s13 已完成：后台任务 —— `test_s13_background_tasks.py` 17/17（14 单元 + 3 集成）。
   daemon 线程 + background_results 共享内存；collect 删除已收集条目防重复通知。

✅ s12 已完成：Task System —— `test_s12_task_system.py` 22/22（20 单元 + 2 集成）。
   Task dataclass + CRUD + blockedBy 依赖门控，TOOL_HANDLERS 接入四个 task 工具。

✅ s11：错误恢复 `test_s11_error_recovery.py` 21/21（19 单元 + 2 集成）。
✅ s10：System Prompt 运行时组装 `test_s10_system_prompt.py` 12/12。
✅ s09：跨会话记忆层 `test_s09_memory.py` 26/26。
✅ s08：四层压缩 `test_s08_compact.py` 19/19（接线免集成测试）。

📦 **s10–s15 已预生成**（测试 + learn-web 教学内容都就绪，全是红的）：按顺序一章一章做绿即可，
无需再消耗对话额度生成。每章测试都 0 API、确定性。s16–s20 仍待生成。
