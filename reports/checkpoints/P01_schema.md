# P01 — Schema Inspection 与数据源验真（实测记录）

- 阶段：P01（数据源获取 / 验真 / schema inspection）
- 日期：2026-09-13
- 原则：**只记录实际执行并观测到的结果**；所有校验值均由本机重新计算
- 原始文件只读；`data/raw/*` 不进入 Git，登记见 `data/raw/manifest.json`

---

## 1. CBDB：release 固定与校验

| 项 | 值 |
| --- | --- |
| release id | `cbdb_20260912` |
| release date | 2026-09-12（`latest.json.generated_at_utc = 2026-09-12T19:14:03Z`） |
| 元数据来源 | `https://raw.githubusercontent.com/cbdb-project/cbdb_sqlite/master/latest.json` |
| 下载地址 | `https://huggingface.co/datasets/cbdb/cbdb-sqlite/resolve/main/history/cbdb_202609/cbdb_20260912.zip` |
| 本地文件 | `data/raw/cbdb/cbdb_20260912.zip`（139,130,577 B）、`cbdb_20260912.sqlite3`（586,194,944 B） |

校验（本机 `shasum -a 256`）：

```text
0f9ff7b93cd6b8b6c172f7edf03eaff66e8a593164f3047c3f4eff434d104678  cbdb_20260912.zip
604a4ce0872776a958b4f5b263ec10133644710524bcb1cb904d8866a04daa62  cbdb_20260912.sqlite3
```

- `sqlite3` 的 SHA256 **等于** `latest.json` 公布的 `sha256` → **校验通过**。
- zip 的 SHA256 **等于** Hugging Face 仓库中该路径与 `latest.zip` 的 LFS oid（`0f9ff7b9…`）→ 证明该 release 在 HF 侧不可变，且 `latest.zip` 当前即指本 release。

发行渠道说明（已核实的阻碍）：

- `cbdb.hsites.harvard.edu`（站点与下载页）对脚本化访问返回 **HTTP 403**（WAF），因此改用 CBDB 项目自己的 `cbdb_sqlite` 仓库指定的 Hugging Face 渠道。
- GitHub REST API 在本机出口 IP 上 **HTTP 403 rate limited**；改用 `git ls-remote` 与 `raw.githubusercontent.com`。
- release 以 HF LFS 固定 + SHA256 记录，满足"固定 release、不自动更新"。

CBDB User's Guide（`cbdb_users_guide.pdf`）实时站点同样 403；已从 **Internet Archive 快照 `20251104010037`** 取回（`data/raw/cbdb/cbdb_users_guide_2025-11-04_wayback.pdf`，8,767,479 B，157 页），文本抽取至 `data/interim/cbdb_users_guide_wayback_2025-11-04.txt`（派生文件，不入库）。

## 2. CBDB 结构总览

```sql
SELECT type, count(*) FROM sqlite_master GROUP BY type;
-- table = 78, index = 76
```

- 78 张表，合计 **5,620,619 行**。
- 核心表行数：`BIOG_MAIN` 661,659；`POSTED_TO_OFFICE_DATA` 591,256；`POSTING_DATA` 591,225；`KIN_DATA` 562,265；`POSTED_TO_ADDR_DATA` 465,122；`BIOG_ADDR_DATA` 461,320；`ENTRY_DATA` 264,905；`ALTNAME_DATA` 208,771；`ASSOC_DATA` 190,036；`STATUS_DATA` 73,450；`BIOG_SOURCE_DATA` 1,253,775。
- **该 release 不含** User Guide §表清单中的 `ETHNICITY_TRIBE_CODES`，也不含便利视图与 `ADDRESSES` 表（CBDB 项目提供 `scripts/create_views.sh` / `create_addresses_table.py` 在**副本**上生成；P02 如需 views，必须在 `data/interim/` 的副本上做，`data/raw` 保持只读）。

研究相关核心表（列名实测）：

