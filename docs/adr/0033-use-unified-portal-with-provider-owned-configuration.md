---
status: accepted
date: 2026-08-20
---

# 统一管理门户但由各 Provider 独立拥有配置

能力平台提供一个统一管理门户，让管理员发现各 Provider、查看脱敏配置状态并进入对应配置台；每个 Provider 仍独立拥有字段定义、校验规则、配置会话、持久状态和凭证存储。门户不得接收或转存跨 Provider 配置载荷，不建设万能键值配置表或共享密钥库；首版只接入 Feishu Provider，未来 SVN 等 Provider 通过各自边界扩展。

## Considered Options

- 由门户集中保存所有 Provider 配置和 Secret：拒绝，因为它会重新合并身份、权限、故障与撤销边界，并形成跨系统高敏感单点。
- 每个 Provider 只提供完全孤立、不可发现的管理页面：拒绝，因为管理员无法从公共能力工作空间获得一致的就绪状态和配置入口。
- 使用带 Provider 前缀的共享配置表：拒绝，因为字段名前缀不能替代独立校验、凭证存储和生命周期所有权。

## Consequences

- 门户只能聚合 Provider 身份、脱敏状态和配置入口，不能把某个 Provider 的配置成功解释为其他 Provider 已就绪。
- 单个 Provider 的配置服务或凭证存储不可用时，不得阻断其他 Provider 的配置与只读状态。
- Provider 配置页面可以共享视觉规范，但不能共享配置记录、Secret 字段或隐式管理员授权。
