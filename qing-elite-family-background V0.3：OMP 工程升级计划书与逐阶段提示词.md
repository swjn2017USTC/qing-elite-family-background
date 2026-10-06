

# `qing-elite-family-background`

# V0.3 `cohort-and-kin-network`

## OMP 工程升级计划书

版本：2026-09-27\
前置状态：V0.2 U00–U03 已完成；U03 required-source gate 已通过，但 D 层祖先信息覆盖仍基本失效。\
新分支建议：

`upgrade/v0.3-cohort-kin-network`

---

# 0. 一句话目标

V0.3 不再试图通过：

`最终官位 → 回头寻找父祖曾祖`

来判断“寒门”。

主研究路径改为：

`标准化家世史料 → 家族/宗族政治教育资本 → 人物实体 → 科举/任官职业轨迹 → 最终权力位置`

原 V0.2 的 A/B/C/D 样本、四态 evidence、source ledger、review ledger、官职框架等全部保留，但降为：

1. early/mid-Qing extension；
2. source-bias benchmark；
3. 与新 cohort design 的外部比较。

---

# 1. 本轮最重要的工程原则

## 1.1 不推倒 V0.2

以下直接继承：

- `positive / explicit_negative / unknown / conflict`
- `source_documents`
- `source_search_log`
- `evidence_assertions`
- `review_decisions`
- Pandera schema
- frozen raw source + hash
- CBDB adapter
- CGED-Q adapter
- Wikisource dump
- LLM call audit
- release gate 思想
- tests
- `uv`
- Parquet

旧的 `processed_v02/` **只读冻结**。

新数据全部放：

```text
data/
  processed_v03/
  interim_v03/
config/
  v03/
audit/
  v03/
reports/
  upgrade_v03/
```

---

# 2. 这次要接入哪些“已有轮子”

## A. 已经在本仓库里的：全部复用

| 轮子                            | V0.3 用法                           |
| ----------------------------- | --------------------------------- |
| Pandera                       | 所有阶段 schema gate                  |
| DuckDB/Parquet                | 主分析存储与 join                       |
| Splink                        | 保留为 linkage baseline / comparison |
| CBDB adapter                  | career / kin / degree 补证          |
| CGED-Q adapter                | career panel                      |
| Wikisource dump               | 自由文本补充，不再做主家世源                    |
| source/evidence/review ledger | 直接扩展                              |
| DeepSeek extraction pipeline  | 改成 rule-first / LLM-second        |
| pytest                        | 每阶段 regression gate               |

---

## B. 你其他项目已经做好的轮子

### 1. `ACADEMIC_LITERATURE_PIPELINE`

直接复制框架，不复制旧项目内容：

```text
sources/literature/
registry
search-plan
fulltext acquisition
parser / normalize
Paper Digest
Literature Evidence Ledger
Historiography
Claim Delta
```

并接入你已经搭好的：

- Academic-Search；
- OA 自动获取；
- CNKI/CARSI acquisition gate；
- `cdp-cnki.mjs`；
- PDF/HTML/OCR parsing；
- digest；
- historiography；
- claim-delta。

它在本项目承担两个任务：

**U04R：帮我们决定数据和研究问题怎么设计。**

**U09R：防止最终统计解释重新发明已有文献已经回答的问题。**

---

### 2. PaddleOCR-VL-1.6 + PP-DocLayoutV3

你现在已经有 PaddleOCR，本轮直接升级到：

**PaddleOCR-VL-1.6**

而不是从头另外造古籍 OCR。

它 2026 年版本强化了古籍、生僻字、印章和复杂文档解析；模型只有约 0.9B，适合本地先跑。

原则：

`OCR → structured parse → verifier`

而不是：

`OCR → 人工逐页检查`

DeepSeek Vision 只负责：

**OCR confidence 低或结构异常的页面二次视觉 adjudication。**

---

## C. 本仓库目前没有、这次必须接入的轮子

### 1. 官方 CGED-Q `person_id`

2026-08-31 的 CGED-Q release 已经包含：

`1760–1798 + 1850–1864 + 1900–1912`

并提供官方 `person_id`。

所以：

**禁止再把 CGED-Q 内部 person dedupe 当主要工程任务。**

旧 U02 dedupe 保留作 reproducibility comparison。

新版本：

```text
official_cged_person_id
legacy_v02_entity_id
crosswalk_status
```

---

### 2. `ML-Chinese-record-linkage`

仓库：

`bruceyyu/ML-Chinese-record-linkage`

直接借用：

- stroke embeddings；
- pinyin；
- candidate blocking；
- active learning；
- classifier ensemble；
- graph clustering；
- temporal connectivity validation。

这套方法本身就是在 CGED-Q JSL 上开发的，而且研究者明确指出框架可以扩展到同年齿录、明经通谱、履历档案和同官录。

**Splink 不删除。**

新架构：

```text
deterministic
    ↓
ML-Chinese-record-linkage
    ↓
Splink baseline
    ↓
agreement / disagreement
    ↓
risk scorer
    ↓
极小 residual review
```

---

### 3. NetworkX

用于建立真正的：

```text
person
 ├─ father
 ├─ grandfather
 ├─ great-grandfather
 ├─ uncle
 ├─ great-uncle
 ├─ brother
 ├─ cousin
 ├─ son
 └─ nephew
```

不是为了画漂亮网络图。

真正用于生成：

