# `qing-elite-family-background` v0.2 升级计划书

**目标仓库**：`/Users/wahrfreiheit/OMP/qing-elite-family-background/`  
**审查基线**：`v0.1-one-day`，commit `a60274e`  
**审查日期**：2026-09-14  
**建议版本**：`v0.2-evidence-balanced`  

## 1. 总体判断

v0.1 已经完成了一个很有价值的工程 MVP：固定数据 release、保存原始校验值、区分结构化数据与 LLM 抽取、记录调用成本和提示版本、保持 evidence 可回源，并在 P07 发现并修复了一处会改变回归参照组的实现错误。当前 73 项测试全部通过。

但 v0.1 还不能支持“不同官位层级的真实家世结构有何差异”这一核心比较。它目前最可靠的成果是：**不同层级人物的家世资料可见度极不均衡，且现有完整案例是强选择样本**。升级版必须先解决测量语义、人物链接、同源可比性和人工验收，再增加模型复杂度。

建议将现有最终报告标为 `exploratory baseline`，完整保留、不覆盖；v0.2 另建数据、报告和 release 目录。

## 2. 审查发现与优先级

### 2.1 P0：发布前必须修复

| 问题 | 当前证据 | 影响 | v0.2 处理 |
| --- | --- | --- | --- |
| “未载”仍被用于生成 0 | `person_indicators()` 在祖先身份可知、任官字段为空时生成 `ancestor_official_any=0`；三代身份可知且功名/任官字段为空时可生成 `strict_commoner_3g=1` | 祖先“数据库未载任官”被解释成“没有任官”，与 `unknown != 0` 原则不完全一致 | 属性采用 `positive / explicit_negative / unknown / conflict` 四态；没有明确否定证据时不得生成 0；`strict_commoner_3g` 降为 legacy 指标 |
| D 层依赖未经充分验证的 medium 链接 | D 层有结局者 371 人，其中 medium 230 人；medium 组祖先任官率 61.7%，high 组 41.4% | 报告中的 D≈55.5% 对链接阈值敏感，当前未审计 | medium 不再自动进入主分析；先建 gold、校准概率链接并做人审；主表至少报告 high-only、reviewed、all-candidate 三套结果 |
| 同一传记被分给多个候选人物 | harvest index 中 30 篇传记对应 62 个总体人物；其中 61 人进入 enrichment | 同名人和 CGED-Q 分裂 ID 可能共享同一人的家世事实 | 传记先链接到 canonical entity；未消歧时一律 quarantine；禁止一个传记断言被复制给多个未确认实体 |
| “消歧升级”结果没有参与裁决 | P05 有 86 人触发第二次调用，但 `build_outputs()` 仍读取第一次调用；P05 也报告升级新增断言为 0 | 多花调用成本但没有真正的 adjudication | 定义明确的 reconcile 状态机：agree 自动保留；disagree 进入人工队列；任何二次结果不得被静默忽略 |
| pilot 的人工复核未完成 | `audit/manual_validation.csv` 40/40 的 `human_review_status=pending` | 当前 accuracy 主要是结构化 gold 与自动规则比对，不能称完成人工验证 | 盲法双人或至少单人逐条复核；记录 reviewer、时间、理由、最终裁决；pending>0 时 release gate 必须失败 |
| P04 预设高信息量样本门槛未闭环 | P04 要求父系明示 ≥60、功名明示 ≥30、每层 ≥8；P05 实际 A2/A3 各仅 2 个可匹配人物，新增父槽位 50 | pilot 的准确率和跨层泛化证据不足 | 重新做基于“可测字段”的 validation sample；门槛按可比字段而非抽中人数定义 |
| 两条 unknown 审计是恒等式 | banner 检查为 `eq("unknown") & ne("unknown")`；province 检查为 `isna().sum()-isna().sum()` | 必然 PASS，不能发现错误 | 改为源表→分析表键连接后的不变量检查，并加故意注入错误的负向测试 |
| 发布门禁未执行 | P07 明示张百龄冲突待裁决、验收前不进 P08；P08 仍发布；计划要求 Figure 1/5，最终只交付 4 张图 | “阶段完成”与“阶段验收”混在一起 | 新建机器可执行 release gate；任何 pending/conflict/missing artifact/test failure 都返回非零 |

