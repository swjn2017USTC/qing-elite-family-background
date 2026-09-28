# V0.2 POSTMORTEM — v0.1/v0.2 工程与研究设计复盘

- 阶段：V0.3 U03R（研究重置）
- 日期：2026-09-27
- 审查基线：`v0.2-u03-frozen-20260927`（tag → commit `af98ba3`）
- 本文件只做复盘，不修改 v0.1/v0.2 的任何产物。
- 证据均为可复跑命令 + 文件，均标注出处；未执行的检查不写入本文件。

---

## 0. 一页结论

v0.1 造出了一条**可审计的数据流水线**，v0.2 把它升级成**带四态证据与链接门槛的基础设施**，这两部分是真实资产，V0.3 全部继承。

但 v0.1/v0.2 作为**研究设计**是失败的，原因不是工程做得不够，而是**识别结构搭反了**：

```text
v0.1/v0.2： 最终官位 (tier) → 回头找父/祖/曾祖 → 判断"寒门"
V0.3：      家世史料 → 家族资本 (exposure) → 职业轨迹/最终官位 (outcome)
```

前者的"家世"是**从结果倒推、且只在有记载的人身上可见**的量。U03 的 pilot 把它量化到了极限：**D 层 30/30 人拿不到任何祖先材料（`ancestral_information_rate = 0.0`），冻结 frame 内 D 的文档率为 0/400**。也就是说，v0.2 的主对比在测"史料可见度"，不是在测家世结构。

第二个失败是**抽样建立在因变量上**：v0.2 按 tier（A/B/C/D）分层抽样，而 tier 在研究问题里本来是要被解释的结果。用结果分层 → 结果与暴露的关系被抽样设计本身决定。

工程上有三处需要记入 V0.3 的硬约束：阶段门禁此前是散文而非机器可执行；v0.2 的 xfail 复现机制已被"公开仓库清理"降级；一次冻结后的产物删除让基线不再全绿。

---

## 1. 证据基线

| 项 | 值 | 出处 |
| --- | --- | --- |
| 冻结 commit / tag | `af98ba3` / `v0.2-u03-frozen-20260927` | `git rev-parse v0.2-u03-frozen-20260927` |
| v0.1 测试数 | 73 项全通过 | `reports/upgrade/U00_BASELINE_AUDIT.md` §5 |
| v0.2 U03 测试数 | 137 项 | `reports/upgrade/U03.md` §2.7 |
| 当前冻结基线测试 | `2 failed, 122 passed, 6 skipped, 8 xfailed` | `audit/v03/u03r_baseline_pytest.txt` |
| LLM 调用（v0.1） | 690 次 / 0.947 RMB，无密钥入库 | `reports/final/reproducibility.json`、`audit/llm_calls/calls.jsonl` |
| v0.2 研究总体 | 39,988 → primary 13,345（33.4%） | `reports/upgrade/U02.md` §4 |
| v0.2 primary 中祖先身份可考 | 246 人（v0.1 同口径 698） | 同上 |
| U03 pilot（每层 30 人） | required-source completion 100%；D 层 `ancestral_information_rate = 0.0` | `audit/v03/u03_pilot_metrics.csv`、`audit/v03/u03_gate.json` |

---

## 2. 研究设计复盘

### 2.1 致命项：estimand 是倒置的

v0.1/v0.2 的主量是 `P(documented ancestor office = 1 | tier)`（`config/v02/sample_design.json:estimand`）。
它把**最终官位当自变量**、把**父祖曾祖的记载当因变量**，于是"家世差异"实际上混入了三件事：

1. 家世的真实分布；
2. 该层人物被立传、被录入 CBDB 的概率；
3. 家世材料本身被保存的概率（父 > 祖 > 曾祖）。

三者无法在现有数据里分开，因此任何方向性解读都不可信。**这正是 V0.3 要把 exposure/outcome 换位的原因。**

### 2.2 致命项：D 层对比事实上不可估

| 口径 | A | B | C | D | 出处 |
| --- | --- | --- | --- | --- | --- |
| frame 文档率 | 18.8% | 4.5% | 7.0% | **0.0%** | `config/v02/sample_design.json:observed_documented_official_rate` |
| "祖先身份可考"子集条件率 | 47.1%（n=34） | 20.0%（n=45） | 34.1%（n=41） | **不可估（n=0）** | 同上 `observed_ascertained` |
| pilot 祖先信息率 | 56.7% | 63.3% | 73.3% | **0.0%** | `audit/v03/u03_pilot_metrics.csv` |

D 层在 frame 内 400 人**全部**没有祖先前史材料；`u03_gate.json` 里 all-source 口径仍为 FAIL（Sinica 端点不可机器查询）。
**结论：v0.2 的 A/B/C vs D 家世对比在证据上不成立，不是"需要更多补证"，而是这个对比本身在测史料可见度。**

### 2.3 致命项：在因变量上抽样