- direct-line depth；
- collateral breadth；
- elite-generations；
- senior-elite-kin count；
- kin-degree density；
- kin-office density；
- maximum kin office rank；
- network embeddedness。

---

### 4. Snakemake

原计划 U08 才加，现在 V0.3 提前建立骨架，但**不在一开始就重写所有代码**。

阶段模块成熟一个，再加入 DAG。

最终：

```text
raw
↓
normalize
↓
OCR
↓
extract
↓
entity-link
↓
kin-graph
↓
career-panel
↓
indicators
↓
analysis
↓
figures
↓
report
↓
release_gate
```

一条命令重建。

---

# 3. 数据源优先级重构

## Tier S：标准化结构化数据

### CGED-Q

负责：

- 人物；
- 籍贯；
- 出身；
- 任职；
- 官职；
- 时间；
- career trajectory。

不再负责猜家世。

---

### CBDB

负责：

- canonical person；
- aliases；
- kin；
- degree；
- office；
- dates；
- source cross-check。

---

## Tier A：标准化 family-history sources

优先调查：

1. 同官录；
2. 同年齿录；
3. 明经通谱；
4. 硃卷；
5. 履历档案；
6. 生员录；
7. 其他标准化题名/履历。

2026 年新发表的同官录研究已经编码 18,585 条官员记录、80,988 条亲属记录和 90,307 条功名/经历记录，而且特别覆盖监生、低级生员、捐纳功名等地方官群体。

但当前公开数据列表中我没有看到这个完整数据集的明确 bulk-download，因此 **V0.3 不允许假定我们已经可以下载它**。U04R 必须自动核验：

```text
PUBLIC_STRUCTURED
PUBLIC_SCAN
PUBLIC_UI_ONLY
ACCESS_REQUEST
PAPER_TABLE_ONLY
UNAVAILABLE
```

不让单一数据库访问问题把整个工程卡死。

---

# 4. “寒门”变量彻底重构

V0.3 禁止让 LLM 输出：

```text
is_commoner = true
```

主数据只记录事实。

## direct line

```text
father_degree
father_office

grandfather_degree
grandfather_office

great_grandfather_degree
great_grandfather_office
```

## extended kin

```text
senior_collateral_degree_count
senior_collateral_office_count

same_generation_degree_count
same_generation_office_count
```

## Derived variables

```text
direct_3g_degree_count
direct_3g_office_count
elite_generations
max_ancestor_office_rank

senior_collateral_elite_count
extended_family_elite_count

kin_degree_density
kin_office_density
```

以及两个有严格定义的 descriptive category：

```text
direct_line_new_entrant
extended_family_new_entrant
```

这很重要，因为最新同年齿录/明经通谱研究已经有 34,313 位功名获得者及约 950,927 位亲属，并发现一部分“父祖曾祖皆无功名/官职”的 apparent new blood 实际存在有地位的叔伯等高级旁系。

---

# 5. 人工审核的总原则

## 不再采用：

> 随机 10% 全人工检查。

改成：

# `risk-based + active-learning + sequential audit`

每个 assertion/link 自动计算：

```text
risk_score =
    linkage_uncertainty
  + OCR_uncertainty
  + parser_disagreement
  + source_conflict
  + rare_relation
  + office_unknown
  + chronology_violation
  + evidence_span_failure
```

---

## 三档

### LOW

满足全部 deterministic invariants：

**自动接受。**

### MEDIUM

交给：

- 第二 parser；
- DeepSeek Flash；
- 或 vision verifier；

自动 adjudicate。

### HIGH

才进入：

`manual_review_queue`

---

## 人工预算规则

不预先承诺百分比。

而设：

```text
manual_link_new_budget_target <= 300
manual_assertion_review_target <= 200
```

这里 **优先复用 U02 已有人审/gold**。

如果模型在该预算内达不到预设质量：

**stage FAIL。**

而不是把全部几万条扔给你人工检查。

---

# 6. OMP / DeepSeek 模型路由

当前最简单的方案就是：

## OMP coding

### 默认

`DeepSeek V4.1 Flash`

`reasoning=high`

负责：

- coding；
- test；
- refactor；\
  -普通 architecture；\
  -阶段执行。

DeepSeek 当前 API 的正式模型名已经是 `deepseek-flash`；旧 `deepseek-v4-flash` 会路由至 V4.1 Flash。官方也已经提供 OMP 专门配置说明。

### XHigh / Max

只用于：

- U03R research contract；
- U06R linkage architecture；
- U09R statistics review；
- U11R adversarial audit。

不要拿 max 写普通 Python。

---

## 正式史料抽取

不要通过 OMP session 批量调用。

本地 Python：

```text
deepseek-flash
thinking = disabled
JSON output
```

普通字段抽取尽可能非思考。

只有：

- source conflict；
- 复杂关系链；
- chronology ambiguity；

才：

```text
thinking = enabled
effort = low/high
```

---

## OCR / Vision

主路径：

```text
PaddleOCR-VL-1.6
```

second-pass：

```text
DeepSeek V4.1 Flash Vision
```

只处理：

`LOW_OCR_CONFIDENCE`

因此不会给 DeepSeek 批量喂几万页图。

---

# 7. 开工前：你只需要手动做这些

## 7.1 Git

```bash
cd /Users/wahrfreiheit/OMP/qing-elite-family-background

git status
git branch --show-current
git log --oneline -5
```

确认没有未提交的重要修改。

然后：

