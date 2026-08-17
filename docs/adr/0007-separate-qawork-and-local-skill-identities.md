---
status: accepted
date: 2026-08-17
---

# 区分 QAWORK 原版与独立本地版

QAWORK 原版保留 `qa-case-xlsx` 身份并只在 QAWORK 仓库范围内发现，独立本地版改用 `qa-case-xlsx-local` 身份，以 `D:\project\qa-case-xlsx` 为唯一源码并通过用户级发现入口供其他工作区调用。两个版本不覆盖、合并或互相同步；这个选择以两个明确调用名换取 QAWORK 原版不变、独立版跨工作区可用，并避免 Codex 同时发现两个同名 Skill 时产生歧义。
