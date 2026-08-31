# 飞书来源用例生成合同

## 生成核心

飞书只是来源与交付通道，不改变用例语义。生成仍按以下层次进行：

1. `source_facts.json`：只写来源能证明的事实；冲突与缺图保持 `pending`。
2. `generation_blueprint.json`：按业务目标、角色、入口、状态、数据边界、异常恢复和回归关系拆分。
3. `completeness_matrix.json`：逐项审计 GR-01～GR-08。
4. `pending_boundary_confirmations.json`：承载需要用户决定的边界，不混入执行用例。
5. 基础用例、分类、候选、横向/项目规则评估、最终用例和映射账本。

GR-01～GR-08 分别是主流程闭环、权限资格与对象、状态生命周期、数据计算与边界、异常恢复与幂等、界面交互与表现、关联模块与回归、时效范围与兼容。每项只能为 `covered`、`not_applicable` 或 `pending`；`not_applicable` 必须给具体事实理由。

## 复用已发布规则

- 读取已安装 `qa-case-xlsx-local` Skill 的公开 `generation-blueprint.md`、`humanization.md`、`project-router.md`、规则索引和本次触发的规则文件。
- 运行其 SKILL.md 已公开的 `run_case_pipeline.py validate-rules` 与 `validate-run` CLI，复用同一 50 条发布规则和 A:J 字段校验。统一纵切调用 `validate-run` 时显式传入本 Skill 的 `project-modules.json` 与 `provisional-modules.json`，不得把非游戏来源伪装为 SAMO 模块。
- 这是 artifact/CLI 级组合；不得 `import` 其 `scripts/pipeline`，不得调用 `build_source_packet.py` 读取飞书，也不得调用本地工作簿构建器作为飞书交付。
- 若已安装本地 Skill 的规则发布清单不可读、哈希不一致或数量不是 50，停止正式生成。

## 最终用例

固定列为：

1. 用例编号
2. 一级模块
3. 二级模块
4. 检查点
5. 前置条件
6. 操作步骤
7. 预期结果
8. 优先级
9. 测试结果
10. 备注

使用自然数量；只有用户明确要求固定、抽样或精简时才改变。状态、权限、对象、时序或主要预期不同通常拆分；同状态下的一组静态展示目标可以合并。

操作步骤逐行连续编号，预期必须可直接观察。禁止“功能正常”“显示正常”“符合配置”等空话。优先级仅 `P0/P1/P2`，测试结果初始为空，备注为空或使用正式前缀。

## 正式门禁

只有以下条件全部成立，才允许构建飞书 Sheet 规范：

- 所有用户声明来源状态为 `complete`，所有视觉资产已复核；
- 事实冲突为零，完整性矩阵无 `pending`；
- 待确认边界清单为空；
- 50 条规则发布包验证通过；
- `validate-run` 返回 `status=ok`；
- 最终用例非空、编号连续、语义签名唯一、来源与规则映射完整。

当前纵切不向飞书交付待确认草稿；任一正式门禁未通过时停止在本地审计状态，不创建远端预览。

校验命令：

```powershell
& <bundled-python> <qa-case-xlsx-local-root>\scripts\run_case_pipeline.py validate-run `
  --run-dir <task-root>\audit `
  --rules-dir <qa-case-xlsx-local-root>\references\rules `
  --modules <skill-root>\references\project-modules.json `
  --provisional-modules <skill-root>\references\provisional-modules.json
```
