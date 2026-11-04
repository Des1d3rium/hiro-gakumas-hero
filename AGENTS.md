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

- 不要把游戏素材（卡面、图标、字体、Sprite、主数据原文件）、截图、trace、账号、密钥提交进仓库。`.gitignore` 已挡住
  `resources/`、`tests/fixtures/`、`cache/`、`traces/`。
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
2. 游戏窗口：PlayCover 里打开游戏，保持竖屏窗口在前台；截图走 MaaTools（KAA 文档有说明）。
   macOS 要给运行它的终端 / 程序「屏幕录制」权限；截图报 `could not create image from display` 时先查这个权限和显示器状态。
3. 素材（见 `docs/assets.md`）：**强烈建议**打一两场考试后跑一次 `tools/assets/capture_icons.py`，补齐考试效果名（やる気、好印象…）
   和 P 道具名。不跑的话这两个字段一直是 `None`，策略层只能用保守规则，分数会明显低。
4. 游戏内设置：`PYTHONPATH=src .venv/bin/python tools/check_settings.py` 检查快进 / 跳过演出是否全开。
5. LLM：`HIRO_LLM` 选适配器（`claude-cli:sonnet`、OpenAI 兼容 API 等，见 `hiro/brain/llm.py`）。

## 四、跑一局

跑之前确认：游戏停在首页；没有别的设备上进行中的培育；AP 够或有 APドリンク；游戏里已经编好支援卡和回忆的编成。

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
| `HIRO_SUPPORT_SET` / `HIRO_MEMORY_SET` | **必填**，游戏里的编成号。没设就在碰游戏前退出（码 2）。写 `auto` 等于 KAA「おまかせ」，支援卡会直接确认列表第一张租借卡，只在你明确想这样时用 |
| `HIRO_LLM` | LLM，如 `claude:sonnet`（默认，走 `claude -p`）、`openai:<model>`（OpenAI 兼容）。`--no-llm` 只用规则 |
| `HIRO_KAA_GAME_DATA` | KAA 公开数据目录（含 `game.db`），默认 `resources/game_data` |
| `HIRO_USER_ASSETS` | 自建素材目录（`capture_icons` 的产出），可选 |
| `HIRO_ASSET_SOURCE` / `HIRO_EXAM_STATE_SOURCE` / `HIRO_EXAM_ADVISOR` | 换插件实现（`包.模块:类名`） |
| `HIRO_STRATEGY` | 换培育策略类（KAA `HifGrindStrategy` 子类），默认 `hiro.produce.hif:HiroHifStrategy` |
| `HIRO_PAUSE_FLAG` | 外部暂停旗标文件路径：文件存在就暂停对局 |
| `HIRO_RESCUE_LLM=0` | 关掉认不出画面时的 LLM 看图兜底（默认开） |
| `HIRO_DEBUG_RECOG=1` | 把全部识别互验写进 trace，页面变化都存帧 |

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

## 六、改策略的规矩

- 社区攻略是最高依据（`docs/hif-playbook.md`，每条写来源）。自己的实机记录只当状态数据，不当打法依据。
- 改变实机行为的改动放在开关后面、默认关，用回放 / 复盘验证后再打开。
- 每局复盘里发现的错误，写成合成用例放进 `examples/cases/`，防止回归。