### 2.2 P1：决定研究能否升级为可比较结果

1. **统一来源协议**：A/B/C 主要来自 CBDB，《清史稿》又偏向高官；D 的 99% 没有家世材料。不能继续只在“碰巧有资料的人”中比较。应构造一个分层抽样子样本，对每层执行相同的来源检索顺序、检索深度和停止规则。
2. **先去重再链接**：CGED-Q 的 `person_id` 需要内部 dedupe；之后再做 CGED-Q↔CBDB/人名权威链接。当前“唯一姓名”不等于同一人物，尤其不能用于旗人称名。
3. **重新定义研究量**：主要结果改为固定来源协议下的 `documented_family_capital` 与资料覆盖；“寒门”只用于史料明确表述寒素、世无仕宦等情况。若没有明确否定证据，保持 unknown。
4. **建立真正的 evidence ledger**：每个字段单独保存 source、页/卷、连续引文、字符偏移、断言极性、抽取器、review 状态和冲突关系，不能只给整个祖先槽位一条 evidence。
5. **官职本体扩充**：现在 `ancestor_high_official` 有 30/54 条 LLM 官职无法归层；应把“职官品级”“行政层级”“是否实职”“署理/兼任/加衔”拆成不同变量并加入时间有效期。

### 2.3 P2：提高研究解释力与工程质量

1. 把“资料被记录的概率”作为单独结果建模；主分析使用抽样权重和标准化比例。若资料缺失仍明显 MNAR，应给识别区间或情景敏感性，不能用多重插补制造确定答案。
2. 把 tier 作为职业结果来建模，家世是先定暴露；不要只做 `ancestor_official_any ~ tier` 的倒置式回归。共同窗口可比较“是否进入 B/C/A”或职业最高层级，清楚区分描述性与因果问题。
3. 小格子或完全分离时可用 Firth logistic 或带弱信息先验的层级模型；它们只能缓解估计不稳定，不能修复选择偏差。
4. 将手工串联的命令改成 DAG 工作流；所有派生表先通过 schema 和语义不变量再落盘；报告中的数字从结果文件生成，避免手抄漂移。
5. 增加数据流程图、链接质量图、来源覆盖图、主结果和敏感性图；旗籍/地域样本不足时明确不生成实质比较图。

## 3. v0.2 研究设计

### 3.1 主要问题

v0.2 只回答两个层次的问题：

1. 在执行相同史料检索协议后，各官位层级人物的父系三代身份与政治—教育资本记录率如何变化？
2. 在 1760–1798 的可比窗口中，已观察到的家庭资本与人物最终进入 D、C、B、A 层之间有什么描述性关联？

不做因果宣称。母系、姻亲和完整家族网络可以保留扩展字段，但不进入 v0.2 primary estimand。

### 3.2 分层抽样而非盲目全量补证

先做功效与成本模拟，再冻结样本量。默认起点：

- A：116 人全收；
- B：按 cohort × 地域 × 旗籍证据分层抽 200 人；
- C：同法抽 200 人；
- D：同法抽 400 人，并保存抽样概率；
- validation oversample：另抽同名、无姓旗人、medium 链接和来源冲突人物，不进入加权总体估计。

约 916 人的核心样本比继续对 39,988 人做低命中检索更可控。若 U03 pilot 显示某层固定来源协议的可得率仍低于 20%，先换来源或修改研究量，再扩样。

### 3.3 证据状态

每个 `person × ancestor_slot × attribute` 保存：

```text
assertion_state = positive | explicit_negative | unknown | conflict
```

