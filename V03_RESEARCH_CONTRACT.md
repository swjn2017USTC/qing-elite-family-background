# V0.3 RESEARCH CONTRACT

- 版本：`v0.3.0`（阶段 U03R）
- 机器可读来源：`config/v03/research.yaml`（本文件是它的说明；两者冲突时以机器可读文件为准，并必须修好另一份）
- 违规检测：`src/qing_elite/v03/design.py:validate_research_contract`，并由 `python -m qing_elite.v03.gate` 强制

---

## 1. 主问题

> 在相似的科举/制度进入资格下，家庭政治—教育资本以及更广泛的宗族精英嵌入，与一个人的职业轨迹和最终官位之间有怎样的描述性关联？

范围：清代（主线为 19 世纪标准化家世史料可达的 cohort；1644–1820 的 A/B/C 精英样本作为 early/mid-Qing 扩展，见 U10R）。

**claim level 固定为 `descriptive_association`，`identification = none`。**

## 2. 变量角色（本契约的核心）

| 角色 | 变量 | 说明 |
| --- | --- | --- |
| **exposure（自变量）** | `direct_3g_degree_count`、`direct_3g_office_count`、`direct_elite_generations`、`max_direct_office_rank`、`senior_collateral_degree_count`、`senior_collateral_office_count`、`all_senior_elite_kin_count` | 家族政治—教育资本及其宗族嵌入 |
| exposure（次要） | `kin_degree_density`、`kin_office_density`、`network_degree`、`elite_neighbor_share` | 网络密度类，只在数据门槛满足时报 |
| **outcome（因变量）** | `highest_office_rank`、`highest_admin_level`、`central_local_route` | 职业轨迹的结果面 |
| outcome（次要） | `first_substantive_office`、`time_to_rank`、`career_length`、`appointment_regular_share`、`acting_share` | 生涯过程 |
| **legacy only** | `highest_tier`、`tier_group`（A1/A2/A3/B/C/D） | 只作早期/中期清代扩展与来源偏差基准，**不得作为 V0.3 primary outcome 或分组变量** |

对比 v0.1/v0.2：那时 tier 是自变量、家世记载是因变量（`P(documented ancestor office = 1 | tier)`）。**该量在 V0.3 降级为 `estimands.legacy_benchmark`（来源偏差基准），不得回到 primary。**

## 3. 亲属范围：从 direct three-generation 扩展到 extended kin

| 范围 | 关系（versioned ontology：`config/v03/relations.yaml`） | 作用 |
| --- | --- | --- |
| direct line | `father`、`grandfather`、`great_grandfather`（可选 `great_great_grandfather`） | 传统三代指标 |
| **extended kin** | `uncle_paternal`、`great_uncle_paternal`、`brother`、`cousin_paternal`、`son`、`nephew_paternal` | 旁系精英嵌入（V0.3 新增，v0.1/v0.2 完全没有） |
| 保留待后续 | `mother`、`grandmother`、`wife`、`father_in_law` | 母系与姻亲，先记录不进入 V0.3 primary 结论 |

设计理由：只数父—祖—曾祖会把"有地位叔伯"的家族误判为寒素。V0.3 因此把 `direct_line_new_entrant` 与 `extended_family_new_entrant` **分开定义**，并要求足够的 observability 才能取真值，否则一律 `unknown`。

关系、类别与世代方向由 `config/v03/relations.yaml` 版本化（`ontology_version`）；`generation_delta` 约定以 ego 为 0、正数表示长辈。

## 4. Estimand 层次

| 层次 | 内容 | 报告要求 |
| --- | --- | --- |
| primary | `P(career outcome | family capital, credential, cohort)`：同一来源框架、同一进入资格层内的描述性关联 | 必须同时给未调整描述表与分层结果 |
| secondary | 家族资本按 cohort / credential / region 的分布；direct-only 与 direct+extended 两套 exposure 的对照 | 必须给两套 exposure 分母 |
| legacy benchmark | v0.2 的 `P(documented ancestor office = 1 | tier)` 文档率 | 只能作为来源覆盖比较；**不得写成寒门率** |
| not estimable | 家世对职业结果的因果效应；"清代是否 meritocracy" | 明令禁止 |

前置顺序固定（`research.yaml:reporting.mandatory_order`）：样本流 → 来源覆盖 → 链接质量 → 家族资本分布 → 职业结果分布 → 分层描述 → 预注册模型与敏感性。

## 5. 抽样与分母

- 单位：`person`；frame 由 **source × cohort × region × credential** 定义。
- **禁止** `sampling_on_outcome_tier` 与 `oversampling_by_final_office`（机器强制）。
- 每个指标必须给 `numerator`、`denominator`、`observable_kin_count`、来源覆盖；亲属不可观测时取 `unknown`，不得记 0。

## 6. 混杂、分层与中介

`conditioning.must_report_separately = [cohort, credential, region, banner, entry_status]`。

- `cohort`、`region`、`banner`、`entry_status` 作分层；
- `credential` **同时是分层变量与可能的mediator**：科举功名既由家世影响、又影响官位。因此 V0.3 必须**并列报告**"不控制 credential"与"在 credential 层内"两套结果，并显式说明差异来源，而不是机械塞进模型。

## 7. 明令禁止的输出

- LLM 或规则不得输出 `is_commoner` 一类判断（`exposure.forbidden_outputs`，机器强制）；
- 不得由"记载缺失"推出"无任官/无功名/平民"（`unknown != 0`、`missing != commoner`）；
- 不得报告未实际执行的计算；缺件必须写明缺什么。

## 8. 与 v0.2 的关系

本契约**不推翻** v0.2 的工程资产：四态证据、source/evidence/review ledger、Pandera schema、冻结来源与 hash、CBDB/CGED-Q adapter、LLM 审计、release gate 思想全部继承。
被推翻的只有**识别结构与抽样结构**：exposure/outcome 换位、抽样改为 source frame、D 层对比降级为基准。

## 9. 变更程序

任何对 exposure、outcome、抽样规则、claim level 的改动都必须：
1. 改 `config/v03/research.yaml`；
2. 改本文件；
3. 重跑 `uv run python -m qing_elite.v03.gate`；
4. 在阶段报告中记录理由与影响。
