# 01 — U04R 文献检索计划与执行记录

- 阶段：V0.3 U04R
- 执行日期：2026-09-27
- 机器配置：`config/v03/literature.yaml`（`pipeline_version: 0.3-lit-1`）
- 框架来源：三通项目已验证的 `ACADEMIC_LITERATURE_PIPELINE_HANDOFF`（只复用框架与字段约定）

## 1. 检索设计

12 个必查 query cluster，共 36 条 query，逐条对两个提供方检索（cache 落 `data/interim_v03/lit/raw/`）：

| cluster | 主题 | query 数 |
| --- | --- | --- |
| `Q1_qing_social_mobility` | 清代社会流动 | 3 |
| `Q2_family_background` | 家世/家族背景 | 3 |
| `Q3_kin_network` | 亲属网络 | 3 |
| `Q4_tongnianchilu` | 同年齿录 | 3 |
| `Q5_mingjingtongpu` | 明经通谱 | 3 |
| `Q6_tongguanlu` | 同官录 | 3 |
| `Q7_zhujuan` | 硃卷 | 3 |
| `Q8_shengyuanlu` | 生员录 | 3 |
| `Q9_jinshenlu` | 缙绅录 | 3 |
| `Q10_career_mobility` | 职业流动 | 3 |
| `Q11_office_purchase` | 捐纳 | 3 |
| `Q12_record_linkage` | 中文历史人物记录链接 | 3 |

## 2. 提供方实际状态（本次执行的真实结果）

| 提供方 | 结果 | 证据 |
| --- | --- | --- |
| **Crossref** | 36/36 query 成功 | `data/interim_v03/lit/raw/provider_log.json` 全部 `status: ok` |
| **Semantic Scholar** | 36/36 返回 429（公共池限流），**0 条落盘** | 无 `semanticscholar-*.json` 缓存文件；首次脚本探测（2 次请求）曾返回 200 |
| **OpenAlex** | 匿名检索暂停 | `{"error":"Search temporarily unavailable","message":"Anonymous search is paused while the search cluster recovers from heavy load..."}`（503） |
| Unpaywall | 未使用 | API 要求填**本人**邮箱（返回 422 拒绝占位邮箱）；本阶段不伪造身份 |

→ 本阶段的候选池由 Crossref 单独支撑；摘要来自 Crossref 的 abstract 字段。OpenAlex 与 S2 的状态写入
`config/v03/literature.yaml:providers_unavailable`，供后续阶段决定是否申请 API key。

## 3. 检索 → 候选池 → core

```text
36 query × 2 provider（实际只有 crossref 有返回）
  → 原始候选 1,000+ 条 → 去重（DOI，否则归一化标题）→ 709 条
  → relevance_score（config 权重）→ 主题门槛（标题级 required_any_patterns）→ 同题多版本折叠
  → core 10 / candidate 703
```

- **主题门槛只看标题**：摘要里的 "elite/official/career" 常见于离题论文（例如"官员自杀研究"），
  用它选核心论文会引入无关文献；实测该规则把 suicide / COVID / fiscal governance 三类论文挡在 core 之外。
- **同题多版本折叠**：同一篇论文常以期刊版/仓储版/预印本三种 DOI 出现，按（有摘要 > 被引 > 年份）保留一条，
  其余降为 candidate 并记录 `duplicate_of`。
- **策展入口**：检索 API 有覆盖盲区（2026 年 Tongguanlu 论文在 S2/Crossref 关键词检索里未命中），
  因此 `config/v03/literature_manual.yaml` 允许登记策展条目，但**每条必须带 evidence_url 与 retrieved_at**。

## 4. 全文获取

`python -m qing_elite.v03.lit.acquire` 只抓取「许可清楚的公开 OA 位置」：

| 结果 | 数量 | 说明 |
| --- | --- | --- |
| 有 OA 位置并成功解析 | **2** | `LIT-0007`（OSF 预印本，20 页）、`LIT-0017`（SSHA 工作论文，8 页） |
| 有摘要、无 OA 全文 | 8 篇 core | 期刊论文，Crossref 未提供 OA |
| 无摘要、仅题录 | 582（candidate） | 不进 core，不写 digest |
| 人工获取队列 | 见 `sources/literature/queues/acquisition_queue.md` | 禁批量下载机构全文；需人工放件 |

**本阶段没有取得任何机构授权全文，也没有绕过任何登录/付费墙。**

## 5. 预期覆盖与实际缺口

- 覆盖到位：清代社会流动、家世/亲属网络、捐纳、同年齿录/明经通谱/同官录、记录链接五类主题都有 core 论文。
- 缺口（显式记录，不猜）：
  1. 硃卷（zhujuan）只有 1 篇 core（`LIT-0009`，单案例），缺少系统性硃卷研究；
  2. 缙绅录方法学论文（JCH 2020）只拿到题录（Cambridge Core 当日 404），降为 candidate；
  3. 同期 S2 限流导致中文期刊（CNKI 系）文献基本缺失——本阶段未使用 CNKI/CARSI 通道（需登录态，禁止绕过）。
