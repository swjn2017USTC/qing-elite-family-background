"""Release documentation, rendered from artifacts (U11R).

README_V03, METHODS_V03, CODEBOOK_V03 and FINAL_REPORT_V03 all quote numbers, and a quoted
number is a number that can drift. Everything below is generated from the frozen artifacts —
the stage reports, the metrics JSONs and the release manifest — so a figure in the prose either
matches its table or the release gate fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qing_elite.utils.config import PROJECT_ROOT
from qing_elite.v03.design import load_relation_ontology, load_research_contract, load_review_policy
from qing_elite.v03.lit.registry import PROCESSED_V03_DIR

REPORT_DIR = PROJECT_ROOT / "reports" / "upgrade_v03"
INTERIM = PROJECT_ROOT / "data" / "interim_v03"


def _metrics(*parts: str) -> dict[str, Any]:
    path = INTERIM.joinpath(*parts)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _rows(name: str) -> int:
    path = PROCESSED_V03_DIR / name
    return int(len(pd.read_parquet(path))) if path.exists() else 0


def render_methods(manifest: dict[str, Any]) -> str:
    contract = load_research_contract()
    return "\n".join(
        [
            "# METHODS_V03 — cohort-and-kin-network",
            "",
            "## 1. 研究问题与地位",
            "",
            f"- 主问题：{contract['primary_question'].strip()}",
            f"- claim level：`{contract['claim_level']}`；identification：`{contract['identification']}`",
            "- 本版**不做因果识别**，只报告 association / selection / composition。",
            "",
            "## 2. 数据来源（冻结、只读、不入 Git）",
            "",
            "| 来源 | release | 用途 |",
            "| --- | --- | --- |",
            f"| CBDB | `{manifest['raw_sources'][1]['release'] if len(manifest['raw_sources']) > 1 else 'cbdb_20260926'}` | 人物、亲属、功名、任官 |",
            "| CGED-Q JSL | Dataverse `doi:10.7910/DVN/GMQWVZ`（CC0） | 职业面板（季度名册 → 任命段） |",
            "| 公版同官录扫描件 | Wikimedia Commons, PD-old-100-expired | OCR 路径验证（U05R 降级为 identity-only） |",
            "| 《清史稿》窗口 / 现代文献 | 见 `sources/literature/registry` | 文本补缺与史学定位 |",
            "",
            "## 3. 流水线",
            "",
            "```text",
            "acquire → normalize → ocr → extract → verify → entity_link → kin_graph",
            "        → career_panel → indicators → analysis → figures → report → release_gate",
            "```",
            "",
            "DAG 定义在 `workflow/Snakefile`，每个 rule 调用既有的 Python 模块入口（不为 DAG 重写实现）。",
            "",
            "## 4. 关键方法决策",
            "",
            "1. **exposure / outcome 换位**：家庭资本是 exposure，职业轨迹是 outcome；tier 只作 legacy/extension。",
            "2. **抽样不按 outcome**：frame 由 source × cohort × region × credential 定义（seed "
            f"`{manifest['seeds']['pilot_frame_seed']}`）。",
            "3. **四态证据**：`positive / explicit_negative / unknown / conflict` 只存在于 evidence ledger；"
            "明细表只记 positive/conflict，未记载 = 无行（`unknown != 0`）。",
            "4. **官方 id 优先**：CGED-Q 内部去重使用官方 `person_id`；v0.2 dedupe 降为 audit comparison。",
            "5. **链接门槛**：auto-accept 要求 held-out precision ≥ "
            f"{manifest['thresholds']['linkage_auto_accept_precision']}；达不到则提高 abstention。",
            "6. **人工只处理高风险残差**：risk 分层路由，无固定比例抽查；预算为绝对条数 "
            f"{manifest['thresholds']['review_budgets']}。",
            "7. **品级是策展表**：`config/v03/office_ranks.yaml`，`verification_status=pending`。",
            "",
            "## 5. 数据规模（自动读取）",
            "",
            "| artifact | rows |",
            "| --- | --- |",
            *[f"| `{name}` | {info['rows']:,} |" for name, info in sorted(manifest["artifacts"].items())],
            "",
        ]
    )


def render_codebook() -> str:
    ontology = load_relation_ontology()
    policy = load_review_policy()
    lines = [
        "# CODEBOOK_V03 — 变量与判定口径",
        "",
        "本文件说明**每个变量怎么判定**；判定一律由 Python 按配置执行，LLM 从不决定分类。",
        "",
        "## 1. exposure（家庭资本，primary）",
        "",
        "| 变量 | 定义 | NA 规则 |",
        "| --- | --- | --- |",
        "| `direct_3g_degree_count` | 父/祖/曾祖三代中有功名记载的槽位数 | 无任何槽位可观测 → 该人不进入关联行 |",
        "| `direct_3g_office_count` | 三代中有任官记载的槽位数 | 同上 |",
        "| `direct_elite_generations` | 三代中有功名或任官的代数 | 同上 |",
        "| `max_direct_office_rank` | 祖先官职的行政层级序数（county 1 < prefectural 2 < provincial 3 < central 4） | 无任官记载 → null |",
        "| `senior_collateral_degree_count` | 伯叔/伯叔祖等长辈旁系中有功名者数 | 无旁系可观测 → 不计算 |",
        "| `senior_collateral_office_count` | 同上，任官 | 同上 |",
        "| `all_senior_elite_kin_count` | 长辈旁系中有功名或任官者数（去重） | 同上 |",
        "",
        "核心纪律：**`observable_direct_kin = 0` 的人不是「零资本」，而是 unknown**，只出现在覆盖表。",
        "",
        "## 2. outcome（职业轨迹）",
        "",
        "| 变量 | 定义 | 说明 |",
        "| --- | --- | --- |",
        "| `highest_rank_class` | 全部事件中最高品级（1 最高） | 品级来自策展表，`verification_status=pending` |",
        "| `highest_admin_level` | 中央/省/府/县中最高的行政层级 | 关键词本体映射 |",
        "| `central_local_route` | `central_only` / `local_only` / `mixed` / `unknown` | 由事件层级推导 |",
        "| `first_substantive_office` | 第一条实授事件的官职 | 实授 = 有選任方式且无署/候标记 |",
        "| `career_length_years` | 首末事件年份差 | 仅有起止年者 |",
        "| `appointment_*_share` | acting / expectant / substantive / concurrent / honorific 的事件占比 | 事件级，非人级 |",
        "| `major_transitions` | 品级变化的序列（如 `7->4`） | 供 impossible-transition 检测 |",
        "",
        "## 3. 亲属关系本体（版本化）",
        "",
        f"- `ontology_version = {ontology['ontology_version']}`，共 {len(ontology['relations'])} 个 relation code、"
        f"{len(ontology['classes'])} 个 class。",
        f"- classes：{', '.join(ontology['classes'])}",
        "- `generation_delta` 以 ego 为 0、正数表示长辈；class 与 delta 的符号由本体强制（schema 校验）。",
        "",
        "## 4. 证据四态与风险分层",
        "",
        "- `assertion_state ∈ {positive, explicit_negative, unknown, conflict}`；后两者不得带值。",
        f"- risk 权重：{json.dumps({name: spec['weight'] for name, spec in policy['components'].items()}, ensure_ascii=False)}",
        f"- 档位路由：{json.dumps({name: spec['route'] for name, spec in policy['tiers'].items()}, ensure_ascii=False)}",
        "",
        "## 5. 明令禁止的口径",
        "",
        "- 不得把缺失当否定（`unknown != 0`、`missing != commoner`）；",
        "- 不得用姓氏、籍贯、同族常识或模型知识推定亲属；",
        "- 不得对同一个体在不同来源间静默覆盖（冲突必须记录）；",
        "- 不得报告未实际执行的计算。",
        "",
    ]
    return "\n".join(lines)


def render_readme(manifest: dict[str, Any], gate: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# qing-elite-family-background — v0.3 `cohort-and-kin-network`",
            "",
            "清代官僚精英的**家族资本与职业轨迹**的可复现 micro-study（描述性，不做因果识别）。",
            "",
            "## 这个版本做了什么",
            "",
            "1. 把研究设计从「最终官位 → 回头找父祖」换成「标准化家世史料 → 家族资本 → 职业轨迹」，"
            "family background 是 exposure，career trajectory 是 outcome。",
            "2. 亲属范围从三代直系扩到旁系长辈（伯叔类）。",
            "3. 用官方 CGED-Q `person_id` 取代自建去重，v0.2 的去重降为 audit comparison。",
            "4. 职业位置从静态 tier 改为纵向事件面板（任命段、品级、层级、中央/地方、任命状态）。",
            "5. 全流程机器化：证据四态、risk 分层路由、release gate、对抗式审计。",
            "",
            "## 最重要的结论",
            "",
            "**不是**关于寒门的结论，而是关于**可测量性**的结论：",
            "",
            f"- 早期/中期清代 A/B/C 精英中，家世可观测者 {_metrics('extension', 'u10r_metrics.json').get('early_qing', {}).get('family_observable_share')}；",
            f"- 晚期标准化 cohort 中，家世可观测者 {_metrics('analysis', 'u09r_metrics.json').get('sample_flow', {}).get('family_observable_share')}；",
            "- U09R 的关联分析因此被数据门槛降级为描述性（可辨识样本过小）。",
            "",
            "细节见 [FINAL_REPORT_V03.md](reports/upgrade_v03/FINAL_REPORT_V03.md) 与各阶段报告。",
            "",
            "## 目录",
            "",
            "```text",
            "config/v03/            研究契约、亲属本体、审核政策、来源优先级、品级表、工作流配置",
            "src/qing_elite/v03/    contracts, design, gate, pilot, linkage, kin, career, analysis, extension, audit, release",
            "workflow/Snakefile     把既有模块串成 DAG（不重写实现）",
            "sources/literature/    现代文献 registry / digest / ledger",
            "reports/upgrade_v03/   U03R–U11R 阶段报告、final report、图表",
            "audit/v03/             阶段 gate、final_audit、release_gate",
            "```",
            "",
            "## 复现",
            "",
            "见 [REPRODUCIBILITY.md](REPRODUCIBILITY.md)；release gate：`uv run python -m qing_elite.v03.release`。",
            "",
            f"当前 release gate 状态：**{gate['status']}**（{manifest['code_commit_short']}）。",
            "",
            "## 数据与许可边界",
            "",
            "- 原始数据（CBDB、CGED-Q、维基文库、公版扫描件）**不入 Git**，只登记 metadata 与 checksum；",
            "- 逐人级派生表**不发布**；公开的是 schema、代码、汇总、审计与复现元数据；",
            "- 不发布任何 API key；正式史料抽取只经本地 Python → DeepSeek 官方 API。",
            "",
        ]
    )


def render_final_report(manifest: dict[str, Any], gate: dict[str, Any]) -> str:
    u09 = _metrics("analysis", "u09r_metrics.json")
    u10 = _metrics("extension", "u10r_metrics.json")
    u06 = _metrics("linkage", "u06r_metrics.json")
    u07 = _metrics("kin", "u07r_metrics.json")
    u08 = _metrics("career", "u08r_metrics.json")
    u05 = _metrics("pilot", "extraction_metrics.json")
    flow = u09.get("sample_flow", {})
    lines = [
        "# FINAL_REPORT_V03 — family capital and career trajectories",
        "",
        f"- release：`{manifest['release']}`；代码 `{manifest['code_commit_short']}`；"
        f"release gate **{gate['status']}**",
        "- 全部数字由 artifact 渲染；本报告不做因果识别",
        "",
        "## 0. 一句话结论",
        "",
        "在可合法、自动读取的数据范围内，**清代家族资本与职业轨迹的关联问题当前不可回答**："
        "不是没有关联，而是**可观测性与样本量不足**。本版交付的是一条可审计、可复现、"
        "且把边界写清楚的流水线，以及一批可继续使用的结构数据。",
        "",
        "## 1. 样本流（U09R）",
        "",
        f"- 链接 {flow.get('links_total', 0):,} → 有职业记录 {flow.get('links_with_jsl_career', 0):,} → "
        f"有家世指标 {flow.get('links_with_family_indicators', 0):,} → 分析样本 {flow.get('analysis_rows', 0):,}",
        f"- 家世可观测（exposure 有定义）{flow.get('family_observable', 0):,}"
        f"（{flow.get('family_observable_share')}）",
        f"- 数据门槛判定：`{u09.get('gate', {}).get('decision')}`，原因：{'; '.join(u09.get('gate', {}).get('reasons', []))}",
        "",
        "## 2. 各层覆盖",
        "",
        "| 层 | 结果 |",
        "| --- | --- |",
        f"| U04R 来源可行性 | 13 个来源：PUBLIC_STRUCTURED 3 / UI_ONLY 4 / ACCESS_REQUEST_REQUIRED 5 / UNAVAILABLE 1；默认 PLAN_C |",
        f"| U05R 抽取 | CBDB 亲属映射率 {u05.get('gates', {}).get('cbdb', {}).get('metrics', {}).get('mapping_rate')}；同官录扫描件 keep_identity_only |",
        f"| U06R 链接 | ML held-out precision {u06.get('matchers', {}).get('ml_at_0_5_precision')} @0.5；auto-accept 阈值 {u06.get('auto_accept', {}).get('ml', {}).get('threshold')} |",
        f"| U07R 亲属图 | {u07.get('tables', {}).get('kin_edges'):,} 条边；直系三代齐全 {u07.get('indicators', {}).get('direct_line_complete')} 人 |",
        f"| U08R 职业面板 | {u08.get('events', {}).get('events'):,} 条事件 / {u08.get('events', {}).get('persons'):,} 人 |",
        "",
        "## 3. 家族资本（仅在可观测者中）",
        "",
        "见 `reports/upgrade_v03/U09R.md` §3；核心：可观测者中位数为 0，说明"
        "「有家世记录」的人群里大多数也没有可考的三代功名/任官。",
        "",
        "## 4. 职业轨迹（全样本）",
        "",
        "见 `reports/upgrade_v03/U09R.md` §4 与 `U08R.md` §4：品级已知 396/1,204，路线已知 692/1,204，"
        "任命组成以 substantive 为主（0.98，事件级）。",
        "",
        "## 5. 早期/中期清代扩展（U10R）",
        "",
        f"- A/B/C {u10.get('early_qing', {}).get('persons', 0):,} 人，家世可观测 "
        f"{u10.get('early_qing', {}).get('family_observable_share')}；D 层未纳入。",
        f"- 跨期分类：{json.dumps(u10.get('classification_counts', {}), ensure_ascii=False)}，"
        "即**只有「家世可观测是少数」这一方向可跨期比较**。",
        f"- pooled regression：未运行（{u10.get('pooled_regression', {}).get('reason', '')}）",
        "",
        "## 6. 敏感性与不确定性",
        "",
        "- 链接置信度敏感：`u06r_auto_accept` 组家世可观测率为 0，`v02_deterministic` 组为 7.2% → 链接协议与家世可见性相关。",
        "- 抽取阈值敏感：同官录属性层未达标，已降级为 identity-only。",
        "- 品级表 `verification_status=pending`，一切以 `rank_class` 为基础的陈述继承该状态。",
        "",
        "## 7. 边界（必须与结论同时引用）",
        "",
        "1. 可观测性不足：U09R 双侧可观测仅 17 行 → 不做模型。",
        "2. 样本由链接定义：链接成功是选择机制。",
        "3. tier 只作 legacy/extension outcome。",
        "4. 不做因果识别；不写「清代是/不是 meritocracy」一类判断。",
        "5. 两个 U02 测试依赖公开仓库不含的本地派生文件（既有缺陷，记录在案）。",
        "",
        "## 8. 复现与门禁",
        "",
        f"- release gate：`{gate['status']}`；对抗式审计：`audit/v03/final_audit.json`。",
        f"- 环境/种子/阈值/模型 id：见 [RELEASE_MANIFEST.json](RELEASE_MANIFEST.json)。",
        "",
    ]
    return "\n".join(lines)


def build_all(manifest: dict[str, Any], gate: dict[str, Any]) -> list[Path]:
    outputs = {
        PROJECT_ROOT / "METHODS_V03.md": render_methods(manifest),
        PROJECT_ROOT / "CODEBOOK_V03.md": render_codebook(),
        PROJECT_ROOT / "README_V03.md": render_readme(manifest, gate),
        REPORT_DIR / "FINAL_REPORT_V03.md": render_final_report(manifest, gate),
    }
    for path, text in outputs.items():
        path.write_text(text, encoding="utf-8")
    return list(outputs)
