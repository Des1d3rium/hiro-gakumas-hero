# hiro-gakumas-hero · 数字小广

让大模型自己玩《学园偶像大师》（学マス）的 H.I.F 模式：截图识别、状态推算、决策、点击执行、逐回合复盘，全部自动完成。
项目从 Campus 模式的篠澤広起步，现在主力路线是 [VEIL] 秦谷美鈴的本战批量周回。

> 非官方的个人研究项目，与游戏的开发商和运营方无关。仓库的 `assets/pack/` 带了一套识别用的游戏素材（卡面、图标、字体、
> 界面图、布局），**权利属于游戏的权利人，不属于本仓库的 GPL**，仅供识别游戏画面的研究用途，详见 [assets/pack/README.md](assets/pack/README.md)；
> 主数据不在仓库里，由 `setup.sh` 从公开来源下载。

## 成绩

H.I.F 本战两轮，R2 第 1 名即優勝。

### [VEIL] 秦谷美鈴 · 本战批量周回（2026-11-07 起）

路线：`tools/batch_hif.py`，`watch --auto` 常驻推进画面，考试由 VEIL 插件按账本出牌，不读考试存档。
开局、换卡、客制化、购买、结算回忆都有固定流程，对话代理（Claude Code / Codex）只在停点上接手。

2026-11-09 用回忆 `veil-deck2` 连打 18 局，**18 局全部優勝**（其中 1 局 R2 首挑敗退、再挑戦一次拿下）：

| | 分数 |
|---|---|
| R2 中位数 | 1,460,059 |
| R2 最高 | 1,567,707 |
| 两轮合计最高 | 2,573,821（R1 1,062,438 / R2 1,511,383） |
| 单局用时 | 约 23–28 分钟（从首页开局到 R2 结算） |

换更早的回忆 `veil-senbatsu` 时单轮最高打到 R2 1,752,153（2026-11-07）。

还没做到的：按 [docs/phase1-standard.md](docs/phase1-standard.md) 的口径——同一提交、同一组开关连续三局「零人工介入、两轮第 1、复盘全对」——
目前还没达标。18 局里多数局有过一次识别或记账错误（逐局修进代码，大多放在默认关的开关后面），或代理手动处理过一次停点。

### 篠澤広 · 单局路线（2026-11-04 前）

路线：`tools/play_game.py` / `run_hif`，从选拔打到本战。共夺冠 6 次，其中两次是从选拔打起的整局：

| 局 | 形式 | 两轮合计 | R1 | R2 |
|---|---|---|---|---|
| 20 | 只打本战 | 1,108,753 | 678,478 | 430,275 |
| 21 | **整局**（选拔三场全满星 → 本战） | 1,200,550 | 541,016 | 659,534 |
| 22 | 只打本战 | 1,228,979 | 620,481 | 608,498 |
| 31 | 只打本战 | **1,303,847** | 538,425 | 765,422 |
| 32 | 只打本战 | 1,135,657 | 549,384 | 586,273 |
| 33 | **整局** | 1,094,909 | 450,164 | 644,745 |

对手第一名（十王星南）两轮合计稳定在约 108 万。这 6 局用的是作者的完整系统（考试局面来自解密的考试存档，不开源）。

Windows + MuMu 6.8（RapidOCR + Codex）上也实机打过这条路线：2026-11-07–09 共 22 局，3 次優勝。

## 它是怎么工作的

```
                       ┌───────────── 策略层 hiro.brain / hiro.defaults.advisor ─────────────┐
                       │ 社区攻略先验（docs/hif-playbook.md）→ 规则链 / 循环规划 → LLM 兜底   │
                       └──────────────▲────────────────────────────────┬──────────────────┘
                                      │ 状态（契约）                    │ 决策（契约）
截图 ─► 识别层 hiro.vision ─► Observation ─► reduce（纯函数，可重放）─► 状态 hiro.state
           ▲ 素材：插件 AssetSource                              │
设备 ◄── 操作层 hiro.device / exec / loop / produce ◄── 执行器（点击、等稳、落地核对、弹窗、恢复、安全拦截）
                                      │
                 trace（每一步：截图 + 观测 + 状态 + 决策 + 执行结果）
                                      ▼
                 复盘页 tools/viewer · 审计与离线回放 tools/audit
```