| 表 | 关键列 |
| --- | --- |
| `BIOG_MAIN` | `c_personid, c_name_chn, c_surname_chn, c_mingzi_chn, c_index_year, c_index_year_type_code, c_birthyear, c_deathyear, c_dy, c_ethnicity_code, c_household_status_code, c_fl_earliest_year, c_fl_latest_year, c_index_addr_id, c_index_addr_type_code` |
| `KIN_DATA` | `c_personid, c_kin_id, c_kin_code, c_source, c_pages` |
| `KINSHIP_CODES` | `c_kincode, c_kinrel_chn, c_kinrel, c_upstep, c_dwnstep, c_marstep, c_colstep` |
| `POSTED_TO_OFFICE_DATA` | `c_personid, c_office_id, c_posting_id, c_sequence, c_firstyear, c_lastyear, c_appt_code, c_assume_office_code, c_inst_code, c_inst_name_code, c_office_category_id, c_dy, c_notes, c_pages` |
| `POSTING_DATA` | `c_personid, c_posting_id` |
| `OFFICE_CODES` | `c_office_id, c_dy, c_office_chn, c_office_chn_alt, c_office_trans, c_office_pinyin, c_source, c_notes` |
| `ENTRY_DATA` | `c_personid, c_entry_code, c_year, c_exam_rank, c_kin_code, c_kin_id, c_assoc_code, c_assoc_id, c_inst_code` |
| `ENTRY_CODES` | `c_entry_code, c_entry_desc, c_entry_desc_chn` |
| `STATUS_DATA` | `c_personid, c_status_code, c_firstyear, c_lastyear, c_supplement, c_source` |
| `ALTNAME_DATA` | `c_personid, c_alt_name_chn, c_alt_name_type_code, c_sequence` |
| `BIOG_ADDR_DATA` | `c_personid, c_addr_id, c_addr_type, c_firstyear, c_lastyear, c_natal` |
| `ADDR_CODES` | `c_addr_id, c_name_chn, c_admin_type, c_firstyear, c_lastyear, c_x_coord, c_y_coord` |
| `DYNASTIES` | `c_dy, c_dynasty_chn, c_start, c_end` |

## 3. 本研究要用的代码表（实测内容）

- `DYNASTIES`：**清 = 20**（1644–1911）、明 = 19、南明 = 80。
- `KINSHIP_CODES`：**父 = 75（F）**、**祖父 = 62（FF）**、**曾祖 = 48（FFF）**；另有 嗣父 82、繼父 98、養父 107、從父 79、本生父 363（本库 0 行）、嗣祖父 440、本生祖 563、伯叔祖 64 等。三代变量必须显式声明纳入哪些变体，否则覆盖率会被低估。
- `BIOG_ADDR_CODES`：地址类型 **13 = Eight Banner Qing Dynasty / 八旗清代**，其 `c_index_addr_rank = 6`，与 User Guide 的籍贯指派优先级列表第 6 项"Eight Banners (Qing dynasty)"一致。
- `STATUS_CODES`：**167–174 = 八旗（正黄/正蓝/正白/正红/镶黄/镶蓝/镶白/镶红）**；175–178 = 内务府包衣旗人。但见 §5：清代人物中实际使用者极少。
- `HOUSEHOLD_STATUS_CODES`：**16 = 旗籍（Qi household）**。清代人物中该字段实际只出现 未詳(0)/民戶(1)/軍戶(2) 等，**旗籍 0 人** → 该字段不可用于清代旗籍识别。
- `INDEXYEAR_TYPE_CODES`：31 种，除"據生年/據卒年"外大量为**派生规则**（如 `05 據進士登科年-30`、`11 據其父親生年+30`、`27 據其祖父生年+60`）。P02/P06 的时代构成必须显式区分实测年与派生年。
- `ENTRY_CODES` 在清代高频项（按人数）：鄉貢舉人 37,081；進士(籠統) 30,844；監生(籠統) 24,756；廩貢生 7,281；拔貢 7,178；附貢生 3,771；行伍 3,659。

## 4. 清代人物查询（已执行）

```sql
-- 清代人物总数
SELECT count(*) FROM BIOG_MAIN WHERE c_dy = 20;                          -- 237,826
-- 指数年在研究窗口内
SELECT count(*) FROM BIOG_MAIN WHERE c_dy=20 AND c_index_year BETWEEN 1644 AND 1820;  -- 42,231
-- 生卒年可用性
SELECT count(*) FROM BIOG_MAIN WHERE c_dy=20 AND c_birthyear IS NOT NULL;  -- 24,735
SELECT count(*) FROM BIOG_MAIN WHERE c_dy=20 AND c_deathyear IS NOT NULL;  -- 20,853
SELECT count(*) FROM BIOG_MAIN WHERE c_dy=20 AND c_index_year IS NULL;     -- 169,056
```

