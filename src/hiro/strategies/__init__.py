"""策略档案：使用者可以参考、替换的策略数据（不是代码）。

- profiles：回忆档案（客制化 / 换卡 / 饮料 / 结算回忆优先级），合并、快照、运行时读取。
- registry：偶像档案（考试引擎、牌名、顺序、阈值、开关默认值、能力位）。
- names：卡 / 饮料的 ID ↔ 显示名（game.db）。
- schema：档案的 JSON Schema 校验（schemas/*.schema.json，前端也按它们生成表单）。

说明见 docs/strategies.md。
"""
