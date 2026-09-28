# U00 — 冻结 v0.1 基线与缺陷复现

- 阶段：U00（冻结与复现，不修复）
- 日期：2026-09-14
- 基线：tag `v0.1-one-day` → commit `a60274e`（`fa967607b60f4cd75f7341ee6c5a9464b029d658`）
- 升级分支：`upgrade/v0.2-evidence-balanced`（本阶段新建，未重建）
- 本阶段未修改旧 tag、旧报告、`reports/final` 任何文件、`data/processed` 任何文件；未运行任何付费 API；未改动任何最终数字。

---

## 1. 当前 P 阶段

U00（v0.2 升级链的第 0 步）。本阶段只冻结基线并**机器可复现地**证明 7 项 P0 缺陷存在，不进入 U01。

## 2. 已完成事项

1. 确认 tag `v0.1-one-day` = `a60274e`，且与当前 HEAD 一致；未移动 tag、未改写旧报告。
2. 新建分支 `upgrade/v0.2-evidence-balanced`（`git branch` 创建；已存在时沿用，本仓库此前无该分支）。
3. 用可写 cache 跑通现有测试，并把**原始输出**存档：`audit/v02/u00_baseline_pytest.txt`。
4. 逐项核对 `reports/final/reproducibility.json`：输入 hash、派生行数、表图清单、LLM 成本；核对结果落 `audit/v02/u00_artifact_hashes.csv`。
5. 新增 `tests/test_u00_defect_repro.py`：7 项 P0 各 1 条可复现用例（共 8 个 test function，U00-07 拆 2 条）。
6. 新增 `audit/v02/u00_findings.csv`（7 行，含证据、复现命令、期望契约、目标阶段）。
7. 存证：`audit/v02/u00_defect_repro_pytest.txt`（`--runxfail` 下的真实失败输出）。

## 3. 数据规模

| 对象 | 规模 |
| --- | --- |
| 研究总体 `officials_master.parquet` | 39,988 人 |
| 任职记录 `appointments.parquet` | 56,346 行 |
| 结构化家世长表 `family_structured.parquet` | 119,964 行（39,988 × 3 槽位） |
| LLM 富化 `family_enriched.parquet` | 963 行（321 人 × 3 槽位） |
| 最终家世 `family_final.parquet` | 119,964 行 |
| 个人指标 `person_indicators.parquet` | 39,988 人 |
| 至少一位祖先可考 | 698 人（身份可考槽位 946 个，其中 335 个有任官字段） |
| D 层有结局 | 371 人（high 128 / medium 230 / 其他 13） |
| LLM 调用 | 690 次，0.947 RMB（3 个 prompt 版本） |

## 4. 当前 coverage

- 祖先身份 coverage（`n_slots_sufficient>0`）：698 / 39,988。
- 祖先属性 ascertainment：946 个身份可考槽位中 335 个有任官（35.4%）、302 个有功名；611 个槽位**只有姓名**。
- 发布表清单：23 张（T1–T11 + A1–A12），图 4 张 × (png+pdf) = 8 个文件；与 `reproducibility.json` 记录一致。

## 5. 基线测试结果与 warning（原样记录）

命令：`uv run pytest -q`（可写 cache：仓库内 `.pytest_cache/`；uv 使用默认用户 cache；无网络访问）

```
........................................................................ [ 98%]
.                                                                        [100%]
=============================== warnings summary ===============================
tests/test_p06_analysis.py::test_model_falls_back_when_the_full_formula_separates
  /Users/wahrfreiheit/OMP/qing-elite-family-background/.venv/lib/python3.12/site-packages/statsmodels/regression/_tools.py:133: RuntimeWarning: divide by zero encountered in scalar divide
    scale = np.dot(wresid, wresid) / df_resid

tests/test_p06_analysis.py::test_model_falls_back_when_the_full_formula_separates
  /Users/wahrfreiheit/OMP/qing-elite-family-background/.venv/lib/python3.12/site-packages/statsmodels/regression/_tools.py:133: RuntimeWarning: invalid value encountered in scalar divide
    scale = np.dot(wresid, wresid) / df_resid

tests/test_p06_analysis.py: 12 warnings
  /Users/wahrfreiheit/OMP/qing-elite-family-background/.venv/lib/python3.12/site-packages/statsmodels/genmod/generalized_linear_model.py:1269: PerfectSeparationWarning: Perfect separation or prediction detected, parameter may not be identified
    return self._fit_irls(

tests/test_p06_analysis.py::test_model_falls_back_when_the_full_formula_separates
  /Users/wahrfreiheit/OMP/qing-elite-family-background/.venv/lib/python3.12/site-packages/statsmodels/genmod/generalized_linear_model.py:1486: SingularMatrixWarning: The design matrix is rank-deficient. The model parameters are not uniquely determined.
    wls_results = wls_model.fit(method=wls_method2)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
```