```bash
git tag v0.2-u03-frozen-20260927
git switch -c upgrade/v0.3-cohort-kin-network
```

不要删除：

```text
data/processed_v02
reports/upgrade/U00-U03
audit/v02
audit/v03
```

---

## 7.2 DeepSeek

确认：

```bash
echo $DEEPSEEK_API_KEY
```

不要把 key 写入 repo。

OMP 官方 DeepSeek 配置仍建议放：

```text
~/.omp/agent/models.yml
```

而且官方特别要求：

```text
supportsToolChoice: false
requiresReasoningContentForToolCalls: true
requiresAssistantContentForToolCalls: true
```

否则 thinking + tool-call 长会话可能出现 400。

如果你之前已经按 DeepSeek 官方 provider 配好了，就**不要重新配置**。

---

## 7.3 不需要提前安装一大堆包

让 OMP 每个阶段只增加实际需要的依赖。

你手工只要保证：

```bash
uv sync
uv run pytest -q
```

目前 baseline 正常即可。

---

# 8. 总阶段路线

```text
U03R  Research reset + postmortem
 ↓
U04R  Sources + literature feasibility
 ↓
U05R  Standardized-family-source pilot
 ↓
U06R  Entity-resolution V2
 ↓
U07R  Kin graph + family-capital panel
 ↓
U08R  Career-event panel
 ↓
U09R  Career-process analysis
 ↓
U10R  Early-Qing / legacy extension
 ↓
U11R  DAG + adversarial audit + release
```

---

# U03R — Research reset + V0.3 contract

## 目标

OMP 首先彻底读懂已有项目。

禁止写大量新代码。

产出：

```text
V02_POSTMORTEM.md
V03_RESEARCH_CONTRACT.md
V03_AUTOMATION_POLICY.md
V03_DATA_MODEL.md
V03_SOURCE_PRIORITY.md
V03_STAGE_PLAN.md
```

并建立：

```text
config/v03/
data/processed_v03/
audit/v03/
reports/upgrade_v03/
```

---

## 必须明确

Primary question：

> 在相似的科举/制度进入资格下，家庭政治—教育资本以及更广泛宗族精英嵌入，与一个人的职业轨迹和最终官位之间有怎样的描述性关联？

不宣称 causal identification。

---

## Gate

必须证明：

1. 不覆盖 V0.2；
2. 新 schema 能表达 direct + extended kin；
3. `unknown != negative`；
4. tier 变成 outcome；
5. 人工审核按 risk 分层；
6. 原 D-layer comparison 不再是主 estimand。

---

## Commit

```text
U03R: reset v0.3 research design around cohort and kin networks
```

---

## 给 OMP 的提示词

```text
你现在开始 qing-elite-family-background 的 V0.3 U03R。

首先完整阅读，不要跳过：
README.md
METHODS.md
CODEBOOK.md
QING_ELITE_FAMILY_BACKGROUND_UPGRADE_PLAN.md
OMP_UPGRADE_STEP_PROMPTS.md
reports/upgrade/U00*
reports/upgrade/U01*
reports/upgrade/U02*
reports/upgrade/U03.md
config/
src/qing_elite/
tests/

特别注意 U03 报告最初 FAIL、调整后 required-source gate PASS，
但是 D 层 ancestral_information_rate 仍为 0%。

本阶段禁止进入新的大规模数据抓取、LLM 抽取或统计分析。
不要重写 v0.2 文件。

任务：

1. 对 v0.1/v0.2 做工程+研究设计 postmortem。
2. 新建 V03_RESEARCH_CONTRACT.md：
   - 把 family background 设为 exposure；
   - career trajectory / maximum office / central-local route 为 outcome；
   - 从 direct three-generation 扩展到 extended kin；
   - 明确 descriptive association，不做未经识别的因果宣称。
3. 新建 V03_AUTOMATION_POLICY.md：
   - rule-first；
   - machine verification second；
   - risk-based review；
   - human only for residual high-risk；
   - 不允许固定 10% 人工审核。
4. 新建 V03_DATA_MODEL.md，设计至少：
   persons
   source_documents
   career_events
   kin_edges
   credentials
   offices
   evidence_assertions
   entity_links
   review_queue
   risk_scores
5. 建立 config/v03、data/processed_v03、data/interim_v03、
   audit/v03、reports/upgrade_v03。
6. 写 schema/test skeleton，但不要实现后续阶段业务逻辑。
7. 全量运行现有 tests。
8. 写 reports/upgrade_v03/U03R.md，
   包含 PASS/FAIL/EVIDENCE。
9. 若 gate PASS，commit：
   "U03R: reset v0.3 research design around cohort and kin networks"

不要进入 U04R。
```

---

# U04R — 来源与现代研究 feasibility benchmark

这是最重要的一步之一。

## 目标

不是马上下载几万份史料。

先查：

**到底哪些轮子真的能拿到。**

---

## 接入 Academic Literature Pipeline

复制你的现有框架：

```text
sources/literature/
registry
digest
ledger
historiography
claim_delta
```

围绕主题自动检索：

```text
Qing social mobility
imperial examinations
family background
kin networks
Tongnianchilu
Mingjingtongpu
Tongguanlu
Zhujuan
Jinshenlu
official careers
office purchase
Shengyuan
Jiansheng
```

必须包含：

- Ho；
- Jiang & Kung；
- Campbell/Lee 系列；
- 2026 Tongnianchilu paper；
- 2026 Tongguanlu paper；
- 2026 ML linkage paper。

