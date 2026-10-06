# V0.3 STAGE PLAN

- 版本：`v0.3.0`（阶段 U03R 制定）
- 依据：V0.3 `cohort-and-kin-network` 计划书；本文件只登记阶段边界、产出与门槛，不改研究参数（研究参数见 `V03_RESEARCH_CONTRACT.md`）。

---

## 1. 路线

```text
U03R  研究重置 + contract + postmortem          ← 本阶段
U04R  来源与现代研究 feasibility benchmark
U05R  标准化家世来源 pilot（抽取与验证）
U06R  Entity Resolution V2（官方 person_id + ML 链接）
U07R  Kin graph + family capital panel
U08R  Career-event panel
U09R  Career-process analysis
U10R  Early/mid-Qing legacy extension
U11R  DAG + adversarial audit + release
```

分支：`upgrade/v0.3-cohort-kin-network`；基线 tag：`v0.2-u03-frozen-20260927`（`af98ba3`）。

## 2. 阶段表

| 阶段 | 目标 | 主要产出 | 硬门槛（不达标即 FAIL，不进入下一阶段） |
| --- | --- | --- | --- |
| **U03R** | 冻结 V0.3 研究设计 | `V02_POSTMORTEM.md`、`V03_RESEARCH_CONTRACT.md`、`V03_AUTOMATION_POLICY.md`、`V03_DATA_MODEL.md`、`V03_SOURCE_PRIORITY.md`、`V03_STAGE_PLAN.md`、`config/v03/*`、`src/qing_elite/v03/{contracts,design,gate}.py`、`tests/test_v03_*` | 见 `reports/upgrade_v03/U03R.md` §gate；`python -m qing_elite.v03.gate` 退出 0 |
| U04R | 判断哪些 family-background 来源值得用 | literature pipeline（registry/digest/ledger/historiography/claim-delta）、`source_feasibility.parquet/.md`、`claim → variable → source → testability` 映射、PLAN A/B/C + 自动默认方案 | 每个来源有 access_type 与 terms；不得因单一来源不可得而阻塞；默认方案由预定义规则选出 |
| U05R | 标准化家世材料能否自动生成 kin graph | 按 source × cohort × region × credential 的 200–500 人 pilot；parser 层级（规则→OCR→非思考 LLM→verifier→vision）；field-level precision/recall/abstention/error taxonomy；machine gate | 至少回答 kin/degree/office/evidence-span precision、OCR 误差、abstention；自动化不达标的来源**drop**，不靠人工补 |
| U06R | 中文历史人物身份消解 V2 | CGED-Q 官方 `person_id` 接入与 crosswalk、ML 链接（登记 upstream commit/license）、Splink 基线、三方法一致性、`linkage_features/risk/review_queue.parquet`、`linkage_benchmark.md` | primary auto-accept held-out precision ≥ 0.99，否则提高 abstention；人工新增 ≤300 pair |
| U07R | 证据感知的亲属图与家族资本 | `persons/kin_edges/credentials/offices/evidence_assertions` canonical 表；NetworkX 计算层；7 个 primary 家族资本指标；两个 new-entrant 定义；循环/世代悖论/年表矛盾检测；source/cohort 覆盖报告 | 每个指标给 numerator/denominator/observable_kin_count/source coverage/lineage；observability 不足必须 unknown |
| U08R | 纵向职业序列 | office ontology 拆分（title/rank/level/central-local/authority/appointment/acting/expectant/substantive/concurrent/honorific/era）；`career_events.parquet`；派生 outcome；年表校验与 impossible-transition 检测 | 自动年表 quarantine；与旧 A/B/C/D 建 crosswalk，tier 只作 legacy outcome |
| U09R | 首次实质性分析 | 样本流、来源/亲属可观测性、链接质量、暴露与结果分布、分层描述、预注册模型、敏感性、Literature Claim Delta（CONFIRM/REFINE/CONTRADICT/NEW/NOT_COMPARABLE） | separation / 小格 / 秩亏 / 高缺失 / 来源依赖 自动降级为描述结果；primary 不依赖 unknown=0 或未完成 review |
| U10R | 早期/中期清代扩展 | 旧 A/B/C 映射到 V0.3 family-capital ontology；比较结果分类 PERIOD-SPECIFIC / CROSS-PERIOD CONSISTENT / NOT COMPARABLE | 旧 D 层不得恢复为"寒门率对照组"；不得跨 source 可比性做 pooled 回归 |
| U11R | 工程化、对抗式审计与发布 | Snakemake DAG（acquisition/research/release）、只读 adversarial reviewer、release gate、`README_V03/METHODS_V03/CODEBOOK_V03/FINAL_REPORT_V03/REPRODUCIBILITY/RELEASE_MANIFEST` | 任一 schema/test/lineage/conflict/dirty artifact/report 数字不一致即非零退出 |

## 3. 阶段纪律（继承 .omp/AGENTS.md 并加严）

1. 一次对话只做一个阶段；写完阶段报告并 commit 后停止等人工验收。
2. 不擅自扩大研究问题或新增数据源。
3. 每个阶段必须给出机器可执行的 gate（`audit/v03/*_gate.json` + 非零退出）。
4. gate FAIL 时必须给出**不改样本规模**的调整方案（改来源或改 estimand），不得靠扩样或扩大人工解决。
5. 不重写 v0.1/v0.2 冻结产物；V0.3 只写入 `config/v03`、`data/processed_v03`、`data/interim_v03`、`audit/v03`、`reports/upgrade_v03`、`src/qing_elite/v03`、`tests/test_v03_*`。
6. 正式批量 LLM 调用前必须输出预计 token 与 RMB；软预算 20 元、硬预算 30 元；未到正式抽取阶段不产生批量费用。
7. 缺少输入时测试必须 skip 并写明原因，不得用 xfail 掩盖环境错误。

## 4. 成本阶梯

```text
规则 > 本地 OCR > 廉价非思考 LLM > 推理 LLM > 人工
```

coding 侧：默认 `opencode-go/deepseek-v4.1-flash`；正式史料抽取：本地 Python → DeepSeek 官方 API（`deepseek-flash`，`thinking=disabled`，JSON output）。

## 5. 阶段失败即重设计的触发条件

来源（主家世源不可合法机器获取）、抽取（关键字段 precision 不足）、链接（99% 只能靠大量人工）、覆盖（核心 cohort 仍有强烈阶层差异）、职业序列（无法可靠恢复）——任一出现即 **FAIL + redesign**，不为完成 TODO 继续硬跑。
