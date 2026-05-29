# my-agent

我从零构建的 coding agent —— 跟着「Learn Claude Code · Agent Harness 工程」边学边造。
每学一个机制就加一层，最终是一个完整的 agent harness。这个 repo 既是学习产物，也是作品集。

## 已实现的 harness 机制

- **Agent Loop**：`while` 循环 + `stop_reason` 驱动（s01）
- **多工具 + dispatch map**：bash / read / write / edit / glob / todo（s02）
- **权限**：deny list + 危险命令拦截（s03）
- **Hooks**：PreToolUse / PostToolUse / Stop 扩展点（s04）
- **TodoWrite**：先计划后执行 + nag 提醒（s05）
- **Subagent**：fresh context 子 agent，只回摘要（s06）
- *(进行中)* **Skill 两层加载**（s07）

## 运行

```sh
python3 -m venv .venv                    # 首次：建虚拟环境
.venv/bin/pip install -r requirements.txt
cp .env.example .env                      # 填入你的 API key
.venv/bin/python agent.py                 # 终端交互，输入问题回车，q 退出
```

## 测试

每个机制配一个验收测试（只测 harness 逻辑，不调 LLM，快且免费）：

```sh
.venv/bin/python tests/test_s07_skills.py
```

全绿 = 该机制达标。
