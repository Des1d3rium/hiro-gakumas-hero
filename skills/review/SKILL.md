---
name: hiro-review
description: 复盘一局：生成逐回合复盘页、跑低级失误审计、定位决策或识别问题，并把发现写成回归用例。一局打完、或使用者问「这一步为什么这样打」时用。
---

# 复盘

```bash
python tools/viewer/build_exam_view.py <trace> --out reports/viewer     # 逐回合复盘页（单个 HTML）
python -m tools.audit.blunders <trace>                                  # 低级失误：有牌却结束回合、读数被拦、页面认不出、点击没生效…
python -m tools.audit.transitions <trace>                               # 过场 / 动画耗时
python tools/preview_stats.py <trace>                                   # 每步预演耗时与世界数（有预演时）
```

看复盘页时按这个顺序：
1. 每场考试开局牌库和饮料是否合理（轮间买的东西、删卡、强化）。
2. 每一步的候选、理由、状态曲线；可疑的一步看当时的截图。
3. 对照 `docs/hif-playbook.md` 的社区打法：偏离了吗？有没有写明依据？

发现问题后：
- 读数错 → 找对应识别模块和 `tests/test_*_read.py`，用这一帧加用例。
- 页面卡住 / 字段丢失 → 查 `hiro/loop/transitions.py` 的转移表和对应钩子。
- 打法错 → 写一条合成用例到 `examples/cases/`，再改规则；改变实机行为的改动放在开关后面，默认关，告诉使用者。