- `positive`：原文或结构化来源明确记载该属性；
- `explicit_negative`：原文明示无功名、未仕、世业农等，且语义经人工确认；
- `unknown`：没有材料、材料未提、无法消歧；
- `conflict`：两个可接受来源给出不相容值，尚未裁决。

祖先姓名已知只提高 identity coverage，不自动把功名或任官设为 negative。

### 3.4 指标重构

主指标：

- `ancestor_identity_coverage_1g/2g/3g`；
- `attribute_ascertainment_office/degree`；
- `documented_ancestor_official_any`；
- `documented_ancestor_degree_any`；
- `documented_family_capital_any`；
- `source_protocol_complete`；
- `documented_commoner_explicit`，仅接受明确否定/寒素证据。

legacy 指标单独输出，名称前加 `legacy_`，不得进入摘要结论。

### 3.5 来源协议

对核心样本中的每个人按同一顺序检索并记录“查过但无结果”：

1. 固定 release 的 CBDB SQLite 与公开 REST API交叉核验；
2. 中研院人名权威/清代职官的公开 LOD 或允许的查询接口；
3. 固定日期的中文维基文库 dump 中《清史稿》；
4. 权利状态清楚、允许批量处理的公开传记、履历、题名录或地方志材料；
5. 只有合法取得且许可允许时，才对《清史列传》等扫描件 OCR；否则登记为 unavailable，不绕过访问限制。

每个来源必须登记 release/date、URL、license/terms、文件 hash、获取方式、覆盖层级和已知偏差。

## 4. 可复用的公开组件

