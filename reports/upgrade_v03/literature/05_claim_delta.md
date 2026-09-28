# 05 — Claim Delta：现代研究如何修订 V0.3 的工作假设

- 阶段：V0.3 U04R
- 决策枚举：`CONFIRMED` / `QUALIFIED` / `REVISED` / `REJECTED` / `UNCHANGED` / `UNRESOLVED`
- 依据：只读 digest（`sources/literature/digests/`）与来源探针（`data/processed_v03/source_feasibility.parquet`）
- 每条必须写：旧表述 → 证据 → 改的是哪一层（事实/因果/范围条件/置信度）→ 新表述

---

## VC-1：父—祖—曾祖三代足以刻画家族政治—教育资本

- **旧表述**：exposure 用 direct three-generation 指标即可。
- **证据**：`LIT-0007` 实测——按 Ho (1962) 定义属"新"的 Jinshi 中 **39.6% 至少另有一位有功名的年长男性亲属**；合计只有 16.0% 的 Jinshi 完全没有有功名的年长男性亲属 [P15]。亲属功名数量在亲属群体内高度聚集（相关 0.32–0.48）[P15–P16]。
- **改变层**：**范围条件 + 事实**（不只是精度问题，是测量对象不完整）。
- **决策**：**REVISED**
- **新表述**：exposure 必须同时给 `direct_3g_*` 与 `senior_collateral_*`；`direct_line_new_entrant` 与 `extended_family_new_entrant` 分开定义，且后者仍需标注可能为下界。
- **对 V0.3 的落地**：`V03_RESEARCH_CONTRACT.md` §3、`config/v03/relations.yaml:metric_families`。

## VC-2：家世差异可以用"有/无位祖先前史"的汇总比例表达

- **旧表述**：v0.2 的 `P(documented ancestor office | tier)` 文档率即为家世差异。
- **证据**：v0.2 自身的 U03 结果（D 层祖先信息率 0/30、frame 文档率 0/400）说明该比例主要在测**史料可见度**；`LIT-0007` 与 `LIT-0012` 都用个体级链接/测度而非汇总比例。
- **改变层**：**事实层**（该比例不是家世结构）。
- **决策**：**REJECTED**（作为 primary 量）
- **新表述**：文档率降级为 source-bias benchmark；primary 为个体级 family capital × career outcome 的描述性关联。

## VC-3：科举是清中期以后选官的主要资格渠道

- **旧表述**：以功名（科举）作为 exposure 的核心资格维度。
- **证据**：`LIT-0011` 主张捐纳"至少与科举同等重要"，财富是关键，捐纳者获得任官保证，并使显族出现世袭任官可能 [abstract]；`LIT-0016` 的样本中大量地方官持**捐纳功名与府级生员功名** [abstract]。
- **改变层**：**范围条件**（credential 的类别必须包含捐纳/荫/生员，而不只是进士—举人阶梯）。
- **决策**：**QUALIFIED**
- **新表述**：`credential` 分层必须保留 purchased / shengyuan / yinsheng 类别；credential 同时可能是 mediator，须并列报告控制/不控制两套结果。

## VC-4：功名越低，"新进者"占比越高

- **旧表述**：寒门比例随功名/官位层级下降而上升。
- **证据**：`LIT-0007` 报告 Gongsheng 的"新"人模式与 Juren 相似，作者自称"有些意外"，因为通常认为贡生更易为寒门获得 [P13]；同时直系三代无功名比例 Jinshi 26.47%、Juren 30.72%、Gongsheng 32.74%（差异小于预期）[P14]。
- **改变层**：**置信度**（不是被推翻，而是原有单调预期缺乏证据）。
- **决策**：**UNRESOLVED**
- **新表述**：V0.3 不预设单调梯度；只报告分层结果与分母。

## VC-5：旗籍只是分层变量，不改变家族再生产的机制

