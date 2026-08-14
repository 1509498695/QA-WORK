# 离线项目路由

项目路由只决定读取哪份项目风险参考，不决定业务事实。

## 识别顺序

1. 用户在本次消息中明确指定项目。
2. 源文件正文、标题或文件名出现 registry 中的明确别名。
3. 无唯一匹配时使用 `generic`，不根据目录名、历史对话或 QAWORK 状态猜测。

多个项目信号同时出现且无法判断主项目时，记录 `project_classification=pending`，使用通用规则并生成草稿。

## 路由表

- ROK / 万国觉醒：读取 `project-rok.md`。
- CoD / COD / SAMO / 万龙觉醒：读取 `project-cod.md`。
- Beagle / 比格：读取 `project-beagle.md`。
- Dobe：读取 `project-dobe.md`。
- generic：不读取任何项目专属规则。

项目参考中的条目只在源包出现相应机制时触发。不得仅因项目名称命中就批量生成全部项目用例。