现代研究不能只用于“写综述”。

还必须形成：

```text
claim → required variable → possible source
```

---

## 来源自动 feasibility probe

对每个 source 保存：

```text
source_name
coverage_year
population
kin_scope
career_scope

access_type
bulk_available
api_available
scan_available
machine_readable

terms
redistribution
local_cache_allowed

estimated_n
estimated_ocr_pages
automation_score
research_value
```

---

## 不允许做的事

不能因为论文说：

> 我们有 Tongguanlu dataset

就假设本项目能直接下载。

查不到 bulk download：

```text
ACCESS_REQUEST_REQUIRED
```

然后继续评估其他路径。

---

## Gate

最后输出三个方案：

### PLAN A

已有结构化数据直接能做。

### PLAN B

结构化 + 少量 OCR。

### PLAN C

主要靠扫描 OCR。

自动根据：

```text
coverage
research fit
machine accessibility
manual burden
```

选择默认方案。

不是根据“哪个听起来最好”。

---

## Commit

```text
U04R: benchmark standardized family-background sources
```

---

## OMP 提示词

```text
开始 V0.3 U04R。

先阅读：
V03_RESEARCH_CONTRACT.md
V03_AUTOMATION_POLICY.md
V03_DATA_MODEL.md
reports/upgrade_v03/U03R.md

本阶段目标不是批量生产研究数据，而是自动判断哪些 family-background
来源值得用于 V0.3。

优先复用我其他项目已经建立的 ACADEMIC_LITERATURE_PIPELINE。
如果本机已有相关 handoff / registry / scripts，
只复制框架和 schema，不复制旧项目研究结论或旧 topic data。

要求：

A. 建立 literature pipeline：
- search plan
- metadata registry
- full-text acquisition status
- Paper Digest
- Literature Evidence Ledger
- Historiography
- Claim Delta

检索并重点处理：
Qing social mobility
family background
kin network
Tongnianchilu
Mingjingtongpu
Tongguanlu
Zhujuan
Shengyuanlu
Jinshenlu
career mobility
office purchase
Chinese historical record linkage

B. 对候选史料逐一做机器化 feasibility probe：
CBDB
CGED-Q latest public release
Tongguanlu
Tongnianchilu
Mingjingtongpu
Zhujuan
Lvli Dangan
Shengyuanlu
Sinica
以及检索中新发现且相关的标准化来源。

每个来源标记：
PUBLIC_STRUCTURED
PUBLIC_SCAN
PUBLIC_UI_ONLY
ACCESS_REQUEST_REQUIRED
PAPER_TABLE_ONLY
UNAVAILABLE

禁止绕过登录、访问控制或使用条款。

C. 建立 source_feasibility.parquet 和 source_feasibility.md，
至少记录：
年代、人口覆盖、kin范围、career范围、访问方式、是否bulk、
是否API、license/terms、预估机器成本、预估人工成本。

D. 把现代文献中的 claim 映射为：
claim -> variable -> source -> V0.3 testability。

E. 自动提出 PLAN A/B/C 三套数据实现方案，并按预定义规则选默认方案。
如果重要数据集当前不能合法/稳定机器获取，不得让整个项目阻塞，
而应形成 fallback。

F. 新增测试与 schema。
G. 写 reports/upgrade_v03/U04R.md。
H. gate PASS 后 commit：
"U04R: benchmark standardized family-background sources"

不要进入大规模 OCR、linkage 或分析。
```

---

# U05R — Standardized family-background pilot

## 目标

第一次真正回答：

> 标准化家世材料是否能可靠、自动地生成 kin graph？

---

## Sampling

不按最终官阶抽。

按：

```text
source
× cohort
× region
× credential
```

抽 pilot。

建议：

`200–500 focal persons`

够判断 feasibility 即可。

---

## Parser hierarchy

### 1. deterministic parser

先做：

- 关系词；
- 姓名；
- 功名；
- 官职；
- 世代；
- 格式标记。

### 2. PaddleOCR-VL

仅扫描材料需要。

### 3. DeepSeek non-thinking

把 parser 不能处理的片段转换为 JSON candidate。

### 4. verifier

必须检查：

```text
quote exists
offset exact
relation valid
chronology possible
office ontology valid
person unique
```

### 5. Vision arbitration

仅：

```text
OCR_confidence < threshold
```

---

## 关键原则

LLM 不判断：

`elite / commoner`

只抽：

`relation / name / degree / office / evidence`

---

## Gate

至少回答：

- kin relation precision；
- degree extraction precision；
- office extraction precision；
- evidence-span precision；
- OCR error；
- abstention rate；
- source-specific error。

如果某类来源自动化效果太差：

**drop source**

不是叫你人工把它全部修完。

---

## Commit

```text
U05R: validate standardized kin-source extraction pipeline
```

---

## OMP 提示词

