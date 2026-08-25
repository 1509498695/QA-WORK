---
status: accepted
date: 2026-08-19
---

# 本地文件使用共享构件库而非 Provider

本地文件读写由 monorepo 内的共享本地构件库提供，预期归入 `packages/local-artifacts/`；`providers/` 只承载飞书、SVN 等具有独立远端身份、授权和一致性问题的外部系统。业务 Skill 可以在不启动 MCP、不联网的情况下使用本地构件能力，但正式写入用户目标位置仍必须经过本次交付通道确认，并执行安全路径校验、原子写入、哈希和精确回读；任务暂存区写入继续按非正式交付处理。

## Considered Options

- 建立 Local Provider/MCP：拒绝，因为本地文件没有需要单独守护进程拥有的远端身份，却会让纯本地 Skill 增加运行时和故障依赖。
- 每个业务 Skill 自行实现本地文件读写：拒绝，因为路径边界、原子写入、哈希、回读和审计规则会重复并逐渐分叉。

## Consequences

- `qa-case-xlsx-local` 迁入后可以直接复用本地构件库，同时继续保持无 Provider、无网络的兼容边界。
- 本地构件库不得包含飞书降级逻辑、外部链接跟随或任何远端凭证。
