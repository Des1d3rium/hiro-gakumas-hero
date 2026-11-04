# hiro-gakumas-hero · 数字小广

让大模型自己玩《学园偶像大师》（学マス）的 H.I.F 模式：从截图识别、状态推算、决策，到点击执行和逐回合复盘，
全部自动化。角色是 Campus 模式的篠澤広。

> 非官方的个人研究项目，与游戏的开发商和运营方无关。仓库**不含任何游戏素材**（卡面、图标、字体、界面图、主数据原文件），
> 素材由使用者从公开数据和自己的客户端准备。

## 成绩

截至 2026-11-04，H.I.F 本战（两轮合计第 1 名）共夺冠 5 次。培育和考试的每一步都是自动完成的（个别局夺冠后的 LIVE 开始页 / 结算由人点过一次，已修）：

| 局 | 形式 | 两轮合计 | R1 | R2 |
|---|---|---|---|---|
| 20 | 只打本战 | 1,108,753 | 678,478 | 430,275 |
| 21 | **整局**（选拔三场全满星 → 本战） | 1,200,550 | 541,016 | 659,534 |
| 22 | 只打本战 | 1,228,979 | 620,481 | 608,498 |
| 31 | 只打本战 | **1,303,847** | 538,425 | 765,422 |
| 32 | 只打本战 | 1,135,657 | 549,384 | 586,273 |

对手第一名（十王星南）两轮合计稳定在约 108 万。上面的成绩用的是作者的完整系统（含不开源的模拟器和预演专家，见下文）；
只用本仓库的默认实现也能打完一局，但考试决策没有那么强。

## 它是怎么工作的

```
                       ┌───────────── 策略层 hiro.brain / hiro.defaults.advisor ─────────────┐
                       │ 社区攻略先验（docs/hif-playbook.md）→ 规则链 → LLM 兜底             │
                       └──────────────▲────────────────────────────────┬──────────────────┘
                                      │ 状态（契约）                    │ 决策（契约）
截图 ─► 识别层 hiro.vision ─► Observation ─► reduce（纯函数，可重放）─► 状态 hiro.state
           ▲ 素材：插件 AssetSource                              │
设备 ◄── 操作层 hiro.device / exec / loop / produce ◄── 执行器（点击、等稳、补点、弹窗、恢复、安全拦截）
                                      │
                 trace（每一步：截图 + 观测 + 状态 + 决策 + 执行结果）
                                      ▼
                 复盘页 tools/viewer · 低级失误审计 tools/audit · 离线回放
```

- **三层**：识别层（看）、策略层（想）、操作层（动）。层与层之间只交换数据契约（`hiro.contracts`），读不到的字段是 `None`，不猜。
- **状态只由纯函数修改**：同样的观测序列永远得到同样的状态，每一局都能离线重放、逐步比对。
- **记账 + 互验**：每个动作先推算下一步应该读到什么，再和识别读数对照；明显不合理的读数（掉位、越界、倒退）丢弃重读。
- **三个插件**（`hiro/plugins.py`），换实现不用改主循环：

  | 插件 | 回答什么 | 本仓库的默认实现 |
  |---|---|---|
  | `asset_source` | 素材和主数据从哪来 | KAA 公开数据 + 使用者自建素材 |
  | `exam_state_source` | 考试局面从哪来 | 画面识别 + 状态机记账 |
  | `exam_advisor` | 考试每一步怎么打 | 社区规则链 + LLM 兜底 |

- **安全拦截写在代码里**：不用ジュエル、不破棄、不按跨端培育的「開始する」、通信错误不点タイトルへ、LLM 兜底只能点识别出的文字框
  （完整列表见 [AGENTS.md](AGENTS.md)）。
- **每一步可审计**：每局自动生成逐回合复盘页（开局牌库、手牌、候选动作、状态曲线），人可以在任意一步标注「应该怎么做」，
  标注变成回归用例。

### 作者的完整系统里多出来的部分（不在本仓库）

