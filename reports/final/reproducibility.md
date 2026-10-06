# 复现信息（v0.1-one-day）

- 生成时间（UTC）：2026-09-14T01:01:46Z
- 记录生成时的 git commit：`d0dba1f`（main）；生成后工作区仍有改动，即本文件所在的打包提交
- Python：3.12.14（macOS-26.5-arm64-arm-64bit）
- 测试：73 tests, all passed（exit 0）
- LLM 调用：690 次，累计 **0.947 元**（模型 `deepseek-flash`，官方 API）

## 原始数据（只读，不入 Git）

| 数据集 | release | 日期 | 文件 | SHA256（前 16 位） | 校验 |
| --- | --- | --- | --- | --- | --- |
| cbdb_sqlite_20260912 | cbdb_20260912 | 2026-09-12 | `data/raw/cbdb/cbdb_20260912.zip` | `0f9ff7b93cd6b8b6` | True |
| cbdb_sqlite_20260912 | cbdb_20260912 | 2026-09-12 | `data/raw/cbdb/cbdb_20260912.sqlite3` | `604a4ce0872776a9` | True |
| cbdb_sqlite_20260912 | cbdb_20260912 | 2026-09-12 | `data/raw/cbdb/cbdb_20260912.json` | `bd215bc3033ab6db` | None |
| cbdb_sqlite_20260912 | cbdb_20260912 | 2026-09-12 | `data/raw/cbdb/cbdb_users_guide_2025-11-04_wayback.pdf` | `ee222b719232c226` | None |
| cgedq_jsl_public_2026_08_28 | DataSpace@HKUST DOI 10.14711/dataset/E9GKRS, version 24.0 | 2026-08-31 | `data/raw/cgeq/cgedq_jsl_public_1760-1912_personid_2026-08-28.tab` | `92d74b4dbca2de3a` | True |
| cgedq_jsl_public_2026_08_28 | DataSpace@HKUST DOI 10.14711/dataset/E9GKRS, version 24.0 | 2026-08-31 | `data/raw/cgeq/cgedq_jsl_chushen_recodes.tab` | `83860ac33840c22a` | True |
| cgedq_jsl_public_2026_08_28 | DataSpace@HKUST DOI 10.14711/dataset/E9GKRS, version 24.0 | 2026-08-31 | `data/raw/cgeq/cgedq_jsl_province_recodes.tab` | `ed1a30e5fdf741c3` | True |
| cgedq_jsl_public_2026_08_28 | DataSpace@HKUST DOI 10.14711/dataset/E9GKRS, version 24.0 | 2026-08-31 | `data/raw/cgeq/cgedq_jsl_user_guide_v4_2025-07.pdf` | `6da8501a8f859428` | True |
| cgedq_jsl_public_2026_08_28 | DataSpace@HKUST DOI 10.14711/dataset/E9GKRS, version 24.0 | 2026-08-31 | `data/raw/cgeq/cgedq_r_tutorial_v2_2025-06.pdf` | `2900263db5ff9a3e` | True |
| sinica_person_authority | — | — | `（无本地文件）` | `—` | None |
| sinica_qing_office | — | — | `（无本地文件）` | `—` | None |
| qingshigao_machine_readable | — | — | `（无本地文件）` | `—` | None |

完整 manifest 见 `data/raw/manifest.json`，逐文件校验值见 `reports/final/reproducibility.json`。

## 派生数据

| 文件 | 行数 | SHA256（前 16 位） |
| --- | --- | --- |
| `data/processed/officials_master.parquet` | 39,988 | `d72d9ff2029cd867` |
| `data/processed/appointments.parquet` | 56,346 | `93274ad37d3ded09` |
| `data/processed/family_structured.parquet` | 119,964 | `8c7edb8702317446` |
| `data/processed/family_enriched.parquet` | 963 | `58b279b61a62c708` |
| `data/processed/family_final.parquet` | 119,964 | `c419fa5d8c7df14f` |
| `data/processed/person_indicators.parquet` | 39,988 | `9e923d658e879a41` |

## 图表

| 文件 | 字节 | SHA256（前 16 位） |
| --- | --- | --- |
| `figures/p06_coverage_by_tier.pdf` | 34,280 | `11c3acf63f062fe5` |
| `figures/p06_coverage_by_tier.png` | 59,681 | `3cd6124b2f6cc3a5` |
| `figures/p06_prevalence_by_tier.pdf` | 33,900 | `8d59f4e96f7d408e` |
| `figures/p06_prevalence_by_tier.png` | 59,935 | `eef0f0619a2c659c` |
| `figures/p06_elite_generations.pdf` | 23,399 | `0dca350496f1a9ec` |
| `figures/p06_elite_generations.png` | 35,908 | `f170cdd3028873b9` |
| `figures/p06_cohort_trend_abc.pdf` | 24,793 | `26679cf1c1251ae1` |
| `figures/p06_cohort_trend_abc.png` | 68,688 | `1cc22de01e5fc80f` |

## 复现命令（按顺序）

```bash
uv sync
uv run python -m qing_elite.build_universe
uv run python -m qing_elite.build_family
uv run python -m qing_elite.llm.harvest --volumes 250-399
uv run python -m qing_elite.llm.harvest --build-index
uv run python -m qing_elite.llm.enrich --plan
uv run python -m qing_elite.llm.enrich --concurrency 6
uv run python -m qing_elite.build_analysis
uv run python -m qing_elite.build_audit [--replicate]
uv run python -m qing_elite.build_final
uv run pytest
```
