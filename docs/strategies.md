# 策略档案：使用者可以参考、替换的策略

脚本里「选什么」的部分都在档案里，不在代码里。改档案不用改代码，下一局生效。

| 档案 | 管什么 | 随仓库的缺省 | 你的覆盖放哪 |
|---|---|---|---|
| 回忆档案 | 每份回忆一套：1 客制化、2 换卡（换入 / 换出 / 差し入れ技能卡）、3 饮料、4 结算回忆优先级 | `src/hiro/strategies/data/memory-defaults.json` | `conf/strategies/memories/<回忆ID>.json` |
| 偶像档案 | 每个偶像一套考试策略：引擎、核心牌、出牌顺序、停手阈值、考试饮料、全力値阈值、姿态限定牌、开关默认值、能力位 | `src/hiro/strategies/data/idols/<偶像 skin id>.json` | `conf/strategies/idols/<偶像 skin id>.json` |
| 按钮注册表 | 固定点击坐标（720x1280 逻辑坐标） | `src/hiro/ui/buttons.json` | `conf/ui/buttons.json` |

`conf/` 被 `.gitignore` 挡住，你的档案不会进仓库。示例在 `examples/strategies/`。`HIRO_STRATEGIES` 可换 `conf/strategies` 目录。

## 回忆档案

回忆 ID 就是批次首检的 `--memory`，也是回忆栏文件 `reports/memories/<回忆ID>.json` 的名字。

```json
{
  "schema": "memory-profile/1",
  "memory": "veil-senbatsu",
  "idol": "i_card-skin-hmsz-3-016",
  "customize": [{"card": {"id": "p_card-03-men-2_080", "upgrade": 1}, "item": "強気追加"}],
  "change": {
    "in": {"rule": "once", "column": 3},
    "out": [{"id": "p_card-03-men-1_039"}],
    "out_swapped": true, "out_duplicate": true, "fp_balance": true,
    "gift_card": {"prefer": [], "avoid": [], "fallback": "once", "column": 3}
  },
  "drinks": {"high_value": ["pdrink_00-2-008"], "priority": ["センブリソーダ"], "fallback": "rightmost"},
  "memory_regen": [{"name": "エンターテイナー+：パラメータ+12", "card": "エンターテイナー", "need": ["パラメータ+12"], "forbid": []}]
}
```

- **只写要改的项**。没写的沿用上一层，合并顺序：代码缺省 → 旧 `conf/batch-rules.json` → 偶像档案的 `memory_defaults` → 回忆档案 → 批次 `init --set`。字典逐项合并，列表整个替换。
- **卡和饮料写 ID 或名字都行**。ID 不怕「ハッピー♪」这类符号，显示名由 `game.db` 查：
  - 卡：`{"id": "p_card-…", "upgrade": 1}`；`upgrade` 省略 = 不分强化（换出、回忆优先级按基名匹配）。
  - 饮料：`"pdrink_…"` 或 `{"id": "pdrink_…"}`。
  - 名字解析不唯一或查不到时停下，不猜。
- 各项的含义、取值范围见 schema：`src/hiro/strategies/schemas/memory-profile.schema.json`（前端也按它生成表单）。

| 项 | 作用 | 原来在 |
|---|---|---|
| `customize[]` | 轮间按顺序客制化（自动模式）。`need_maxed`：同名多张时只选已把这一项做满的那张 | `interval.customize` |
| `change.in` | 授業换卡阶段 1：`once` = 一次性卡优先，第 `column` 列优先；`hold` = 停下 | `prep.change_in` / `change_column` |
| `change.out` | 阶段 2 先换出这些（不分强化），再换出本局换进来的（`out_swapped`）、同名重复卡里没客制化的（`out_duplicate`）。客制化过的卡一律不换 | `prep.change_out*` |
| `change.fp_balance` | 按牌组全力値来源总量调候选优先级（偶像档案有 `fp_balance` 能力位时才生效） | `prep.veil_fp_balance` |
| `change.gift_card` | 差し入れ选技能卡：`prefer` 顺序选第一张命中，`avoid` 不选，没命中按 `fallback` | `prep.card_*` |
| `drinks.high_value` | `shop-auto` 只买这些 | `shop.high_value`、`batch_shop.HIGH_VALUE` |
| `drinks.priority` / `fallback` | 差し入れ选饮料的优先顺序；都不在表里 `rightmost` = 选最右，`hold` = 停下 | `prep.drink_*`、`batch_prep.DRINK_PRIORITY` |
| `memory_regen[]` | 结算回忆按顺序挑第一条命中的版本：卡名（强化版）+ 效果里必须有 `need`、不能有 `forbid` | `batch_memory.PRIORITY` |

## 偶像档案

