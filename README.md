# my-agent

我从零构建的 agent harness —— 跟着「Learn Claude Code · Agent Harness 工程」边学边造，s01–s20 全部机制装进**同一个 `agent_loop`**。

> **定位：教学实现**。存储用文件、并发用线程、总线用目录——为的是把 harness 机制看透，不是上生产。同样的概念换成真基建（PG/Redis/SSE/Docker）的版本见我的另一个 repo `project_agent`。

## 已实现的 harness 机制（s01–s20）

| 层 | 机制 |
|---|---|
| 核心循环 | Agent Loop（`while` + `stop_reason` 驱动，s01）· 多工具 dispatch map：bash/read/write/edit/glob/todo（s02） |
| 安全 | 权限 deny list + 危险命令拦截（s03）· Hooks：PreToolUse/PostToolUse/Stop 扩展点（s04） |
| 任务组织 | TodoWrite 先计划后执行 + nag（s05）· Subagent fresh-context 子 agent 只回摘要（s06）· 任务系统：依赖图 + 磁盘持久化（s12） |
| 上下文工程 | Skill 两层按需加载（s07）· 四层压缩管线 snip/micro/budget/auto（s08）· Memory 文件仓库 + 索引 + 按需注入（s09）· System Prompt 运行时组装（s10） |
| 健壮性 | 错误恢复：429 退避重试 + fallback 模型（s11）· prompt 过长 reactive compact 后恢复重试（s20） |
| 长期运行 | 后台任务：线程 + 完成通知注入（s13）· Cron 调度（s14） |
| 多 agent | Teams 文件消息总线（s15）· 协作协议：shutdown request/response（s16）· 自主认领：自治循环 idle_poll scan+claim（s17）· Worktree 隔离并行不互踩（s18） |
| 扩展 | MCP 插件：连接后工具池重建（s19）· 综合收口：全部机制共用一个 loop（s20） |

## 运行

```sh
python3 -m venv .venv                    # 首次：建虚拟环境
.venv/bin/pip install -r requirements.txt
cp .env.example .env                      # 填入你的 API key（Anthropic 协议，DeepSeek 等兼容端点可用）
.venv/bin/python agent.py                 # 终端交互，输入问题回车，q 退出
```

## 测试（14 个文件 · 274 条 checks · 全绿）

每章一个测试文件 = **单元块**（孤立测机制函数）+ **集成块**（fake client 拦截 `messages.create`，断言 `agent_loop` 真把机制接通了）。**只测 harness 逻辑，不调 LLM**——快、免费、确定性，"绿"是可靠的达标信号。

```sh
# 单章
.venv/bin/python tests/test_s09_memory.py
# 全回归
for f in tests/test_s*.py; do .venv/bin/python "$f"; done
```

## 失败模式与恢复（真实测试输出）

harness 对四类失败的处理路径，都有集成测试锁住行为：

| 失败 | 处理 | 测试证据（实际输出节选） |
|---|---|---|
| LLM 429 限流 | 指数退避重试，必要时 fallback 模型 | `✅ 集成：fake client 被调用了至少 2 次（第 1 次 429 + 第 2 次成功）`（s11，21/21） |
| prompt 超长 | reactive compact 压缩历史后恢复重试原请求 | `✅ C prompt 过长：恢复后重试了 create`（s20，7/7） |
| 危险命令 | 权限层直接拒绝，结果以 error 回填给模型 | s03 deny list（固化在起点） |
| 上下文膨胀 | 每轮 budget/snip/micro 三层修剪，超 `CONTEXT_LIMIT` 触发 L4 全量压缩 | s08（19/19） |

## 限制

- 单机单用户：会话/记忆/任务/邮箱全是本地文件，无并发控制、无多租户
- 压缩阈值、重试次数等是写死的常量，未做配置化
- MCP 仅实现 client 侧最小协议（连接 + 工具池重建），未实现 server
