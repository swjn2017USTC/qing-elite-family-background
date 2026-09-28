# V0.3 AUTOMATION POLICY

- 版本：`review-policy-v1`
- 机器可读来源：`config/v03/review_policy.yaml`
- 违规检测：`src/qing_elite/v03/design.py:validate_review_policy`（拒绝任何把人工审核写成比例的键，拒绝非 risk 分层路由）

---

## 1. 总原则

```text
rule-first → machine verification → risk-based review → human only for residual high-risk
```

自动化顺序（机器强制首尾）：

```text
deterministic_rule → machine_verifier → llm_second_pass → vision_verifier → human_review
```

成本阶梯同理：**规则 > 本地 OCR > 廉价非思考 LLM > 推理 LLM > 人工**。
绝不把可以用确定性规则做的事送去问模型，也绝不把机器能自动裁决的事丢给人。

## 2. 允许与禁止的审核方式

| 允许 | 禁止 |
| --- | --- |
| 确定性规则自动接受（全部不变量成立） | **固定比例抽样人工审核（例如"随机 10% 回源复核"）** |
| 机器验证器自动裁决（第二 parser / DeepSeek Flash 非思考 / vision verifier） | 把未裁决的大批量候选一次性导出给人工 |
| 高风险残差进入 `manual_review_queue`，人工有绝对预算 | 用扩大人工批量来挽救自动化不达标 |
| 复用已有 v0.2 人工 gold，不重复消耗人工 | 把人工/机器裁决结果静默覆盖而不写回 ledger |

v0.2 的 U06 曾计划"随机 10% 回源复核"。该模式在 V0.3 **被禁止**，理由：固定比例把人工预算绑在语料规模上，既不按风险排序，也无法在语料变大时保持可行。

## 3. 风险打分

```text
total_score = Σ wᵢ · componentᵢ        componentᵢ ∈ [0, 1] ⇒ total_score ∈ [0, 1]
```

| 分量 | 权重 | 来源 |
| --- | --- | --- |
| `linkage_uncertainty` | 0.20 | entity link 的分数与多方法一致性 |
| `ocr_uncertainty` | 0.10 | OCR confidence |
| `parser_disagreement` | 0.15 | 规则 parser vs LLM verifier |
| `source_conflict` | 0.15 | 冲突的 evidence assertions |
| `rare_relation` | 0.05 | 关系词频 |
| `office_unknown` | 0.10 | 官职本体映射状态 |
| `chronology_violation` | 0.15 | 生涯/亲属年表校验器 |
| `evidence_span_failure` | 0.10 | 逐字 span 校验器 |

三档路由（阈值机器强制递增）：

| 档 | 条件 | 动作 |
| --- | --- | --- |
| LOW | `total_score ≤ 0.25` | `auto_accept`（必须满足全部确定性不变量） |
| MEDIUM | `≤ 0.60` | `machine_adjudicate`（第二 parser / 非思考 LLM / vision）；持续不一致 → 人工 |
| HIGH | `≤ 1.00` | `human_review`（进入 `manual_review_queue`） |

契约保证：`route=human_review` 仅当 `risk_tier=HIGH`；`decision=grey` 的链接必须 `review_status=pending`（否则 schema 失败）。

## 4. 人工预算

```yaml
manual_link_new_target: 300        # 新增人工链接裁决（条）
manual_assertion_review_target: 200 # 人工断言复核（条）
unit: absolute_count
on_exceeded: stage_fail_not_expand
```

- 预算是**绝对条数**，不是语料占比；
- 优先消耗 U02 已有人工 gold（`reuse_existing_human_evidence: true`）；
- 预算内达不到质量门槛 → **阶段 FAIL 并重设计**，不是把几万条丢给人工。

## 5. 质量门槛与失败条件

```yaml
quality_gates:
  linkage_auto_accept_precision: 0.99
  on_miss: raise_abstention_not_expand_review
```

宁可 `unknown`，不增加人工海洋。触发以下任一情况即停止并重设计，不硬跑下一阶段：

- 主家世来源无法合法机器获取；
- 关键字段 precision 明显不足；
- 99% precision 只能靠大量人工；
- 核心 cohort 仍有强烈阶层差异的覆盖；
- 职业序列无法可靠恢复。

## 6. 人工应该只看什么

理想状态下人工只剩四类介入：

1. U04R 后看一次默认来源方案是否有史学方向错误（不是查几百人）；
2. active learning 真正不确定的少量困难配对（优先消耗 U02 gold）；
3. OCR 极差 / 关系极复杂 / 来源真冲突的个案；
4. U09R/U11R 的**统计结果历史含义**审核。

人工**不应该**做的事：逐条确认"张三是不是李四的祖父"。

## 7. 审计与可追溯

- 每个自动裁决必须写 `review_queue`（含 `route`、`reason_codes`、`risk_id`）；人工裁决写 `decision`、`rationale`、`reviewer`、`resolved_at`（schema 强制）。
- 正式史料 LLM 调用仍按 v0.2 规则写 `audit/llm_calls/calls.jsonl`（model、thinking_mode、prompt_version、input_sha256、tokens、成本、status、retry），**禁止**记录 API key / auth header / 完整环境变量。
- 凭证只存在于 shell 环境变量 `DEEPSEEK_API_KEY`。
- 任何阶段的门禁必须机器可执行且非零退出（范例：`python -m qing_elite.v03.gate`）。
- 测试规范：缺少输入时 **skip 并写明原因**，不得用 xfail 掩盖环境错误（见 `reports/upgrade_v03/V02_POSTMORTEM.md` E2）。
