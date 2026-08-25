---
name: feishu-auth
description: 处理独立 Workspace Feishu Provider 的本机 OAuth 授权、重新授权、缺少 Scope、授权状态和恢复链接。用户要求连接飞书、重新授权，或 Workspace Feishu 返回 auth_required 时使用；普通文档内容读取不单独触发。
---

# Workspace Feishu Authorization

## 固定本机入口

首版本机中央服务的授权入口为：

`http://localhost:3000/oauth/start`

需要授权或重新授权时，始终返回可点击链接：

`[重新进行飞书授权](http://localhost:3000/oauth/start)`

不要把回调 URL、一次性 `code`、`state`、Access Token、Refresh Token、App Secret 或本地客户端 Secret 复制给用户或写入对话。

## 流程

1. 若读取工具返回 `auth_required`，优先使用其 `details.authorization_url`；仅接受 `http://localhost:<port>/oauth/start` 形式的本机链接。
2. 若错误未携带安全链接，使用固定本机入口。
3. 告知用户在网页中完成授权并回到当前对话确认。
4. 用户确认授权成功后，重新执行原来的读取操作；不要复用旧回调 URL 或旧 `state`。
5. 授权失败时只报告页面或工具返回的安全状态码，并重新生成/提供授权入口。

## 独立性

- 不调用或复用 `lg-feishu`。
- 本插件只依赖 QA Skill Hub 的本机授权服务、Windows 当前用户的 DPAPI Profile 和任务级短租约。
- 本 Skill 不负责修改飞书开放平台配置；缺少 Scope 时说明所需只读能力，并在权限发布后重新授权。
