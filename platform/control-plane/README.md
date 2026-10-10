# QA Skill Hub Control Plane

这是中央控制面的开发态真实纵切。它通过 Feishu OAuth 服务建立稳定平台身份，把用户、唯一团队成员资格、团队管理员、平台管理员、会话、邀请和审计状态持久化到独立 SQLite；控制面不保存 App Secret、Access Token 或 Refresh Token。

先保持 Feishu OAuth 服务运行：

```powershell
.\scripts\run-local-auth.ps1
```

再在第二个终端启动控制面：

```powershell
.\scripts\run-control-plane-ui.ps1 -OpenBrowser
```

首次空状态会在终端生成十五分钟有效的一次性 Bootstrap 凭证，并把它只保留在当前服务进程内存。打开 `/start` 后直接点击“使用飞书账号初始化并登录”即可完成首位平台管理员验证，无需复制临时链接。之后固定使用四个主入口：

- `/start`
- `/account`
- `/team`
- `/platform`

默认状态库位于 `%LOCALAPPDATA%\WorkspaceCapabilities\control-plane\state-v1.sqlite3`。当前开发阶段不迁移不兼容 Schema；必须由平台管理员在“平台设置 → 开发重置”输入 `RESET` 后显式清空并重新 Bootstrap。

关键不变量：

- 用户主键来自 `tenant_key + app_id + open_id`，显示名称不参与授权。
- 一个用户在当前控制面中最多只有一个有效团队成员资格。
- 团队与平台都始终至少保留一名对应管理员。
- 平台管理员资格和团队角色独立；平台管理员不会自动获得团队内容访问。
- 邀请有租户、到期、撤销和次数限制，完整链接只在创建响应中显示一次。
- OAuth 服务使用短时、单次、PKCE 绑定的票据向控制面交付身份属性，任何 OAuth Token 都不会进入平台状态库。
