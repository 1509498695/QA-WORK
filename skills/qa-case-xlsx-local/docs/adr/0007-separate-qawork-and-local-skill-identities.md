---
status: accepted
date: 2026-08-17
---

# 区分 QAWORK 原版与独立本地版

QAWORK 原版保留 `qa-case-xlsx` 身份，唯一源码仍是 QAWORK 仓库内的 `.agents/skills/qa-case-xlsx`。当 Codex 任务以外层 `D:\project\QAWORK` 为工作区时，只允许通过外层项目级目录联接暴露同一份原版源码；不得复制原版，也不得把原版注册到用户级 Skill 目录。

独立本地版使用 `qa-case-xlsx-local` 身份，以 `D:\project\qa-case-xlsx` 为唯一源码并通过用户级发现入口供其他工作区调用。两个版本不覆盖、合并或互相同步；这个选择以两个明确调用名换取 QAWORK 原版不变、独立版跨工作区可用，并避免同名 Skill 产生歧义。
