# V0.3 U04R — 候选史料来源可行性（machine-readable: `data/processed_v03/source_feasibility.parquet`）

- 探测日期：2026-09-27；来源数：13；access_type 分布：ACCESS_REQUEST_REQUIRED=5, PUBLIC_UI_ONLY=4, PUBLIC_STRUCTURED=3, UNAVAILABLE=1
- 判定枚举：`PUBLIC_STRUCTURED`（结构化可机读）、`PUBLIC_SCAN`（公开扫描件）、`PUBLIC_UI_ONLY`（只有在线检索界面）、`ACCESS_REQUEST_REQUIRED`（须申请/订阅）、`PAPER_TABLE_ONLY`（只存在于论文附表）、`UNAVAILABLE`（找不到）。

## 1. 逐来源结论

| 来源 | 层 | 角色 | 年代 | 亲属覆盖(范围/人群) | access_type | bulk | api | 条款 | 置信 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `keju_timulu` | A | backfill_only | 隋–清（题名录）；本项目关心 1644–1912 | PARTIAL/MEDIUM | ACCESS_REQUEST_REQUIRED | False | False | 籍合网《历代进士登科数据库》与爱如生系列为机构订阅；DocuSky 为免费学术查询界面（无批量接口）… | MEDIUM |
| `lvli_dangan` | A | backfill_only | 乾隆–光绪为主（引见官员履历）；原档 1644–1912 | PARTIAL/LOW | ACCESS_REQUEST_REQUIRED | False | False | 一史馆公开全文库仅《清实录》《清会典》；《全编》须到馆查阅或商业订阅（爱如生《中国谱牒库》）… | HIGH |
| `qingji_officials` | C | backfill_only | 清季（约 1850–1912） | NONE/NONE | PUBLIC_UI_ONLY | False | False | 页面明示『本資料庫免費開放…直接進入使用』… | MEDIUM |
| `sinica_authority` | C | backfill_only | 明清（重点清） | PARTIAL/LOW | PUBLIC_UI_ONLY | False | False | 页面标注版權所有 中央研究院歷史語言研究所；未见开放数据许可或批量授权说明… | HIGH |
| `cbdb` | S | exposure_side | 先秦–1912；清段 1644–1912 | PARTIAL/LOW | PUBLIC_STRUCTURED | True | True | 未在本阶段核对条款页（cbdb.fas.harvard.edu 对脚本请求 403）；沿用 v0.1 口径：仅学术使用、… | MEDIUM |
| `familysearch_genealogy` | C | exposure_side | 跨期（含明清—民国中国族谱） | PARTIAL/UNKNOWN | ACCESS_REQUEST_REQUIRED | False | False | 需注册账号；影像通常仅对会员/合作馆开放；数据 API 需认证（匿名 401）… | HIGH |
| `mingjingtongpu_qer` | A | exposure_side | 19 世纪（如道光乙酉 1825 科、咸丰十一年 1861 科） | BROAD/MEDIUM | PUBLIC_UI_ONLY | False | False | 原书公版；编码数据并入 Lee-Campbell 检索平台，无独立授权/下载说明… | MEDIUM |
| `shengyuanlu` | A | exposure_side | 清（未见成体系的公开数字化名册） | NONE/NONE | UNAVAILABLE | False | False | 未发现任何独立数字化产品；学政/县学名册多在地方志中，未成批量公开资源… | MEDIUM |
| `shl_genealogy` | A | exposure_side | 历代（含明清），图谱跨期 | NARROW/LOW | PUBLIC_STRUCTURED | False | True | 平台定位为开放数据（提供 /apitest、/developguide 与 SPARQL 端点）；许可证文本未逐字核对，… | HIGH |
| `tongguanlu` | A | exposure_side | 约 1832–1924（已编码数据集以 1830–1911 为主） | BROAD/HIGH | ACCESS_REQUEST_REQUIRED | False | False | 编码数据集（HKUST/华中师大团队）未见公开授权或下载条款；原书公版… | HIGH |
| `tongnianchilu_qer` | A | exposure_side | 19 世纪（按科年）；会试同年齿录含 1829–1895 等 18 场会试 | BROAD/HIGH | PUBLIC_UI_ONLY | False | False | 原书公版；编码数据仅在 Lee-Campbell 检索平台公开可查，未见批量导出或 API 授权说明 → 使用前须确认条… | HIGH |
| `zhujuan` | A | exposure_side | 康熙–光绪（约 1662–1908），以中后期为主 | BROAD/MEDIUM | ACCESS_REQUEST_REQUIRED | False | False | 数字化渠道全部为商业/机构产品（书同文、爱如生、中华科举库、历代进士登科库），无免费公开 bulk… | HIGH |
| `cgedq_jsl` | S | outcome_side | 1760–1798、1850–1864、1900–1912（2026-08-28 release） | NONE/NONE | PUBLIC_STRUCTURED | True | True | CC0 1.0（Harvard Dataverse 数据集 license 字段实测）… | HIGH |

