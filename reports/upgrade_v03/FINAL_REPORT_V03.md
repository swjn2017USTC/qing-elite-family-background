# FINAL_REPORT_V03 — family capital and career trajectories

- release：`v0.3-cohort-kin-network`；代码 `021f76e`；release gate **PASS**
- 全部数字由 artifact 渲染；本报告不做因果识别

## 0. 一句话结论

在可合法、自动读取的数据范围内，**清代家族资本与职业轨迹的关联问题当前不可回答**：不是没有关联，而是**可观测性与样本量不足**。本版交付的是一条可审计、可复现、且把边界写清楚的流水线，以及一批可继续使用的结构数据。

## 1. 样本流（U09R）

- 链接 1,723 → 有职业记录 1,697 → 有家世指标 1,592 → 分析样本 1,204
- 家世可观测（exposure 有定义）65（0.054）
- 数据门槛判定：`descriptive_only`，原因：rows with both sides observed 17 < 200; missingness 0.99 > 0.5; small cells — cohort_id: 1760-1775=12, 1776-1785=1, 1786-1798=4; 9 of 9 exposure×outcome cells hold fewer than 5 observations

## 2. 各层覆盖

| 层 | 结果 |
| --- | --- |
| U04R 来源可行性 | 13 个来源：PUBLIC_STRUCTURED 3 / UI_ONLY 4 / ACCESS_REQUEST_REQUIRED 5 / UNAVAILABLE 1；默认 PLAN_C |
| U05R 抽取 | CBDB 亲属映射率 None；同官录扫描件 keep_identity_only |
| U06R 链接 | ML held-out precision 1.0 @0.5；auto-accept 阈值 0.35 |
| U07R 亲属图 | 768 条边；直系三代齐全 11 人 |
| U08R 职业面板 | 148,288 条事件 / 53,701 人 |

## 3. 家族资本（仅在可观测者中）

见 `reports/upgrade_v03/U09R.md` §3；核心：可观测者中位数为 0，说明「有家世记录」的人群里大多数也没有可考的三代功名/任官。

## 4. 职业轨迹（全样本）

见 `reports/upgrade_v03/U09R.md` §4 与 `U08R.md` §4：品级已知 396/1,204，路线已知 692/1,204，任命组成以 substantive 为主（0.98，事件级）。

## 5. 早期/中期清代扩展（U10R）

- A/B/C 1,179 人，家世可观测 0.2774；D 层未纳入。
- 跨期分类：{"PERIOD_SPECIFIC": 15, "NOT_COMPARABLE": 13, "CROSS_PERIOD_CONSISTENT": 2}，即**只有「家世可观测是少数」这一方向可跨期比较**。
- pooled regression：未运行（cohorts differ in sampling design (elite census vs link-defined roster sample) and in variable definitions; pooling would estimate the design difference）

## 6. 敏感性与不确定性

- 链接置信度敏感：`u06r_auto_accept` 组家世可观测率为 0，`v02_deterministic` 组为 7.2% → 链接协议与家世可见性相关。
- 抽取阈值敏感：同官录属性层未达标，已降级为 identity-only。
- 品级表 `verification_status=pending`，一切以 `rank_class` 为基础的陈述继承该状态。

## 7. 边界（必须与结论同时引用）

1. 可观测性不足：U09R 双侧可观测仅 17 行 → 不做模型。
2. 样本由链接定义：链接成功是选择机制。
3. tier 只作 legacy/extension outcome。
4. 不做因果识别；不写「清代是/不是 meritocracy」一类判断。
5. 两个 U02 测试依赖公开仓库不含的本地派生文件（既有缺陷，记录在案）。

## 8. 复现与门禁

- release gate：`PASS`；对抗式审计：`audit/v03/final_audit.json`。
- 环境/种子/阈值/模型 id：见 [RELEASE_MANIFEST.json](RELEASE_MANIFEST.json)。
