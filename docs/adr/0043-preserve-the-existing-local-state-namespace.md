---
status: accepted
date: 2026-08-25
---

# 保留现有本机状态命名空间

项目、Plugin 和开发者的用户可见身份统一为 `QA Skill Hub`，但现有 `%LOCALAPPDATA%\WorkspaceCapabilities\providers\feishu\` 路径及相关 DPAPI 描述继续作为兼容性存储命名空间。该字符串不再定义仓库或产品身份，只用于确保目录重构、Plugin 更新和重新安装后仍能读取当前部署绑定与 Provider Profiles。

本次不得自动复制、移动、删除或重加密现有本机状态。若未来需要统一存储命名，必须单独设计包含精确预览、原路径保留、逐文件回读、失败回退和重新授权处置的状态迁移，并取得独立授权。