```text
开始 V0.3 U05R。

读取：
V03_RESEARCH_CONTRACT.md
V03_AUTOMATION_POLICY.md
V03_DATA_MODEL.md
reports/upgrade_v03/U04R.md
source_feasibility.*

只使用 U04R 默认方案中已经确认合法且机器可访问的来源。
不要假设未获得的数据存在。

目标：构建 standardized-family-background pilot。

1. 按 source × cohort × region × credential 构造 200–500 人以内的
   reproducible pilot。
2. 对结构化来源优先直接 parser，禁止无意义地调用 LLM。
3. 对扫描来源使用本机 PaddleOCR；若版本允许，优先 PaddleOCR-VL-1.6
   + PP-DocLayoutV3。
4. parser 输出至少：
   focal_person
   relation_type
   kin_name
   generation
   degree_raw
   office_raw
   source_id
   locator
   quote
   offsets
5. rule parser 无法收敛时才调用 DeepSeek 官方 API：
   deepseek-flash
   thinking disabled
   JSON output。
6. OCR/结构低置信页面才能调用 vision verifier。
7. 实现自动 evidence verifier：
   exact quote
   offsets
   valid relation
   chronology
   ontology lookup
   duplication
   conflicts
8. 建 risk_score，不做固定比例人工 review。
9. 构造一个很小但覆盖困难类型的 gold/validation sample，
   优先复用已有人工证据，人工只处理机器无法自动裁决的高风险样本。
10. 输出 field-level precision/recall/abstention/error taxonomy。
11. 自动淘汰无法在合理自动化程度下达到质量门槛的 source，
    不准用大规模人工录入挽救。
12. 写 U05R.md 和 machine-readable gate。
13. tests 全过后 commit：
    "U05R: validate standardized kin-source extraction pipeline"

停止，不进入 U06R。
```

---

# U06R — 中文历史人物 Entity Resolution V2

## 目标

把目前最浪费人工的部分重做。

---

## 第一件事：CGED-Q migration

保留旧数据：

```text
CGED_v24_legacy
```

新加入：

```text
CGED_20260828_person_id
```

建立 crosswalk。

优先信官方 person ID。

旧 U02 dedupe 不删，用来比较：

```text
legacy_id vs official_person_id
```

---

## 第二件事：接入 ML-Chinese-record-linkage

不要 copy-paste notebook 后硬改。

封装成：

```text
src/qing_elite/linkage_ml/
```

或者：

```text
vendor/ml_chinese_record_linkage/
```

首先检查 license。

保留 upstream commit hash。

---

## blocking feature

至少：

```text
name
stroke embedding
pinyin
zihao
native place
province
credential
career chronology
office sequence
```

如果 kin 数据已经可用：

增加：

```text
relative-name overlap
```

---

## weak labels

可以自动产生：

### positive weak-label

极高置信：

```text
official person_id
exact name
exact native place
compatible dates
compatible career
```

### negative

明显：

```text
same-name
incompatible province
impossible simultaneous career
chronology contradiction
```

但 weak label 不当 gold。

---

## Active learning

优先喂 U02 已经审核过的 pair。

只有模型真正 uncertainty 高时才让你标。

禁止一次导出 47,000 pair。

建议每轮：

`25–50 pairs`

而不是论文原实现机械复制 1000 人工标签。

直到：

- validation plateau；
- 或人工预算达到上限。

---

## Gate

Primary auto-accept：

`precision >= 0.99`

如果达不到：

提高 abstention。

**宁可 unknown，不增加人工海洋。**

---

## Commit

```text
U06R: upgrade Chinese historical entity linkage
```

---

## OMP 提示词

```text
开始 V0.3 U06R。

这是 entity-resolution 专项阶段。
先读 U02、U03 和 U05R 的所有 linkage / schema / report。

任务：

1. 下载/注册最新 CGED-Q public release（若本机尚无）：
   必须与旧 v0.2 raw 并存，不覆盖。
   校验 hash。
2. 验证官方 person_id 的唯一性、跨edition一致性和字段语义。
3. 建 legacy_v02_id <-> official_cged_person_id crosswalk。
4. CGED-Q 内部 dedupe 默认采用官方 person_id；
   旧 U02 dedupe 只保留为 audit comparison。

5. 接入 bruceyyu/ML-Chinese-record-linkage 的思想和可合法复用代码：
   stroke blocking
   pinyin
   supervised matching
   active learning
   graph clustering
   temporal connectivity。
   记录 upstream repo、commit、license。
   不要破坏原项目数据模型。

6. Splink 保留为 baseline。
   输出 deterministic / ML / Splink 三套分数和 agreement pattern。

7. 优先复用 U02 已有人工/gold labels。
   构造 weak-positive / weak-negative 只能用于训练辅助，
   不得混称 gold。

8. active learning 每轮只选最有信息量的少量 pair。
   默认人工新增预算目标 <=300；
   达不到 precision 时提高 abstention，而不是扩大万人 review。

9. 对 linkage 建自动 chronology / geography / career transition validator。

10. primary auto-accept 要求 held-out precision >=99%。
    无法达到则 FAIL 或缩小 auto-accept region。

11. 输出：
    entity_links.parquet
    linkage_features.parquet
    linkage_risk.parquet
    linkage_review_queue.parquet
    linkage_benchmark.md
    U06R.md

12. tests + reproducibility gate。
13. PASS 后 commit：
    "U06R: upgrade Chinese historical entity linkage"

停止。
```

---

# U07R — Kin graph + family capital

## 目标

这一步才是真正从“三代寒门表”升级成家族网络研究。

---

## 图模型

统一：

```text
nodes:
  person

edges:
  relation
  source
  evidence
  confidence
```

relation ontology：

```text
father
grandfather
great_grandfather

uncle
great_uncle

brother
cousin

son
nephew

mother
grandmother
wife
...
```

Primary analysis 暂时以父系男性高级亲属为主。