考试存档读取、模拟器、预演专家和价值表不开源，只说明方法：每一步把合法动作放进若干「确定化世界」（只打乱看不到的信息：
牌堆顺序、未公开的回合属性），回合内搜索后续打到考试结束，按配对差比较期望终局分；轮间买卖、饮料时机等资源决策用同一批种子配对模拟。
社区攻略是先验，模拟显著更好（约 2 个标准误）才偏离。它们通过上面的插件接口接入，你也可以写自己的实现。

## 快速开始

只支持 macOS（Apple Silicon）+ PlayCover 运行的游戏客户端。

```bash
git clone --recurse-submodules https://github.com/matto49/hiro-gakumas-hero
cd hiro-gakumas-hero
uv venv -p 3.10 && uv pip install -e ".[dev,play]"     # play 组装 KAA（vendor/kaa 子模块）
PYTHONPATH=src .venv/bin/python -m pytest -q            # 没有截图样例时，读截图的用例会跳过
```

1. **素材**：KAA 首次运行会下载公开数据到 `resources/game_data/`。打一两场考试后跑一次
   `tools/assets/capture_icons.py`，补齐考试效果名（やる気、好印象…）和 P 道具名。详见 [docs/assets.md](docs/assets.md)。
2. **打一局**（游戏停在首页，已编好支援卡 / 回忆编成）：

   ```bash
   HIRO_SUPPORT_SET=<支援卡编成号> HIRO_MEMORY_SET=<回忆编成号> \
     python tools/play_game.py --idol <偶像卡 skin id> --main-attr <Vo|Da|Vi>
   ```

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

| 读数 | 默认（只有 KAA 公开数据） |
|---|---|
| 判页、HUD（剩余回合 / 元気 / 体力）、考试分数、名次、手牌、P 饮料、主数据、日程 | 可用 |
| 考试效果名（やる気、好印象…）、P 道具名 | 跑一次 `capture_icons` 后可用 |
| 开始确认页的支援卡、支援卡效果、考试要求 / 倍率 / 星级基准 | 不可用（策略层走规则退路） |

完整表和退路说明见 [docs/assets.md](docs/assets.md)。

## 目录

| 路径 | 内容 |
|---|---|
| `src/hiro/contracts/` | 数据契约（dataclass + JSON schema） |
| `src/hiro/vision/`、`perception/` | 识别层 |
| `src/hiro/state/`、`loop/reduce.py` | 状态、记账、不变量 |
| `src/hiro/brain/`、`defaults/advisor.py` | 策略层：规则、社区先验、LLM 适配（`claude -p`、OpenAI 兼容） |
| `src/hiro/exec/`、`loop/`、`produce/`、`device.py` | 操作层：执行器、主循环、页面处理、恢复、安全拦截 |
| `src/hiro/plugins.py`、`defaults/` | 插件接口与默认实现 |
| `tools/play_game.py`、`src/hiro/run_hif.py` | 入口 |
| `tools/viewer/`、`tools/audit/` | 复盘页、低级失误审计、过场耗时 |
| `tools/assets/capture_icons.py` | 从自己的截图补齐图标素材 |
| `docs/` | 契约、状态机、素材与退路、H.I.F 社区打法 |
| `vendor/kaa` | KAA（kotones-auto-assistant），子模块，固定版本 |

## 致谢与许可

- 操作层与识别层建立在 [KAA（kotones-auto-assistant）](https://github.com/XcantloadX/kotones-auto-assistant)
  之上，KAA 以 GPL-3.0 发布；本仓库随之采用 **GPL-3.0-or-later**。从 KAA 派生的文件在文件头注明。
- H.I.F 打法整理自社区攻略（seesaawiki、wikiwiki 等），出处写在 `docs/hif-playbook.md` 和 `hiro/brain/hif_rules.py`。
- 《学園アイドルマスター》及相关名称、图像的权利属于其权利人。本仓库不分发游戏素材；请遵守游戏的用户协议，
  不要把相关技术用于破坏公平性或损害他人利益的用途。