- 退出码 0；73 项全部通过（与 `reproducibility.json` 的 `tests.summary = "73 tests, all passed"` 一致）。
- warning 全部来自 `test_model_falls_back_when_the_full_formula_separates` 的分离/秩亏模型路径，未被抑制，原样保留。
- 加入 U00 复现用例后（81 项，其中 8 项 strict-xfail）`uv run pytest -q` 仍退出 0；xfail 一旦转 XPASS 会立即变红（见 §7）。

## 6. `reproducibility.json` 核对

`audit/v02/u00_artifact_hashes.csv` 给出逐文件对照；结论：

| 类别 | 数量 | hash 一致 | 行数一致 |
| --- | --- | --- | --- |
| 原始来源文件（`data/raw/**`） | 9 | 9 / 9 | n/a |
| 派生数据（`data/processed/*.parquet`） | 6 | 6 / 6 | 6 / 6 |
| 冻结表（`reports/final/tables/*`） | 23 | 23 / 23 | 23 / 23（CSV） |
| 冻结图（`reports/final/figures/*`） | 8 | 8 / 8 | n/a |

- 输入 hash：`cbdb_20260912.sqlite3` = `604a4ce0…`、CGED-Q JSL tab = `92d74b4d…` 等 9 个文件全部与记录一致（`cbdb_20260912.json`、`cbdb_users_guide_*.pdf` 在 manifest 中 `verified=null`，但实测 hash 与记录一致）。
- LLM 成本：690 次调用 / 0.947 RMB；按 prompt 版本 p04-family-v1 44 次 0.061、v2 125 次 0.1306、v3 521 次 0.7554，合计与记录一致。
- 结论：**旧 artifact 全部 hash 未变**，且本阶段结束后再次校验仍一致（§10 复现命令）。

## 7. 7 项 P0 缺陷的复现结果

复现机制：`tests/test_u00_defect_repro.py`。每个用例断言"v0.2 应有的契约"，在冻结实现下**必然失败**，因此用 `@pytest.mark.xfail(strict=True)` 标注：现在计为 XFAIL（套件保持绿），一旦实现被修正则变成 XPASS(strict) **硬失败**，强制在修复阶段显式撤销标记。用 `--runxfail` 运行即可看到真实失败信息（存档 `audit/v02/u00_defect_repro_pytest.txt`，8/8 FAILED）。

| ID | 缺陷 | 复现命令（`--runxfail`） | 实测证据 |
| --- | --- | --- | --- |
| U00-01 | 仅知祖先姓名、无任官字段 → `ancestor_official_any=0` | `uv run pytest tests/test_u00_defect_repro.py::test_known_ancestor_without_office_field_is_not_coded_as_zero --runxfail` | 698 名有可考祖先者中 387 人 =0、336 人 `elite_generations_count=0`、51 人 `strict_commoner_3g=1`；611/946 可考槽位无任官字段 |
| U00-02 | `manual_validation.csv` 全部 pending | `... ::test_manual_validation_has_no_pending_review --runxfail` | 40/40 `human_review_status=pending` |
| U00-03 | D 层 high 与 medium 结果差异大 | `... ::test_d_layer_estimate_is_not_linkage_dependent --runxfail` | D 有结局 n=371：all=55.5%、high-only=41.4%（n=128）、medium=61.7%（n=230），差 14.1pp |
| U00-04 | 同一 `source_ids` 分给多个 `person_uid` | `... ::test_one_biography_is_not_shared_by_unconfirmed_persons --runxfail` | harvest index 30 个 source_ids → 62 人（61 人进入 enrichment）；已提交 `family_enriched.parquet` 29 个 → 60 人 |
| U00-05 | 86 个 escalation 未参与 `build_outputs` 裁决 | `... ::test_every_escalation_enters_the_adjudication --runxfail` | 407 次调用中 86 次 escalation（21.1%），13 条 upgraded 输出无法解析为槽位对象；37/86 原始输出与首次不同；按逐字证据门槛 2 人 2 槽位取值不同、新增断言 0；`build_outputs` 无读取 `escalated` 的路径 |
| U00-06 | banner/province 两条审计是恒等式 | `... ::test_unknown_coding_checks_can_actually_fail --runxfail` | `eq("unknown") & ne("unknown")` 与 `isna().sum()-isna().sum()` 恒为 0；对注入的"旗籍 unknown 被写成 non_banner""缺失籍贯被写成 unknown_province"不报警 |
| U00-07 | P07 未裁决冲突与计划缺图未阻止 P08 | `... ::test_p08_has_a_release_gate --runxfail` / `... ::test_p08_ships_the_planned_figure_set --runxfail` | `reports/checkpoints/` 只有 P00–P07，**无 P08.md**；P07.md 明示"验收通过前不进入 P08"且張百齡 conflict 待裁决；无 `qing_elite.release_gate`；`build_final.FIGURES` 仅 4 张（计划要求 Figure 1 数据流程等 5 类）；缺表只写 `status=missing` 不报错 |

机读清单见 `audit/v02/u00_findings.csv`（字段：`finding_id, area, severity, claim, v01_evidence, observed, reproduce_command, expected_v02_contract, target_phase, status`）。

