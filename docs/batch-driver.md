# watch 自动模式（G4 / G5，默认关）

目的：少叫醒代理。watch 照旧推进画面、判停点；自动模式只在 watch **已确认的常规停点**上替代理下命令，命令就是代理平时敲的那几条
`batch_hif.py --batch <ID> …`（子进程执行），所以锁、记账、trust 自检、危险字样规则全都照旧。

**只有一套页面判断**：停点理由来自 watch 的 `hold_reason` / prep / debug 再挑戦 / `result_next`。自动模式不另认画面，
原来 `drive` 命令自己的 `observe` / `classify` / 再挑戦读框已删掉。

## 打开

```bash
batch_hif.py --batch <ID> watch --auto        # 只这次打开
```

或在 `conf/batch-rules.json` 里设 `"driver": {"enabled": true}`。没打开时 watch 行为和以前完全一样。

| 规则 | 默认 | 说明 |
|---|---|---|
| `driver.enabled` | `false` | 自动模式总开关（`watch --auto` 临时打开） |
| `driver.max_refresh` | `3` | 轮间 `shop-auto` 最多刷新几次 |
| `interval.recover_enabled` | `true` | 自动模式下轮间先回血（只有自动模式用） |
| `interval.target_hp` | `10` | VEIL 回到这么多体力（使用者 2026-11-08；和考试体力闸保底线 `HIRO_VEIL_HP_FLOOR` 一致） |
| `interval.reserve_p` | `0` | 回血 / 购物后至少留下的 P |
| `retry.on_defeat` | `hold` | `auto`：自动模式下普通敗退也点再挑戦 |
| `retry.tickets` | `false` | `auto` 时是否用再挑戦チケット（TODO「再挑戦也赢不了时不用券」没定，默认交给代理） |
| `prep.auto_drink_replace` | `false` | 差し入れ 饮料栏满、候选有更好的高价值饮料时先丢后收 |

首局开局、首检、第一份 `memory-load` 仍由代理做（同 `/hif-main`）。

## 停点 → 命令

| watch 停点 | 条件 | 命令 |
|---|---|---|
| 考试中 | 本局账本已建 | `exam <局> --round N`（N：结束轮间前都是 1，之后都是 2；`run_round` 再按画面核对） |
| 轮间客制化 | 还没结束过轮间 | 依次 `interval-recover` → `customize`（按 `interval.customize`）→ `shop-auto` → `interval-end --guarded` |
| 结算回忆 / 结算回忆生成 | 自动模式考完过 R2 | `settle <局> --result …`；settle 自己开了下一局（`open.auto_start=repeat`）且不是停在开局 / 首页 / 弹窗类页面时，再补 `playing`、`memory-load`（同一份回忆） |
| 敗退「再挑戦確認」 | `retry.on_defeat=auto` | watch 里点「再挑戦」：有「本日あと N 回無料」（N≥1）直接点；否则要 `retry.tickets=true`；每轮最多 3 次；次数角标「あと0回」或用满就停下，**不点「プロデュース終了」** |
| 其他（换卡阶段 2、确认框、首页、选择页…） | — | 照旧打印 `hold` 交给代理 |

进度不靠结果页名次推（TODO：结果页 0 分时 score / rank 读不出）：看到轮间页本身就是 R1 晋级的证据；`exam` 的分数名次只写进结算说明。

## 安全

- **意图先落盘**：每条命令动手前写 `active.driver.pending`，命令返回 `ok: true`（且没有 `ledger_todo`、牌组对账没有 diff、结算确实记进批次文件）才清。
- **花钱的命令**（`interval-recover`、`customize`、`shop-auto`）动手前重读一帧整屏 OCR：停点还是同一类、没有危险字样才做。**每下点击之前不再整屏 OCR**，只查暂停旗标（读一个文件）。
- **没成功 / 进程中途退出**：
  - 可重做的（`exam` 从考试记忆续跑、`interval-end`、`settle`、`playing`、`memory-load`）在同一停点再做一次，连续 2 次不行就停；
  - 花钱的不重做，记 `driver.error`，watch 照旧报 `hold` 交给代理。代理核对实机和账本后：
    ```bash
    batch_hif.py --batch <ID> auto-resolve <局> applied --note "画面已买到 …，账本已补"
    batch_hif.py --batch <ID> auto-resolve <局> not-applied --note "客制化页没进去，账本无变动"
    ```
    `applied` 按成功记进度，`not-applied` 当没发生；两种都留记录（`driver.resolved`），不用手改批次 JSON。
- **prep 换饮料**：实读和账本都按 `L.norm` 统一写法再比；替换后实读对不上时留 `pending_drink_replace`，watch 停在原地报 `hold`（「账本待补：…」），不接着往下点。
- 不在批次锁里跑命令：watch 在锁里决定、落盘，锁外跑子进程（命令自己要拿锁），跑完回锁里核对。命令跑的时候 watch 不点屏幕。

## 和原 watch 一起改的

- 敗退框「再挑戦」可不可点：原来按按钮饱和度判灰，但可点的「再挑戦」本来就是白底灰字（`walkthrough/17-retry-confirm.png`
  饱和度 0），debug 敗退会一次都不重试直接点「プロデュース終了」。改成看次数角标「あと0回」。
- 不带有效免费次数的敗退框存一帧到 `traces/retry-dialogs/`（不进仓库），用来校准用券时那一行的读法。

## 离线验证

```bash
python tools/replay_batch_driver.py --cases examples/cases/batch-driver.json
PYTHONPATH=src:vendor/kaa pytest -q tests/test_batch_driver.py
```

回放按停点理由序列走 `next_step` / `begin` / `finish`，覆盖整局、购买途中退出、考试途中退出、不认识的停点。
**没有在实机跑过**：自动开下一局后补 `playing` 的判断、轮间回血的实际扣 P、券的确认框文字，都要第一次实机对照着看。