随机抽样（`ORDER BY random() LIMIT 10`，`c_dy=20 AND c_index_year BETWEEN 1644 AND 1820`）实测输出：

```text
(340040, '章朝栻', '章', '朝栻', None, None, 1750, '05', 1, 0, 20)
(557268, '張氏(孫楫妻)', '張', '氏(孫楫妻)', None, None, 1644, '2204', None, None, 20)
(361770, '樊丙南', '樊', '丙南', None, None, 1817, '05', 1, 0, 20)
(78814,  '何思溫', '何', '思溫', 1727, 1777, 1727, '01', 0, 0, 20)
(387192, '高鍾嶽', '高', '鍾嶽', None, None, 1682, '05', 1, 0, 20)
(361701, '萬禮祖', '萬', '禮祖', None, None, 1693, '05', 1, 0, 20)
(82886,  '羅興義', '羅', '興義', 1663, None, 1663, '01', 0, 0, 20)
(70418,  '沈蕙香', '沈', '蕙香', None, None, 1764, '08', 0, 0, 20)
(74279,  '朱鴻猷', '朱', '鴻猷', 1742, 1783, 1742, '01', 0, 0, 20)
(360466, '戴南昆', '戴', '南昆', None, None, 1781, '05', 1, 0, 20)
```

指数年十年分布（清代，1640–1830）：1640s 1,978 / 1650s 1,577 / 1660s 1,588 / 1670s 2,171 / 1680s 2,012 / 1690s 2,261 / 1700s 2,821 / 1710s 2,134 / 1720s 2,206 / 1730s 2,414 / 1740s 2,047 / 1750s 2,138 / 1760s 2,309 / 1770s 2,764 / 1780s 2,758 / 1790s 3,644 / 1800s 2,893 / 1810s 2,978 / 1820s 3,393 / 1830s 390。

## 5. CBDB 清代变量覆盖（决定 P02/P03 可行性）

| 变量（清代人物 % of 237,826） | 人数 | 覆盖率 |
| --- | --- | --- |
| 有任何任职记录 `POSTED_TO_OFFICE_DATA` | 147,481 | 62.0% |
| 有任何入仕记录 `ENTRY_DATA` | 129,546 | 54.5% |
| 有异名 `ALTNAME_DATA` | 51,522 | 21.7% |
| 有任何亲属记录 `KIN_DATA`（任意亲等） | 35,423 | 14.9% |
| **父**（kin 75） | 12,803 | **5.4%** |
| **祖父**（kin 62） | 4,183 | **1.8%** |
| **曾祖**（kin 48） | 1,261 | **0.5%** |
| 有任意地址 `BIOG_ADDR_DATA` | 181,061 | 76.1% |
| **八旗地址**（`c_addr_type=13`） | 4,141 | **1.7%** |
| 八旗 `STATUS_DATA`（167–178） | 19 | 0.008% |
| `c_household_status_code=16`（旗籍） | 0 | 0% |

八旗地址细分（前几类，人数）：滿洲鑲黃旗 495、滿洲正黃旗 442、滿洲正白旗 416、滿洲正藍旗 345、滿洲鑲藍旗 306、滿洲鑲白旗 303、滿洲鑲紅旗 270、滿洲正紅旗 249、漢軍鑲黃旗 180、漢軍正白旗 165、漢軍正黃旗 122、蒙古正黃旗 99 ……（`ADDR_CODES.c_admin_type = 'Baqi'`）。

**结论（P01 核心发现之一）**：CBDB 的清代**三代家世覆盖极薄**（父 5.4%、祖 1.8%、曾祖 0.5%），远不足以独立支撑 A/B/C 层的家世指标；旗籍也**只能靠八旗地址**（1.7%），且 `c_ethnicity_code` 因 `ETHNICITY_TRIBE_CODES` 表未随 release 发布而**无法本地解码**（清代取值：NULL 154,996 / 0 56,457 / 1 23,628 / 131 1,711 / 169 1,034）。这直接决定 P04/P05 的 LLM 抽取不可省略，也决定 coverage 必须作为第一结果。

## 6. CGED-Q JSL：release 固定与校验

| 项 | 值 |
| --- | --- |
| 数据集 | China Government Employee Database-Qing (CGED-Q) Jinshenlu Public Release |
| DOI | `10.14711/dataset/E9GKRS`（DataSpace@HKUST，version 24.0） |
| release date | 2026-08-31（`latestVersion.releaseTime = 2026-08-31T01:38:06Z`） |
| 许可 | **CC0 1.0** |
| 获取方式 | Dataverse API（**无需浏览器人工下载**）：`/api/datasets/:persistentId/` 取文件清单，`/api/access/datafile/<id>` 取文件 |