女性、姻亲先保留，不急着做 substantive conclusion。

---

## NetworkX 只负责计算

主数据依然存 Parquet / DuckDB。

不能把 NetworkX pickle 当 canonical data。

---

## 指标

Primary：

```text
direct_3g_degree_count
direct_3g_office_count

direct_elite_generations
max_direct_office_rank

senior_collateral_degree_count
senior_collateral_office_count

all_senior_elite_kin_count
```

Secondary：

```text
kin_degree_density
kin_office_density
network_degree
elite_neighbor_share
```

---

## “New entrant”

严格分开：

```text
direct_line_new_entrant
```

与：

```text
extended_family_new_entrant
```

并要求 sufficient observability。

没有足够亲属记录：

`unknown`

---

## Gate

任何指标必须给：

```text
numerator
denominator
observable_kin_count
source coverage
```

---

## Commit

```text
U07R: build evidence-aware Qing kinship graph
```

---

## OMP 提示词

```text
开始 V0.3 U07R。

读取 U05R、U06R 以及 V03_DATA_MODEL.md。

目标是构建 evidence-aware kin graph，
而不是做漂亮网络图。

1. 创建 canonical:
   persons.parquet
   kin_edges.parquet
   credentials.parquet
   offices.parquet
   evidence_assertions.parquet

2. relation ontology 必须版本化。
   区分 direct line、senior collateral、same-generation、
   junior、female、marital。

3. 所有 kin edge 必须回源到 evidence assertion。
   machine-inferred edge 与 source-explicit edge 要分开。

4. 用 NetworkX 作为计算层而不是持久化真相层。

5. 生成 primary family-capital variables：
   direct_3g_degree_count
   direct_3g_office_count
   direct_elite_generations
   max_direct_office_rank
   senior_collateral_degree_count
   senior_collateral_office_count
   all_senior_elite_kin_count

6. 构造：
   direct_line_new_entrant
   extended_family_new_entrant
   但只有在 observability 足够时才允许 true/false，
   否则必须 unknown。

7. 每个 derived indicator 都保存：
   numerator
   denominator
   observable kin
   source coverage
   evidence lineage。

8. 检测 cycle、generation paradox、chronology contradiction、
   duplicated kin、conflicting relationship。

9. 生成 source-specific 和 cohort-specific coverage report。

10. 不做 substantive regression。
11. 输出 U07R.md + machine gate。
12. PASS 后 commit：
   "U07R: build evidence-aware Qing kinship graph"

停止。
```

---

# U08R — Career-event panel

## 目标

把：

`官员属于 A/B/C/D`

升级为真正的职业序列。

---

## 主要来源

优先：

1. CGED-Q；
2. CBDB；
3. standardized resume；
4. structured office history；
5. biographies 只补缺。

---

## Career event schema

```text
person_id
event_date_start
event_date_end

office_raw
office_normalized

rank
administrative_level

central_local
province
jurisdiction

appointment_type
acting
expectant
substantive

selection_method

source
confidence
```

---

## Outcome

生成：

```text
highest_office_rank
highest_admin_level

ever_central
ever_provincial
ever_prefectural
ever_county

ever_governor_general
ever_governor
ever_board_minister

first_substantive_office
age_at_first_office

time_to_rank_*
career_length

central_local_transitions
appointment_regular_share
acting_share
```

---

## 官职 ontology

复用 V0.2。

但拆：

```text
rank
authority
administrative level
appointment status
honorific
concurrent office
acting
```

不能再一个 `ancestor_high_official` 全包。

---

## Gate

career path 自动 chronology check。

例如：

- 任职先于出生；
- 死后任职；
- 相互不可能的同一日期；
- 品级跨越异常；

自动 quarantine。

---

## Commit

```text
U08R: build longitudinal Qing official career panel
```

---

## OMP 提示词

```text
开始 V0.3 U08R。

本阶段把职业位置从静态 tier 改为 longitudinal career events。

1. 优先使用最新 CGED-Q official person_id career records。
2. 接入 CBDB office/event records。
3. 若 U04R/U05R 的 standardized source 有 resume，
   按 evidence-aware 方式并入。
4. biographies 仅作补缺，不能覆盖结构化 source 而不记录冲突。

5. 重构 office ontology：
   office title
   rank
   administrative level
   central/local
   authority type
   appointment status
   acting
   expectant
   substantive
   concurrent
   honorific
   effective period

6. 生成 career_events.parquet。

7. 派生：
   maximum office
   career length
   first substantive office
   central/local route
   time-to-rank
   appointment composition
   major career transitions。

8. 加 chronology validator 与 impossible-transition detector。
9. 所有 derived outcomes 有 lineage。
10. 与旧 A/B/C/D 建 crosswalk，但 tier 只保留为 legacy/extension outcome。

11. 写 U08R.md。
12. 全测试 PASS 后 commit：
    "U08R: build longitudinal Qing official career panel"

停止。
```

---

# U09R — Career-process analysis

## 目标

到这一阶段才真正回答历史问题。

---

# Primary analysis

核心结构：

```text
family capital
      ↓
career outcome
```

而不是：

```text
tier
 ↓
ancestor status
```

---

## 第一层：描述统计

必须先做：

```text
family capital distributions
by cohort
by credential
by region
by career outcome
```

所有数字报告：

`n / denominator / coverage`

---

## 第二层：matched / stratified comparison

优先做：

```text
same cohort
same credential
similar region
similar entry status
```