## 2. 探测证据（每行一条实际观测）

### `cbdb` — 中國歷代人物傳記資料庫（CBDB）

- access_type: **PUBLIC_STRUCTURED**（bulk=True, api=True, scan=False, machine_readable=True）
- 覆盖：先秦–1912；清段 1644–1912；人群：清段约 23.8 万人（本项目 v0.2 计数 237,826）
- 亲属：KIN_DATA 亲属码：父(75)/祖父(62)/曾祖(48) 及部分旁系；另有社会关系表（范围档 PARTIAL，人群覆盖 LOW）
- 职业：POSTED_TO_OFFICE_DATA 任官 + ENTRY_DATA 功名
- 体量估计：237826 (清人, v0.2 计数); API 全量 SQLite 约 877 MB；OCR 页数估计：0
- 条款：未在本阶段核对条款页（cbdb.fas.harvard.edu 对脚本请求 403）；沿用 v0.1 口径：仅学术使用、不再分发原始数据。落盘前须人工核对条款。
- 再分发：不再分发原始数据库（README 已声明）；允许本地缓存：True
- 自动化/研究价值：0.95 / 0.8
- 探测观测：GET https://raw.githubusercontent.com/cbdb-project/cbdb_sqlite/master/latest.json -> 200 [text/plain; charset=utf-8] { "sqlite_filename": "cbdb_20260926.sqlite3", "sha256": "3b8809b2e57d2ab89c837fd45fd53ec1b64779d2b428bd352ddcd8f730a2a62d", "generated_at_utc": "2026-09-26T19:14:20Z", "format": "sqlite3", "huggingface_path": "history/cbdb_202609/cbdb_20260
- 备注：probe: latest.json 返回 sqlite_filename=cbdb_20260926.sqlite3（比 v0.1 冻结的 20260912 更新）； HF dataset API 200；/api/query_relatives_2 匿名 GET 200 JSON（王安石 58 条亲属）； Harvard Dataverse doi:10.7910/DVN/PAGGQS 全量 200。kin_coverage_grade=PARTIAL 依据： v0.2 U02 显示 D 层（地方官）high-only 链接覆盖仅 0.78%、U03 pilot 祖先信息率 0.0（30 人）， 即 CBDB 对清代地方官员的亲属覆盖稀疏，不能单独支撑 exposure。


### `cgedq_jsl` — 中國政府職官資料庫（CGED-Q）縉紳錄

- access_type: **PUBLIC_STRUCTURED**（bulk=True, api=True, scan=False, machine_readable=True）
- 覆盖：1760–1798、1850–1864、1900–1912（2026-08-28 release）；人群：季度名册人物；v0.2 的 1760–1798 切片 54,238 人
- 亲属：无亲属记录（名册只记本人）（范围档 NONE，人群覆盖 NONE）
- 职业：任职、官职、籍贯、旗籍、出身/铨选、季度在任状态；自 2026-08 起含官方 person_id
- 体量估计：含 person_id 的 .tab 233,703,315 bytes；出身/籍贯编码表各 20 KB / 2.8 KB；OCR 页数估计：0
- 条款：CC0 1.0（Harvard Dataverse 数据集 license 字段实测）
- 再分发：CC0 允许再分发；原始大文件仍不入本仓库；允许本地缓存：True
- 自动化/研究价值：0.95 / 0.85
- 探测观测：GET https://dataverse.harvard.edu/api/datasets/:persistentId?persistentId=doi:10.7910/DVN/GMQWVZ -> 200 [application/json;charset=UTF-8] {"status":"OK","data":{"id":4929177,"identifier":"DVN/GMQWVZ","persistentUrl":"https://doi.org/10.7910/DVN/GMQWVZ","protocol":"doi","authority":"10.7910","separator":"/","publisher":"Harvard Dataverse","publicationDate":"2021-07-26","storag
- 备注：probe: Harvard Dataverse doi:10.7910/DVN/GMQWVZ 返回 200（versionState=RELEASED，version 10.0， releaseTime 2026-08-30，license CC0 1.0，文件含 person_id 全量 .tab）； HKUST DataSpace 同日连续 503。kin=NONE → 只能作 outcome 来源。


