---
name: hiro-play-hif
description: 在实机上开一局 H.I.F（整局或只打本战），盯住运行并在停机时判断原因。使用者说「开一局」「打本战」「续跑」时用。
---

# 打一局

开局前确认（问使用者，不要猜）：
- 游戏停在首页；没有别的设备上进行中的培育；AP 够或有 APドリンク。
- 支援卡 / 回忆编成号（`HIRO_SUPPORT_SET` / `HIRO_MEMORY_SET`）、偶像卡、主属性。
- 这一局是整局还是只打本战。

```bash
# 整局
HIRO_SUPPORT_SET=<n> HIRO_MEMORY_SET=<n> python tools/play_game.py --idol <skin id> --main-attr <Vo|Da|Vi>
# 只打本战
HIRO_SUPPORT_SET=<n> HIRO_MEMORY_SET=<n> python tools/play_game.py --main-only --idol <…> --main-attr <…>
# 停机后续跑（同一培育）
PYTHONPATH=src python -m hiro.run_hif resume --mode qualify|main
```

运行中盯 `reports/live/<trace>.log`：
- 关键行：考试每轮结果（本战轮次结果）、轮间结束（IntervalEnd）、停机（ERROR / EXIT）。
- 看门狗停机（3 分钟没进展）、认不出页面、决策没给出合法动作时，程序会自己停下并存帧，**不要强行点继续**。
  先看停机前的截图（`traces/<trace>/frames/` 最后几张），判断是识别、页面转移还是决策问题，报告使用者。

绝不做：用ジュエル、破棄 / リタイア、在开局确认页替别的设备按「開始する」、`--allow-discard`（除非使用者明确同意放弃另一份进度）。

结束后报告：每场分数 / 名次、轮间买了什么、停机次数和原因、LLM 用量（`reports/runs/<trace>.json` 的 llm）。
