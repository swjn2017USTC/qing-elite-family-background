# V0.3 DATA MODEL

- 版本：`v0.3.0`（阶段 U03R，schema/test skeleton）
- 实现在 `src/qing_elite/v03/contracts.py`（Pandera 显式 schema + 跨表校验）
- 本阶段**只定义与校验**，不生产数据；业务逻辑在 U05R–U09R 落地。

---

## 1. 十条表

| 表 | 主键 | 粒度 | 作用 |
| --- | --- | --- | --- |
| `persons` | `person_id` | 人 | 规范人物实体（跨源归并后的 canonical person） |
| `source_documents` | `document_id` | 文档/记录 | 来源登记（含 release、权利、access type、hash） |
| `career_events` | `event_id` | 任职事件 | 纵向职业序列（U08R 产出） |
| `kin_edges` | `edge_id` | 亲属边 | 父系直系 + 旁系 + 母系/姻亲（U07R 产出） |
| `credentials` | `credential_id` | 功名 | 科举/捐纳/恩荫等资格（U05R/U07R 产出） |
| `offices` | `office_id` | 官职本体项 | 官称 → 品级/层级/中央地方/实职随时间（U04R 产出） |
| `evidence_assertions` | `assertion_id` | 断言 | **唯一的四态证据账本** |
| `entity_links` | `link_id` | 配对 | 去重/跨库/传记归属的决定与分数 |
| `review_queue` | `item_id` | 待办 | risk 分层后的自动/人工路由 |
| `risk_scores` | `risk_id` | 打分 | 每条待核对象的 risk 分量与档位 |

全部表 `strict=True`：**多一列即失败**，所以"顺手加个字段"必须改 schema。

---

## 2. 核心不变式

### 2.1 `unknown != negative`（继承 v0.2，并收紧）

- 四态（`positive / explicit_negative / unknown / conflict`）**只存在于 `evidence_assertions`**。
- 明细表（`career_events`、`kin_edges`、`credentials`）只允许 `positive` 与 `conflict`：
  **一条未被史料记载的亲属关系/官职不是一行 0，而是没有这一行。**
  由此，"没有记载"永远不会被下游误读为"没有任官"。
- `unknown` 与 `conflict` 断言**不得携带任何值**（`value_raw`/`value_normalized`/`quote`/offsets 全为空），`conflict` 必须有 `conflict_group_id`。
- `positive` / `explicit_negative` 必须有 source + locator + 逐字 quote。
- 否定事实（寒素、世业农、未仕）只作为 `explicit_negative` 断言挂在 `subject_type=person` 上，绝不生成明细行。

### 2.2 每条明细回指一条断言（新增）

`validate_detail_rows_cite_ledger`：`career_events`、`kin_edges`、`credentials` 的每一行都必须引用一条存在、且 `subject_type/subject_id` 与本行一致的 `evidence_assertion_id`，并且两者 `source_document_id` 相同。
→ 任何派生指标都能回落到一条可逐字校验的原文 span。

### 2.3 span 逐字回源

`validate_evidence_spans`：`quote_start:quote_end` 必须与 `quote` 逐字相同；结构化来源（无 `text`）不得带 offset。
（与 v0.2 一致，但键从"槽位"改为 `assertion_id`，支持一字段一 span。）

### 2.4 tier 不是输入变量

`persons` 与 `career_events` 的 schema **没有任何 tier 列**；schema 为 `strict=True`，加进来就会失败。
A1/A2/A3/B/C/D 只存在于 legacy/extension 输出与来源偏差基准中（`V03_RESEARCH_CONTRACT.md` §2）。

---

## 3. 逐表字段

### 3.1 `persons`

`person_id`(PK) · `canonical_name` · `surname` · `given_name` · `name_variants` · `birth_year` · `death_year` · `native_province` · `native_county` · `banner_status` · `cohort_id` · `primary_source`(enum: cbdb/cgedq/standardized_family_source/literature) · `source_person_ids` · `link_status`(enum, 继承 v0.2) · `link_evidence` · `resolution_status` · `primary_eligible`

