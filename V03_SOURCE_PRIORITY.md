# V0.3 SOURCE PRIORITY

- 版本：`source-priority-v1`
- 机器可读来源：`config/v03/source_priority.yaml`
- 本文件只登记**设计优先级与判定规则**；实际可行性由 U04R 的机器 probe 写入 `data/processed_v03/source_feasibility.parquet`。

---

## 1. 铁律

```text
在 U04R 完成 probe 之前，不得假设任何来源可以下载。
```

- 论文里写"我们有 Tongguanlu dataset" ≠ 本项目能下载它；
- 禁止绕过登录、验证码、访问控制或使用条款；
- 未取得 bulk 权限的来源只能登记 `ACCESS_REQUEST_REQUIRED`，不得抓取；
- 每个来源必须登记：release/日期、URL、license/terms、文件 hash、获取方式、覆盖层级、已知偏差；
- 单一数据库打不开**不得阻塞整个工程**：必须给出 fallback。

## 2. 访问类型枚举（机器强制）

```text
PUBLIC_STRUCTURED · PUBLIC_SCAN · PUBLIC_UI_ONLY
ACCESS_REQUEST_REQUIRED · PAPER_TABLE_ONLY · UNAVAILABLE
```

## 3. 来源分层

| 层 | 定义 | 承担 |
| --- | --- | --- |
| **S** | 标准化结构化数据，机器可读 | person / career / kin 主干 |
| **A** | 标准化家世史料（题名、履历、齿录类），机器可读或可 OCR | **家族资本暴露** |
| **B** | 自由文本补充（传记、方志） | 只做补缺与交叉验证 |
| **C** | 在线 UI 查询 | 仅人工核验 |

## 4. 候选来源（`probe_status` 全部为 `pending`）

| 来源 | 层 | 角色 |
| --- | --- | --- |
| `cbdb` | S | canonical person、kin、degree、office |
| `cgedq_jsl` | S | career panel、出身/籍贯 |
| `tongguanlu` | A | 同官录：官员—亲属—功名（标准化） |
| `tongnianchilu` | A | 同年齿录：科年 cohort + 亲属 |
| `mingjingtongpu` | A | 明经通谱：五贡/监生类 |
| `zhujuan` | A | 硃卷：应试者家世履历 |
| `lvli_dangan` | A | 履历档案：任官序列 |
| `shengyuanlu` | A | 生员/低级生员名录 |
| `jinshenlu` | A | 缙绅录：年度官职名册 |
| `wikisource_dump` | B | 《清史稿》等自由文本补缺 |
| `sinica_authority` | C | 中研院人名权威（人工核验） |

## 5. 暴露侧与结果侧必须分开

```text
exposure_side: cbdb, tongguanlu, tongnianchilu, mingjingtongpu, zhujuan, lvli_dangan, shengyuanlu
outcome_side:  cgedq_jsl, jinshenlu, cbdb
backfill_only: wikisource_dump, sinica_authority
```

理由：v0.1/v0.2 从**最终官位**出发回头找家世，导致暴露与结果共用同一批"高官才有记载"的来源。V0.3 要求在 U04R 就按侧登记，避免设计回退。

## 6. U04R 的 probe 协议（下一阶段执行）

对每个候选来源保存：

```text
source_name, coverage_year, population, kin_scope, career_scope,
access_type, bulk_available, api_available, scan_available, machine_readable,
terms, redistribution, local_cache_allowed,
estimated_n, estimated_ocr_pages, automation_score, research_value
```

并输出 PLAN A（结构化直做）/ PLAN B（结构化 + 少量 OCR）/ PLAN C（主要靠扫描 OCR）三套方案，按预定义规则（coverage、research fit、machine accessibility、manual burden）自动选默认方案，**不是按"哪个听起来最好"**。

## 7. 已知问题（来自 v0.2 U03，必须被 U04R 处理）

| 问题 | 证据 | V0.3 处理 |
| --- | --- | --- |
| `sinica_lod` 端点不可机器查询（4 个候选端点全部返回 HTML 落地页） | `data/interim/v03/sinica_probe.json`、`reports/upgrade/U03.md` §5 | 保持 `PUBLIC_UI_ONLY`，仅人工核验；任何结论不得依赖该源 |
| CBDB API 对"无记录"返回 HTTP 404 | `reports/upgrade/U03.md` §5 次要项 | probe 时登记 status 语义；404 → `not_found`，不是 `error` |
| zhwikisource 逐卷抓取会缺卷 | v0.1 依赖在线 API | 改用**固定日期 dump**（U04R 起），登记 checksum，禁止在线增量 |
| CGED-Q 内部 dedupe 已由上游解决 | 2026-08-28 release 提供官方 `person_id` | 官方 ID 优先；v0.2 dedupe 只作对比基线 |

## 8. 与估计量的关系

来源可得性直接决定 estimand 的可估性（V0.2 的教训）：
若 A 层家世来源在 U04R 被判定为不可机器获取，**必须先改 estimand 或换来源，不得先扩样、更不得把失败写成"覆盖不足"继续往下一步走**。
