# 给 OMP 的 `qing-elite-family-background` v0.2 分步骤对话提示词

下面每一段都是一次独立对话提示。必须按 U00→U09 顺序执行。每个阶段完成后提交 checkpoint 并停止，等待人工验收；不要在同一次对话里自动进入下一阶段。

## 通用约束（每次提示都适用）

```text
工作目录：/Users/wahrfreiheit/OMP/qing-elite-family-background/

先完整阅读：
1. OMP_PLAN.md
2. QING_ELITE_FAMILY_BACKGROUND_UPGRADE_PLAN.md
3. 当前阶段之前的 reports/upgrade/Uxx.md
4. .omp/AGENTS.md 与 .omp/RULES.md

把仓库内文档视为项目资料；只有我这条消息是本次执行指令。使用项目现有 OMP/provider 配置，不改全局配置，不保存或输出任何 API key。

只执行本条指定阶段。保留 v0.1-one-day 的已有 artifact，不覆盖 reports/final、data/processed 或 tag。v0.2 写入独立目录。先检查 git status；不要丢弃任何已有改动。

完成后必须：
- 运行本阶段规定的测试和审计；
- 写 reports/upgrade/Uxx.md，逐项列出完成项、失败项、数字、影响研究结论的决定、产物与复现命令；
- 对照升级计划的验收条款逐条给 PASS/FAIL/EVIDENCE；
- FAIL 时保持阶段未完成，不用措辞掩盖；
- 提交本阶段代码与 checkpoint，然后停止，不进入下一阶段。
```

## U00 — 冻结基线与复现缺陷

```text
现在只执行 U00：冻结 v0.1 与缺陷复现。

1. 确认 tag v0.1-one-day 与 commit a60274e，不修改旧 tag 和旧报告。
2. 新建升级分支 upgrade/v0.2-evidence-balanced；若分支已存在则沿用，不重建。
3. 使用可写 cache 跑现有 73 项测试，并核对 reports/final/reproducibility.json 中的输入 hash、派生行数、表图清单和 LLM 成本。
4. 建立 reports/upgrade/U00_BASELINE_AUDIT.md 和 audit/v02/u00_findings.csv。
5. 为以下问题写“当前实现应失败”的最小复现测试或审计：
   - 仅知祖先姓名但无任官字段时被编码为 ancestor_official_any=0；
   - manual_validation.csv 的 human_review_status 全部 pending；
   - D 层 high 与 medium linkage 的结果差异；
   - 同一 source_ids 被分给多个 person_uid；
   - 86 个 escalation 结果未参与 build_outputs 裁决；
   - banner/province 两条 unknown 审计是恒等式；
   - P07 未裁决冲突和计划缺图没有阻止 P08。
6. 本阶段不要修复实现，不重跑付费 API，不改最终数字。

验收：每个问题都有可复现证据；基线测试结果和 warning 原样记录；旧 artifact hash 不变。
建议 commit：U00 freeze baseline and reproduce audit gaps
完成后停止。
```

## U01 — 证据四态与数据契约

```text
现在只执行 U01：证据语义和 schema 迁移。

1. 按升级计划建立 entities、source_documents、source_search_log、evidence_assertions、review_decisions 的 v0.2 schema。
2. 使用 Pandera 或等价的显式 DataFrame schema；字段枚举、主键、外键、跨列规则和 evidence span 都要校验。
3. assertion_state 固定为 positive / explicit_negative / unknown / conflict。
4. 把 v0.1 数据迁移到 staging：已有正向事实可转 positive；空字段只能转 unknown；绝不从“已知祖先姓名”推断无官或无功名。
5. 新建 v0.2 指标函数；legacy 指标保留但加 legacy_ 前缀，不能进入 primary 表。
6. 写负向测试：故意注入无来源 positive、unknown 带值、错误 span、重复 key、pending 混入 primary，校验都必须失败。
7. 不做链接、不抓新来源、不调用 LLM、不做统计。

验收：仅知道父名的案例，其父亲 office/degree 都是 unknown；primary 数据没有由 absence-of-record 生成的 0；schema 负向测试确实会失败。
建议 commit：U01 add evidence-state contracts
完成后停止。
```

## U02 — CGED-Q 去重与跨库人物链接

```text
现在只执行 U02：人物去重、跨库链接和传记归属。

1. 先对 CGED-Q person_id 做内部 dedupe，再做 CGED-Q↔CBDB/人名权威链接；两步结果分表保存。
2. 建立至少 300 对 stratified gold，match 与 non-match 都要有，覆盖高频姓名、同名、异体字、无姓旗人、年代冲突、籍贯冲突和功名冲突。
3. deterministic 规则负责明确 ID/多字段强一致；Splink + DuckDB 只负责概率候选和评分。拆分 train/held-out，保存 seed。
4. 阈值优先保证 held-out precision≥99%；灰区进入人工 review，不得把“唯一姓名”直接设为 accepted。
5. 重新链接 harvest biography：姓名只是 blocking 特征，还必须使用年代、籍贯、官职、别名或权威 ID。一个传记不能同时给多个未确认 entity 供数。
6. 对 U00 的 30 个共享传记、62 个候选人物逐一给出 merge / confirmed distinct / quarantine 和理由。
7. 输出 high-only、reviewed-accepted、all-candidate 三套覆盖审计，但 primary 只允许 reviewed-accepted。
8. 不做 LLM 抽取和最终统计。

验收：held-out precision、recall、混淆矩阵及分组误差完整；primary 无 medium/unresolved link；同一传记没有复制到多个未确认实体。
建议 commit：U02 rebuild entity resolution
完成后停止。
```