### `familysearch_genealogy` — FamilySearch 家譜索引與影像

- access_type: **ACCESS_REQUEST_REQUIRED**（bulk=False, api=False, scan=True, machine_readable=False）
- 覆盖：跨期（含明清—民国中国族谱）；人群：未披露
- 亲属：族谱世系（潜力大），但须登录/合作馆访问（范围档 PARTIAL，人群覆盖 UNKNOWN）
- 职业：弱（偶载功名/仕宦）
- 体量估计：unknown；OCR 页数估计：unknown
- 条款：需注册账号；影像通常仅对会员/合作馆开放；数据 API 需认证（匿名 401）
- 再分发：受限；不得批量导出再分发；允许本地缓存：False
- 自动化/研究价值：0.1 / 0.35
- 探测观测：GET https://www.familysearch.org/service/search/hr/v2/collections/1787988 -> 401 [None] ok
- 备注：probe: /service/search/hr/v2/collections/1787988 匿名返回 401；集合页为 JS 壳。不建议作为默认通道。

### `keju_timulu` — 科舉題名錄（进士题名碑录／乡会试录／登科录）

- access_type: **ACCESS_REQUEST_REQUIRED**（bulk=False, api=False, scan=True, machine_readable=False）
- 覆盖：隋–清（题名录）；本项目关心 1644–1912；人群：历代进士约 10 万余人；明清乡会试逐科名录
- 亲属：题名碑录多仅姓名/籍贯/名次；会试录常附三代脚色（亲属潜力中等）（范围档 PARTIAL，人群覆盖 MEDIUM）
- 职业：科第身份、名次、考官；非任官轨迹
- 体量估计：登科数据库称 10 万余条隋–清登科人物；OCR 页数估计：n/a
- 条款：籍合网《历代进士登科数据库》与爱如生系列为机构订阅；DocuSky 为免费学术查询界面（无批量接口）
- 再分发：订阅库禁止再分发；DocuSky 未见开放许可；允许本地缓存：False
- 自动化/研究价值：0.15 / 0.5
- 探测观测：GET https://examination.ancientbooks.cn/docDengke/ -> 200 [text/html;charset=UTF-8] <!DOCTYPE html> <html> <head> <meta charset="UTF-8"> <meta name="renderer" content="webkit"> <meta http-equiv="X-UA-Compatible" content="IE=edge"> <title>首页-历代进士登科数据库</title> <meta name="renderer" content="webkit"> <meta http-equiv="X-UA-Co
- 备注：probe: examination.ancientbooks.cn/docDengke/ 200（机构订阅）；docusky.org.tw CBDB 进士库 200（免费 UI）； 台湾华文电子书库《清朝進士題名碑錄》SSL 失败（未取得内容）。替代：CBDB ENTRY 体系已含进士/举人/生员编码。


### `lvli_dangan` — 履歷檔案（《清代官员履历档案全编》／宫中履历档案）

