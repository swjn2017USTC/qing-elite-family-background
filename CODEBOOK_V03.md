# CODEBOOK_V03 — 变量与判定口径

本文件说明**每个变量怎么判定**；判定一律由 Python 按配置执行，LLM 从不决定分类。

## 1. exposure（家庭资本，primary）

| 变量 | 定义 | NA 规则 |
| --- | --- | --- |
| `direct_3g_degree_count` | 父/祖/曾祖三代中有功名记载的槽位数 | 无任何槽位可观测 → 该人不进入关联行 |
| `direct_3g_office_count` | 三代中有任官记载的槽位数 | 同上 |
| `direct_elite_generations` | 三代中有功名或任官的代数 | 同上 |
| `max_direct_office_rank` | 祖先官职的行政层级序数（county 1 < prefectural 2 < provincial 3 < central 4） | 无任官记载 → null |
| `senior_collateral_degree_count` | 伯叔/伯叔祖等长辈旁系中有功名者数 | 无旁系可观测 → 不计算 |
| `senior_collateral_office_count` | 同上，任官 | 同上 |
| `all_senior_elite_kin_count` | 长辈旁系中有功名或任官者数（去重） | 同上 |

核心纪律：**`observable_direct_kin = 0` 的人不是「零资本」，而是 unknown**，只出现在覆盖表。

## 2. outcome（职业轨迹）

| 变量 | 定义 | 说明 |
| --- | --- | --- |
| `highest_rank_class` | 全部事件中最高品级（1 最高） | 品级来自策展表，`verification_status=pending` |
| `highest_admin_level` | 中央/省/府/县中最高的行政层级 | 关键词本体映射 |
| `central_local_route` | `central_only` / `local_only` / `mixed` / `unknown` | 由事件层级推导 |
| `first_substantive_office` | 第一条实授事件的官职 | 实授 = 有選任方式且无署/候标记 |
| `career_length_years` | 首末事件年份差 | 仅有起止年者 |
| `appointment_*_share` | acting / expectant / substantive / concurrent / honorific 的事件占比 | 事件级，非人级 |
| `major_transitions` | 品级变化的序列（如 `7->4`） | 供 impossible-transition 检测 |

## 3. 亲属关系本体（版本化）

- `ontology_version = relations-v2`，共 20 个 relation code、7 个 class。
- classes：direct_line, senior_collateral, same_generation, junior_collateral, female_line, marital, affinal
- `generation_delta` 以 ego 为 0、正数表示长辈；class 与 delta 的符号由本体强制（schema 校验）。

## 4. 证据四态与风险分层

- `assertion_state ∈ {positive, explicit_negative, unknown, conflict}`；后两者不得带值。
- risk 权重：{"linkage_uncertainty": 0.2, "ocr_uncertainty": 0.1, "parser_disagreement": 0.15, "source_conflict": 0.15, "rare_relation": 0.05, "office_unknown": 0.1, "chronology_violation": 0.15, "evidence_span_failure": 0.1}
- 档位路由：{"LOW": "auto_accept", "MEDIUM": "machine_adjudicate", "HIGH": "human_review"}

## 5. 明令禁止的口径

- 不得把缺失当否定（`unknown != 0`、`missing != commoner`）；
- 不得用姓氏、籍贯、同族常识或模型知识推定亲属；
- 不得对同一个体在不同来源间静默覆盖（冲突必须记录）；
- 不得报告未实际执行的计算。