### 复现纪律

- 复现用例只读取**已提交**的冻结 artifact（`data/processed/*.parquet`、`audit/manual_validation.csv`）或构造合成输入；`harvest_index.parquet` 位于 gitignore 的 `data/interim/`，因此 U00-04 改用已提交的 `family_enriched.parquet`（同一缺陷，29/60）。
- 用例对文件系统**无写副作用**：U00-06 通过 `monkeypatch` 把 `build_audit.AUDIT_DIR` 指向 `tmp_path`（首轮实现曾写脏 `audit/p07_unknown_coding.csv`，已 `git checkout` 复原并复验 hash = `477d0134…`）。

## 8. 失败项

- `n/a（U00 只做冻结与复现，不修复实现；7 项 P0 全部按预期被复现，无"预期失败但实际通过"的用例）`

## 9. 验收条款对照（PASS/FAIL/EVIDENCE）

| 验收条款（U00） | 判定 | EVIDENCE |
| --- | --- | --- |
| 确认 tag `v0.1-one-day` 与 commit `a60274e`，不改旧 tag/报告 | PASS | `git rev-parse v0.1-one-day` = `fa967607…`，`git rev-parse --short` = `a60274e`；`git status` 中 `reports/final`、`data/processed`、旧 audit 表均无改动 |
| 新建/沿用 `upgrade/v0.2-evidence-balanced` | PASS | `git branch --list` 显示该分支，本阶段新建，未重建 |
| 可写 cache 跑现有 73 项测试 | PASS | `audit/v02/u00_baseline_pytest.txt`：退出码 0、73 passed、warning 原样 |
| 核对 `reproducibility.json`（输入 hash、派生行数、表图、成本） | PASS | `audit/v02/u00_artifact_hashes.csv`：46 项全部 match；行数 6/6、表 23/23 一致；LLM 690 次 / 0.947 RMB 一致 |
| 建立 `reports/upgrade/U00_BASELINE_AUDIT.md` 与 `audit/v02/u00_findings.csv` | PASS | 本文件；`audit/v02/u00_findings.csv`（7 行） |
| 7 个问题各有"当前实现应失败"的最小复现 | PASS | `tests/test_u00_defect_repro.py`（8 条，U00-07 拆 2 条）；`--runxfail` 下 8/8 FAILED，存档 `audit/v02/u00_defect_repro_pytest.txt` |
| 基线测试结果和 warning 原样记录 | PASS | §5 原文块（未删改、未加 `-W ignore`） |
| 旧 artifact hash 不变 | PASS | §6；另：首轮复现曾写脏 `audit/p07_unknown_coding.csv`，已复原并复验 hash `477d0134…`，之后测试改为 `tmp_path` 隔离 |
| 本阶段不修复实现、不重跑付费 API、不改最终数字 | PASS | `git status` 无 `src/` 改动；无 API 调用（无新增 `audit/llm_calls` 行）；`reports/final` 与 `output/` 未改 |

计划书 U00 验收原文（"P0 问题都能由测试或审计表稳定复现；若事实与本计划不符，先更新审计说明，不直接改实现"）：**PASS** —— 7 项 P0 与计划书 §2.1 的描述逐条吻合，无需修订审计说明。

## 10. 影响研究结论的方法决策

本阶段不改变任何研究结论，但确立三条 U01+ 的硬约束：

1. **unknown != 0 必须是机器强制**：四态断言、schema 约束、负向测试三者缺一不可；`strict_commoner_3g` 这类由缺失字段推出的指标一律降级 legacy。
2. **primary 只接受 reviewed-accepted 链接**：medium/unresolved 不得进入主分析，D 层目前 14.1pp 的阈值敏感性必须在 U07 以三套 linkage 并列报告。
3. **没有发布门禁就没有"发布"**：P07 的待裁决冲突与计划缺图必须在 U08 由 `python -m qing_elite.release_gate` 以非零退出阻断，`reports/checkpoints/P08.md` 的缺失同样是验收缺口。

## 11. 下一步人工验收内容

1. 确认 tag `v0.1-one-day` = `a60274e` 未被移动、`reports/final` 与 `data/processed` 未改写（对照 `audit/v02/u00_artifact_hashes.csv`）。
2. 确认 U00 的 7 项 P0 判读与严重级别，以及 `audit/v02/u00_findings.csv` 中各项的 `target_phase`（U01/U02/U05/U07/U08）分配。
3. 确认"strict-xfail + `--runxfail` 存证"这一复现机制可接受（固定套件保持绿，修复时必须显式撤标）。
4. 确认是否接受 U00-04 以已提交的 `family_enriched.parquet`（29/60）替代 untracked 的 `harvest_index.parquet`（30/62）。
5. 复现命令：
   - 基线：`uv run pytest -q`
   - 缺陷：`uv run pytest tests/test_u00_defect_repro.py -rxX`（8 XFAIL）与 `--runxfail`（8 FAILED）
   - hash 复核：见 `audit/v02/u00_artifact_hashes.csv`

**验收通过前不进入 U01。**