- **三层**：识别层（看）、策略层（想）、操作层（动）。层与层之间只交换数据契约（`hiro.contracts`），读不到的字段是 `None`，不猜。
- **状态只由纯函数修改**：同样的观测序列永远得到同样的状态，每一局都能离线重放、逐步比对。
- **记账 + 互验**：每个动作先推算下一步应该读到什么，再和识别读数对照；对不上的读数丢弃重读，或者以画面为准重置账本。
- **三个插件**（`hiro/plugins.py`），换实现不用改主循环：

  | 插件 | 回答什么 | 本仓库的实现 |
  |---|---|---|
  | `asset_source` | 素材和主数据从哪来 | KAA 公开数据 + 随仓库素材包 `assets/pack/` + 使用者自建素材 |
  | `exam_state_source` | 考试局面从哪来 | 默认：画面识别 + 状态机记账；`SimScreenExamStateSource`：画面 + 模拟器预演；`VeilScreen`：画面 + 全套账本 |
  | `exam_advisor` | 考试每一步怎么打 | 默认：社区规则链 + LLM 兜底；`VeilAdvisor`：循环流派规划 |

- **安全拦截写在代码里**：不用ジュエル、不破棄、不按跨端培育的「開始する」、通信错误不点タイトルへ、LLM 兜底只能点识别出的文字框
  （完整列表见 [AGENTS.md](AGENTS.md)）。
- **改行为先进开关**：会改变实机行为的改动放在开关后面、默认关，先在已有 trace 上离线回放，看它改了哪些步，再上实机。

### VEIL 路线：不读存档也能记住整副牌

`produce/veil_exam.py`（`VeilScreen`）+ `brain/veil.py`、`brain/play/`（`VeilAdvisor`）。考试局面全部从画面和账本推出来：

- **牌组账本**：培育中的每次换卡、客制化、购买都记账；开考时和画面上的牌库一览对一次。
- **牌堆记账**（`brain/piles.py`）：按主数据算每张牌该抽几张，跟踪山札 / 手牌 / 捨札；对不上时打开牌库一览按画面重置。
- **卡牌效果按主数据算**（`brain/card_fx.py`）：姿态、全力値、追加行动、抽牌、使用条件，不靠 OCR 牌面文字。
- **P 道具附魔引擎**（`brain/play/enchants.py`）：持有的 P 道具按主数据装上触发效果，在账本推演里执行。
- **出牌落地核对**（`exec/landing`）：点之前和点之后按卡图认整手牌，判断牌有没有真的打出去。
- **出牌策略**（`brain/play/`）：阶段、循环、资源、规划拆成模块，「循环状态」可以推广到别的偶像。

### 批量周回

`tools/batch_hif.py` 把一局拆成可续跑的命令（`begin` / `open` / `exam` / `settle` …），进度存在 `reports/batches/<批次>.json`。
`watch --auto` 连点过场、在已知停点上自己下命令；前六周的行动、相談购买、饮料取舍、敗退后用免费次数再挑戦、
「データ更新」「日付変更」回标题恢复都由它处理。见 [docs/batch-hif.md](docs/batch-hif.md)、[docs/batch-driver.md](docs/batch-driver.md)。

「选什么」写在策略档案里（回忆档案、偶像档案、按钮坐标），不在代码里，改档案下一局生效，见 [docs/strategies.md](docs/strategies.md)。

### 模拟器（`hiro/sim` + `vendor/gakumas-player`）

引擎 `gakumas_sim` 按社区公开的 master 数据（vertesan/gakumasu-diff 固定提交，setup 时下载构建）实现考试规则，并用实机记录校准。
每一步把合法动作放进若干「确定化世界」（只打乱看不到的信息：牌堆顺序、未公开的回合属性），回合内搜索后续打到考试结束，
按配对差比较期望终局分；轮间买卖、饮料取舍等资源决策用同一批种子配对模拟。社区攻略是先验，模拟显著更好（约 2 个标准误）才偏离。

