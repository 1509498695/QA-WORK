---
status: accepted
date: 2026-08-25
---

# 提交确定性的自包含 Plugin Runtime

`plugins/workspace-feishu/runtime/` 作为可直接安装的生成快照纳入 Git，但不是可编辑源码真源。确定性构建脚本只从 `platform/capability-contracts`、`providers/feishu/protocol` 与 `providers/feishu/mcp-server` 组装 runtime，并生成记录来源版本与逐文件哈希的 `BUILD-MANIFEST.json`；校验发现缺失、陈旧或手工修改时失败关闭。

拒绝让已安装 Plugin 通过仓库外相对路径引用开发源码，因为安装缓存、跨机器运行和旧仓清理都会使路径失效；也拒绝默认要求安装前现场构建，因为 Marketplace 中每个提交都应保持可验证、可直接安装。Runtime 不再包含授权控制面实现，后续更新必须同时通过 runtime 同步校验、Plugin 清单校验、缓存刷新、重新安装和新任务加载验证。
