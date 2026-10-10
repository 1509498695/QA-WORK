---
status: accepted
date: 2026-09-01
---

# 使用 PKCE 绑定的一次性票据交付 Feishu 平台身份

`providers/feishu/auth-service` 继续独立拥有飞书应用凭证、OAuth 授权码交换、Refresh Token Profile 和 Access Token；`platform/control-plane` 只拥有平台用户、会话、团队和角色。控制面发起登录时生成服务端保存的高熵 verifier，并把对应 PKCE challenge、随机 state 和固定白名单回调交给 OAuth 服务。真实 OAuth、租户准入和用户信息回读成功后，OAuth 服务签发短时单次不透明票据；控制面后台使用原 verifier 兑换稳定身份属性，随后以 `tenant_key + app_id + open_id` 幂等建立 `user_ref`。

身份票据不得包含 Access Token、Refresh Token 或 App Secret，不得长期保存，也不得由浏览器直接解包。OAuth 服务只接受配置中的精确 localhost 控制面回调；票据兑换失败、过期、重放或 PKCE 不匹配都必须失败关闭。`union_id`、认证姓名和头像只作为辅助或展示字段，不参与主键与授权。

## 被拒绝的方案

- 控制面直接读取 Provider 的 DPAPI Profile 或导入 OAuth 服务实现：拒绝，因为会把凭证所有权和部署边界复制进平台包。
- 在回调 URL 中放入签名后的完整用户声明：拒绝，因为身份字段会进入浏览器历史、日志和错误报告，且长期签名密钥会成为新的共享凭证。
- 只传 `profile_ref` 并让控制面信任浏览器：拒绝，因为 `profile_ref` 不是用户身份本身，浏览器参数也不能作为授权证明。
- 为本机公开客户端配置固定 shared secret：拒绝，因为它需要复制和轮换第二份长期凭证；高熵单次票据加 PKCE 已能完成当前 localhost 威胁模型下的发送方绑定。

## 结果

- Provider 和平台之间增加一个 Provider 私有身份交付合同，但公共业务 Skill 和能力契约不感知该协议。
- 平台数据库可以持久化真实身份与角色，同时通过自动化证明 OAuth Token 和应用密钥不会进入该库。
- 当前实现只接受精确 HTTP localhost 回调；正式内网部署必须在启用 HTTPS 与可信代理边界后重新审查回调注册、Cookie Secure 和服务间认证。