`hiro.sim.screen_source` 从画面推考试局面：用培育记账的牌组合成开局，按执行的动作跟踪，抽到的牌按画面对齐，对不上就按画面重建
（Windows 实机记录离线重放：97% 以上的步能给出预演）。读解密存档的考试局面和高保真考试引擎不在本仓库。

## 快速开始

| 平台 | 状态 |
|---|---|
| macOS（Apple Silicon）+ [hguandl 的 PlayCover 分支](https://github.com/hguandl/PlayCover) | 主力平台，上面两条路线都在这里打 |
| Windows + MuMu 12（`HIRO_DEVICE=mumu`，截图 / 点击走 adb） | 实机打过单局路线（22 局 3 次優勝） |
| 安卓设备 / 模拟器（ADB）、雷电、Windows DMM 版 | 与 [KAA](https://github.com/XcantloadX/kotones-auto-assistant) 对齐，代码和离线测试都有，没有实机打过 |

macOS 上截图和点击默认走 hguandl 分支里的 MaaTools 服务：在游戏的 PlayCover 设置里打开 `maaTools`（端口 `maaToolsPort`），
不要求窗口在前台，也不动鼠标。用原版 PlayCover 时退回 Quartz 截图，游戏窗口要在前台，点击也更不稳。设备细节见 [AGENTS.md](AGENTS.md)「设备」。

```bash
git clone --recurse-submodules https://github.com/matto49/hiro-gakumas-hero
cd hiro-gakumas-hero
scripts/setup.sh                                        # 装依赖、生成 KAA 文件、下载公开游戏数据、自检（约 4 分钟）
PYTHONPATH=src .venv/bin/python -m pytest -q            # 没有截图样例时，读截图的用例会跳过
```

Windows 用 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1`。

1. **素材**：公开游戏数据由 `setup.sh` 下载，识别用的图标、卡面、字体随仓库（`assets/pack/`），不用再准备。
   客户端更新出新界面、新图标时，用自己的截图跑 `tools/assets/capture_icons.py` 补。详见 [docs/assets.md](docs/assets.md)。
2. **打一局**（游戏停在首页，已编好支援卡 / 回忆编成）：

   ```bash
   HIRO_SUPPORT_SET=<支援卡编成号> HIRO_MEMORY_SET=<回忆编成号> \
     python tools/play_game.py --idol <偶像卡 skin id> --main-attr <Vo|Da|Vi>
   ```

   也可以让程序按社区攻略选支援卡和回忆、生成开局编成方案，开局时自动核对编成，见 [docs/formation-plan.md](docs/formation-plan.md)。
   本战批量周回由对话代理照 [docs/batch-hif.md](docs/batch-hif.md) 操作。

3. **看复盘**：`python tools/viewer/build_exam_view.py <trace>`，打开生成的 HTML。

更完整的说明（环境变量、续跑、出问题怎么查）在 [AGENTS.md](AGENTS.md)。这份文件也是给 AI 看的：把仓库交给一个 AI 会话，
让它读 `AGENTS.md` 和 `skills/`，它就能装环境、开局和排查问题。

没有游戏也可以先看复盘页长什么样：

```bash
python examples/make_demo_trace.py
python tools/viewer/build_exam_view.py demo-run --traces examples/traces --out /tmp/hiro-view
open /tmp/hiro-view/demo-run.html
```

## 默认素材下能读到什么

| 读数 | 默认（KAA 公开数据 + 随仓库素材包） |
|---|---|
| 判页、HUD（剩余回合 / 元気 / 体力）、考试分数、名次、手牌、P 饮料、主数据、日程 | 可用 |
| 考试效果名（やる気、好印象…）、P 道具名、开始确认页的支援卡 | 可用（素材包带图标和支援卡图） |
| 支援卡效果、考试要求 / 倍率 / 星级基准 | 不可用（策略层走规则退路） |

不想用随仓库的素材包时，设 `HIRO_BUNDLED_ASSETS=0`，识别层退回只用 KAA 公开数据，考试效果名等要跑 `capture_icons` 补。
完整表和退路说明见 [docs/assets.md](docs/assets.md)。

## 每一局都能查

- 每一步写进 `traces/<run>/steps.jsonl`（截图在同目录）；每局生成逐回合复盘页（开局牌库、手牌、候选动作、状态曲线），
  人可以在任意一步标注「应该怎么做」，标注变成 `examples/cases/` 里的回归用例。
- `tools/audit/` 在 trace 上离线核对：`trace_trust`（局面能不能信）、`pile_replay`（牌堆有没有位置不明的牌）、
  `pitem_replay`（P 道具计数对不对得上画面）、`landing_replay`（出牌落地判定）、`ledger_check`（账本对主数据）、`blunders`（低级失误）。

## 目录

| 路径 | 内容 |
|---|---|
| `src/hiro/contracts/` | 数据契约（dataclass + JSON schema） |
| `src/hiro/vision/`、`perception/` | 识别层 |
| `src/hiro/state/`、`loop/reduce.py` | 状态、记账、不变量 |
| `src/hiro/brain/`、`defaults/advisor.py` | 策略层：规则、社区先验、VEIL 出牌（`veil.py`、`play/`）、牌堆、卡牌效果、P 道具、LLM 适配 |
| `src/hiro/exec/`、`loop/`、`produce/`、`device.py` | 操作层：执行器、主循环、页面处理、恢复、安全拦截 |
| `src/hiro/sim/` | 模拟器接入与画面局面来源 |
| `src/hiro/strategies/`、`ui/` | 策略档案缺省值、按钮注册表、本机读写服务 |
| `src/hiro/plugins.py`、`defaults/` | 插件接口与默认实现 |
| `tools/play_game.py`、`src/hiro/run_hif.py` | 单局入口 |
| `tools/batch_*.py` | 本战批量周回 |
| `tools/viewer/`、`tools/audit/` | 复盘页、审计、离线回放 |
| `tools/assets/` | 从自己的截图补齐图标、饮料模板 |
| `docs/` | 契约、状态机、素材与退路、H.I.F 社区打法、策略档案、批量流程、交付标准 |
| `skills/` | 给 AI 代理的操作说明（装环境、打一局、补素材、复盘） |
| `vendor/kaa` | KAA（kotones-auto-assistant），子模块，固定版本 |

## 致谢与许可

- 识别层的部分逻辑基于 [KAA（kotones-auto-assistant）](https://github.com/XcantloadX/kotones-auto-assistant) 的识别接口、主数据与界面模板；
  培育的页面流程沿用 KAA 的任务流程，在 `hiro/loop/kaa_patches.py` 里打运行时补丁。KAA 以 GPL-3.0 发布；
  本仓库随之采用 **GPL-3.0-or-later**。从 KAA 派生的文件在文件头注明。
- 截图和点击默认通过 [hguandl 的 PlayCover 分支](https://github.com/hguandl/PlayCover)（GPL-3.0，派生自 PlayCover）里
  [PlayTools](https://github.com/hguandl/PlayTools)（AGPL-3.0）内置的 MaaTools 服务完成：它在游戏进程内开 TCP 服务，进程内截图、注入触摸，
  不要求游戏在前台，也不动鼠标。本仓库只按它的协议做客户端（`hiro/device.py`），不包含它的代码。连不上时退回 kotonebot 的 Quartz 截图
  + `CGEventPostToPid` 投递。致谢 MaaTools 的作者们。
- 考试模拟引擎在 `vendor/gakumas-player`，主数据来自社区公开的 vertesan/gakumasu-diff。
- H.I.F 打法整理自社区攻略（seesaawiki、wikiwiki 等），出处写在 `docs/hif-playbook.md` 和 `hiro/brain/hif_rules.py`。
- 《学園アイドルマスター》及相关名称、图像的权利属于其权利人。`assets/pack/` 里随仓库带的识别素材同样属于权利人，仅供识别研究用途（见 `assets/pack/README.md`）；请遵守游戏的用户协议，
  不要把相关技术用于破坏公平性或损害他人利益的用途。