之后再比较：

`different family capital`

对应：

`different career trajectories`

---

## 第三层：模型

根据数据适用性：

### ordered / multinomial

最终官位。

### discrete-time hazard / survival

晋升速度。

### transition model

中央/地方职业路径。

### hierarchical models

地区/cohort 小样本 partial pooling。

PyMC 只作为需要时使用。

不为了“高级”强上 Bayes。

---

## interaction

重点：

```text
family capital × credential
family capital × region
family capital × cohort
```

---

## uncertainty

必须跑：

```text
linkage threshold sensitivity
extraction threshold sensitivity
source restriction
direct-only vs extended-kin
official-person-id only
complete-kin-source only
```

---

## Literature Claim Delta

重新调用：

`Academic Literature Pipeline`

把结果与：

- Jiang & Kung；
- Tongnianchilu；
- Tongguanlu；
- 传统 Ho social mobility；

逐项比较。

输出：

```text
CONFIRM
REFINE
CONTRADICT
NEW
NOT_COMPARABLE
```

---

## 禁止

不能写：

> 清代是 meritocracy。

也不能写：

> 清代官僚体系完全由家族复制。

只写数据能支持的机制层次。

---

## Commit

```text
U09R: analyze family capital and career trajectories
```

---

## OMP 提示词

```text
开始 V0.3 U09R。

这是第一次正式 substantive analysis。

先读取：
V03_RESEARCH_CONTRACT.md
U04R literature/historiography
U07R kin graph
U08R career panel
全部 coverage/linkage/extraction audit。

必须遵守：
association != causation
unknown != zero
coverage before outcome
source selection must remain visible。

分析顺序：

1. 样本流图。
2. source / kin observability。
3. linkage quality。
4. family-capital distributions。
5. career-outcome distributions。
6. stratified/matched descriptive comparisons。
7. 预注册模型。
8. sensitivity。
9. historiographical Claim Delta。

Primary exposures：
direct_3g_degree_count
direct_3g_office_count
direct_elite_generations
senior_collateral_elite_count
以及预注册的少量核心指标。

Primary outcomes：
highest office
central/local route
first substantive appointment
career advancement / time-to-rank
major authority positions。

优先同 cohort / credential / region 分层。

只有数据门槛允许时才运行复杂模型。
自动检测：
separation
small cells
rank deficiency
unstable coefficients
high missingness
source dependence。
触发时降级为 descriptive result。

敏感性至少包括：
official person_id only
high-confidence crosslinks only
complete standardized-family-source only
direct-line only
direct + extended kin
不同 extraction threshold
不同 linkage threshold。

重新运行 Academic Literature Pipeline 的 Claim Delta，
把结果分为：
CONFIRM / REFINE / CONTRADICT / NEW / NOT_COMPARABLE。

自动生成 tables/figures 和 U09R.md。
所有报告数字从 artifact 生成，禁止手抄。

通过 gate 后 commit：
"U09R: analyze family capital and career trajectories"

停止。
```

---

# U10R — Early/mid-Qing extension

这一步才重新接回你旧项目最初的 1644–1820。

---

## 目标

不要把晚清 cohort 的结论偷偷推广到整个清代。

建立：

```text
late-Qing main panel
vs
1644–1820 elite extension
```

---

## 旧 A/B/C

保留：

- 大学士；
- 南书房；
- 军机；
- 六部；
- 督抚。

但不再与旧 D 强行做“寒门率”比较。

---

## 问题

主要问：

1. direct-family elite composition；
2. region；
3. banner；
4. elite reproduction；
5. 与十九世纪 standardized cohort 的指标方向是否类似。

---

## 输出

结果分：

```text
PERIOD-SPECIFIC
CROSS-PERIOD CONSISTENT
NOT COMPARABLE
```

---

## Commit

```text
U10R: integrate early-Qing elite extension
```

---

## OMP 提示词

```text
开始 V0.3 U10R。

目标：把现有 1644–1820 A/B/C 研究作为 early/mid-Qing extension
接入，而不是让它重新主导研究设计。

1. 完整复用 v0.1/v0.2 已清洗成果，不重复无意义采集。
2. 把旧 family variables 映射至 V0.3 family-capital ontology。
3. 不能映射的变量显式 NOT_COMPARABLE。
4. 旧 D 层不得恢复为“寒门率对照组”。
5. 比较 early/mid-Qing elites 与 late-Qing standardized cohort 时，
   必须明确 source/population differences。
6. 分析：
   direct family capital
   regional composition
   banner status
   elite persistence
   limited career outcomes
7. 把结论分类：
   PERIOD-SPECIFIC
   CROSS-PERIOD CONSISTENT
   NOT COMPARABLE
8. 不做超出 source comparability 的 pooled regression。
9. 写 U10R.md 和比较图表。
10. gate PASS 后 commit：
    "U10R: integrate early-Qing elite extension"

停止。
```

---

# U11R — Workflow + adversarial audit + release

## 目标

最终才真正做“可发布版”。

---

## Snakemake

接管成熟模块：

```text
download/register
normalize
ocr
parse
verify
link
kin_graph
career
indicators
analysis
figures
report
release_gate
```

网络依赖和本地确定性分析分开。

例如：

```bash
snakemake acquisition
snakemake research
snakemake release
```

---

## 自动 adversarial audit

单独一个 reviewer agent：

**不能修改数据。**

只允许读。

它主动尝试寻找：