- access_type: **ACCESS_REQUEST_REQUIRED**（bulk=False, api=False, scan=True, machine_readable=False）
- 覆盖：乾隆–光绪为主（引见官员履历）；原档 1644–1912；人群：《全编》约 4 万余人、55,883 件（论文口径，未独立核实原书）
- 亲属：履历折含三代脚色（曾祖/祖/父）及兄弟子侄，但只有影像或商业全文库（范围档 PARTIAL，人群覆盖 LOW）
- 职业：出身、历官、保举、捐纳等
- 体量估计：约 4 万余人（《全编》）；爱如生谱牒库自称影像 3,000 万页；OCR 页数估计：《全编》30 册，逐册数百页
- 条款：一史馆公开全文库仅《清实录》《清会典》；《全编》须到馆查阅或商业订阅（爱如生《中国谱牒库》）
- 再分发：受版权/馆藏限制，不可再分发；允许本地缓存：False
- 自动化/研究价值：0.1 / 0.6
- 探测观测：GET https://fhac.com.cn/ -> 200 [text/html; charset=utf-8] <!doctype html> <html> <head> <link href="/Public/favicon.ico" type="image/x-icon" rel="shortcut icon"/><!-- Favicon Icon --> <meta http-equiv="X-UA-Compatible" content="IE=edge,chrome=1"> <meta name="renderer" content="webkit"> <meta name=
- 备注：probe: fhac.com.cn 首页/检索页 200（线上开放 44 个全宗**目录**检索，全文库仅《清实录》《清会典》）； /ess/* 路径为后台管理页、匿名目录查询返回空；爱如生产品页 200（商业授权）；国图/超星为登录影像。


### `mingjingtongpu_qer` — 明經通譜（贡生同年录）— 同一编码数据集

- access_type: **PUBLIC_UI_ONLY**（bulk=False, api=False, scan=False, machine_readable=False）
- 覆盖：19 世纪（如道光乙酉 1825 科、咸丰十一年 1861 科）；人群：贡生 12,155 人（明经通谱部分）
- 亲属：与同年齿录共用编码管线：直系三代 + 旁系亲属（范围档 BROAD，人群覆盖 MEDIUM）
- 职业：功名、科份；官职有限
- 体量估计：12155 名贡生；OCR 页数估计：未见统计
- 条款：原书公版；编码数据并入 Lee-Campbell 检索平台，无独立授权/下载说明
- 再分发：未见编码数据再分发许可；允许本地缓存：False
- 自动化/研究价值：0.2 / 0.9
- 探测观测：GET https://camerondcampbell.blog/kinship-information-in-the-%E5%90%8C%E5%B9%B4%E9%BD%BF%E5%BD%95-and-related-sources-completed-in-august-2024/ -> 200 [text/html; charset=UTF-8] <!doctype html> <html lang="en-US"> <head> <meta charset="UTF-8"> <meta name="viewport" content="width=device-width, initial-scale=1"> <link rel="profile" href="http://gmpg.org/xfn/11"> <meta name='robots' content='index, follow, max-image-
- 备注：probe: 国学大师书目页 200（明示只有馆藏线索、无影印）；检索平台 200；出版方 blog 说明 12,155 名贡生与同年齿录一并编码。与 tongnianchilu_qer 同源同通道。


### `qingji_officials` — 《清季職官表附人物錄》查詢系統（中研院史語所×台大）

- access_type: **PUBLIC_UI_ONLY**（bulk=False, api=False, scan=False, machine_readable=False）
- 覆盖：清季（约 1850–1912）；人群：清季京内外职官年表与人物录
- 亲属：无（范围档 NONE，人群覆盖 NONE）
- 职业：职官年表 + 人物录（任职与任期）
- 体量估计：未披露；OCR 页数估计：0
- 条款：页面明示『本資料庫免費開放…直接進入使用』
- 再分发：未明示；仅免费开放使用；允许本地缓存：False
- 自动化/研究价值：0.2 / 0.4
- 探测观测：GET http://mhdb.mh.sinica.edu.tw/databaseinfo.php?b=012 -> 502 [None] ok
- 备注：probe: mhdb.mh.sinica.edu.tw/databaseinfo.php?b=012 返回 200 HTML，明示免费开放；无 JSON/SPARQL。

### `shengyuanlu` — 生員錄（生员/低级功名名册）

- access_type: **UNAVAILABLE**（bulk=False, api=False, scan=False, machine_readable=False）
- 覆盖：清（未见成体系的公开数字化名册）；人群：生员（未入仕者占多数）；在官员群体中主要出现在同官录等履历类来源
- 亲属：无独立亲属记载；亲属信息只在履历类来源（同官录/硃卷）中出现（范围档 NONE，人群覆盖 NONE）
- 职业：功名层级（生员/庠生）可由 CBDB ENTRY 编码识别
- 体量估计：unknown；OCR 页数估计：unknown
- 条款：未发现任何独立数字化产品；学政/县学名册多在地方志中，未成批量公开资源
- 再分发：n/a；允许本地缓存：False
- 自动化/研究价值：0.05 / 0.4
- 探测观测：GET https://input.cbdb.fas.harvard.edu/api/entry_list_by_name?eName=%E7%94%9F%E5%93%A1&start=1&list=2&accurate=0 -> 200 [application/json] {"total":6,"start":1,"end":2,"data":[{"eId":47,"eName":"school: licentiate","eNameChn":"\u5b78\u6821: \u751f\u54e1(\u5ea0\u751f)"},{"eId":173,"eName":"county school student","eNameChn":"\u7e23\u5b78\u751f\u54e1"}]}
- 备注：检索未见独立"生员录"数据库（Harvard Dataverse 'Tongnian' 0 命中；中文检索仅回到 CGED-Q 相关页面）。 替代路径：CBDB `/api/entry_list_by_name?eName=生員` 实测 200 JSON（eId 47 等）， 以及同官录中记载的捐纳/生员功名者。故本条目记为 UNAVAILABLE（无独立来源）， 但在 claim_source_map 中把"生员层级"映射到 cbdb / tongguanlu。


### `shl_genealogy` — 上海圖書館開放數據平台（家譜人物圖 SPARQL）

- access_type: **PUBLIC_STRUCTURED**（bulk=False, api=True, scan=False, machine_readable=True）
- 覆盖：历代（含明清），图谱跨期；人群：家谱人物图 2,395,870 三元组；全平台人名图 37,167,377
- 亲属：观测到 spouseOf 8,053、roleOfFamily 206,503、orderOfSeniority 5,370、generationCharacter；roleOfFamily 实为宗谱角色标签（始遷祖/顯祖/始祖/房祖/名人…），**不是父—子边**（范围档 NARROW，人群覆盖 LOW）
- 职业：弱（人物姓名/字号/性别/宗谱角色；另有姓名/科贡专题端点未逐一实测）
- 体量估计：gen 图 person 2,395,870 三元组；spouseOf 主语 8,053；OCR 页数估计：0
- 条款：平台定位为开放数据（提供 /apitest、/developguide 与 SPARQL 端点）；许可证文本未逐字核对，落盘前须读 /developguide
- 再分发：未载明；再分发前须核对条款；允许本地缓存：True
- 自动化/研究价值：0.85 / 0.3
- 探测观测：GET https://data.library.sh.cn/ -> 200 [text/html;charset=UTF-8] <!DOCTYPE html> <head> <title>上海图书馆开放数据平台</title> <!-- 上海图书馆开放数据平台 --> <meta charset="utf-8"> <meta http-equiv="X-UA-Compatible" content="IE=edge"> <meta name="viewport" content="width=device-width, initial-scale=1.0"> <link href="/ontology
- 备注：probe: POST https://data.library.sh.cn/sparql/ 返回 200 application/sparql-results+json； GET 带 query 被 WAF 403（'Forbidden: Malicious request detected'）；jiapu.library.sh.cn 412。 关键实证：roleOfFamily 的宾语是宗谱角色词表（非亲属边），谓词分布中未见 father/parent 类谓词 → 该源可支撑"家族存在与姓氏/籍贯"，**不能**支撑 exposure 的父系亲属计数。


### `sinica_authority` — 中研院史語所 人名權威—人物傳記資料庫 與 清代職官資料庫

- access_type: **PUBLIC_UI_ONLY**（bulk=False, api=False, scan=False, machine_readable=False）
- 覆盖：明清（重点清）；人群：人名权威库（权威号 SN）；清代职官库覆盖中央/地方官职与任期
- 亲属：人名权威库『關聯』区块含亲属（实测示例：父 周煌、子 周廷授）（范围档 PARTIAL，人群覆盖 LOW）
- 职业：人名权威库含履歷（历官年表）；职官库含任职者年表与任期
- 体量估计：未披露；OCR 页数估计：0
- 条款：页面标注版權所有 中央研究院歷史語言研究所；未见开放数据许可或批量授权说明
- 再分发：未获授权，不应再分发抓取结果；允许本地缓存：False
- 自动化/研究价值：0.2 / 0.5
- 探测观测：GET https://newarchive.ihp.sinica.edu.tw/sncaccgi/sncacFtp -> 200 [text/html] <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd"> <html xmlns="http://www.w3.org/1999/xhtml"> <HEAD> <script language=javascript nonce="">document.writeln('<META HTTP-E
- 备注：probe: sncacFtp 与 officertp 均返回 200 **text/html**（ACTION=TQ,... GET 语法；详情页含亲属与履历）； data.ascdc.tw/sparql 返回 218 KB HTML 落地页而非 JSON（与 v0.2 U03 sinica_probe.json 一致）。 字段最贴合本课题但只有 HTML → 小规模人工核验可行，批量须申请（史語所）。


### `tongguanlu` — 同官錄（地方官员履历+亲属名册）

- access_type: **ACCESS_REQUEST_REQUIRED**（bulk=False, api=False, scan=True, machine_readable=False）
- 覆盖：约 1832–1924（已编码数据集以 1830–1911 为主）；人群：州县/府/省地方文官及盐漕等专门机构官员；含捐纳监生/贡生、生员等低级功名者
- 亲属：父/祖/曾祖 + 伯叔/堂伯叔 + 兄弟 + 子/姪/孙 + 母/祖母 + 妻/女；论文口径 80,988 条亲属（范围档 BROAD，人群覆盖 HIGH）
- 职业：任官履历、实授/候补/署理/候选状态、功名资格 → 可直接作 outcome
- 体量估计：2026 论文：28 种版本、18,585 条官员记录、80,988 条亲属、90,307 条功名履历；OCR 页数估计：无总册数统计；单册数十至数百页
- 条款：编码数据集（HKUST/华中师大团队）未见公开授权或下载条款；原书公版
- 再分发：编码数据不可再分发；单册公版扫描件（如 Wikimedia Commons）可再分发；允许本地缓存：False
- 自动化/研究价值：0.15 / 0.95
- 探测观测：GET https://researchportal.hkust.edu.hk/en/publications/kin-networks-of-local-officials-in-19th-and-early-20th-century-ch/ -> 200 [text/html;charset=UTF-8] <!DOCTYPE html> <html lang="en" class="nexus-font-support"> <head> <meta charset="utf-8"/> <meta http-equiv="X-UA-Compatible" content="IE=edge"/> <meta name="viewport" content="width=device-width, initial-scale=1"/> <meta name="google" cont
- 备注：probe: T&F DOI 403（Cloudflare）；HKUST 成果页 200（题录+摘要）；Harvard Dataverse 'Tongguanlu' 0 命中；OSF 预印本仅 PDF；Wikimedia Commons 有单册《江南宁蜀同官录》PDF（零散）。 → 无合法批量通道；须向作者/机构申请。V0.3 exposure+outcome 一体化程度最高。


### `tongnianchilu_qer` — 同年齒錄（含鄉試錄）— CGED-Q Examination Records 编码数据

- access_type: **PUBLIC_UI_ONLY**（bulk=False, api=False, scan=True, machine_readable=False）
- 覆盖：19 世纪（按科年）；会试同年齿录含 1829–1895 等 18 场会试；人群：进士 6,118 + 同年齿录举人 27,580 + 乡试录举人 29,971（合 73,744 条记录）
- 亲属：亲属资料完整：34,313 名功名持有者、950,735 条亲属记录（人均 27.7；进士人均 90；举人人均 21.6）；含父/祖/曾祖、伯叔/堂伯叔/伯叔祖、兄弟/堂兄弟、子/姪/孙、母/祖母、妻/女（范围档 BROAD，人群覆盖 HIGH）
- 职业：功名、科份、名次、考官；官职较弱
- 体量估计：950,735 条亲属记录 / 34,313 名功名持有者（2024-08 完成）；OCR 页数估计：未见统计（原书按科年成册）
- 条款：原书公版；编码数据仅在 Lee-Campbell 检索平台公开可查，未见批量导出或 API 授权说明 → 使用前须确认条款/取得授权
- 再分发：编码数据未见再分发许可；公版扫描件可再分发；允许本地缓存：False
- 自动化/研究价值：0.2 / 0.95
- 探测观测：GET https://searchjsl.leecampbellgroup.blog/ -> 200 [text/html] <!doctype html> <html lang="en"> <head> <meta charset="UTF-8" /> <meta name="viewport" content="width=device-width, initial-scale=1.0" /> <meta name="description" content="中国历史官员量化数据库 China Government Employee Database (CGED) 搜索系统" /> <meta
- 备注：probe: searchjsl 平台 200（SPA，含 AWS API Gateway 端点，无公开文档）；Harvard Dataverse 检索 'Tongnian' 0 命中；出版方 blog 与检索平台均无下载入口。kin 覆盖 BROAD 是 V0.3 exposure 的 理想来源，但当前无合法批量通道 → 须 ACCESS_REQUEST_REQUIRED 或 OCR 公版扫描件。


### `zhujuan` — 硃卷（乡会试及五贡硃卷）

- access_type: **ACCESS_REQUEST_REQUIRED**（bulk=False, api=False, scan=True, machine_readable=False）
- 覆盖：康熙–光绪（约 1662–1908），以中后期为主；人群：《清代硃卷集成》约 8,235 份（乡试 5,186 / 会试 1,635 / 五贡 1,576 等），涉进士近 1.2 万人
- 亲属：履历详载曾祖/祖/父三代、族谱世系、母系与姻亲、师承；家族有功名者全列（范围档 BROAD，人群覆盖 MEDIUM）
- 职业：功名、科份、名次、考官批语；官职较少
- 体量估计：约 8235 份（书同文称八千余种；爱如生称清人朱卷 8000 余件）；OCR 页数估计：约 8000+ 份，每份数十页
- 条款：数字化渠道全部为商业/机构产品（书同文、爱如生、中华科举库、历代进士登科库），无免费公开 bulk
- 再分发：馆藏原卷与商业影像库均不可自由再分发；允许本地缓存：False
- 自动化/研究价值：0.1 / 0.85
- 探测观测：GET https://www.unihan.com.cn/books/mingqing/zjjc -> 200 [text/html; charset=utf-8] <!DOCTYPE html> <html lang="zh-CN" class="static detail-product contents"> <head> <meta charset="utf-8" /> <title>书同文 - 清代科举硃卷集成</title> <link data-timestamped="true" href="https://www.unihan.com.cn/Themes/MyTheme/styles/site.min.css?v=6392
- 备注：probe: 书同文产品页 200、中华科举库登录页 200、北大/复旦馆购通报 200、历代进士登科库帮助页 200 （均提示需登录/订阅）。无公开 bulk；须购/订/馆际或 OCR 已出版影印本（受版权约束）。


## 3. PLAN A / B / C 与默认方案

**默认方案：PLAN_C**（规则 `exposure_side_ui_or_request_only`）

- 规则说明：亲属丰富的暴露侧来源只有 UI 检索或需机构申请（无批量通道）， 必须取得扫描件/授权后再 OCR 或抽取。
- 支撑来源：tongnianchilu_qer, mingjingtongpu_qer, tongguanlu, zhujuan, familysearch_genealogy
- 求值顺序：exposure_side_broad_kin_high_coverage_structured → exposure_side_broad_or_partial_covered_bulk → exposure_side_scan_bulk → exposure_side_ui_or_request_only → fallback

| 方案 | 定义 | 对后续阶段的影响 |
| --- | --- | --- |
| **PLAN_A** 结构化直做 | 暴露侧（家世/亲属）已有标准化结构化数据可直接使用；职业侧来自 CBDB / CGED-Q 结构化记录。 无需 OCR，无需机构申请。 | U05R 直接写字段映射与验证器，不做扫描件 OCR；人工成本主要在链接与冲突裁决 |
| **PLAN_B** 结构化 + 少量 OCR | 暴露侧主干依赖已有结构化数据，但需要 OCR 少量扫描件（标准化题名/履历类）来补齐 功名层级或旁系亲属。 | U05R 同时维护 rule parser 与 OCR/vision 分支；必须给出 OCR 字段级 gold 与字符错误率 |
| **PLAN_C** 主要靠扫描 OCR | 暴露侧主要来自公开扫描件，缺少结构化索引；自动化可行性取决于 OCR 管线质量。 | U05R 的 gate 以 OCR 字段级 precision 为生死线；若达不到门槛则 drop 来源，不得靠人工录入挽救 |

