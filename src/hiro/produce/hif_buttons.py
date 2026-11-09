"""H.I.F 本战固定按钮表（720x1280 竖屏坐标）。

值在按钮注册表 hiro/ui/buttons.json（使用者覆盖 conf/ui/buttons.json，见 hiro.ui.buttons）；这里只是按原名取值的别名，
旧代码 `BTN.CORNER` 照常用。新按钮加进 buttons.json，再在这里加一行别名。

来源：仓库各模块的实测坐标，加上使用者整理的本战界面截图与说明（2026-11-07，覆盖本战几乎所有界面）。
截图量出的位置与已有实测一致（误差约 10px 内），表里优先用已有实测值。

用法约定：
- 同一位置只写一次，按「位置」命名，注释里列出所有用到它的页面；新代码从这里取，不再各自写死。
- 表里没有的页面：一直点 CORNER（或 BAR_GAP）快进 / 跳过即可（使用者：「除此之外都可以点 (12,12) 或屏幕底部跳过」）。
- 花钱 / 不可撤销的按钮（ジュエル、破棄、リタイア、プロデュース終了、開始する）只给位置，调用方必须先做文字核对再点。
"""

from __future__ import annotations

from hiro.ui import buttons as B

# ---------------------------------------------------------------- 通用快进
CORNER = B.get("CORNER")  # 绝大多数页面无害：过场、名次页、スター性獲得、P 道具获得、最終プロデュース評価、LIVE 结束后
BAR_GAP = B.get("BAR_GAP")  # 底栏第 4 格饮料（右缘 x≈352）与行动记录图标（左缘 x≈421）之间的空缺：考试 / 行动页都没有按钮
#                       （使用者：(12,12) 不够保险时用它；选择中也不允许快进）

# ---------------------------------------------------------------- 底部居中（两档高度）
BOTTOM_LOW = B.get("BOTTOM_LOW")  # アイドル選択「次へ」、相談行动页唯一选项「相談」、回忆列表「OK」、
#                           相談 / 轮间「交換する」(y≈1077–1088)、卡 / 饮料获取「受け取る」(y≈1098–1104)
BOTTOM_HIGH = B.get("BOTTOM_HIGH")  # 回忆页 / 挑选页「決定」「次へ」、单按钮弹窗（删卡 / 强化）
SETTLE = B.get("SETTLE")  # 结算页「×」(360,1188) 与后面「次へ」圆角框的重合处：有 × 关 ×、没有点到次へ，一路回首页
BOTTOM_BLANK = B.get("BOTTOM_BLANK")  # 底部空白：活动登录奖励（正中是道具框，点它不翻页）、领奖页推进

# ---------------------------------------------------------------- 空白处（推进 / 取消选中）
TOP_BLANK = B.get("TOP_BLANK")  # 画面上方空白：P 道具浮条点不掉 (12,12) 时用；过场推进的备用点
STAGE_BLANK = B.get("STAGE_BLANK")  # 考试舞台空白：取消手牌选中、跳过考试动画
TOAST = B.get("TOAST")  # 画面中下：「〜をカスタマイズしました」提示、授業结束后的训练员台词框
SHELF_GAP = B.get("SHELF_GAP")  # 商店货架第 2、3 列之间的空隙：跳过获得动画，回到相談后误点也不会选中商品

# ---------------------------------------------------------------- 弹窗两键（左取消、右确认，与大多数确认框同版式）
DIALOG_CANCEL = B.get("DIALOG_CANCEL")
DIALOG_OK = B.get("DIALOG_OK")

# ---------------------------------------------------------------- 三选一 / 选项行
THREE_X = B.get("THREE_X")  # 行动页三张卡（授業 ×3、公開レッスン Vo/Da/Vi）：位置和顺序固定；おでかけ / 差し入れ 只有两张，居中在 x≈278 / 444
THREE_Y = B.get("THREE_Y")  # 三张卡的卡面中心（文字标签在 y≈1050–1100）
OPTION_ROWS = B.get("OPTION_ROWS")  # 授業 / イベント选项三行。非凡（アノマリー）角色：上 = 強気、中 = 全力、下 = 未特定
REST = B.get("REST")  # 行动页右上「休む」
EXAM_DRINK_DISCARD = B.get("EXAM_DRINK_DISCARD")  # 考试饮料详情框「捨てる」（OCR 找不到时的兜底）

