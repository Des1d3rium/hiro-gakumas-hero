# AGENTS.md：给接手这个仓库的 AI

你拿到的是「数字小广」（hiro-gakumas-hero）的开源部分：一套让大模型自动玩《学园偶像大师》H.I.F 模式的框架。
读完这份文件，你应该能装好环境、在使用者自己的 Mac 上打一局，并在出问题时知道去哪里查。

先读这三份：

1. 本文件（规则、架构、怎么跑、怎么查）。
2. `docs/assets.md`：识别要用的素材从哪来、缺了会怎样、怎么补。
3. `docs/hif-playbook.md`：H.I.F 的社区打法（策略层的先验）。

## 一、绝对不能做的事（代码里有拦截，你也不要绕过）

这些操作不可撤销或会花真钱，任何时候都不做；遇到需要做的情况，停下来交给使用者。

| 规则 | 代码里的拦截 |
|---|---|
| 不用ジュエル（付费货币）。AP 不够只喝 APドリンク；确认框 / 再挑戦按钮带ジュエル就点キャンセル | `loop/ap_refill.py`、`produce/hif.py`（再挑戦）、考试危险词 |
| 不按「破棄」「リタイア」，不放弃进行中的培育 | `loop/kaa_patches.py`（NeverConfirmDiscard）、`loop/rescue.py` |
| 在别的设备开着的培育：开局确认页绝不按「開始する」（会破弃另一台设备的进度），停下来 | `loop/resume.py`、`loop/controller.py` |
| 通信错误弹窗不点「タイトルへ」 | `loop/comm_error.py`、`loop/rescue.py` |
| LLM 兜底只能点 OCR 识别出的文字框或固定空白处；选择页、考试画面不点；点之前重新截图核对；次数有上限 | `loop/rescue.py` |
| 再挑戦只用免费次数 / 再挑戦チケット，最多 3 次 | `produce/hif.py` |
| 不默认选第一个：换卡 / 删卡认不出或全是保护卡就キャンセル；租借格挑不到就停；考试决策越界时按规则重选，不退回第一个合法动作 | `produce/`、`defaults/advisor.py` |

另外：

- 不要把截图、trace、账号、密钥、主数据原文件提交进仓库。`.gitignore` 已挡住 `resources/`、`tests/fixtures/`、`cache/`、`traces/`。
  `assets/pack/` 是仓库有意带的识别素材包（权利属于游戏的权利人，见 `assets/pack/README.md`），除此之外不要再往仓库里加游戏素材或截图。
- 不要改 `vendor/kaa`（KAA 子模块，固定版本）。需要改 KAA 行为时，在 `hiro/loop/kaa_patches.py` 里打补丁。

## 二、架构

```
                       ┌───────────── 策略层 hiro.brain / hiro.defaults.advisor ─────────────┐
                       │ 社区规则（docs/hif-playbook.md）→ 规则链 → LLM 兜底（hiro.brain.llm） │
                       └──────────────▲────────────────────────────────┬──────────────────┘
                                      │ GameState / BattleState        │ Decision（契约）
截图 ─► 识别层 hiro.vision / perception ─► Observation ─► reduce（纯函数）─► 状态 hiro.state
           ▲ 素材：插件 AssetSource                              │
           │                                                      ▼
设备 ◄── 操作层 hiro.device / tap / exec / loop / produce ◄── 执行器（点击、等稳、补点、弹窗、恢复）
                                      │
                       trace（每一步：截图 + 观测 + 状态 + 决策 + 执行结果）
                                      ▼
                 复盘页 tools/viewer · 审计 tools/audit · 回放 hiro.loop.replay
```

- **契约**（`hiro/contracts`）是层与层之间唯一的接口。读不到的字段是 `None`，不要猜。
- **插件**（`hiro/plugins.py`）：三处可以换实现，主循环不用改。

| 插件 | 回答什么 | 开源默认实现 |
|---|---|---|
| `asset_source` | 素材和主数据从哪来 | `defaults/assets.py`：KAA 公开数据 + 使用者自建素材 |
| `exam_state_source` | 考试局面从哪来 | `defaults/exam_state.py`：画面识别 + 状态机记账（不知道牌库顺序） |
| `exam_advisor` | 考试每一步怎么打 | `defaults/advisor.py`：社区规则链 + LLM 兜底 |

  换实现：环境变量 `HIRO_ASSET_SOURCE` / `HIRO_EXAM_STATE_SOURCE` / `HIRO_EXAM_ADVISOR`（写成 `包.模块:类名`），
  或在 `pyproject.toml` 的入口点组 `hiro_gakumas.plugins` 里登记，或代码里 `hiro.plugins.register(...)`。