- 约束：`primary_eligible == (resolution_status == "resolved")`；生卒年有序。
- `banner_status = unknown` 表示"旗籍未知"，**不等于非旗人**。

### 3.2 `source_documents`

`document_id`(PK) · `source_id`（登记键，如 `cbdb`、`cgedq_jsl`、`wikisource_dump`） · `source_release` · `document_type`(enum) · `title` · `volume` · `locator` · `text` · `text_sha256` · `chars` · `rights_status` · `license` · `access_type`(enum，与来源可行性分类一致) · `machine_readable` · `ocr_used` · `url` · `retrieved_at`

- 约束：有 `text` 必须有 `text_sha256` 且 `chars == len(text)`。

### 3.3 `career_events`

`event_id`(PK) · `person_id` · `office_id` · `office_raw` · `office_normalized` · `start_year` · `end_year` · `date_precision` · `reign` · `province` · `jurisdiction` · `rank_label` · `administrative_level` · `central_local` · `appointment_type`(substantive/acting/expectant/honorary/concurrent/unknown) · `selection_method` · `assertion_state`(positive/conflict) · `evidence_assertion_id` · `source_document_id` · `confidence` · `review_status`

- 约束：positive 必须有 `office_raw`；conflict 不得携带已解决的 `office_normalized`/`rank_label`/`administrative_level`；年份有序。

### 3.4 `kin_edges`

`edge_id`(PK) · `ego_person_id` · `alter_person_id`(可空) · `alter_name_raw` · `relation_code`(ontology) · `relation_class`(ontology) · `lineage_side` · `generation_delta` · `edge_origin`(source_explicit/machine_inferred) · `assertion_state` · `evidence_assertion_id` · `source_document_id` · `confidence` · `review_status`

- 约束：`relation_class` 与 `generation_delta` 必须是 `relation_code` 在 `relations.yaml` 中的投影（禁止 parser 自填）；无自环；未解析的 alter 必须保留原文姓名；已解析的同 (`ego`,`alter`,`relation_code`) 不得重复。
- `edge_origin` 区分"史料明示"与"机器推断"，下游指标必须能分别统计。

### 3.5 `credentials`

`credential_id`(PK) · `person_id` · `credential_type`(jinshi/juren/gongsheng/jiansheng/shengyuan/yinsheng/juanna/wuju/other_exam/other_privilege) · `exam_route` · `credential_year` · `rank_in_exam` · `assertion_state` · `evidence_assertion_id` · `source_document_id` · `confidence` · `review_status`

- 约束：positive 必须有具体 `credential_type`。

### 3.6 `offices`（本体表）

`office_id`(PK) · `office_title` · `office_title_variants` · `rank_label` · `rank_class`(1–9) · `rank_side` · `administrative_level` · `central_local` · `authority_type` · `institutional_body` · `substantive_default` · `valid_from_year` · `valid_to_year` · `mapping_status`(matched/unmatched/ambiguous) · `unmapped_reason` · `ontology_version`

- 约束：非 matched 必须有 `unmapped_reason`（"未匹配"永远要给原因，不允许静默降级）；品级与年代区间合法。
- 设计要点：**品级、行政层级、中央/地方、实职/虚衔、署理、兼任、有效年代分列**，不再用一个 `ancestor_high_official` 全包。

### 3.7 `evidence_assertions`（四态账本）

`assertion_id`(PK) · `subject_type`(person/kin_edge/career_event/credential/entity_link) · `subject_id` · `field` · `assertion_state` · `value_raw` · `value_normalized` · `source_document_id` · `source_locator` · `quote` · `quote_start` · `quote_end` · `extractor`(cbdb_data/cgedq_data/rule_parser/ocr_parser/llm_extraction/manual_entry/migration) · `extractor_version` · `extraction_run_id` · `review_status` · `confidence` · `conflict_group_id`

### 3.8 `entity_links`