| 组件 | 用途 | 引入条件 |
| --- | --- | --- |
| [Splink](https://moj-analytical-services.github.io/splink/) + DuckDB | CGED-Q 内部 dedupe 与跨库概率链接；可输出 match weights、候选和评估图 | U02 gold pair 完成后使用；在 held-out gold 上达到预设 precision 才允许自动接受 |
| [CBDB REST API](https://input.cbdb.fas.harvard.edu/cbdbapi/index.html) | 用 canonical ID、别名、地址、亲属、任官、来源字段做交叉核验 | 响应缓存并记录访问日期；不得用在线结果覆盖冻结 release 而不留版本 |
| [中研院开放资料 SPARQL](https://data.ascdc.tw/sparql) | 人名权威、旗籍和清代职官链结数据的补证与标准化 | 先验证查询条款、字段覆盖和稳定 endpoint；限速、缓存、记录 query hash |
| [Wikimedia zhwikisource dumps](https://dumps.wikimedia.org/zhwikisource/) | 替代逐卷 API 抓取，消除 429 与“本次缺卷”问题 | 固定 dump 日期和 checksum；只抽取需要的 namespace/pages |
| [Label Studio](https://labelstud.io/guide/predictions) | 导入 LLM 预标注，完成人工复核、纠错和 adjudication | gold/validation 进入 U05 时使用；review 状态必须回写 evidence ledger |
| [Pandera](https://pandera.readthedocs.io/en/stable/) | 对 Parquet/CSV 做列类型、枚举、键唯一性、跨列约束 | U01 起用于所有阶段边界；校验失败阻止写入正式结果 |
| [Snakemake](https://snakemake.readthedocs.io/en/stable/) | 把获取、链接、抽取、分析、打包变成可复算 DAG | U08 引入；保留现有 Python module 作为 rule 的执行单元 |
| [PaddleOCR](https://www.paddleocr.ai/) | 可选的繁体、竖排、古籍 OCR 初稿 | 仅用于权利状态清楚的公开扫描件；必须做页级 OCR gold 和字符错误率评估 |
| [R `logistf`](https://search.r-project.org/CRAN/refmans/logistf/help/logistf.html) 或 [PyMC](https://www.pymc.io/) | 分离/小样本模型与部分汇聚 | U07 数据门槛满足后作为敏感性；不替代来源平衡与缺失审计 |

不要一次安装全部组件。U00–U03 只需要 Pandera、Splink；Label Studio/PaddleOCR/Snakemake 按对应阶段再决定。

## 5. 目标数据模型

至少新增以下表：

```text
data/processed_v02/entities.parquet
data/processed_v02/entity_links.parquet
data/processed_v02/source_documents.parquet
data/processed_v02/source_search_log.parquet
data/processed_v02/evidence_assertions.parquet
data/processed_v02/review_decisions.parquet
data/processed_v02/analysis_sample.parquet
data/processed_v02/person_indicators.parquet
```

`evidence_assertions` 的最小字段：

```text
entity_id, ancestor_slot, relation_type, attribute,
assertion_state, value_raw, value_normalized,
source_id, source_locator, quote, quote_start, quote_end,
extractor, extractor_version, extraction_run_id,
review_status, confidence, conflict_group_id
```

硬约束：

- `positive` 或 `explicit_negative` 必须有 source + locator + quote；
- `quote_start:quote_end` 必须逐字等于 quote；
- `unknown` 不得携带推断值；
- unresolved entity 不得进入主分析；
- 同一 source biography 不得同时归属多个未确认 entity；
- `review_status=pending` 或 `conflict` 的断言不得进入 primary indicator。

## 6. 分阶段实施

### U00 — 冻结 v0.1 与缺陷复现

建立升级分支与 `reports/upgrade/U00_BASELINE_AUDIT.md`。复现 73 tests、所有已发布行数与 hash；为本计划列出的 P0 问题各写一个可失败的回归测试。v0.1 文件只读，不重写。

**验收**：P0 问题都能由测试或审计表稳定复现；若事实与本计划不符，先更新审计说明，不直接改实现。

### U01 — 证据语义与 schema 迁移

实现四态 assertion、source ledger、review ledger 和 Pandera schema。把 v0.1 数据迁移为 v0.2 staging：已有正向记录可迁移为 positive；字段缺失只能迁移为 unknown；不得自动生成 explicit_negative。

**验收**：注入错误值时 schema 必须失败；由“仅知祖先姓名”构造的测试人物，其 office/degree 必须保持 unknown；primary 数据中没有 legacy commoner 推断。

### U02 — 人物去重、跨库链接与传记链接

先对 CGED-Q 做内部 dedupe，再做跨库链接。建立至少 300 对 stratified gold（match/non-match 均有，覆盖常见姓名、同名、异体字、旗人称名、年代/籍贯冲突）。用 deterministic high-confidence rules + Splink 评分；灰区人工复核。

**验收**：held-out precision ≥99%，并报告 recall 与各子组误差；自动接受阈值冻结；medium 不进入 primary；30 个共享传记冲突全部被正确合并或 quarantine；传记归属不再只按姓名决定。

### U03 — 样本设计与来源 pilot

生成可复现分层样本、抽样概率和功效/成本模拟。对每层至少 30 人执行完整来源协议，测量 identity、office、degree 的实际命中率和工时。

**验收**：样本 seed、strata、权重和排除理由可复算；各层使用同一检索协议；若任一层 source-protocol completion <80% 或有效信息率 <20%，不得扩样，先调整来源或 estimand。

### U04 — 来源接入与职官本体

接入固定 Wikimedia dump、CBDB API 缓存和经条款确认的中研院公开数据；建立清代职官映射，拆分品级、行政层级、实职/虚衔、署理、兼任和有效年代。利用 CGED-Q `選任方式` 补足署理信息，并审计官职串优先级规则。

**验收**：来源 manifest 全部有 hash/terms；Wikimedia 全卷清单无静默缺卷；官职高频串覆盖 ≥99%，剩余 unknown 按频率人工抽查；LLM 官职 54 条全部给出 matched/unmatched 原因。

### U05 — 抽取、人工标注与裁决

规则抽取先召回，LLM 只做结构化候选；每个字段单独证据 span。validation set 至少含 150 个有事实字段与 150 个正确空缺/否定案例，覆盖所有 tier 与高风险链接类；人工复核预标注，并对一部分做双人盲审。

**验收**：人工 review 无 pending；报告 field-level precision/recall、abstention、错误类型和层级差异；生产阈值以 precision 优先；二次调用的 reconcile 分支有单元测试，disagreement 必须进入人工队列。

### U06 — 全样本补证与质量控制

按 U03 冻结样本执行来源协议，保存每次查询和“无结果”记录，按批次 checkpoint。质量面板按 tier、source、linkage class、ancestor slot 显示完成率和错误率。

**验收**：核心样本 source protocol completion ≥95%；pending/conflict 单列且不进入 primary；随机 10% 回源复核和全部高风险病例复核完成；预算与调用日志闭合。

### U07 — 分析重建

先报告来源完成率和四态结果，再报告加权、标准化的 descriptive association。tier 作为职业结果；预注册 primary/secondary estimand、协变量角色和模型回退。Firth/层级模型只作敏感性；对 linkage 与 extraction uncertainty 做阈值/Monte Carlo 情景分析。

**验收**：原始加权比例与模型方向不应在无解释时相反；每个系数能追溯到样本分母；小格子、分离、rank deficiency 自动阻断实质解读；D 层 high-only/reviewed/all-candidate 结果并列。

### U08 — 工作流、报告与可复现打包

用 Snakemake 串起现有 Python 入口；报告数字从 CSV/Parquet 自动生成；加入 Figure 1 数据流程和来源/链接质量图。release manifest 记录代码、输入、参数、模型、prompt、review 和环境 hash。

**验收**：从冻结输入可一条命令重建非网络结果；空目录 dry-run 能列出完整 DAG；任何缺表、测试失败、dirty required artifact、pending review、unresolved conflict 都阻止打包。

### U09 — 独立审计与发布

只读审计研究问题、样本、证据、链接、统计、图表和文字结论。逐项对照本计划验收，不能用“测试通过”代替研究验收。修复后重新审计，发布 `v0.2-evidence-balanced`。

**验收**：审计清单 100% closed；摘要中的每个数字和句子均有 artifact-level lineage；报告明确哪些是事实、关联、解释和未解决边界。

## 7. 统计方案边界

Primary 输出顺序固定为：

1. 样本流图和 source-protocol completion；
2. entity linkage/dedupe 质量；
3. identity 与 attribute ascertainment；
4. 四态家世变量分布；
5. 加权层级比例及区间；
6. 预注册模型和敏感性；
7. 史学解释与局限。

以下条件任一成立时，只报告描述表，不报告实质模型结论：

- 任一主要层 source-protocol completion <80%；
- held-out linkage precision <99%；
- primary indicator 中 pending/conflict >0；
- 最小有效格 <20，且部分汇聚/惩罚模型仍高度依赖先验；
- 结果对 high-only 与 reviewed linkage 的方向不一致；
- 结论只能依赖把 unknown 当 0 才成立。

## 8. 发布门禁

`python -m qing_elite.release_gate --release v0.2-evidence-balanced` 必须检查并以非零状态失败：

- git/manifest/release 配置一致；
- 全部 schema 与测试通过；
- required artifacts 完整；
- manual review `pending=0`；
- unresolved primary conflicts `=0`；
- primary sample 无 medium/unresolved links；
- evidence span 逐字回源；
- 全部指标分母与排除人数闭合；
- 报告数字与生成表一致；
- 图表清单齐全；
- API 调用数、token 与成本可对账；
- 旧版 artifact 未被覆盖。

## 9. 最终交付

```text
reports/upgrade/U00-U09.md
reports/v02/FINAL_REPORT.md
reports/v02/reproducibility.{json,md}
reports/v02/tables/
reports/v02/figures/
audit/v02/
workflow/Snakefile
config/v02/
data/processed_v02/
```

计划完成的判断依据是所有验收项和 release gate；不能仅以 pipeline 跑完、测试通过或报告生成作为完成。
