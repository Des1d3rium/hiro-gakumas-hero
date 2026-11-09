# 固定真实数据快照

来源：[vertesan/gakumasu-diff 的固定提交](https://github.com/vertesan/gakumasu-diff/tree/c67b88ebfdf0a1654bd30facc746c6bb41f7bf0a)，提交时间 2026-10-15 05:02:50 UTC。这是社区提取的 master 数据；不声称官方提供、已与本机客户端逐表对齐，或所有隐藏/未来条目均已上线。

当前内容：286 张表、162,211 条记录、468 个卡牌 ID、1,788 个强化版本。饮料、定制选项、成长变换、支援、角色、考试、NPC、评分配置和条件等表全部保留。

## 文件结构

- `master/current.json`：显式指定当前快照及数据库、manifest 的 SHA256。
- `master/<完整提交>/raw/*.yaml.gz`：原始 YAML 压缩副本；解压后与 Git blob 完全一致。
- `master/<完整提交>/manifest.json`：原 URL、提交、Git blob SHA1、原文 SHA256 与压缩件 SHA256。
- `master/<完整提交>/catalog.sqlite`：运行时查询索引。每条原始记录以 JSON+zlib 保存，卡牌另有名称/ID/强化等级索引。
- `master/<完整提交>/build.json`：行数、构建版本、时间和解析修正审计。
- `community/hif-wiki-tables.json`：社区 wiki 原始表格单元格，包含合并行列信息。
- `schedules/hif.json`：H.I.F 完整 20＋9 步日历、可选行动、训练 master 引用与数据来源。

卡牌主键为 `(id, upgradeCount)`；`ProduceCard.order` 是图鉴显示顺序，不能拿来当运行时牌堆固定排序键。定制表同一 ID 可能对应多个 `customizeCount`；评分表同一 ID 对应多个属性节点。底层不丢弃重复 ID，单行查询遇到歧义会拒绝，使用 `Catalog.by_id()` 获取完整组。

原始 YAML 有两处格式问题：Localization 的一个块缩进，以及 Rule 中 12 个 YAML 不接受的垂直制表字符。构建仅对固定原文做可审计解析处理；Rule 的控制字符在解析后逐个还原。原文始终保存不变，来源验证仍按原文字节和 Git blob 校验。

## 离线查询

在项目根目录执行，运行只需 Python 标准库：

```bash
python3 -m gakumas_sim master stats
python3 -m gakumas_sim master verify
python3 -m gakumas_sim master search p_card-02 --limit 20
python3 -m gakumas_sim master card p_card-00-act-0_001 --upgrade 1
python3 -m gakumas_sim master modes
python3 -m gakumas_sim master schedule --mode produce-007
python3 -m gakumas_sim master schedule --mode produce-008
python3 -m gakumas_sim master schedule --mode produce-007 --position 7 --idol-card i_card-shro-3-000
python3 -m gakumas_sim master coverage > /tmp/gakumas-coverage.json
```

`card` 同时输出原始卡牌、直接效果、条件、所有定制等级和成长变换引用，以及可执行定义或具体阻断原因。加 `--raw` 保留完整原始描述。`--output <路径>` 可用于卡牌、搜索、日程及单局运行。

## 接入程度

**收录数据完整，原生执行覆盖仍是子集。** 当前 1,788 个版本全部可查询，其中 397 个版本通过有限效果适配。所有 392 行定制配置已收录，部分费用、开局手牌、数值和效果替换已执行；其他类型、覆盖语义和冲突的多级倍率继续阻断。覆盖扫描只报告首个阻断原因，不代表只缺少这一处行为。

已接入固定基础得分、多段得分、护盾、专注、干劲、好印象、抽牌、再动、额外回合、固定恢复、护盾转分与消耗、单字段门槛与条件快照、部分状态费用、永久增益、基础体力增减状态，以及有限的回合开始效果与预约。复杂条件、状态/姿态、触发器、其他费用、搜牌/生成牌仍明确阻断。

执行入口会根据卡牌 ID、强化等级、定制配置及固定快照重新解析并核对定义。擅改卡牌系数而保留 `master-derived` 标签会被拒绝。沙盒使用中性回合倍率和无道具/支援触发环境，不能声称是原生完整考试或 H.I.F 通关率。

## 日程与动态输入

逐日行动来自[社区 H.I.F 观察表](https://seesaawiki.jp/gakumasu/d/H.I.F)，不是把动画用的 `ProduceStepTransition` 当作日程。与 master 的步数交叉检查：选拔 20 步；本战 9 步，对应 6 天准备和同一考试日的 R1、间隔、R2。

48 条 `ProduceStepOpenLesson` 数值全部关联到 8 个训练位置。训练基础主/副属性、体力及星星来自 master，基础 P 点来自社区收益表。课程的基础参数/费用、逐日行动及考试日有各自来源。考试还可关联偶像对应难度、真实回合数、完整评分节点、NPC 和 gimmick 引用。

SP 是否出现、各选项的副属性、候选卡/饮料、支援/道具触发、亲爱度与星星加成等依赖单局状态。`HIFSchedule.training_scene()` 要求显式提供 SP 和副属性，当前只执行明确的中性基础收益；带增益时拒绝推测未经移植的取整与触发。完整日历可用不等于所有日程动作已能无输入自动结算。

## 验证与重建

```bash
python3 -m gakumas_sim validate
python3 -m gakumas_sim master benchmark --seeds 100
```

真实数据批次包含三个由真实卡牌构成的沙盒、全部准入版本逐卡烟测、完整来源校验、7,288 处引用检查、29 步日历和 8 次训练策略选择；输出独立目录、逐版本覆盖、逐局日志和回放；落后简单对照的种子自动保存双方反例回放。

从已保存的原始快照重建需构建依赖 `PyYAML==6.0.3`，无需重新联网：

```bash
python3 scripts/build-master.py
python3 scripts/build-hif-calendar.py
```

网络下载脚本为 `scripts/fetch-master.mjs`，它校验每个 Git blob 后保存压缩原文。默认固定到上述提交，不自动追随 main；升级快照应新建提交目录，并重跑来源、引用、卡牌覆盖及日历检查。
