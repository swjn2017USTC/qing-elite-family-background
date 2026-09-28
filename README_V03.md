# qing-elite-family-background — v0.3 `cohort-and-kin-network`

清代官僚精英的**家族资本与职业轨迹**的可复现 micro-study（描述性，不做因果识别）。

## 这个版本做了什么

1. 把研究设计从「最终官位 → 回头找父祖」换成「标准化家世史料 → 家族资本 → 职业轨迹」，family background 是 exposure，career trajectory 是 outcome。
2. 亲属范围从三代直系扩到旁系长辈（伯叔类）。
3. 用官方 CGED-Q `person_id` 取代自建去重，v0.2 的去重降为 audit comparison。
4. 职业位置从静态 tier 改为纵向事件面板（任命段、品级、层级、中央/地方、任命状态）。
5. 全流程机器化：证据四态、risk 分层路由、release gate、对抗式审计。

## 最重要的结论

**不是**关于寒门的结论，而是关于**可测量性**的结论：

- 早期/中期清代 A/B/C 精英中，家世可观测者 0.2774；
- 晚期标准化 cohort 中，家世可观测者 0.054；
- U09R 的关联分析因此被数据门槛降级为描述性（可辨识样本过小）。

细节见 [FINAL_REPORT_V03.md](reports/upgrade_v03/FINAL_REPORT_V03.md) 与各阶段报告。

## 目录

```text
config/v03/            研究契约、亲属本体、审核政策、来源优先级、品级表、工作流配置
src/qing_elite/v03/    contracts, design, gate, pilot, linkage, kin, career, analysis, extension, audit, release
workflow/Snakefile     把既有模块串成 DAG（不重写实现）
sources/literature/    现代文献 registry / digest / ledger
reports/upgrade_v03/   U03R–U11R 阶段报告、final report、图表
audit/v03/             阶段 gate、final_audit、release_gate
```

## 复现

见 [REPRODUCIBILITY.md](REPRODUCIBILITY.md)；release gate：`uv run python -m qing_elite.v03.release`。

当前 release gate 状态：**PASS**（021f76e）。

## 数据与许可边界

- 原始数据（CBDB、CGED-Q、维基文库、公版扫描件）**不入 Git**，只登记 metadata 与 checksum；
- 逐人级派生表**不发布**；公开的是 schema、代码、汇总、审计与复现元数据；
- 不发布任何 API key；正式史料抽取只经本地 Python → DeepSeek 官方 API。