`config/v02/sample_design.json` 按 tier 分层（A 全收 85、B 200/531、C 200/398、D 400/12,331）。
tier 是 V0.3 的 outcome。用 outcome 分层后，层间差异是**抽样设计定义出来的比例**，不是总体比例；加权只能修正层内抽样比，不能修正"按结果选层"本身。
V0.3 因此改为按 **source × cohort × region × credential** 抽样（`config/v03/research.yaml:sampling`）。

### 2.4 严重项：分位数/分母的口径漂移

同一份报告里同时存在三种不同分母的"率"：documentation rate（frame 分母）、conditional rate（身份可考分母）、v0.1 的 prevalence 口径。
`audit/v03/u03_power_cost.csv` 把两套参照率并列时差异极大（A 层 0.55 vs 0.188，D 层 0.555 vs 0.0）——同一个"寒门率"在两套口径下含义完全不同。
V0.3 的对策是硬性规定：**每个数字必须带 numerator / denominator / coverage / unknown**（`research.yaml:reporting`）。

### 2.5 严重项：selection 与 MNAR 被写进结论的风险

v0.1 摘要用"有家世记录的选择性子样本"作对比（A 55% vs B/C 14–30% vs D 55%），并在 README 里承认不能解释为家世结构差异。
问题在于：**记录是否存在的概率与官位高度强相关**，属典型 MNAR。任何只报告条件率、不报告 frame 文档率的表格都会诱导因果化误读。
V0.3 的对策：先报来源完成率与可观测性，再报结果；条件量必须与 frame 分母并列；禁止把 unknown 插补成 0。

### 2.6 中等项：验证不独立

- `audit/manual_validation.csv` 40/40 `human_review_status=pending`，即 v0.2 的抽取准确率**从未**经过人工确认（U00-02 复现）。
- U02 的 linkage gold 是**规则锚定**的：正样本由"姓名+籍贯+功名一致"定义，而 Splink 的比较向量也含 province/degree，held-out precision=1.0 是**一致性检查**而非独立验证（`reports/upgrade/U02.md` §5.2）。
- U03 pilot 的"人工分钟数"是**假设值**（0.5 min/人），不是实测（`U03.md` §6.5）。

V0.3 的对策：validation 必须由独立来源或人工盲审构成；risk-based review 记录真实人工成本（`V03_AUTOMATION_POLICY.md`）。

### 2.7 中等项：v0.2 的"寒门"概念没有落到可观测的定义

v0.2 引入了 `documented_commoner_explicit`（必须有明确否定/寒素证据），这是正确的方向，但 v0.2 全程 `explicit_negative = 0`（`reports/upgrade/U01.md` §3），所以该指标从未有非空取值。
V0.3 的对策：把"新进者"拆成 `direct_line_new_entrant` 与 `extended_family_new_entrant`，且仅在 observability 足够时给 true/false，否则 `unknown`。

---

## 3. 工程复盘

### 3.1 真实资产（V0.3 直接继承）

1. **冻结来源 + hash 登记**：`data/raw/manifest.json`、`audit/v02/u00_artifact_hashes.csv`（46 项 hash 全部 match）。
2. **证据四态与 schema 强制**：`src/qing_elite/v02/contracts.py`，17 条负向测试保证"无来源 positive""unknown 带值""错误 span""重复 key"都会失败（`reports/upgrade/U01.md` §8）。
3. **LLM 调用审计**：690 次调用逐条落 `audit/llm_calls/calls.jsonl`，含 token 与成本，无密钥（`METHODS.md` §5）。
4. **链接不再靠唯一姓名**：`unique_name` 判据被删除，medium 链接整体退出 primary（`reports/upgrade/U02.md` §6.1–6.2）。
5. **一篇传记只能供一个实体**：`biography_links.parquet`，`biographies_double_merged = 0`。

### 3.2 缺陷项

**E1 — 冻结后删除产物，基线不再全绿（当前 HEAD 的实际状态）。**
commit `af98ba3`（"Prepare public research repository"）删除了 `data/processed/*.parquet` 与 `data/processed_v02/*.parquet` 的跟踪。
后果：`src/qing_elite/v02/linkage.py:_office_lookup()` 读 `data/processed/appointments.parquet`，两个 U02 测试直接 FileNotFoundError：

```
tests/test_u02_linkage.py::test_biography_is_merged_only_when_corroborated_and_unique
tests/test_u02_linkage.py::test_biography_without_corroboration_is_quarantined
E  FileNotFoundError: data/processed/appointments.parquet
```

同一文件里的另外 3 个测试有 "v0.2 tables missing" 的 skip 保护（`-rs` 输出显示 6 skipped），这两个没有。
→ **V0.3 命令**：任何"清理仓库"的提交都必须重跑基线套件；"测试全绿"不能靠记忆，只能靠命令。