| 文件（本地名） | 官方文件名 | 字节 | 校验 |
| --- | --- | --- | --- |
| `cgedq_jsl_public_1760-1912_personid_2026-08-28.tab` | `CGED-Q Public Release 1760-1798 1850-1864 1900-1912 with person_id 28 Aug 2026.tab`（id 3014） | 233,703,315 | MD5 `3aa263e87030f50d60f00912c4e82afb` **通过** |
| `cgedq_jsl_chushen_recodes.tab` | Chushen Recodes（id 3011） | 9,527 | MD5 `14efe7f3b3d7e308e3e15fee9ec44823` **通过**（须 `?format=original`） |
| `cgedq_jsl_province_recodes.tab` | Province of Origin 籍貫省 Recodes（id 3012） | 999 | MD5 `fd678776d9959e9fab726fe4ae52b34c` **通过**（须 `?format=original`） |
| `cgedq_jsl_user_guide_v4_2025-07.pdf` | （FINAL）中国历史官员量化数据库公开版用户指南 v4 2025 July（id 2702） | 2,743,070 | MD5 `78e3877b9e2eab8442766c8a2679d9c8` **通过** |
| `cgedq_r_tutorial_v2_2025-06.pdf` | R语言在CGED-Q中的运用V2.0-June-2025（id 2703） | 15,258,336 | MD5 `d91a024523a640eba136772838ca1eab` **通过** |

**踩坑（必须记录）**：`/api/access/datafile/3011`（默认）返回的是 Dataverse 对 tabular 文件**重新摄取后的版本**（11,328 B，MD5 `db9c009b…`，内容带 BOM 与改写），与数据集登记的原始文件不符；只有加 `?format=original` 才拿到登记版本。脚本化复现时必须固定使用 `?format=original`。（3014/2702/2703 默认端点即原始字节。）

未下载的对照版本：id 3013（pinyin 变量名版本，231,313,180 B，MD5 `202e0984c809e6e41feb86b78d8fc2a8`），本阶段不取；如需 ASCII 列名再取。

## 7. CGED-Q 数据文件结构

- **48 列**（中文变量名，UTF-8，TSV，无引号包裹；DuckDB 需 `quote=''`、`ignore_errors=true`、`all_varchar=true` 才能解析）。
- 列名（按文件顺序）：`阳历年份, 季节号, 序号, 插补号, 卷号, 册号, 书名, 出版单位, 版本年号, 版本年代, 版本干支, 版本季节, 页码, 地区, 地名_题头, 選任方式, 机构一, 机构二, 机构三, 缺分, 官缺等级, 官职一, 官职二, 加级, 加级军功, 爵位, 身份一, 姓, 名, 字号, 原籍省, 原籍县, 籍贯省, 籍贯县, 身份二, 旗分, 出身一, 科年一, 出身二, 科年二, 铨选年号, 铨选年代, 铨选闰月, 铨选月, 铨选方式, 季节, record_number, person_id`
- **没有任何 CBDB id 列** → 与 CBDB 的 linkage 必须由本项目在 P03 自行构建（CGED-Q 团队内部的 46,538 条 ↔ 9,048 人匹配结果不在公开版里）。
- 总行数 **1,261,616**；`person_id` 为空的行 92,260（7.3%），去重后 `person_id` 132,361 个。
- 年代覆盖（`阳历年份` 取整）：1760,1761,1765,1768,1773,1777,1786,1788,1796,1797,1798（"1760–1798"批次）+ 1850–1864 + 1900–1912 三个批次同文件。
- **1760–1798 子集：190,472 行 / 54,238 人 / 16 个季节年**；季节号只出现 1（春，x.00）、3（夏，x.50）、4（秋，x.75），**无冬（2）**。`.tab` 的 `阳历年份` 为小数编码（`1765.75`=1765 年第 4 季）。
- 岗位空缺行：`名 = 空白` 85,067、`塗黑` 1,787、`涂黑` 18（全文件）；1760–1798 内 6,595 行。这些行 `person_id` 为空，**不能计入人数**。
- 同 `(person_id, 阳历年份, 季节号)` 的多行结构（1760–1798）：1 行 173,490 组、2 行 4,022 组、3 行 322 组、4 行 68 组、5 行 8 组、6 行 3 组、7 行 2 组、8 行 5 组 → **"一人一季多条"是兼任/多职/重复记录混在一起**，P02 去重必须逐案判断，不能简单 `drop_duplicates`。

