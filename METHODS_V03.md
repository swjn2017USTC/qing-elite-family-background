# METHODS_V03 — cohort-and-kin-network

## 1. 研究问题与地位

- 主问题：在相似的科举/制度进入资格下，家庭政治—教育资本以及更广泛的宗族精英嵌入， 与一个人的职业轨迹和最终官位之间有怎样的描述性关联？
- claim level：`descriptive_association`；identification：`none`
- 本版**不做因果识别**，只报告 association / selection / composition。

## 2. 数据来源（冻结、只读、不入 Git）

| 来源 | release | 用途 |
| --- | --- | --- |
| CBDB | `cbdb_20260926` | 人物、亲属、功名、任官 |
| CGED-Q JSL | Dataverse `doi:10.7910/DVN/GMQWVZ`（CC0） | 职业面板（季度名册 → 任命段） |
| 公版同官录扫描件 | Wikimedia Commons, PD-old-100-expired | OCR 路径验证（U05R 降级为 identity-only） |
| 《清史稿》窗口 / 现代文献 | 见 `sources/literature/registry` | 文本补缺与史学定位 |

## 3. 流水线

```text
acquire → normalize → ocr → extract → verify → entity_link → kin_graph
        → career_panel → indicators → analysis → figures → report → release_gate
```

DAG 定义在 `workflow/Snakefile`，每个 rule 调用既有的 Python 模块入口（不为 DAG 重写实现）。

## 4. 关键方法决策

1. **exposure / outcome 换位**：家庭资本是 exposure，职业轨迹是 outcome；tier 只作 legacy/extension。
2. **抽样不按 outcome**：frame 由 source × cohort × region × credential 定义（seed `20260927`）。
3. **四态证据**：`positive / explicit_negative / unknown / conflict` 只存在于 evidence ledger；明细表只记 positive/conflict，未记载 = 无行（`unknown != 0`）。
4. **官方 id 优先**：CGED-Q 内部去重使用官方 `person_id`；v0.2 dedupe 降为 audit comparison。
5. **链接门槛**：auto-accept 要求 held-out precision ≥ 0.99；达不到则提高 abstention。
6. **人工只处理高风险残差**：risk 分层路由，无固定比例抽查；预算为绝对条数 {'manual_link_new_target': 300, 'manual_assertion_review_target': 200, 'unit': 'absolute_count', 'on_exceeded': 'stage_fail_not_expand'}。
7. **品级是策展表**：`config/v03/office_ranks.yaml`，`verification_status=pending`。

## 5. 数据规模（自动读取）

| artifact | rows |
| --- | --- |
| `analysis_coverage.parquet` | 6 |
| `analysis_outcomes.parquet` | 4 |
| `analysis_sample.parquet` | 1,204 |
| `analysis_sensitivity.parquet` | 6 |
| `career_events.parquet` | 148,288 |
| `career_offices.parquet` | 15,725 |
| `career_outcomes.parquet` | 53,701 |
| `career_tier_crosswalk.parquet` | 53,701 |
| `career_validation.parquet` | 412 |
| `credentials.parquet` | 523 |
| `entity_links.parquet` | 419 |
| `evidence_assertions.parquet` | 2,775 |
| `extension_comparisons.parquet` | 30 |
| `extension_early_qing.parquet` | 1,179 |
| `extension_variable_mapping.parquet` | 12 |
| `kin_coverage_by_cohort.parquet` | 4 |
| `kin_coverage_by_source.parquet` | 1 |
| `kin_edges.parquet` | 768 |
| `kin_indicators.parquet` | 300 |
| `kin_validation.parquet` | 7 |
| `linkage_crosswalk.parquet` | 12,682 |
| `linkage_features.parquet` | 419 |
| `linkage_review_queue.parquet` | 102 |
| `linkage_risk.parquet` | 419 |
| `offices.parquet` | 417 |
| `persons.parquet` | 1,046 |