`link_id`(PK) · `left_kind`/`left_id` · `right_kind`/`right_id` · `link_type`(dedupe/cross_source/biography_attribution/kin_person_match) · `method`(deterministic/ml_chinese_record_linkage/splink/agreement/manual) · `score` · `decision`(auto_accept/grey/reject) · `decision_rule` · `evidence` · `risk_id` · `review_status` · `upstream_commit` · `decided_at`

- 约束：无自环；键唯一；`auto_accept` 必须有分数且不得 pending；`grey` 必须 pending。
- `upstream_commit` 用于登记外部链接代码（如 `ML-Chinese-record-linkage`）的版本。

### 3.9 `review_queue`

`item_id`(PK) · `subject_type` · `subject_id` · `risk_id` · `priority` · `reason_codes` · `route`(auto_accept/machine_adjudicate/human_review) · `status`(open/in_progress/resolved) · `decision` · `rationale` · `reviewer` · `created_at` · `resolved_at`

- 约束：resolved 必须有 decision/rationale/reviewer/resolved_at；未 resolved 不得有 resolved_at；同一 `subject` 只能有一条未 resolved 条目。
- `validate_review_routing`：路由必须等于该 risk 档位的政策路由，`human_review` 仅限 HIGH。

### 3.10 `risk_scores`

`risk_id`(PK) · `subject_type` · `subject_id` · 8 个分量（`linkage_uncertainty`/`ocr_uncertainty`/`parser_disagreement`/`source_conflict`/`rare_relation`/`office_unknown`/`chronology_violation`/`evidence_span_failure`） · `total_score` · `risk_tier`(LOW/MEDIUM/HIGH) · `policy_version` · `computed_at`

- 约束：分量与总分都在 [0,1]；每个 subject 一条；`validate_risk_tiers` 要求 `risk_tier` 与 `config/v03/review_policy.yaml` 的阈值一致。

---

## 4. 跨表校验（`validate_all`）

1. 十张表全部 schema 通过（`strict=True`）；
2. 外键：`career_events/credentials/kin_edges → persons`、`*.source_document_id → source_documents`、`career_events.office_id → offices`、`entity_links/review_queue.risk_id → risk_scores`；
3. `validate_detail_rows_cite_ledger`；
4. `validate_evidence_spans`；
5. `validate_risk_tiers` + `validate_review_routing`。

任一项失败即中止写入正式结果（不落盘半成品）。

---

## 5. 指标与派生的位置

- 表只存**事实与边**；家族资本指标（`direct_3g_*`、`senior_collateral_*`、密度类）与 `*_new_entrant` 是**派生量**，在 U07R 计算，禁止写回事实表。
- 指标定义登记在 `config/v03/relations.yaml:metric_families`，不在 Python 里散落。
- 没有足够亲属可观测性时派生量取 `unknown`，且必须同时输出 `numerator`/`denominator`/`observable_kin_count`。

## 6. 与 v0.2 表的关系

v0.2 的 `entities`/`source_documents`/`source_search_log`/`evidence_assertions`/`review_decisions` schema 保留不动（`data/processed_v02` 只读冻结）。
V0.3 的对应关系：

| v0.2 | v0.3 | 变化 |
| --- | --- | --- |
| `entities` | `persons` | 去掉 tier 输入语义，加入 cohort/primary_source |
| `source_search_log` | 来源级可行性（U04R）+ 需要时重新引入来源级检索日志 | v0.2 是"每人 × 每源"穷尽式日志；V0.3 在 U04R 先判"哪些源真的可用"，检索日志在 U06R 需要证明完成率时再建 |
| `evidence_assertions`（槽位级） | `evidence_assertions`（subject 级） | 从"祖先槽位 × 属性"改为任意 subject + 单字段 span |
| `review_decisions` | `review_queue` + `risk_scores` | 从"人审队列"改为"risk 分层自动/人工路由" |
| 无 | `kin_edges`/`career_events`/`credentials`/`offices`/`entity_links` | V0.3 的新增主干 |
