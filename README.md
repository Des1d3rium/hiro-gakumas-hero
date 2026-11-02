# 数字小广（digital-hiro）· 开源框架部分

「数字小广」是一个让大模型自己玩《学园偶像大师》H.I.F 模式的实验项目：从截图识别、状态推算、决策，到点击执行和逐回合复盘，全部自动化，目标是在没有人工介入的情况下稳定打完选拔和本战。

这个仓库开源的是**与具体游戏无关的工程框架**，以及我们用来逐步改进系统的复盘工具和设计文档。能直接操作游戏的部分（截图识别实现、点击执行器、存档读取、游戏素材）**不包含在内**，见文末「不包含的内容」。

## 架构

```
截图 ──► 识别（不开源）──► Observation（契约）
                                │
                       reduce(state, obs)：纯函数，防抖 + 来源优先级 + 页面转移校验
                                │
事件 Event ──► apply_event ──► Prediction ──► check(prediction, obs) ──► Check（读错 / 推算缺口 / 一致）
                                │
                           GameState（契约）──► 决策（规则 + 模拟 + LLM 兜底）──► Decision（契约）
                                │                                          │
                           Step（截图 + 观测 + 状态 + 决策 + 执行结果）◄── ExecutionResult（契约）
                                │
                     trace/ 落盘 ──► 离线重放 · 低级失误审计 · 复盘页 · 用户标注 ──► 黄金用例（门禁）
```

核心思路：

1. **数据契约是唯一的接口**（`hiro.contracts`）。识别、状态、决策、执行之间只交换 Observation / GameState / Action / Decision / Preview / ExecutionResult / Step / Event / Prediction / Check，每个对象有 schema、版本号和往返测试。读不到的字段是 `None`，不猜。
2. **状态只由纯函数修改**（`hiro.loop.reduce`）。同样的观测序列永远得到同样的状态，所以每一局都能离线重放、逐步比对。
3. **状态机记账 + 识别互验**（`hiro.loop.events`、`hiro.state.invariants`）。每个动作和事件先推算下一步应该读到什么，再和识别读数对照：明显不合理的读数（掉位、越界、倒退）直接丢弃重读，不交给决策；读数合理但和推算对不上，就标成「待复核」，触发主动查看。
4. **决策与模型无关**（`hiro.brain.llm`）。LLM 只通过一个适配器调用，换模型只改一处配置；非法输出走规则兜底。
5. **每一步可审计**。每局自动生成逐回合复盘页（手牌、候选动作的期望分、饮料、状态曲线），人可以在任意一步标注「应该怎么做」；标注拉回本地后变成黄金用例，用例不通过就不恢复实机。

## 目录

| 路径 | 内容 |
|---|---|
| `src/hiro/contracts/` | 数据契约（dataclass + JSON schema + 往返 / 校验） |
| `src/hiro/loop/` | `reduce`（状态合成）、`transitions`（页面转移图）、`session`（观测 → reduce → 记录 → 校验，截图 / 分类 / 提取可注入）、`replay`（离线重放）、`events` / `ledger`（事件记账与预测）、`report`（每局度量） |
| `src/hiro/state/` | 培育状态结构、不变量检验（读错检测）与回放 |
| `src/hiro/brain/llm.py` | LLM 适配器：`claude -p` CLI、OpenAI 兼容 API、Mock；提示词带版本号 |
| `src/hiro/vision/observe.py` | 把任意识别器的原始输出打包成契约 Observation 的通用接口（适配器注册表） |
| `tools/viewer/` | 逐回合复盘页生成器（单个 HTML，数据内嵌）、标注后端（标准库 HTTP 服务）、发布 / 拉取脚本 |
| `tools/audit/blunders.py` | 低级失误审计：从 trace 和日志里自动统计出牌 / 识别 / 执行 / 流程 / 资源五类失误 |
| `docs/` | 契约、状态机、状态推算与互验方案、LLM 上下文格式 |

## 快速开始

```bash
uv venv -p 3.10 && uv pip install -e ".[dev]"
PYTHONPATH=src .venv/bin/python -m pytest -q tests
```

接入自己的游戏：

1. 实现识别器，用 `hiro.vision.observe.register_adapter(scene, fn)` 注册每个页面的字段转换；
2. 在 `hiro.loop.transitions` 里登记页面和合法转移；
3. 用 `hiro.loop.session.Session(trace, shoot=..., classify=..., extract=...)` 跑「观测 → reduce → 决策 → 执行 → 校验」；
4. 实现游戏专用的预测器 `predict(state, event)`，传给 `apply_event(..., predictor=...)`；没有预测器时只做不变量检验。

复盘页：

```bash
python tools/viewer/build_exam_view.py <trace 基名> --traces traces --out reports/viewer [--api https://你的域名/hiro-view/api/notes]
HIRO_VIEW_HOST=<ssh 主机名> python tools/viewer/publish.py          # rsync 到服务器
HIRO_VIEW_HOST=<ssh 主机名> tools/viewer/deploy_notes.sh             # 部署标注后端（systemd + nginx）
HIRO_VIEW_HOST=<ssh 主机名> python tools/viewer/pull_notes.py --out datasets/annotations
```

## 不包含的内容

为避免分发游戏素材和规避客户端保护，下列部分不在这个仓库里：

- 截图识别的具体实现（页面签名、卡面 / 饮料 / 图标模板、游戏字体比对）以及游戏素材；
- 点击执行器、页面处理逻辑，以及对 KAA（kotones-auto-assistant）的扩展和补丁；
- 本地存档读取、资源解密，以及基于存档的考试模拟器；
- 游戏专用的决策规则、社区知识表和提示词（仓库里的提示词只是示例）；
- 实机 trace、截图、数据集和账号数据。

## 声明

- 本项目是个人研究，与游戏的开发商和运营方无关。请遵守游戏的用户协议，不要把相关技术用于破坏公平性或损害他人利益的用途。
- 许可证：GPL-3.0-or-later。原项目基于 KAA（GPL-3.0）构建，为保持一致，这里也采用 GPL-3.0。
