# METHODS — 清前中期官僚精英家世与仕进 micro-study（v0.1-one-day）

本文件说明**数据从哪来、代码怎么跑、结论如何产生、如何复现**。所有数字均可由本文命令重新生成；
逐文件校验值与调用清单见 `reports/final/reproducibility.json`（人类可读版 `reproducibility.md`）。

---

## 1. 研究问题与范围

- 时间范围：1644–1820；**地方基层对照仅用 CGED-Q JSL 的 1760–1798**。
- 首轮只回答描述性问题：家庭政治—教育资本是否随官位接近权力中心而变化，以及这种梯度在旗籍与非旗汉人之间是否有差异。
- **不做因果识别**；报告一律使用 association / selection / composition 表述。

## 2. 数据源（固定 release，只读）

| 数据源 | release | 获取方式 | 校验 |
| --- | --- | --- | --- |
| CBDB（中国历代人物传记资料库）SQLite | `cbdb_20260912`（2026-09-12） | Hugging Face `cbdb/cbdb-sqlite`（CBDB 项目自述的官方渠道） | 解压后 SQLite SHA256 = 上游公布值；zip SHA256 = HF LFS oid |
| CGED-Q JSL 公开版 | DataSpace@HKUST DOI `10.14711/dataset/E9GKRS` v24.0（2026-08-31），CC0 | Dataverse API（`?format=original` 取原始字节） | 与数据集登记 MD5 一致 |
| 《清史稿》正文 | 中文维基文库（公版 PD-old） | MediaWiki API，**按卷**抓取 150 卷（卷250–399） | 本地缓存 + 每卷文本入库 |
| 中研院人名權威—人物傳記資料庫 | 在线库（无 release 号） | 脚本化检索（`ACTION=TQ`），**仅用于 A 层核验**，限速 1.5 s/次 | 逐人记录结果与原始字段 |

原始文件保存在 `data/raw/`，**不入 Git**；版本、大小、SHA256/MD5、验证结论登记在 `data/raw/manifest.json`。

## 3. 流水线（阶段与入口）

| 阶段 | 命令 | 产物 |
| --- | --- | --- |
| P02 官员总体 | `uv run python -m qing_elite.build_universe` | `appointments.parquet`、`officials_master.parquet`、13 张 p02 审计表 |
| P03 结构化家世 | `uv run python -m qing_elite.build_family` | `family_structured.parquet`、p03 覆盖/需求/成本表 |
| P05 文本收割 | `uv run python -m qing_elite.llm.harvest --volumes 250-399` | `biographies.parquet`（1,118 篇）、`harvest_index.parquet`（338 人） |
| P05 enrichment | `uv run python -m qing_elite.llm.enrich --plan` → `--concurrency 6` | `family_enriched.parquet`、`cost_report.csv`、`failed_cases.csv` |
| P06 分类统计 | `uv run python -m qing_elite.build_analysis` | `family_final.parquet`、`person_indicators.parquet`、p06 表与图（PNG+PDF） |
| P07 审计 | `uv run python -m qing_elite.build_audit [--replicate]` | `audit/p07_*.csv`、`audit/a_layer_verification.csv` |
| P08 打包 | `uv run python -m qing_elite.build_final` | `reports/final/{tables,figures,reproducibility.*}` |

辅助：`uv run pytest`（73 项行为测试）。

## 4. 关键方法决策

1. **tier 判定**：只认实质任职；加衔/赠官/军机章京/上书房/盛京五部/1901 后新部院一律排除，逐条留痕（`p02_office_tier_map.csv`）。
2. **季度折叠（CGED-Q）**：spell = 同一人同一核心官名在**出版期序列上相邻**的连续观测；跨期第二段算新任期。
   清史稿列传通常只追述一代，故祖父/曾祖的文本可得性天然低于父。
3. **人物链接（entity linkage）**：优先明确 ID（CBDB `c_personid`、`KIN_DATA` 亲属 ID、`MERGED_PERSON_DATA` 归一）；
   RapidFuzz 仅用于**候选召回**（`match_status` 永不自动置为 accepted）；JSL↔CBDB 链接规则为"姓名唯一 + 籍贯佐证"（high）。
4. **LLM 只抽事实**：普通抽取 `thinking=disabled` + JSON output；仅规则法冲突时升级 `thinking=enabled, reasoning_effort=low`；
   送 prompt 前剥离 wiki 标记；**证据不逐字命中即置 unknown**。
5. **成本护栏**：运行前输出 token/RMB 计划；软预算 20 元告警、硬预算 30 元停止新请求；全部调用写审计（含缓存命中与成本）。
6. **统计**：只做覆盖率、比例（Wilson 95% 区间）、分组交叉表与 ≤3 个分类项的 logit；模型仅作诊断。

## 5. LLM 使用记录（可审计）

| 项 | 值 |
| --- | --- |
| 渠道 | 本地 Python → `os.environ["DEEPSEEK_API_KEY"]` → `https://api.deepseek.com` |
| 模型 | `deepseek-flash`（官方现名，DeepSeek-V4.1-Flash） |
| 禁止项 | 未使用 OMP 内 `deepseek` provider 生产任何研究数据；未安装/调用独立 opencode CLI |
| 调用总数 / 花费 | 690 次 / **0.947 元**（v1 44 次、v2 125 次、v3 521 次） |
| 记录字段 | call_id、person_uid、task_type、model、thinking_mode、reasoning_effort、prompt_version、source_ids、input_sha256、input/cached/output tokens、estimated_cost_rmb、status、retry_count、latency_ms、timestamp（**无密钥、无 auth 头**） |

## 6. 复现步骤

```bash
uv sync
# 原始数据：按 data/raw/manifest.json 的 URL 下载并核对 SHA256/MD5（数据源为只读）
uv run python -m qing_elite.build_universe
uv run python -m qing_elite.build_family
uv run python -m qing_elite.llm.harvest --volumes 250-399      # 需网络（维基文库，限速+缓存）
uv run python -m qing_elite.llm.harvest --build-index
uv run python -m qing_elite.llm.enrich --plan
uv run python -m qing_elite.llm.enrich --concurrency 6         # 需 DEEPSEEK_API_KEY；约 0.64 元
uv run python -m qing_elite.build_analysis
uv run python -m qing_elite.build_audit                        # 确定性审计（加 --replicate 再花约 0.02 元）
uv run python -m qing_elite.build_final
uv run pytest
```

- 确定性：P02/P03/P06/P07/P08 全部由本地数据 + 代码决定；P05 的文本收割与 LLM 调用受网络影响，但**中间结果全部落盘**（`data/interim/`），重跑会命中缓存。
- 环境：Python 3.12（`uv` 管理，版本锁定在 `uv.lock`）；关键依赖版本见 `reproducibility.json`。
- 运行时间参考：P02 ≈ 7 分钟、P03 ≈ 20 秒、收割 150 卷 ≈ 9 分钟、enrichment 321 人 ≈ 2 分钟、分析与审计各 ≈ 20 秒 / 3 秒。

## 7. 已知不可复现/受外部条件约束的部分

1. 维基文库文本随时可能被编辑，因此**每卷文本已缓存**并记录来源（`source_ids` = 卷次#人名）；审计中的一致性检查以此缓存为准。
2. 中研院两库无 release 号，A 层核验结果已并入 `audit/a_layer_verification.csv`（含查询时间与原始字段）。
3. LLM 采样：`temperature=0`，但供应商侧实现可能变化；生产结果与审计记录均已冻结在 `data/interim/enrich/done.jsonl` 与 `audit/llm_calls/calls.jsonl`。
