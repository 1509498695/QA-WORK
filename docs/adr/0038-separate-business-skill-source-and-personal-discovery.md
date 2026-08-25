---
status: accepted
date: 2026-08-25
---

# 分离业务 Skill 源码与个人发现入口

业务 Skill 的唯一活动源码统一保存在仓库根 `skills/<skill-name>/`，由 QA Skill Hub 共同版本化；当前用户跨项目使用时，通过 `C:\Users\chenzhen\.agents\skills\<skill-name>` Junction 指向该源码。仓库不在 `.agents/skills/` 再暴露同一 Skill，避免当前仓库同时从 REPO 与 USER 两个作用域发现同名入口。

个人发现入口必须由可审计脚本按精确名称创建、检查或更新，不复制 Skill 文件，也不修改其他用户级 Skills。Provider 使用指导继续由对应 Provider Plugin 自带；需要向其他用户分发业务 Skill 时，另行建立业务 Plugin，不能将业务工作流并入任一 Provider Plugin。
