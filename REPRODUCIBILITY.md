# REPRODUCIBILITY — v0.3 cohort-and-kin-network

- 生成时间：2026-09-28T00:46:38+00:00
- 代码：branch `upgrade/v0.3-cohort-kin-network` @ `021f76e`（基线 tag `v0.2-u03-frozen-20260927`）
- 环境：macOS-26.5-arm64-arm-64bit / Python 3.12.14 / `uv.lock`

## 冻结项

- contracts：{'research_contract': 'v0.3.0', 'claim_level': 'descriptive_association', 'ontology_version': 'relations-v2', 'review_policy': 'review-policy-v1', 'rank_table_verification_status': 'pending'}
- seeds：{'pilot_frame_seed': 20260927, 'linkage_seed': 20260927}
- thresholds：{"linkage_auto_accept_precision": 0.99, "linkage_ml_threshold": 0.35, "review_budgets": {"manual_link_new_target": 300, "manual_assertion_review_target": 200, "unit": "absolute_count", "on_exceeded": "stage_fail_not_expand"}, "extraction_gates": {"min_precision": 0.7, "min_recall": 0.5, "min_kin_name_accuracy": 0.8, "max_ocr_failure_rate": 0.2, "min_attribute_accuracy": 0.7, "action_on_fail": "drop_source", "action_on_attribute_fail": "keep_identity_only"}}
- models：{"extraction": "deepseek-flash (official API, thinking disabled, JSON output)", "ocr": "PaddleOCR-VL-1.6 (official service)", "coding": "opencode-go/deepseek-v4.1-flash", "prompt_versions": ["p04-family-v3 (v0.1 lineage)", "u05r-abstain-v1"]}
- upstream linkage code：`bruceyyu/ML-Chinese-record-linkage` @ `79fec7e5b7c16a9bb852b00c19dd4f969881d849`（CC BY-NC 4.0 (LICENSE.txt); README badge says CC BY-SA 4.0 — treat the stricter）

## 输入（原始数据，本地只读、不入 Git）

| file | sha256 | release |
| --- | --- | --- |
| `data/raw/cbdb/cbdb_20260926.zip` | `663f00a0aff375c3…` | cbdb_20260926 |
| `data/raw/cbdb/cbdb_20260926.sqlite3` | `3b8809b2e57d2ab8…` | cbdb_20260926 |
| `data/raw/tongguanlu/tongguanlu_jiangnan_lingshu_jinkuiguang.pdf` | `fe0716c6ff1ec9dd…` | WZLib-DB-143494 |
| `data/raw/cgeq/cgedq_jsl_public_1760-1912_personid_2026-08-28.tab` | `92d74b4dbca2de3a…` | CGED-Q JSL public release, Dataverse file 14184134 (2026-08-28, with person_id) |
| `data/raw/cgeq/cgedq_jsl_chushen_recodes.tab` | `49b7e7f7bc93fb49…` | Dataverse file 5708952 (Chushen Recodes) |
| `data/raw/cgeq/cgedq_jsl_province_recodes.tab` | `89905382fbd7d5cc…` | Dataverse file 5708951 (Province of Origin Recodes) |
| `data/raw/cgeq/cgedq_user_guide_v4_2025.pdf` | `6da8501a8f859428…` | Dataverse file 11704482 (用户指南 v4, 2025-07) |

## 产物

| artifact | rows | sha256 |
| --- | --- | --- |
| `analysis_coverage.parquet` | 6 | `cd01856eed1c3f94…` |
| `analysis_outcomes.parquet` | 4 | `64c25a233526db21…` |
| `analysis_sample.parquet` | 1,204 | `44e7b3b63edda787…` |
| `analysis_sensitivity.parquet` | 6 | `3aa6648903db2ce2…` |
| `career_events.parquet` | 148,288 | `024eee86f4f44727…` |
| `career_offices.parquet` | 15,725 | `b0994142d5bebf9a…` |
| `career_outcomes.parquet` | 53,701 | `07308c644a4e38a9…` |
| `career_tier_crosswalk.parquet` | 53,701 | `2446636ea0ca6ead…` |
| `career_validation.parquet` | 412 | `d89a1fe87c430dc6…` |
| `credentials.parquet` | 523 | `7c6a789a20864e41…` |
| `entity_links.parquet` | 419 | `38d1c3a09380d9fc…` |
| `evidence_assertions.parquet` | 2,775 | `f2d0c269f03a4532…` |
| `extension_comparisons.parquet` | 30 | `cc1266c444d0f5b4…` |
| `extension_early_qing.parquet` | 1,179 | `0a235dcc04ee0e28…` |
| `extension_variable_mapping.parquet` | 12 | `d59d5a0d84be4990…` |
| `kin_coverage_by_cohort.parquet` | 4 | `007306ce597119a5…` |
| `kin_coverage_by_source.parquet` | 1 | `e9bc4eff4c28804c…` |
| `kin_edges.parquet` | 768 | `9d00f5e68b12cd0a…` |
| `kin_indicators.parquet` | 300 | `30702ab53830c7ae…` |
| `kin_validation.parquet` | 7 | `fd794f1e8f3082a0…` |
| `linkage_crosswalk.parquet` | 12,682 | `ef67edbf5e3115c9…` |
| `linkage_features.parquet` | 419 | `2719c774c43ef33c…` |
| `linkage_review_queue.parquet` | 102 | `8b3e5db3717403f0…` |
| `linkage_risk.parquet` | 419 | `62ad29f0b841af68…` |
| `offices.parquet` | 417 | `347c7347334d53dd…` |
| `persons.parquet` | 1,046 | `75aeb7cd40245753…` |

## 复现命令

```bash
uv sync
# 私有历史副本可运行 scripts/recover_v02_artifacts.sh 恢复 v0.2 gold / crosswalk；
# GitHub 公开无历史快照需从授权原始数据本地重建该基线，不能直接恢复历史 blob。
uv run python -m qing_elite.v03.pilot.frame   # 需要本地 CBDB sqlite
uv run python -m qing_elite.v03.pilot.run
uv run python -m qing_elite.v03.linkage.run
uv run python -m qing_elite.v03.kin.run
uv run python -m qing_elite.v03.career.run
uv run python -m qing_elite.v03.analysis.run
uv run python -m qing_elite.v03.extension.run
uv run python -m qing_elite.v03.release --build-docs
snakemake -n --snakefile workflow/Snakefile   # DAG dry-run
uv run python -m qing_elite.v03.release      # release gate
```

## 已知不可复现 / 受外部条件约束

1. PaddleOCR 与 DeepSeek 服务端行为可能变化；每次调用（OCR token、LLM token/成本）已落盘。
2. Semantic Scholar / OpenAlex 在 U04R 当日限流/暂停（记录于 `config/v03/literature.yaml`）。
3. 官品表（`config/v03/office_ranks.yaml`）是策展表且 `verification_status=pending`。
4. 两个 u0x 测试依赖本地 `data/processed/appointments.parquet`（公开仓库不含该文件）。