- denominator drift；
- unknown→0；
- source leakage；
- duplicate entities；
- impossible kin；
- career chronology errors；
- hidden manual overrides；
- figure/report mismatch；
- selection-bias overclaim；
- causality overclaim；
- unmatched literature claim。

---

## Release gate

只要以下任何一个出现就 FAIL：

```text
schema failure
test failure
unresolved primary conflict
missing evidence span
dirty artifact
broken lineage
report/table mismatch
unregistered raw source
manual override without rationale
primary low-confidence linkage
unresolved high-risk assertion
```

---

## 最终产物

```text
README_V03.md
METHODS_V03.md
CODEBOOK_V03.md

FINAL_REPORT_V03.md

data_flow.svg
source_coverage.*
linkage_quality.*
kin_structure.*
career_trajectory.*
main_results.*
sensitivity.*

REPRODUCIBILITY.md
RELEASE_MANIFEST.json
```

---

## Commit + Tag

```text
U11R: finalize reproducible v0.3 cohort-and-kin-network release
```

之后：

```text
v0.3-cohort-kin-network
```

---

## OMP 提示词

```text
开始 V0.3 U11R。

这是最终工程化、独立审计和 release 阶段。
禁止重新发明研究设计。

A. Snakemake
把已稳定的模块组成 DAG：
acquisition
normalize
ocr
extract
verify
entity_link
kin_graph
career_panel
indicators
analysis
figures
report
release_gate

不要为了 Snakemake 重写已经稳定的 Python module。

B. reproducibility
冻结：
source versions
checksums
environment
model IDs
prompt versions
code commit
thresholds
random seeds
literature registry
upstream linkage code commit。

C. adversarial audit
启动只读 reviewer。
主动寻找：
unknown 被转 0
denominator drift
source selection
entity duplication
linkage leakage
kin chronology contradiction
career chronology contradiction
unreviewed manual override
table/report mismatch
figure/data mismatch
causal overclaim
literature overclaim。

reviewer 不得静默修复。
所有 issue 写 audit/v03/final_audit.*。

D. release gate
实现机器可执行 gate：
任何 required artifact/test/schema/lineage/conflict failure
必须 non-zero exit。

E. 报告
生成：
README_V03.md
METHODS_V03.md
CODEBOOK_V03.md
FINAL_REPORT_V03.md
REPRODUCIBILITY.md
RELEASE_MANIFEST.json
以及主图和 sensitivity 图。

所有数字必须自动从 artifact 渲染。

F. public/private boundary
继续遵守现仓库原则：
不重新分发受限制 raw data；
不发布 API key；
不发布许可不允许公开的逐人文本；
公开 schema、代码、汇总、audit 和 reproducibility metadata。

G. 全量从 clean state 执行 dry-run 与 tests。
最终独立审计 PASS 后：

commit：
"U11R: finalize reproducible v0.3 cohort-and-kin-network release"

并建议 tag：
v0.3-cohort-kin-network

然后停止。
```

---

# 9. 人工介入最终应该只剩什么

理想情况下，你真正需要亲自干的只剩四类。

## ① U04R 后

看一次：

> 默认 source plan 有没有明显史学方向错误。

不是检查几百人。

---

## ② U06R

active learning 必须有人类 truth 时：

**少量 difficult pairs。**

优先消耗旧 U02 gold。

---

## ③ U05/U07

极少量：

- OCR 特别差；
- relation 特别复杂；
- source 真正冲突；

案例。

不是固定比例抽查整个 corpus。

---

## ④ U09/U11

你真正应该花精力的地方：

> 这些 statistical results 在历史上到底意味着什么？

这才应该由你审核。

而不是：

> 张三是不是这个李四的祖父？

---

# 10. 成本控制

## coding

OMP：

`V4.1 Flash high`

约占绝大多数调用。

只有：

```text
U03R final review
U06R architecture/review
U09R modeling review
U11R adversarial review
```

使用 max/xhigh。

DeepSeek 当前 Flash 的价格已经明显下降，而且 off-peak 为 peak 的一半。

---

## extraction

最大节省来自：

```text
rules > local OCR > cheap non-thinking LLM > reasoning LLM > human
```

而不是：

```text
everything → reasoning LLM
```

---

# 11. 什么情况下应该自动停下来，而不是硬跑下一阶段

任何一个阶段出现：

### Source

主 family source 大部分无法合法机器获取；

### Extraction

关键 field precision 明显不足；

### Linkage

99% precision 只能依靠大量人工；

### Coverage

核心 cohort 仍有强烈阶层差异；

### Career

职业序列无法可靠恢复；

就：

**FAIL + redesign。**

不要像传统 agent 项目那样为了“完成 TODO”继续跑。

---

# 12. 最终 V0.3 应该回答的，不是一个问题，而是四层

## Layer A

清代官僚和功名获得者的家族政治—教育资本究竟有多深？

## Layer B

传统父—祖—曾祖三代指标漏掉了多少旁系精英嵌入？

## Layer C

相似进入资格的人，家庭资本差异是否对应不同职业路径？

## Layer D

这种关系在：

```text
功名类型
地域
时期
中央/地方
正式/署理/候补
```

之间如何变化？

如果完成到这里，这个项目就会从原来的：

**“高官 vs 地方官寒门比例 micro-study”**

变成：

**“清代官僚精英形成、家族再生产与职业流动的可复现 cohort-and-kin-network study”。**