- **旧表述**：banner 仅作 stratum。
- **证据**：`LIT-0018` 主张制度性隔离（旗人特权）塑造长期精英再生产；旗人多代流动更低，但两组代际相对流动相当（≈0.4）；汉人靠教育投资与联姻对冲 [abstract]。
- **改变层**：**因果层**（解释机制不同）。
- **决策**：**QUALIFIED**
- **新表述**：banner 仍作 stratum，但结论中必须写明"制度差异可能改变机制"，且不得把两组结果合并成单一流动率。

## VC-6：记录链接可由唯一姓名或少量规则完成

- **旧表述**：v0.1 的 `unique_name` 判据。
- **证据**：`LIT-0001`（专名链接需要姓氏/名字/籍贯消歧，旗人须单独处理）、`LIT-0005`（笔画嵌入 + active learning + 图聚类）、`LIT-0023`（记录链接方法可导致错误推断）[均 abstract]；v0.2 U02 自身也把 medium 链接整体移出 primary。
- **改变层**：**事实层 + 方法层**。
- **决策**：**REJECTED**
- **新表述**：U06R 必须做 deterministic / ML / Splink 三方法一致 + 提高 abstention；`linkage_uncertainty` 进入 risk_score。

## VC-7：硃卷可以与同年齿录同等使用

- **旧表述**：把硃卷当作等价的标准化家世源。
- **证据**：`LIT-0007` 明言硃卷"有亲属师承但覆盖功名持有者不全、个人自印、编集不系统" [P8]；来源探针显示硃卷数字化渠道全部为商业/机构订阅（`zhujuan` = ACCESS_REQUEST_REQUIRED）。
- **改变层**：**范围条件 + 可得性**。
- **决策**：**QUALIFIED**（降为补充源）
- **新表述**：硃卷只作 cross-validation 来源，不作 exposure 主干；若使用须先解决订阅/馆际与抽样代表性问题。

## VC-8：地方低级官员的家世可由既有结构化数据库补足

- **旧表述**：v0.1/v0.2 期望用 CBDB 补 D 层家世。
- **证据**：v0.2 U02/U03（D 层 high-only 链接 0.78%、祖先信息率 0/30）；本阶段探针显示 CBDB 的 kin 覆盖对清代地方官为 **PARTIAL/LOW**，而覆盖该人群的同官录（`tongguanlu`，BROAD/HIGH）为 **ACCESS_REQUEST_REQUIRED**，同年齿录（BROAD/HIGH）为 **PUBLIC_UI_ONLY**。
- **改变层**：**事实层**（数据可得性）。
- **决策**：**REJECTED**
- **新表述**：默认方案 = PLAN_C；U05R 前必须先用申请通道取得同官录/同年齿录编码数据，或改 estimand（见 `data/processed_v03/source_plan.json`）。

## VC-9：记录链接与抽取的不确定性可以用固定比例人工抽查控制

- **旧表述**：v0.2 计划的"随机 10% 回源复核"。
- **证据**：`LIT-0023` 警告链接方法的误用会导致错误推断 [abstract]；V0.3 政策文件已禁止固定比例审核（`V03_AUTOMATION_POLICY.md` §2）。
- **改变层**：**方法层**。
- **决策**：**REJECTED**
- **新表述**：按 risk_score 分层路由，人工只处理 HIGH 残差，预算为绝对条数。

---

## 汇总

| 决策 | 条数 | 条目 |
| --- | --- | --- |
| REJECTED | 3 | VC-2、VC-6、VC-8 |
| REVISED | 1 | VC-1 |
| QUALIFIED | 3 | VC-3、VC-5、VC-7 |
| UNRESOLVED | 1 | VC-4 |
| CONFIRMED / UNCHANGED | 0 | — |

本阶段**没有**因为文献而修改 `V03_RESEARCH_CONTRACT.md` 的 exposure/outcome 定义：文献修订的是测量范围（VC-1）、资格类别（VC-3）与数据可得性（VC-8），
这三项都已在本阶段的 `config/v03/*` 与来源计划中落地。
