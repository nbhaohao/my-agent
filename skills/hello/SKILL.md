---
name: hello
description: 一个示例技能，演示 SKILL.md 的格式（frontmatter + 正文）
---

# Hello Skill

这是技能正文，平时不进 SYSTEM prompt，只有当 agent 调用 `load_skill("hello")`
时才会被完整加载进上下文。

## 用途
演示「两层加载」：
- 第一层（便宜）：只有上面的 name + description 进 SYSTEM 目录
- 第二层（贵）：这段正文按需加载

SKILL_BODY_MARKER_42