**E2 — xfail-strict 复现机制已被环境污染。**
U00 的 8 个 P0 复现用例用 `@pytest.mark.xfail(strict=True)`：契约未满足时算 XFAIL，一旦满足就 XPASS 变红。
现在 `--runxfail` 显示其中 **2 条已经不再复现缺陷本身**：

```
tests/test_u00_defect_repro.py:83  FileNotFoundError: data/processed/person_indicators.parquet
tests/test_u00_defect_repro.py:100 FileNotFoundError: data/processed/family_enriched.parquet
```

即 U00-01 与 U00-04 的 XFAIL 现在由**缺文件**触发，而不是由缺陷触发——strict-xfail 把环境错误静默吃掉了。其余 6 条仍然复现真实缺陷（"40/40 pending"、"tautology check"、"no release_gate entry point"、"only 4 figures" 等）。
→ **V0.3 命令**：xfail 只能用于"实现错误"，不能用于"数据缺失"；缺少输入时测试必须 skip 并带原因。

**E3 — 阶段门禁长期是散文。**
`reports/checkpoints/` 没有 `P08.md`；`python -m qing_elite.release_gate` 至冻结基线仍不存在（U00-07 复现："no python -m qing_elite.release_gate entry point exists"）；P07 已标注"張百齡 conflict 待裁决"却仍然发布（`reports/upgrade/U00_BASELINE_AUDIT.md` §7）。
v0.2 直到 U03 才第一次产出机器 gate（`audit/v03/u03_gate.json`）。
→ **V0.3 命令**：每个阶段从 U03R 起必须有 `audit/v03/*_gate.json`，并带非零退出码。

**E4 — 恒等式审计。**
v0.1 的 banner/province unknown 检查分别是 `eq("unknown") & ne("unknown")` 与 `isna().sum() - isna().sum()`，永真（U00-06）。V0.3 的契约检查必须是"注入错误会失败"（`tests/test_v03_contracts.py` 全部负向）。

**E5 — 手工串联的流水线。**
v0.1/v0.2 的复现是一条 `uv run python -m qing_elite.X` 手抄命令链（`METHODS.md` §6），没有 DAG、没有原子写入、没有依赖 hash。
→ V0.3：模块成熟一个接入一个（U11R 完成 Snakemake），不重写已完成模块。

**E6 — 与上游撞车的工作。**
v0.2 花了整阶段做 CGED-Q 内部 dedupe（13,344 对候选、概率路径阈值 1.0 产出 0 次合并），而 2026-08-28 的 CGED-Q release 已提供官方 `person_id`。
→ V0.3：官方 person_id 优先，旧 dedupe 只保留为对比基线。

---

## 4. 对 V0.3 的直接约束（可检查）

| 编号 | 约束 | 落地位置 |
| --- | --- | --- |
| C1 | exposure = 家族资本，outcome = 职业轨迹/最高官位；tier 只作 legacy/extension outcome | `config/v03/research.yaml`、`V03_RESEARCH_CONTRACT.md` |
| C2 | 抽样不得按 outcome 分层；frame 由 source × cohort × region × credential 定义 | `config/v03/research.yaml:sampling` |
| C3 | 每个数字必须带 n / denominator / coverage / unknown | `research.yaml:reporting` |
| C4 | `unknown != negative`：四态只存在于 evidence ledger，明细表只记录 positive/conflict，未记录 = 无行 | `src/qing_elite/v03/contracts.py` |
| C5 | 每条明细必须回指一条 assertion，且能逐字回源 | `validate_detail_rows_cite_ledger`、`validate_evidence_spans` |
| C6 | 人工审核按 risk 分层、绝对预算（link ≤300、assertion ≤200），禁止固定比例抽查 | `config/v03/review_policy.yaml` |
| C7 | 阶段门禁必须机器可执行且非零退出 | `src/qing_elite/v03/gate.py` |
| C8 | 缺少输入时测试必须 skip 并说明原因，不得借 xfail 掩盖 | 本文档 E2；U04R 起在测试规范中执行 |
| C9 | 不放宽为"唯一姓名"式链接；auto-accept 需 precision ≥0.99，否则提高 abstention | `config/v03/review_policy.yaml:quality_gates` |
| C10 | 不动 v0.1/v0.2 冻结产物；冻结后的仓库整理必须重跑基线 | `reports/upgrade_v03/U03R.md` gate 项 `v02_untouched` |

---

## 5. 复现命令

```bash
# 冻结基线（v0.2 全部测试，含 2 条环境失败）
uv run pytest -o addopts= --ignore=tests/test_v03_contracts.py \
    --ignore=tests/test_v03_design_contract.py
# -> 2 failed (data/processed/appointments.parquet 缺失), 122 passed, 6 skipped, 8 xfailed

# 8 条 P0 复现用例的真实失败原因（其中 2 条已是 FileNotFoundError）
uv run pytest tests/test_u00_defect_repro.py -o addopts= --runxfail -q

# U03 门槛与 D 层证据
cat audit/v03/u03_gate.json audit/v03/u03_pilot_metrics.csv
```