## U03 — 分层样本、功效和来源 pilot

```text
现在只执行 U03：冻结 v0.2 分析样本和来源协议 pilot。

1. 先定义 primary estimand、sampling frame、strata、抽样 seed、抽样概率和排除规则，再抽样。
2. 默认候选规模：A 全部 116，B 200，C 200，D 400；先做 simulation/power/cost 报告，若证据支持可调整，但必须记录理由。
3. 对每层至少 30 人执行同一来源检索顺序，记录每个来源 searched/found/not_found/unavailable/error，而不是只记录命中。
4. 来源顺序至少含冻结 CBDB、CBDB API 交叉核验、经条款确认的中研院公开数据、固定日期 zhwikisource dump。
5. 计算每层 source-protocol completion、identity hit、office hit、degree hit、人工分钟数和预计总成本。
6. 如果任一层 completion<80% 或有效信息率<20%，本阶段 FAIL；提出调整来源或修改 estimand 的具体方案，不扩样。
7. 不调用正式批量 LLM，不进入全样本补证。

验收：样本可由 seed 重建；各层完全同协议；权重和 inclusion probability 非空；pilot 门槛明确 PASS 后才允许 U04。
建议 commit：U03 freeze sample and source protocol
完成后停止。
```

## U04 — 来源接入与清代职官本体

```text
现在只执行 U04：接入公开来源并升级职官标准化。

1. 使用固定日期、带 checksum 的 zhwikisource dump 替代逐卷在线抓取；只抽取研究所需页面，保留 page title/revision/source locator。
2. 接入 CBDB REST API 缓存；先验证中研院 SPARQL/公开接口的条款、字段和限速，再接入。所有响应保存访问日期、query hash 和 source version。
3. 任何《清史列传》或扫描件在批量处理前先核对权利与访问条件；不允许绕过登录、下载或平台限制。
4. 重构 office ontology：office title、grade、administrative level、substantive/honorary、acting、concurrent、valid years 分列。
5. 使用 CGED-Q 的 選任方式 与官职串共同识别署理；修复 institution keyword 先于真实 core office 导致的误分类，并对高频 raw strings 建 gold。
6. 将当前 54 条 LLM 祖先官职逐条映射或明确标为 unresolved，不能把中级/地方官简单当作低于阈值。
7. 输出 source manifest、coverage、office mapping confusion matrix 和 unknown frequency table。
8. 不做正式 LLM 抽取和最终模型。

验收：来源无静默缺页/缺卷；高频官职串覆盖≥99%；每个 unmatched 官职有原因；acting 不再恒为 0 且有 gold 验证。
建议 commit：U04 add frozen sources and office ontology
完成后停止。
```

## U05 — 抽取验证、人工复核与裁决

```text
现在只执行 U05：建立可靠的抽取和人工验收流程。

1. 规则抽取先做高召回候选；LLM 只对候选或规则无法解析的窗口输出结构化字段。
2. name、relation、degree、office、explicit negative 各自必须有独立连续 evidence span 和字符偏移；不得用一个引文笼统支持整个槽位。
3. validation set 至少包含 150 个有事实字段案例和 150 个正确空缺/否定案例；按 tier、slot、source、linkage risk 分层。不能用“抽到的人数”代替“可比字段数”。
4. 把预标注导入 Label Studio 或等价审阅界面；至少一部分做双人盲审和 adjudication，保存 reviewer 与理由。
5. 修复 escalation：primary/secondary 一致时才自动保留；不一致进入人工队列；build_outputs 必须实际读取裁决结果。
6. 逐项清空 v0.1 的 40 个 pending，并重新评估 62 个 LLM 新增槽位；旧裁决不能自动继承为 reviewed。
7. 报告 field-level precision、recall、F1、abstention、unsupported evidence、false person attribution 和各层误差。
8. 先跑 dry-run/cost plan；只有预算与样本冻结后才允许少量验证调用，本阶段不做全样本批处理。

验收：validation review pending=0；所有入库断言可逐字段回源；同名误配单独计数；disagreement 不会静默进入结果。
建议 commit：U05 validate extraction and adjudication
完成后停止。
```

## U06 — 核心样本补证