### 1760–1798 变量完整度（非空 %，含空额行 / 仅具名行）

| 变量 | 含空额行 | 仅具名行 |
| --- | --- | --- |
| 姓 | 76.5% | 79.2% |
| 籍贯省 | 54.0% | 54.3% |
| 籍贯县 | 72.5% | 73.5% |
| 旗分 | 24.1% | 23.8% |
| 出身一 | 64.4% | 65.6% |
| 身份一 | 1.2% | 1.3% |
| 身份二 | 22.0% | 21.5% |
| 官职一 | 99.9% | 99.9% |
| 官缺等级 | 9.7% | 9.7% |
| 科年一 | 12.9% | 12.6% |
| 地区 | 100% | 100% |
| 机构一 | 98.8% | 98.8% |

### recode 表

- `Chushen Recodes`：106 行 × 9 列（`出身一, 出身二, chushen_1, chushen_2, chushen, chushen_category, chushen_order, chushen_order_2, chushen_order_2_eng`）→ 把杂散出身写法归并为 進士/舉人/貢生/監生… 类别，P02 分类直接复用，避免自造映射。
- `Province of Origin 籍貫省 Recodes`：65 行 × 2 列（`籍贯省, 籍貫省_clean`）→ 归并"江南/湖广"等特殊写法，含 `空白`、`外國`、`不顯` 等类别。

## 8. CGED-Q User Guide 要点（已抽取全文 60 页）

文本抽取：`data/interim/cgeq/user_guide_v4_2025-07.txt`（派生，不入库）。关键事实：

- 截至 2025-04，数据库含 **372 个版本、307,569 名清代官员、4,629,016 条记录**（文官 3,899,606 / 武官 729,410）。
- 用户指南（Table 8）称公开三批共 **89 季 1,232,355 条**；本 release（2026-08-31）实测为 **1,261,616 行**，说明公开数据仍在扩充 → 引用时必须写明 release 日期与行数。
- 体例与变量：`地区` 仅区分京师/盛京/行省；`缺分` 与 `官缺等级` 是两件事（分别对应"最要缺/要缺/中缺/简缺"与"冲繁疲难"四要素）；`官职一/二` 同时承载兼任与头衔，**不可自动拆分**；`籍贯省` 在"本籍官"情形下会被省略（约 45% 无值记录多属此类）→ `missing != commoner` 的教科书案例。
- 旗人有"称名不举姓"传统，故 `姓` 缺失高度集中于旗人；链接时姓名+籍贯+出身需组合使用，团队报告约 8.96% 记录至少一项变量不一致。
- 官方对 `person_id`（PersonID）的告诫：**建议使用者抽查准确度**；团队正改进方案。项目若做 person-level 分析必须自建校验。
- 允许的引用：数据集引用 DOI + 指南引用 + RGC 经费致谢（AoE/B-704/22-R、GRF 16602621）。

## 9. 中研院两库（小样本验真，各 ≤3 次查询）

| 库 | 入口 | 检索接口（实测） |
| --- | --- | --- |
| 人名權威—人物傳記資料庫 | `https://newarchive.ihp.sinica.edu.tw/sncaccgi/sncacFtp` | `GET ACTION=TQ,sncacFtpqf,(<姓名>)@TM,1st,search_simple` |
| 清代職官資料庫 | `https://newarchive.ihp.sinica.edu.tw/officerc/officertp` | `GET ACTION=TQ,officerdb,(<姓名>)@XE,officerdb/1st,officerdb/search`；详情 `XN=<记录号>` |

实测（本机 curl，均为 HTTP 200）：

| 人名 | 人名權威 | 清代職官 |
| --- | --- | --- |
| 張廷玉 | **2 筆**（列表页 `共2筆`）；详情含 生卒/籍貫/異名/出身/履歷/權威號 000032 | **24 筆**任职记录 |
| 鄂爾泰 | 命中 1 人（生卒 康熙16年-乾隆10年，權威號 000492） | **18 筆** |
| 陳宏謀 | 命中 1 人（生卒 康熙35年-乾隆36年，權威號 001018） | **36 筆** |

