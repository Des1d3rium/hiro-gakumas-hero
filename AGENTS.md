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
| LLM 兜底只能点 OCR 识别出的文字框或固定空白处；选择页、考试画面不点 | `loop/rescue.py` |
| 再挑戦只用免费次数 / 再挑戦チケット，最多 3 次 | `produce/hif.py` |

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

1. 拉代码和子模块：`git clone --recurse-submodules …`，或已有仓库里 `git submodule update --init`。
2. Python 3.10+：`uv venv && uv pip install -e ".[dev,play]"`（`play` 组装 KAA）。
3. 游戏窗口：PlayCover 里打开游戏，保持竖屏窗口在前台；截图走 MaaTools（KAA 文档有说明）。
4. 素材（见 `docs/assets.md`）：
   - KAA 第一次运行会下载公开数据到 `resources/game_data/`；也可以设 `HIRO_KAA_GAME_DATA` 指到别处。
   - **强烈建议**打一两场考试后跑一次 `tools/assets/capture_icons.py`，补齐考试效果名（やる気、好印象…）和 P 道具名。
     不跑的话这两个字段一直是 `None`，策略层只能用保守规则，分数会明显低。
5. LLM：`HIRO_LLM` 选适配器（`claude-cli:sonnet`、OpenAI 兼容 API 等，见 `hiro/brain/llm.py`）。

## 四、跑一局

> 入口脚本正在从私有仓库迁移（`run_hif` / `play_game`），迁完后这一节补上命令。

跑之前确认：游戏停在首页；没有别的设备上进行中的培育；AP 够或有 APドリンク。

运行中：

- 每一步写进 `traces/<run>/steps.jsonl`（截图在同目录）。
- 看门狗：画面和局面 3 分钟没有进展就停机报警（`loop/repeat_guard.py`），不会自动重启。
- 外部暂停：设了 `HIRO_PAUSE_FLAG=<文件路径>` 时，那个文件存在就暂停对局，删掉就继续（录屏中断时用）。

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