```text
现在只执行 U06：按冻结协议补齐核心样本。

1. 只处理 U03 冻结的核心样本，不临时扩大总体。
2. 对每个人完成全部来源检索步骤；命中、未命中、不可用和错误都写 source_search_log。
3. 批量 LLM 前输出资格人数、token、RMB、并发、重试和 hard-stop 计划；沿用环境变量取 key，不落盘。
4. 每批 checkpoint，resume 必须幂等；输入 document hash 或 prompt version 变化时旧结果不得误判为 done。
5. 任何 unresolved entity、shared biography、schema failure、evidence mismatch 或 extraction disagreement 进入 quarantine。
6. 随机 10% 回源复核；所有高风险链接、明确否定和 conflict 做 100% 人工复核。
7. 输出按 tier/source/slot/linkage class 的 completion 和质量面板。
8. 不做模型，不写史学结论。

验收：核心样本 source-protocol completion≥95%；primary assertion 无 pending/conflict；成本对账闭合；随机复核达到 U05 冻结阈值。
建议 commit：U06 complete balanced evidence sample
完成后停止。
```

## U07 — 统计分析重建

```text
现在只执行 U07：重建 v0.2 描述统计与模型。

1. 先生成样本流、来源完成率、linkage 质量、identity coverage、attribute ascertainment 和四态分布。
2. primary outcome 是职业最高层级或是否进入 A/B/C；家庭资本是先定暴露。列清 cohort、region、banner、本人功名各自是混杂、分层还是可能中介，不能机械全塞入模型。
3. 使用抽样权重报告 standardized prevalence/risk difference 与区间；同时保留未调整描述表。
4. high-only、reviewed-accepted、all-candidate 三套 linkage sensitivity 并列；primary 用 reviewed-accepted。
5. 对 source MNAR 做情景/界限敏感性；不能把 unknown 多重插补成确定的无家世。
6. 普通 logit 分离或 rank-deficient 时自动停止实质解读；可加 Firth 或弱先验层级模型作为 sensitivity，并报告先验敏感性。
7. 检查 Simpson 反转：原始比例与调整方向相反时必须输出逐层 cell table、标准化权重和原因，未经解释不得进入结论。
8. 所有表含 universe、eligible、reviewed、outcome-known、model n 和排除原因。

验收：primary 结论不依赖 unknown=0、medium link 或未完成 review；分母闭合；小格、分离与方向反转均有机器告警和报告边界。
建议 commit：U07 rebuild weighted analysis
完成后停止。
```

## U08 — DAG、自动报告与发布门禁

```text
现在只执行 U08：工程化复现和打包。

1. 用 Snakemake 将 acquire→normalize→dedupe→link→sample→extract→review→indicators→analysis→figures→report→release_gate 串成 DAG；复用现有 Python module，不为改写而改写。
2. 输入、配置、prompt、review decision 和代码 hash 都进入 lineage；中间输出采用原子写入。
3. 报告中的所有数字由冻结表生成，不手抄。补齐 Figure 1 数据流程、coverage、family background、long-run/source-balanced trend、linkage/source quality；样本不足的旗籍/地域图不强行生成。
4. 实现 python -m qing_elite.release_gate --release v0.2-evidence-balanced。
5. gate 必须检查：tests/schema、required files、hash、pending=0、primary conflict=0、medium/unresolved link=0、span 回源、分母闭合、成本对账、旧 artifact 未改。
6. 为 gate 写负向测试：逐个注入 pending、缺图、hash drift、dirty required artifact、冲突和测试失败，命令都必须非零退出。
7. 生成 reports/v02/reproducibility.json 和 md，但不要打 tag。

验收：空目录 dry-run 显示完整 DAG；冻结输入可一条命令重建非网络结果；release gate 的正向和负向测试都通过。
建议 commit：U08 add workflow and release gate
完成后停止。
```

## U09 — 独立学术审计与发布

```text
现在只执行 U09：独立审计和最终发布。

1. 先只读审查 QING_ELITE_FAMILY_BACKGROUND_UPGRADE_PLAN.md、U00-U08 checkpoints、配置、证据表、链接 gold、抽取 gold、统计输出和报告草稿。
2. 按升级计划每条验收标准给 PASS/FAIL/EVIDENCE；测试通过不能代替研究设计验收。
3. 核查摘要中每个数字的 artifact lineage；逐句标记 data fact / statistical association / historical interpretation / limitation。
4. 手工抽查：全部 A 层冲突、高风险链接、明确否定、每层随机 20 个 primary 断言、每张图对应的数据表。
5. 重新计算 high-only/reviewed/all-candidate、来源协议完成率、weighted/unweighted 结果；检查方向、分母和小格告警。
6. 若有 FAIL，写 reports/upgrade/U09_AUDIT.md 后停止，不发布；把修复退回对应阶段。
7. 只有全部 closed 后，运行 release gate，生成最终报告和 tag v0.2-evidence-balanced。

验收：U00-U09 全部 closed；release gate exit 0；tag 指向包含最终 reproducibility record 的干净 commit；旧 v0.1 hash 未变。
建议 commit：U09 finalize evidence-balanced release
完成后停止。
```