- **不在这个仓库里的东西**：考试存档读取、模拟器、预演搜索（带模拟器的出牌专家）、价值表。作者用私有插件接回去；
  你用默认实现就能玩，只是考试决策没有模拟器那么强。

## 三、环境

只支持 macOS（Apple Silicon）+ PlayCover 跑 iOS 版游戏；Android（ADB）留了接口，还没实现。

1. 装环境（一条命令，可重复跑，做过的步骤会跳过；需要 uv、git 和网络）：
   ```bash
   git clone --recurse-submodules https://github.com/matto49/hiro-gakumas-hero && cd hiro-gakumas-hero
   scripts/setup.sh      # 子模块 → .venv（Python 3.10）→ 安装 → 生成 KAA 的 R.py → 下载 KAA 公开数据 + 卡图索引 → 自检
   ```
   从零约 4 分钟。KAA 换版本后加 `--regen` 重新生成；已用 `HIRO_KAA_GAME_DATA` 指向现成数据时加 `--no-data`。
   自检最后会打印插件来源、有没有主数据和自建素材，以及开局命令。
2. 游戏窗口：用 [hguandl 的 PlayCover 分支](https://github.com/hguandl/PlayCover)，在游戏的 PlayCover 设置里打开 `maaTools`（端口 `maaToolsPort`）。
   默认的截图和点击走游戏进程内的 MaaTools 服务（见 `hiro/device.py` 开头注释），**不要求窗口在前台，也不动鼠标**；
   只有 MaaTools 连不上、退回 Quartz 截图 + `CGEventPostToPid` 时，才需要保持竖屏窗口在前台。
   macOS 要给运行它的终端 / 程序「屏幕录制」权限；截图报 `could not create image from display` 时先查这个权限和显示器状态。
3. 素材（见 `docs/assets.md`）：识别用的卡面、图标、字体、Sprite、布局随仓库在 `assets/pack/`，开箱即用。
   客户端更新后认不出新图标时，才打一两场考试，用 `tools/assets/capture_icons.py` 补；设了 `HIRO_BUNDLED_ASSETS=0` 时则必须补，
   否则考试效果名和 P 道具名一直是 `None`，策略层只能用保守规则，分数会明显低。
4. 游戏内设置：`PYTHONPATH=src .venv/bin/python tools/check_settings.py` 检查快进 / 跳过演出是否全开。
5. LLM：`HIRO_LLM` 选适配器（`claude-cli:sonnet`、OpenAI 兼容 API 等，见 `hiro/brain/llm.py`）。

## 四、跑一局

跑之前确认：游戏停在首页；没有别的设备上进行中的培育；AP 够或有 APドリンク；游戏里已经编好支援卡和回忆的编成。

支援卡和回忆可以让程序按社区规则选（[docs/formation-plan.md](docs/formation-plan.md)）：扫库存 → `python -m hiro.brain.formation_plan --idol … --support-set N --memory-set N`
→ 按报告存进这两个编成位。之后开局不用再设编成号和 `--main-attr`，第 2、3 步会按方案核对编成，对不上就停。

```bash
# 整局（选拔 → 本战）
HIRO_SUPPORT_SET=<支援卡编成号> HIRO_MEMORY_SET=<回忆编成号> \
  python tools/play_game.py --idol <偶像卡 skin id> --main-attr <Vo|Da|Vi>

# 只打本战（已经有选拔得到的回忆）
HIRO_SUPPORT_SET=… HIRO_MEMORY_SET=… \
  python tools/play_game.py --main-only --idol <…> --main-attr <…> [--memory Vo,Da,Vi]

# 底层单段命令（play_game 就是把这两段串起来）
PYTHONPATH=src python -m hiro.run_hif new    --mode qualify|main --idol <…> --main-attr <…>
PYTHONPATH=src python -m hiro.run_hif resume --mode qualify|main   # 续跑进行中的培育
```

`play_game` 的 `--` 之后的参数原样交给 `run_hif`（例如 `-- --no-llm` 只用规则）。

| 环境变量 | 作用 |
|---|---|
| `HIRO_SUPPORT_SET` / `HIRO_MEMORY_SET` | 游戏里的编成号。没设、也没有开局编成方案时，在碰游戏前退出（码 2）。写 `auto` 等于 KAA「おまかせ」，支援卡会直接确认列表第一张租借卡，只在你明确想这样时用 |
| `HIRO_MEMORY_CARDS` | 回忆编成带进培育的技能卡 ID（逗号分隔）。决策器把它们算作已有，三选一里不再找；不设时用开局编成方案里的，都没有就当空。手动设编成号时记得一起设 |
| `HIRO_FORMATION_PLAN` | 开局编成方案文件，默认 `conf/formation-plan.json`；`off` = 不读。见 [docs/formation-plan.md](docs/formation-plan.md) |
| `HIRO_LLM` | LLM，如 `claude:sonnet`（默认，走 `claude -p`）、`openai:<model>`（OpenAI 兼容）。`--no-llm` 只用规则 |
| `HIRO_KAA_GAME_DATA` | KAA 公开数据目录（含 `game.db`），默认 `resources/game_data` |
| `HIRO_USER_ASSETS` | 自建素材目录（`capture_icons` 的产出），可选；与随仓库素材包并用，同类图库取文件更多的一个 |
| `HIRO_BUNDLED_ASSETS` | 随仓库素材包目录，默认 `assets/pack`；设 `0` 关掉 |
| `HIRO_ASSET_SOURCE` / `HIRO_EXAM_STATE_SOURCE` / `HIRO_EXAM_ADVISOR` | 换插件实现（`包.模块:类名`） |
| `HIRO_STRATEGY` | 换培育策略类（KAA `HifGrindStrategy` 子类），默认 `hiro.produce.hif:HiroHifStrategy` |
| `HIRO_PAUSE_FLAG` | 外部暂停旗标文件路径：文件存在就暂停对局 |
| `HIRO_CONSULT_DRINK_SWAP=0` | 关掉相談栏满换饮料（默认开：货架上的饮料比持有里最差的一瓶好就先丢后买） |
| `HIRO_CONSULT_LAZY=1` | 相談懒处理（默认关）：watch 交给 shop-auto 前不认获得演出，只等相談主画面就绪（「終了」正常亮度、没有获得名称栏）；盖着就点货架空隙再看，见 docs/batch-hif.md `prep.consult_lazy` |
| `HIRO_LIVE_ROTATE=1` | LIVE 横屏黑屏时先把游戏窗口内容区宽高对调成横向（辅助功能改窗口尺寸，不切全屏、不抢前台），LIVE 结束还原原来的位置和尺寸；开着时不再切全屏，没救回画面就停下交给使用者（窗口已在全屏里时退出照旧走全屏方式）。`batch_hif watch` 默认开（设 `0` 关，退回原来的切全屏），`run_hif` 默认关。要求 PlayCover「宽高比」设为「自由」。手动实测：`PYTHONPATH=src .venv/bin/python -m hiro.loop.landscape rotate\|restore\|show` |
| `HIRO_RESCUE_LLM=0` | 关掉认不出画面时的 LLM 看图兜底（默认开） |
| `HIRO_DEBUG_RECOG=1` | 把全部识别互验写进 trace，页面变化都存帧 |
| `HIRO_DRINK_INSITU=0` | 关掉饮料栏的实机模板读法（默认开：每一格先比实机模板——采到过的饮料用 `tools/assets/capture_drinks.py` 生成的实机中位图，其余按原画渲染成饮料栏外观——认准了就以它为准，包括原画模板判成空的格；实机模板没把握时才留原画模板的结果） |
| `HIRO_CARD_FX=0` | 关掉技能卡结构化效果（默认开：姿态变化、全力値、追加行动、抽牌、生成眠気、使用条件按主数据 `playEffects` / `ProduceExamEffect` / `ProduceExamTrigger` 算，客制化「追加」的效果按效果 id 并进来；查不到主数据的卡照旧按牌面文字解析，见 `hiro/brain/card_fx.py`） |
| `HIRO_CARD_ICON=1` | 换卡页 KAA 认不出的卡格用渲染卡图标再认一次（默认关；要先 `python -m hiro.vision.card_icon_render build`，见 docs/assets.md） |
| `HIRO_VEIL_SIGNATURE=1` | VEIL 牌库按版本记账（默认关）：批量账本里同名卡一次性 / 客制化不同时，各算一个记账键；手里同名几张分不出时点按一次（只选中、不打出）读效果文字分辨，读完点空白取消选中。效果文字的 OCR 区域 `PROBE_TEXT_BOX` 还没实机校准，每次点按的文字都写进 trace（`veil_probe`） |
| `HIRO_VEIL_HIF_FILL=1` | VEIL 本战 R2 按 H.I.F応援棒 记账（默认关）：开考时牌堆补到 22 张，补的「基本」牌名字不知道，先记成占位「基本?」，手里出现账本外、名字带「基本」的牌时改成真名；不开时这张牌被抽到后整副记成位置不明 |
| `HIRO_LANDING_RETRY=1` | 出牌第一次没落地时先不禁用这张牌，重读后再点一次，第二次才禁用（默认关）。VEIL 出牌策略另有 8 个审查开关（`VEIL_CYCLE_PLAN` 等，默认关），见 docs/batch-hif.md |
| `HIRO_VEIL_SUPPORT_BOOST=1` | 考试中支援卡的临时强化按总档位算效果（默认关）：卡图右上角叠的支援卡头像个数 = 临时强化次数（`hiro.vision.support_boost`），卡名「+」被盖住时效果换成「账本档位 + 头像数」那一档；账本不记临时强化。5 张手牌时被盖住的牌数不出头像，只有多一档会改变姿态 / 全力値 / 出牌次数 / 抽牌 / 元気 等、且不在循环状态（手里有核心 4 张）时，点开读一次说明定档位，本回合沿用 |
| `VEIL_PILE_ENGINE=1` | VEIL 牌堆记账换成三层模块 `hiro.brain.piles`（默认关）：效果层按主数据登记每手应抽几张（牌 / 饮料 / 回忆能力）；牌堆层把「应抽却到回合结束都没出现」的记成暗抽，洗牌时对上；对账层遇到「账面山札有剩却抽到捨札的牌」只倒推山札已空、剩下的记存疑，存疑的牌洗回山札后又抽空一轮都没出现就判出局，不再整副位置不明。出矛盾时点右下角牌库一览按分组读一次，以画面为准重置。回放核对：`tools/audit/pile_replay.py` |
| `HIRO_VEIL_STRICT_SHUFFLE=1` | VEIL 牌堆记账按洗牌规则收紧（默认关）：抽牌数大于牌堆剩余时，牌堆里的牌必须全被抽到才认洗牌，否则整副记成位置不明 |

固定编成时，租借格按 `--main-attr` 挑主属性、等级最高的一张；挑不到就停下（不开局）。

别的设备上有进行中的培育时，`run_hif new` 会停下（退出码 3）。只有使用者明确同意放弃那一份进度，才加 `--allow-discard`。

运行中：

- 每一步写进 `traces/<run>/steps.jsonl`（截图在同目录）。
- 看门狗：画面和局面 3 分钟没有进展就停机报警（`loop/repeat_guard.py`），不会自动重启。
- 外部暂停：设了 `HIRO_PAUSE_FLAG=<文件路径>` 时，那个文件存在就暂停对局，删掉就继续（录屏中断时用）。
- 日志 `reports/live/<trace>.log`，每局报告 `reports/runs/<trace>.json`（含 LLM 调用次数、输入 / 缓存 / 输出 token、费用）。
- 选拔进程非 0 退出时 `play_game` 不会自动续跑本战：先看日志，确认没问题再 `run_hif resume`。

## 五、出问题怎么查

| 现象 | 先看 |
|---|---|
| 停机报警 | 日志最后几行 + `traces/<run>/` 里停机前的截图；`python -m tools.audit.blunders <run>` |
| 读数不对（分数、元気、体力…） | `docs/assets.md` 的退路表；对应识别模块的测试（`tests/test_*_read.py`） |
| 决策看起来不对 | 复盘页：`python tools/viewer/build_exam_view.py <run>`，逐步看候选、状态、理由 |
| 页面认不出 | `hiro/vision/quick.py`、`hiro/vision/pages.py`；未知页面会交给 LLM 兜底（`loop/rescue.py`） |

改代码后：`PYTHONPATH=src pytest -q`。没有截图样例时，读截图的用例会自动跳过；有自己的截图可以设 `HIRO_FIXTURES`。
从自己的流程截图笔记 / trace 生成部分样例：`PYTHONPATH=src .venv/bin/python tools/fixtures/build_local.py --walkthrough <笔记.md> --traces traces`（写到被忽略的 `tests/fixtures/`，缺哪张只跳过哪条用例）。

## 六、改策略的规矩

- 社区攻略是最高依据（`docs/hif-playbook.md`，每条写来源）。自己的实机记录只当状态数据，不当打法依据。
- 改变实机行为的改动放在开关后面、默认关，用回放 / 复盘验证后再打开。
- 每局复盘里发现的错误，写成合成用例放进 `examples/cases/`，防止回归。