# ---------------------------------------------------------------- 相談 / 轮间商店（位置全部固定）
SHOP_END = B.get("SHOP_END")  # 「終了」
SHOP_UPGRADE = B.get("SHOP_UPGRADE")  # 「強化」（相談）
SHOP_DELETE = B.get("SHOP_DELETE")  # 「削除」（相談）
REROLL = B.get("REROLL")  # 「リフレッシュ」
INTERVAL_CUSTOMIZE = B.get("INTERVAL_CUSTOMIZE")  # 轮间「スキルカードカスタマイズ」
INTERVAL_UPGRADE = B.get("INTERVAL_UPGRADE")  # 轮间「強化」
INTERVAL_RECOVER = B.get("INTERVAL_RECOVER")  # 轮间「回復」
EXCLUDE = B.get("EXCLUDE")  # 卡牌获取页左下「除外」
# 轮间：饮料固定在货架右下两格，换卡在右上，买卡在左侧（使用者说明）

# ---------------------------------------------------------------- 考试结果（R1 / R2 版式相同）
RESULT_RETRY = B.get("RESULT_RETRY")  # 「再挑戦」：只在核对写明免费 / 再挑戦チケット后点
RESULT_NEXT = B.get("RESULT_NEXT")  # 「次へ」
RETRY_DIALOG_END = B.get("RETRY_DIALOG_END")  # 敗退后「再挑戦確認」左：プロデュース終了（= 失败，不自动点）
RETRY_DIALOG_RETRY = B.get("RETRY_DIALOG_RETRY")  # 右：再挑戦

# ---------------------------------------------------------------- LIVE 后回忆
MEMORY_GENERATE = B.get("MEMORY_GENERATE")  # 「MEMORY」生成页中间「生成」
MEMORY_REGEN = B.get("MEMORY_REGEN")  # 回忆页 / 挑选页左下「再生成」
MEMORY_PREV, MEMORY_NEXT = B.get("MEMORY_PREV"), B.get("MEMORY_NEXT")  # 挑选页切换版本
MEMORY_DOTS_Y = B.get("MEMORY_DOTS_Y")  # 挑选页分页圆点（橙 = 当前）
REGEN_CONFIRM_DY = B.get("REGEN_CONFIRM_DY")  # 「メモリー再生成確認」框「再生成する」：(498, 标题条顶 + 621)
FINAL_CONFIRM_DY = B.get("FINAL_CONFIRM_DY")  # 「メモリー確定確認」框「生成する」：(485, 标题条顶 + 292)

# ---------------------------------------------------------------- AP 药（只用 APドリンク，流程见 hiro.loop.ap_refill，按文字核对后点）
# 1「AP不足」框：右「回復する」= DIALOG_OK 一带 → 2「AP回復」框：APドリンク 行右侧「使う」→
# 3「APドリンクを消費して、APを回復しますか？」框：右下「回復する」= DIALOG_OK 一带 → 回到 H.I.F 首页（選抜試験 / 本戦）。
# 坐标按使用者截图（2026-11-07）缩放估算，误差约 ±15px；按钮行在弹窗底部，和 DIALOG_* 同一高度。
AP_USE = B.get("AP_USE")  # 「AP回復」框 APドリンク「使う」（框高度随道具行数变，优先按文字找）
AP_CLOSE = B.get("AP_CLOSE")  # 「AP回復」框「閉じる」
AP_PLUS = B.get("AP_PLUS")  # 第 3 步确认框滑条右端「＋」

# ---------------------------------------------------------------- 开局（只给位置，开始 / 跨设备保护照旧走文字核对）
IDOL_NEXT = B.get("IDOL_NEXT")  # アイドル選択「次へ」
START = B.get("START")  # 開始確認「プロデュース開始」：绝不跳过核对
MEMORY_CHANGE = B.get("MEMORY_CHANGE")  # 開始確認「選択中の選抜試験メモリー」右侧「変更」
MEMORY_SORT = B.get("MEMORY_SORT")  # 回忆列表排序键按钮 → ソート・フィルタ 弹窗
MEMORY_LIST_OK = B.get("MEMORY_LIST_OK")  # 回忆列表底部「OK」
