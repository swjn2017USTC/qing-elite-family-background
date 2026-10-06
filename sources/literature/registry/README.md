# literature_registry.jsonl — 字段 schema

一行一篇论文。`LIT-NNNN` 全项目唯一、连续。机器校验：`src/qing_elite/v03/lit/schema.py`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `literature_id` | str | `LIT-NNNN`，唯一 |
| `title` | str | 题名（原文语言） |
| `authors` | str \| null | `; ` 连接 |
| `year` | int \| null | 出版年 |
| `venue` | str \| null | 期刊/出版社 |
| `doi` | str \| null | 小写 DOI（无 DOI 时为空） |
| `url` | str \| null | 元数据页 |
| `language` | enum | `en`/`zh`/`ja`/`other` |
| `type` | str \| null | 提供方返回的类型 |
| `query_cluster` | str | 命中的 query cluster（`Q1_…`–`Q12_…`） |
| `discovery_source` | str | 实际检索来源（`semanticscholar:<query>` / `crossref:<query>`） |
| `retrieved_at` | date | 检索日期 |
| `abstract` | str \| null | 提供方返回的摘要原文 |
| `relevance_score` | int | 由 `literature.yaml:core_selection.weights` 确定性打分 |
| `tier` | enum | `core` \| `candidate` |
| `topics` | str \| null | 主题标签 |
| `access_status` | enum | `METADATA_ONLY` / `ABSTRACT_ONLY` / `OPEN_FULLTEXT_HTML` / `OPEN_FULLTEXT_PDF` / `PUBLIC_REPOSITORY_FULLTEXT` / `LOCAL_USER_FILE` / `RESTRICTED` |
| `rights_status` | enum | `OPEN_ACCESS` / `INSTITUTION_LICENSED` / `PERSONAL_COPY` / `UNKNOWN` |
| `evidence_level` | enum | `METADATA` / `ABSTRACT` / `FULLTEXT`（我们实际能引用的层级） |
| `local_file` | str \| null | 本地文件路径（相对仓库根） |
| `sha256` | str \| null | 本地文件校验值 |
| `normalized_text` | str \| null | 解析后的文本路径 |
| `fulltext_verified` | bool | 只有解析成功才能为 true |
| `notes` | str \| null | 提供方返回的 OA/引用信息等（`oa_status=`、`cited_by=`、`oa_url=`） |

## 校验规则（机器强制）

- `fulltext_verified = true` ⇒ 必须有 `local_file`；
- `access_status ∈ {METADATA_ONLY, ABSTRACT_ONLY}` ⇒ 不得 `fulltext_verified`；
- `evidence_level = FULLTEXT` ⇒ 必须 `fulltext_verified`；
- `evidence_level = ABSTRACT` ⇒ 必须有 `abstract`；
- 每篇 `tier = core` 必须有 `digests/<LIT>.md`，且包含全部必需章节。