注：人名權威单一命中时列表页不打印"共 N 筆"，而是直接进入人物页；上表 1 人即由此判定。清代職官详情页（`XN=NO000000755`，HTTP 200，136,964 B）字段为 `職官名稱` + 表 `任職者 / 類別 / 滿漢缺 / 任期 / 備註 / 參考文獻`。

- 旧入口 `archive.ihp.sinica.edu.tw` 已不可用（连接被关闭）；必须用 `newarchive.ihp.sinica.edu.tw`。
- 无验证码、无 JS 挑战、无需登录；返回 HTML（非 JSON），需 `<th>/<td>` 解析。**人名權威的详情链接需复用列表页返回的会话参数（ID/SECU + cookie）**。
- 旁路：中研院数位文化中心 SPARQL `https://data.ascdc.tw/sparql`（实测 HTTP 200，`dqot` 图 164,773 triples），但 `dnb` 人物姓名图结构需进一步探 schema，暂不作为首选。
- **不做大规模爬取**：本阶段两库合计 9 次请求，仅用于 3 个人名的可检索性验证。

## 10. 《清史稿》机器可读来源

**选定**：中文维基文库 MediaWiki API。

```bash
curl 'https://zh.wikisource.org/w/api.php?action=query&prop=revisions&titles=%E6%B8%85%E5%8F%B2%E7%A8%BF/%E5%8D%B7288&rvprop=content&rvslots=main&format=json&formatversion=2'
```

实测：

- `清史稿/卷288`（列傳七十五）：HTTP 200，JSON 21,642 B，正文含 `== 張廷玉 ==`（"張廷玉，字衡臣，安徽桐城人，大學士英次子。康熙三十九年進士…"）与 `== 鄂爾泰 ==`（"鄂爾泰，字毅庵，西林覺羅氏，滿洲鑲藍旗人…"），页脚 `{{PD-old}}`。
- `清史稿/卷307`（列傳九十四）：HTTP 200，含 `==陳宏謀==`（"陳宏謀，字汝諮，廣西臨桂人…雍正元年恩科…"）。**更正**：陳宏謀在卷 307 而非常见误记的卷 308（卷 308 列傳九十五为那蘇圖、楊超曾等）。
- 许可：正文公版（PD-old）；站点贡献 CC BY-SA 4.0 / GFDL；无需鉴权。
- 风险：维基文库文本质量标注为 50%，存在异体字/PUA 残留，**只作定位卷次+人物+原文窗口的快速补证源**，不作唯一依据。

已排除：`ctext.org`（反爬拦截页明确声明会间歇性返回污染数据；`api.ctext.org` 返回 `ERR_REQUIRES_AUTHENTICATION`）。备用（许可不明，未采用）：GitHub `garychowcmu/daizhigev20` 的 `史藏/正史/清史稿.txt`（15,648,733 B，仓库无 LICENSE）。

## 11. 对 P02 / P03 的直接影响（待人工确认）

1. **D 层（CGED-Q）**：1760–1798 只有 16 个季节年、54,238 人，且 `person_id` 由对方生成、官方建议抽查；P02 必须先给出 person-level 去重规则与抽查方案，再决定 linkage 阈值（计划书要求 linkage < ~15% 时 D 层降级为 coverage benchmark）。
2. **CBDB 三代变量**：父 5.4% / 祖 1.8% / 曾祖 0.5% 的覆盖，意味着"CBDB-only 家世指标"在统计上不可行；P03 必须把 CBDB 亲属数据与 LLM 抽取结果分层标注来源（`extraction_method`）。
3. **旗籍**：CBDB 只能用 `BIOG_ADDR_DATA.c_addr_type=13`（1.7%），`STATUS_DATA` 八旗码几乎为空；中研院人名權威库含旗分/籍贯字段，可作为 A/B 层补充 —— 是否允许在 P03 对 A/B 层逐人取用中研院（非批量爬取）需要人工拍板。
4. **视角年**：CBDB 指数年大量为派生值（`INDEXYEAR_TYPE_CODES`），P06 的时代构成需区分实测/派生。
5. **原始库只读**：任何 views / `ADDRESSES` / FK 增强都在 `data/interim/` 副本上进行。
6. **CGED-Q 引用**：报告中必须写明 release 日期（2026-08-31）与实测行数（1,261,616），并附官方要求的 RGC 致谢。