```json
{
  "schema": "idol-profile/1",
  "idol": "i_card-skin-hmsz-3-016",
  "engine": "cycle",
  "capabilities": {"needs_ledger": true, "fp_balance": true, "burst_auto_route": true},
  "cards": {"entertainer": "エンターテイナー+", "drive": "オーバードライブ+", "...": "..."},
  "core": ["entertainer", "drive", "teach", "rebound"],
  "orders": {"2": ["drive", "rebound", "teach", "entertainer"], "1": ["drive", "teach", "rebound", "entertainer"]},
  "limits": {"r1": 700000, "r2": 1400000},
  "flags": {"default_on": ["HP_GUARD", "..."]}
}
```

- `engine`：`cycle` = 清牌 → 过渡 → 循环的五卡循环引擎（`hiro.brain.veil`）；`default` = 通用规则链 + LLM。没有档案的偶像走 `default`。
- `capabilities`（能力位）：代码里不再写 `idol == VEIL_IDOL`，改问能力位。
  - `needs_ledger`：要整副牌组账本（`deck-base` / `memory-save` 校验牌组）。
  - `fp_balance`：换卡 / 差し入れ按全力値平衡。
  - `burst_auto_route`：`burst` 自带停点路由。
  - 批次 `exam` 和常驻 `watch` 要求 `engine == "cycle"`。
- 覆盖时只写要改的项，例如只调停手阈值：
  ```json
  {"idol": "i_card-skin-hmsz-3-016", "limits": {"r1": 800000}}
  ```
- 开关：`flags.default_on` 是默认开的那组；环境变量 `VEIL_<名>=0/1` 仍可临时改一局。
- 换牌组时，先确认档案能表达这副牌组（核心牌、顺序、姿态限定牌、留后牌）。表达不了的机制，先在 schema 里加声明，再给引擎加通用实现，不要在引擎里写牌名。
- 现在的局限：cycle 引擎导入时读一次档案，用的是缺省偶像（VEIL）那份；第二个 cycle 偶像要先把引擎改成按批次偶像取档案。

## 批次怎么用档案

- 首检（`preflight`）按首检的偶像和回忆合并一次档案，快照进批次文件 `profile`，带各层文件的 sha256。每局开局（`begin` / `settle` 开下一局）重新快照。前端或你改了档案，**下一局**生效，正在打的这局不变。
- 每局结算记录带 `profile_sha`，考试 trace 的 `run.json` 带 `profile_sha` 和偶像档案摘要。审计时可以按「哪副牌组、哪套策略」分组比较。
- 档案不合法时，`preflight` / `begin` 在碰游戏前退出，退出码 2。
- 旧批次文件没有快照时，每次现场合并（和原来每次读 `batch-rules.json` 一样）。

命令（都只读，不碰游戏）：

```bash
.venv/bin/python tools/batch_hif.py --batch <ID> profile show            # 本批次正在用的档案（代理以它为准）
.venv/bin/python tools/batch_hif.py --batch <ID> profile validate        # 只校验
.venv/bin/python tools/batch_hif.py --batch <ID> profile migrate --memory <回忆ID> --ids --write   # 旧规则 → 回忆档案
.venv/bin/python tools/batch_hif.py --batch <ID> init 10 --set change.in.column=2   # 批次覆盖一项
```

## 给前端的本机服务

```bash
PYTHONPATH=src .venv/bin/python -m hiro.ui.serve --port 8765   # 只监听 127.0.0.1，不碰游戏
```

| 接口 | 返回 |
|---|---|
| `GET /schemas/<memory-profile\|idol-profile\|buttons>` | JSON Schema（生成表单） |
| `GET /cards?q=` | 技能卡：id、名字、强化、牌面文字、是不是一次性 |
| `GET /drinks` | 饮料：id、名字 |
| `GET /customize/<card_id>` | 这张卡的客制化菜单（`ProduceCardCustomize`） |
| `GET /idols` | 偶像（有档案的标出引擎） |
| `GET /memories` | 回忆栏（`reports/memories/`）和已有的回忆档案 |
| `GET /profiles/memory/<回忆ID>`、`GET /profiles/idol/<偶像>` | 你的档案原文 + 合并后的结果 |
| `PUT /profiles/memory/<回忆ID>`、`PUT /profiles/idol/<偶像>` | 按 schema 校验后写进 `conf/strategies/`，不合法返回 400 |
| `GET /batches/<批次ID>/profile` | 这个批次当前快照的版本（界面上显示「当前批次用的是哪个版本」） |

## 验收工具

- `tools/audit/veil_replay.py <trace>…`：cycle 引擎逐步回放，重构前后各跑一次 diff，每步动作和理由应完全相同。
- `tools/replay_batch_driver.py --cases examples/cases/batch-driver.json`：自动模式停点 → 命令回放。
