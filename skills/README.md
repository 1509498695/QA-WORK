# 业务 Skills

`skills/` 是 QA Skill Hub 的业务工作流目录。每个直接子目录对应一个可独立发现、测试和演进的具名 Skill。

## 目录规则

- 一个公开 Skill 名称对应一个目录：`skills/<skill-name>/`。
- 目录内持有该 Skill 的 `SKILL.md`、`agents/`、私有脚本、资源、参考资料、领域文档和测试。
- 业务 Skill 可以编排多个 Provider，但只能调用对应 Plugin 暴露的公开工具和合同。
- Provider 的 MCP、授权、凭证、远端协议和指导 Skills 留在 `plugins/<provider>/` 及其 Provider 源码边界，不复制进业务 Skill。
- 只有两个及以上业务 Skill 已出现真实、稳定的代码复用需求时，才另行设计 `packages/<package-name>/`；不得用跨目录相对导入或薄壳提前模拟共享包。
- 新 Skill 必须先明确身份、权限、输入输出、证据门禁、测试和发现方式，不能通过另一个 Skill 静默转发。

仓库目录是业务 Skill 的唯一源码真源。当前用户的个人发现入口使用同名 Junction 指向该目录，不复制源码，也不在仓库 `.agents/skills/` 重复暴露同一 Skill。可运行 `scripts/install-personal-skills.ps1 -Check` 校验当前绑定，或在入口缺失时不带 `-Check` 创建它；脚本遇到同名冲突路径会停止，不会覆盖。

## 当前成员

- [`qa-case-xlsx-local`](./qa-case-xlsx-local/)：只读取本地策划案并生成本地 Excel 测试用例；不读取或写入飞书，不依赖 QAWORK、Jira、Code Ask 或网络。

未来需要飞书读写、SVN 读取或多 Provider 编排的生成用例、生成文档等 Skill，仍各自放在 `skills/<skill-name>/`。能力组合不改变业务 Skill 的目录归属。
