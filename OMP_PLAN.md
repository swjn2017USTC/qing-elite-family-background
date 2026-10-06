# OMP 工程计划书：清前中期官僚精英家世与仕进 Micro-study（修订版）

**项目代号**：`qing-elite-family-background`  
**研究时间范围**：1644–1820；地方基层对照主要使用 1760–1798  
**工程目标**：尽可能在一个完整工作日内，从数据源验证一路跑通到第一版可复核统计结果。  
**版本**：v0.2（修正 OpenCode GO 的角色：它是 OMP 内已配置的 provider，而不是独立 OpenCode agent 软件）  
**核心原则**：小、快、低成本、可复现；先完成全流程，再迭代精度。  

---

## 一、项目总原则

### 1. 研究问题

首轮只回答：

> 清代官僚体系中，家庭政治—教育资本是否随着官位接近权力中心而系统性增加？这种梯度在旗籍、非旗汉人和不同地域汉人之间是否存在显著差异？

拆为三个可操作问题：

1. 父、祖、曾祖存在功名或官职的比例，是否从府县官 → 督抚 → 部院尚侍 → 中央最高层逐级变化？
2. 本人科举资格、旗籍、时代构成能够解释多少这种差异？
3. 非旗汉官内部，江南籍等区域进入高层时的家庭背景结构是否不同？

第一版**不做因果识别**。报告中只使用 association / selection / composition 一类表述。

---

## 二、最重要的架构修正

### 2.1 OMP 是唯一 agent harness

本项目不安装、不配置、也不调用独立的 OpenCode agent 软件。

你现有 OMP 中已经配置好的：

- `opencode-go` provider：负责 Coding / repo 操作 /测试 /普通审计；
- `deepseek` provider：可以保留在 OMP 中用于偶发交互，但**不参与正式批量史料数据生产**。

本项目在项目目录内建立**独立的 `.omp/` 项目配置**，覆盖/收窄全局路由，但不重复保存 provider 凭据。

### 2.2 正式研究 LLM 与 OMP provider 分离

正式批量史料抽取使用：

```text
本地 Python pipeline
    ↓
读取 Mac 环境变量 DEEPSEEK_API_KEY
    ↓
DeepSeek 官方 API
    ↓
model = deepseek-flash
```

原因：

- 固定 prompt；
- 固定模型与 thinking；
- 每次输入/输出均可审计；
- token 与成本可准确记录；
- 可断点续跑；
- 不受 OMP 对话上下文污染；
- 能够写成可复现的 Methods。

因此：

> **OMP 内的 `deepseek` provider 不作为正式研究数据生产通道。**

---

# 三、模型与成本策略

## 3.1 Coding：OpenCode GO provider

### 主力模型

项目默认使用：

```text
opencode-go/deepseek-v4.1-flash
```

P00 必须先通过 OMP 自身配置/模型列表确认**实际模型 ID**。如果你当前 OMP 暴露的 ID 略有不同，以 OMP 实际输出为准，并写入项目级配置。

### 思考等级

主力 Coding 任务默认：

```text
thinkingLevel: low
```

适用：

- Python / SQL ETL
- SQLite schema 查询
- CSV / parquet 清洗
- 单元测试
- 统计与画图
- 普通 debug
- 文档生成

不默认使用 medium / high / max。

只有满足以下条件之一时，才在**单个任务**临时升档，而不是改整个项目默认：

1. 同一个逻辑错误连续两轮低推理仍未解决；
2. entity-linkage 出现复杂的跨表歧义；
3. 测试表明 SQL schema 关系理解错误且常规排查失败；
4. 最终代码审计发现无法定位的统计逻辑错误。

即使如此，优先顺序也是：

```text
low → medium/等价中档 → high
```

不自动进入 `max`。

### 是否多模型 Coding

第一版**不建立多模型 Coding 编队**。

所有常规角色统一走 OpenCode GO 的 DeepSeek V4.1 Flash；原因是项目本质为中小型数据工程，不值得承担多模型切换带来的：

- 上下文重读；
- 风格不一致；
- 重复 token；
- 接口重写；
- 额外 debug。

只有 P07 最终审计若主模型明确遇到无法解决的问题，才允许**人工决定**是否调用另一个 OpenCode GO 模型做一次只读 second opinion。

不得自动切模型。

---

## 3.2 正式史料 LLM：DeepSeek 官方 API

### 普通抽取任务

```text
model = deepseek-flash
thinking = disabled
JSON output
```

OpenAI-compatible Chat Completions 示例思想：

```python
client.chat.completions.create(
    model="deepseek-flash",
    messages=messages,
    response_format={"type": "json_object"},
    extra_body={"thinking": {"type": "disabled"}},
)
```

因为 DeepSeek 新版思考默认可能开启，因此代码必须**显式关闭**，不能依赖默认值。

### 人物消歧 / 冲突判断

只有规则法无法解决时：

```text
model = deepseek-flash
thinking = enabled
reasoning_effort = low
```

禁止默认使用 high / max。

### API key

只允许：

```python
os.environ["DEEPSEEK_API_KEY"]
```

不得：

- 写入 Python；
- 写入 YAML；
- 写入 `.omp/`；
- 写入日志；
- 写入 Git；
- 复制一份真实 key 到项目 `.env`。

项目可提供：

```text
.env.example
```

但只写变量名，不写值。

---

## 3.3 DeepSeek 官方 API 成本限制

目标：

```yaml
target_input_tokens: 5_000_000
target_output_tokens: 800_000
soft_budget_rmb: 20
hard_budget_rmb: 30
```

规则：

- 未到 P04 不正式花批量 API 费用；
- P03 必须先根据实际 source-window 长度重新估算成本；
- P04 只做 30–50 人 pilot；
- P05 才允许批处理；
- 达到软预算：停止启动新 batch，输出警告和剩余任务数；
- 达到硬预算：立即停止所有新的请求，保存 checkpoint；
- 禁止“先跑完再核算”。

---

# 四、项目级 OMP 配置

项目目录：

```text
qing-elite-family-background/
└── .omp/
    ├── config.yml
    ├── AGENTS.md
    ├── RULES.md
    └── agents/
```

## 4.1 启动时不要猜 schema

P00 第一件事：

```bash
omp config list
```

或你当前 OMP 版本提供的等价配置查看命令。

目的：

1. 确认项目级 `.omp/config.yml` 实际 schema；
2. 确认 `modelRoles`、`task.agentModelOverrides`、`thinkingLevel` 等字段是否与当前版本一致；
3. 确认 `opencode-go/deepseek-v4.1-flash` 的实际 model ID。

**如果 schema 与本计划示例不同，以 OMP 实际 schema 为准，保留逻辑不变。**

---

## 4.2 `.omp/config.yml` 的逻辑目标

配置思路如下；P00 根据当前 OMP schema 落成实际 YAML：

```yaml
modelRoles:
  default:
    model: opencode-go/deepseek-v4.1-flash
    thinkingLevel: low

  task:
    model: opencode-go/deepseek-v4.1-flash
    thinkingLevel: low

  plan:
    model: opencode-go/deepseek-v4.1-flash
    thinkingLevel: low

  slow:
    model: opencode-go/deepseek-v4.1-flash
    thinkingLevel: low

  smol:
    model: opencode-go/deepseek-v4.1-flash
    thinkingLevel: low

  tiny:
    model: opencode-go/deepseek-v4.1-flash
    thinkingLevel: low

  commit:
    model: opencode-go/deepseek-v4.1-flash
    thinkingLevel: low
```

如 OMP 还需要 `advisor`：

```yaml
advisor:
  enabled: false
  subagents: false
```

第一版：

```yaml
task:
  maxConcurrency: 2
```

或更低。

目的不是跑得最快，而是控制：

- Go provider 并发消耗；
- repo 内重复读取；
- agent 互相覆盖文件。

### agentModelOverrides

如当前 OMP 配置必须显式指定，则：

```yaml
task:
  agentModelOverrides:
    repo-scout:
      model: opencode-go/deepseek-v4.1-flash
      thinkingLevel: low
    implementer:
      model: opencode-go/deepseek-v4.1-flash
      thinkingLevel: low
    tester:
      model: opencode-go/deepseek-v4.1-flash
      thinkingLevel: low
    reviewer:
      model: opencode-go/deepseek-v4.1-flash
      thinkingLevel: low
```

但首轮尽可能少启自定义 subagent。

---

# 五、OMP 中文中间过程要求

`.omp/AGENTS.md` / `.omp/RULES.md` 必须写明：

1. 全程中文向用户汇报；
2. 代码、变量、命令、文件名保留英文；
3. 不输出私有 chain-of-thought；
4. 输出的是可审核的工作摘要；
5. 每个阶段必须显示：
   - 当前 P 阶段；
   - 已完成事项；
   - 数据规模；
   - 当前 coverage；
   - 失败项；
   - 影响研究结论的方法决策；
   - 下一步人工验收内容；
6. 到人工验收点必须停止；
7. 不擅自进入下一阶段；
8. 不擅自扩大研究问题；
9. 不使用模型常识补史料缺口；
10. `unknown != 0`；
11. `missing != commoner`；
12. 任何正式 DeepSeek API 批处理前必须输出预计 token 和 RMB。

---

# 六、数据源

## 6.1 CBDB

第一主数据库。

用途：

- 人物 ID；
- 官职；
- 任职关系；
- 籍贯；
- 科举；
- 父祖亲缘；
- 异名；
- 基本年代信息。

要求：

- 固定 release；
- 保存版本日期；
- 保存 SHA256；
- 在 `data/raw/manifest.json` 登记；
- 原始文件只读；
- 不在一次研究过程中自动更新。

---

## 6.2 CGED-Q JSL 1760–1798

用途：

> 建立乾隆中后期府县等地方官员的纵向截面，不把它错误解释为 1644–1820 全时段基层总体。

重点：

- 去除季度重复；
- 建立 person-level 与 appointment-level 两份表；
- 识别同一人跨季度/跨官缺变化；
- 与 CBDB 尝试链接；
- 链接质量不足时 D 层只做描述。

---

## 6.3 中研院人名权威—人物传记数据库

用途：

- A 层 100% 核验；
- 旗籍；
- 父祖关系；
- CBDB 冲突；
- 重要异常值。

第一版不做大规模网页爬取。

---

## 6.4 中研院清代职官资料库

用途：

- 核定高层/部院/督抚任职者；
- 任期核查；
- 官缺/官名标准化。

---

## 6.5 《清史稿》

作为快速文本补证源。

要求记录：

- 卷次；
- 人物；
- 原文窗口；
- 来源定位。

---

## 6.6 《清史列传》

第一版：

- 不全量 OCR；
- 不全量 LLM；
- 只处理 A 层、关键冲突、关键 missing。

---

# 七、样本层级

## A1：大学士 / 内阁最高层
1644–1820

## A2：南书房
康熙期作为主要政治含义窗口。

雍正以后南书房不得机械等价于决策核心。

## A3：军机大臣
军机处建立至 1820。

## B：六部尚书 / 侍郎
1644–1820

## C：总督 / 巡抚
1644–1820

## D：府县等地方官
主要来自 CGED-Q 1760–1798。

---

# 八、核心原始变量

每人保存：

```text
person_id
canonical_name
alt_names
birth_year
death_year
native_place
province
banner_status
ethnicity_status
own_degree
own_entry_route
highest_office
highest_office_tier
```

三代：

```text
father_known
father_name
father_degree
father_office
father_office_rank

grandfather_known
grandfather_name
grandfather_degree
grandfather_office
grandfather_office_rank

great_grandfather_known
great_grandfather_name
great_grandfather_degree
great_grandfather_office
great_grandfather_office_rank
```

证据：

```text
source_id
source_type
source_locator
source_text
extraction_method
confidence
```

---

# 九、家世指标

## `ancestor_official_any`
三代至少一人明确任官。

## `ancestor_degree_any`
三代至少一人有功名。

## `ancestor_high_official`
三代至少一人达到预注册的中高级官阶。

## `elite_generations_count`
父、祖、曾祖中存在“功名或官职”的代数：0–3。

## `strict_commoner_3g`

只有当：

```text
三代信息达到预设充分覆盖
AND
三代均无已知功名
AND
三代均无已知任官
```

才记为 1。

否则：

- 明确有官/功名 → 0
- 信息不足 → NA

禁止用 NA 填 0。

---

# 十、旗籍与地域

至少：

```text
Manchu banner
Mongol banner
Han bannerman
non-banner Han
other
unknown
```

不得直接压成“旗人/汉人”。

地域：

- 主变量：省份；
- 探索变量：`jiangnan_core = 江苏 / 浙江 / 安徽`。

第一版不建立复杂南北指标。

---

# 十一、coverage 是第一结果

每个层级必须输出：

```text
N_total
N_linked
N_father_observed
N_2gen_observed
N_3gen_observed
coverage_rate
```

第一张正式结果图必须是 coverage。

原因：

> 高层人物更容易留下完整家世，史料可见性本身可能与官位高度相关。

如果不处理，任何“高官更官宦化”的结论都有严重可见性偏差风险。

---

# 十二、项目目录

```text
qing-elite-family-background/
├── OMP_PLAN.md
├── README.md
├── CODEBOOK.md
├── METHODS.md
├── .gitignore
├── .env.example
│
├── .omp/
│   ├── config.yml
│   ├── AGENTS.md
│   ├── RULES.md
│   └── agents/
│
├── config/
│   ├── research.yaml
│   ├── offices.yaml
│   ├── llm.yaml
│   └── pricing.yaml
│
├── data/
│   ├── raw/
│   ├── interim/
│   └── processed/
│
├── src/
│   ├── acquire/
│   ├── cbdb/
│   ├── cgedq/
│   ├── linkage/
│   ├── family/
│   ├── llm/
│   ├── analysis/
│   └── utils/
│
├── tests/
│
├── audit/
│   ├── llm_calls/
│   ├── manual_validation.csv
│   └── exclusions.csv
│
├── reports/
│   ├── checkpoints/
│   └── final/
│
└── output/
    ├── tables/
    └── figures/
```

---

# 十三、Python 软件栈

优先小型稳定依赖：

```text
Python 3.11/3.12
uv
pandas
pyarrow
duckdb
rapidfuzz
pydantic
openai
tenacity
httpx
pyyaml
scipy
statsmodels
matplotlib
pytest
```

SQLite：

```python
sqlite3
```

第一版明确不要：

- Spark
- PostgreSQL
- Neo4j
- Docker
- Airflow
- LangChain
- LlamaIndex
- 大型向量库
- 大型 RAG 框架

---

# 十四、DeepSeek 数据生产审计

每次调用写：

```text
audit/llm_calls/calls.jsonl
```

至少：

```text
call_id
person_id
task_type
model
thinking_mode
reasoning_effort
prompt_version
source_ids
input_sha256
input_tokens
cached_tokens
output_tokens
estimated_cost_rmb
status
retry_count
timestamp
```

禁止保存：

- API key
- auth header
- 完整环境变量

---

# 十五、LLM 输入策略

本地程序先定位文本窗口。

禁止：

> 把完整《清史稿》人物传或整卷《清史列传》直接扔给模型。

优先：

```text
目标人物相关家世关键词 ±500–1000 汉字
```

关键词仅用于 recall：

```text
父
祖
曾祖
世
家
荫
廕
贡
举人
进士
官
任
授
```

模型只做语义抽取。

---

# 十六、LLM 输出 schema

```json
{
  "person_id": "",
  "father": {
    "name": null,
    "degree": null,
    "office": null,
    "evidence": null
  },
  "grandfather": {
    "name": null,
    "degree": null,
    "office": null,
    "evidence": null
  },
  "great_grandfather": {
    "name": null,
    "degree": null,
    "office": null,
    "evidence": null
  },
  "ambiguities": [],
  "confidence": "high|medium|low",
  "insufficient_evidence": false
}
```

没有证据必须 `null`。

不得根据：

- 姓氏；
- 籍贯；
- 同族常识；
- 模型知识；

自行推定亲属。

---

# 十七、Git

分支：

```text
main
```

阶段 commit：

```text
P00 bootstrap
P01 acquire and inspect sources
P02 build official universe
P03 structured family linkage
P04 validate llm extraction
P05 batch llm enrichment
P06 classify and analyze
P07 audit and robustness
P08 finalize report
```

不把大原始数据库提交 Git。

---

# 十八、阶段计划

---

## P00 — 项目 bootstrap 与 OMP 项目级配置

### 目标

只建工程，不研究历史。

### 必做

1. 读取本计划书；
2. 执行 `omp config list` 或当前版本等价命令；
3. 确认 OMP 项目级配置 schema；
4. 确认 OpenCode GO provider 的实际模型 ID；
5. 创建 `.omp/config.yml`；
6. OpenCode GO 主力 Coding 全部设低思考；
7. advisor 默认关闭；
8. 创建 `.omp/AGENTS.md` / `.omp/RULES.md`；
9. 建立 Python / uv / Git；
10. 检查 `$DEEPSEEK_API_KEY` 是否存在，但不打印；
11. 写 DeepSeek API health check；
12. health check 使用：
    - `deepseek-flash`
    - thinking disabled
    - 极小 JSON 输出；
13. 建测试骨架；
14. 不下载正式数据；
15. 不做分析。

### 验收文件

```text
reports/checkpoints/P00.md
```

必须报告：

- OMP schema；
- `opencode-go` 实际 model ID；
- default/task/plan/slow/smol/commit 路由；
- thinking 等级；
- DeepSeek 官方 API health check；
- Python 环境；
- Git commit。

**停止。**

---

## P01 — 数据源下载 / 验真 / schema inspection

### CBDB

- 官方 release；
- SQLite；
- SHA256；
- manifest；
- schema；
- 随机查询清代人物。

### CGED-Q

优先公开 1760–1798 release。

如必须浏览器人工下载：

- 停止该下载动作；
- 告知用户准确放置路径；
- 不绕过站点限制。

### 中研院

测试：

- 张廷玉；
- 鄂尔泰；
- 陈宏谋。

确认：

- 人名权威库可检索；
- 清代职官库可检索。

### 《清史稿》

验证机器可读取正文来源。

### 不做

- 批量爬取；
- DeepSeek 批处理；
- 统计结论。

### 输出

```text
reports/checkpoints/P01.md
reports/checkpoints/P01_schema.md
data/raw/manifest.json
```

**停止。**

---

## P02 — 建立官员总体

不用 LLM。

生成：

```text
appointments.parquet
officials_master.parquet
```

处理：

- A1/A2/A3/B/C/D；
- 多次任职；
- 同名异人；
- 异名；
- 署理；
- 兼任；
- CGED-Q 季度重复；
- person-level 去重。

官职到 tier 必须进入：

```text
config/offices.yaml
```

每条可人工审核。

若 D 层与 CBDB 等的可靠 linkage 极差，例如明显低于 15%，不得硬做“基层家世代表”。

可以改为：

> D 层仅做官员总体 / coverage benchmark。

输出 P02 checkpoint 并停。

---

## P03 — 结构化家世与 entity linkage

不调用 DeepSeek。

先吃完：

- CBDB 父子关系；
- 祖父链；
- 曾祖链；
- 科举；
- 任官；
- 籍贯。

生成：

```text
family_structured.parquet
```

linkage 顺序：

1. 唯一 ID；
2. 明确亲属 ID；
3. 姓名 + 年代 + 籍贯；
4. 姓名 + 科举 + 任职；
5. fuzzy matching 仅作为候选召回。

保存：

```text
match_score
match_features
match_method
match_status
```

阶段末重新估算：

- 真正需要 DeepSeek 的人数；
- 平均 source-window token；
- 总输入 token；
- 输出 token；
- 预计 RMB。

不得正式批跑。

**停止。**

---

## P04 — DeepSeek 小样本 pilot

取 30–50 人。

必须覆盖：

- A/B/C/D；
- 旗人；
- 非旗汉；
- 高/低信息量；
- entity 冲突；
- 结构化信息缺失。

正式研究调用：

```text
本地 Python → $DEEPSEEK_API_KEY → 官方 API
```

不用 OMP 的 `deepseek` provider。

普通抽取：

```text
thinking disabled
```

复杂消歧：

```text
thinking enabled
reasoning_effort low
```

人工 gold sample：

至少 20 个。

指标：

- father identity accuracy；
- degree accuracy；
- office accuracy；
- evidence fidelity；
- JSON validity；
- hallucination rate。

明显 hallucination 或 evidence 不对应：

> 修 prompt，重新 P04。

不得进入 P05。

**停止。**

---

## P05 — 正式 DeepSeek enrichment

只有：

```text
structured missing
AND
存在可靠 source passage
```

才调用。

没有材料：

```text
unknown
```

不得让模型猜。

并发：

```text
5–10
```

如果 API/机器压力不稳，则降到 3–5。

每 50 人 checkpoint。

必须支持 resume。

产出：

```text
family_enriched.parquet
cost_report.csv
failed_cases.csv
reports/checkpoints/P05.md
```

报告：

- 调用数；
- token；
- cached token；
- RMB；
- 结构化直接解决比例；
- LLM 新增信息比例；
- unknown 比例。

**停止。**

---

## P06 — 分类与统计

所有“寒门/官宦”由 Python codebook 判定。

第一结果：

```text
coverage by tier
```

再统计：

```text
ancestor_official_any
ancestor_degree_any
ancestor_high_official
elite_generations_count
strict_commoner_3g
```

分层：

- tier；
- cohort；
- banner_status；
- province；
- jiangnan_core。

### 时间窗口纪律

D 层主要是 1760–1798。

所以 A/B/C/D 正式共同回归只能：

```text
1760–1798
```

不得用：

```text
1644–1820 高层
vs
1760–1798 基层
```

声称统一官位梯度。

1644–1820：

> A/B/C 长期 composition / trend 分析。

### 模型

优先简单：

```text
binary logit
```

或样本合理时：

```text
ordinal model
```

不为了“高级”强上复杂模型。

---

## P07 — 学术审计

### 1. missing sensitivity

- complete case；
- known-positive；
- coverage threshold。

### 2. 寒门口径 sensitivity

- 三代无官；
- 三代无功名；
- 三代无官且无功名；
- 父祖两代；
- 仅父亲。

### 3. A 层 100% 核验

- 大学士；
- 军机大臣；
- 关键南书房。

### 4. LLM 10% 独立重抽

不提供首次答案。

### 5. code audit

检查：

- unknown → 0；
- person 重复；
- 时间错配；
- CGED-Q 季度重复；
- tier 错误；
- regression denominator；
- 旗籍混淆。

Coding 仍用：

```text
opencode-go/deepseek-v4.1-flash
thinking low
```

只有主模型明确失败，才向用户建议 second opinion。

不得自动升级。

**停止。**

---

## P08 — 最终交付

生成：

```text
README.md
CODEBOOK.md
METHODS.md
reports/final/FINAL_REPORT.md
```

最少：

### Table 1
样本与 coverage

### Table 2
家世指标 by tier

### Table 3
1760–1798 共同窗口模型

### Table 4
敏感性分析

### Figure 1
数据流程

### Figure 2
coverage

### Figure 3
family background by tier

### Figure 4
A/B/C long-run trends

### Figure 5
banner / region stratification

报告必须区分：

1. 数据事实；
2. 统计关联；
3. 史学解释；
4. 研究限制。

不能直接写：

- 清代形成封闭世袭官僚集团；
- 清政府系统排斥寒门；
- 南方士人受压制；

除非数据真正支持。

最终 Git：

```text
tag: v0.1-one-day
```

---

# 十九、给 OMP 的分阶段对话提示词

## P00

```text
请严格阅读项目根目录 OMP_PLAN.md。

现在只执行 P00，不要进入 P01。

注意：OpenCode GO 是已经配置在 OMP 里的 provider，不是独立 OpenCode agent 软件。禁止安装、配置或调用独立 opencode CLI/TUI。

任务：
1. 先用当前 OMP 自身命令检查项目级 config schema 和可用 model/provider ID；
2. 在项目目录建立独立 .omp/config.yml；
3. Coding 主模型统一优先使用 OpenCode GO provider 中的 DeepSeek V4.1 Flash；
4. default/task/plan/slow/smol/tiny/commit 等第一版全部最低可工作 thinking level（优先 low）；
5. 不建立多模型 coding 编队；
6. advisor/subagents 默认关闭或最小化；
7. 建立 .omp/AGENTS.md 与 RULES.md，要求全过程中文汇报中间状态；
8. 创建 Python/uv/Git 项目骨架；
9. 检查环境变量 DEEPSEEK_API_KEY 是否存在，但绝对不要输出真实值；
10. 创建正式研究 DeepSeek API client 的最小 health check；
11. health check 使用 deepseek-flash，显式 thinking disabled，极短 JSON output；
12. 正式研究 API 只允许本地 Python 从环境变量读取 key，不得通过 OMP 内 deepseek provider 生产正式研究数据；
13. 不下载正式研究数据，不开始历史分析。

全过程中文汇报；代码、变量、shell 命令保持英文。
不要展示私有推理链，只给可审核的过程摘要。

完成后生成 reports/checkpoints/P00.md，commit，然后停止等待人工验收。
```

---

## P01

```text
P00 已人工验收。

只执行 P01：数据源获取、验真、schema inspection。

1. 下载并固定 CBDB 官方 SQLite release，保存版本、SHA256、manifest；
2. 验证 SQLite 能查询清代人物并输出 schema；
3. 获取 CGED-Q JSL 1760–1798 release；若网站需要人工浏览器下载，停止该下载动作并准确告诉我文件应放到哪里；
4. 阅读/解析 User Guide；
5. 验证中研院人名权威数据库与清代职官数据库，用张廷玉、鄂尔泰、陈宏谋做小样本；
6. 验证机器可读《清史稿》正文来源；
7. 不做大规模爬取；
8. 不调用 DeepSeek 正式批量 API；
9. 更新 data/raw/manifest.json。

全过程中文汇报。

完成 P01.md 与 P01_schema.md 后 commit 并停止。
```

---

## P02

```text
P01 已人工验收。

只执行 P02：建立研究官员总体。

不用任何 LLM。

构建 appointments.parquet 和 officials_master.parquet。

组别：
A1 大学士
A2 南书房
A3 军机大臣
B 六部尚书/侍郎
C 总督/巡抚
D CGED-Q 1760–1798 府县等地方官

重点处理：
- 同名异人
- 异名
- 多次任职
- 署理
- 兼任
- CGED-Q 季度重复
- person-level 去重

把官职标准化与 tier 显式写入 config/offices.yaml。

输出人数、年代、籍贯/旗籍 coverage、CBDB/CGED-Q linkage。

若 D 层可靠链接比例过低，不得掩盖，checkpoint 中提出降级为 descriptive benchmark。

生成 P02.md，测试、commit、停止。
```

---

## P03

```text
P02 已人工验收。

只执行 P03：结构化家世抽取与 entity linkage。

能用 CBDB 结构化关系解决的全部先解决，绝对不要调用 DeepSeek。

提取父、祖父、曾祖父及其科举、任官、籍贯信息。
生成 family_structured.parquet。

人物匹配优先明确 ID。
RapidFuzz 只能召回候选，不能自动认定同一人。

保留 match_method / match_score / match_features / match_status。

阶段末必须根据真实 source passage 重新计算：
- 待 LLM 人数
- 输入 token
- 输出 token
- 官方 API 预计 RMB

不要正式批跑。

生成 P03.md，commit，停止。
```

---

## P04

```text
P03 已人工验收。

执行 P04：DeepSeek 官方 API pilot。

正式研究数据调用路径必须是：
本地 Python → 环境变量 DEEPSEEK_API_KEY → DeepSeek 官方 API。

禁止使用 OMP 内 deepseek provider 生产这些数据。

模型：deepseek-flash。

普通家世事实抽取：
thinking disabled
JSON Output

复杂 entity conflict：
只有规则法失败才 thinking enabled
reasoning_effort low

抽取 30–50 个分层样本。
人工 gold sample >= 20。

计算：
- father identity accuracy
- degree accuracy
- office accuracy
- evidence fidelity
- JSON validity
- hallucination rate

记录完整 token/cost audit。

出现系统性幻觉或 evidence 不对应则修 prompt 重跑 pilot，不得进入 P05。

生成 P04.md + manual_validation.csv，commit，停止。
```

---

## P05

```text
P04 已人工验收。

执行 P05：正式 LLM enrichment。

只处理：
structured missing
AND
存在可靠 source passage
的对象。

没有史料就是 unknown，不得猜。

继续直接调用官方 deepseek-flash：
普通抽取 thinking disabled；
复杂消歧 reasoning low。

并发 5–10，必要时更低。
每 50 人 checkpoint。
必须支持 resume。

soft budget = 20 RMB
hard budget = 30 RMB

达到 hard budget 立即停止新请求。

输出：
family_enriched.parquet
cost_report.csv
failed_cases.csv
P05.md

报告 token、费用、新增信息比例、unknown 比例。

commit 并停止。
```

---

## P06

```text
P05 已人工验收。

执行 P06：coverage、家世分类、主统计。

任何“寒门/官宦”判断都由 Python codebook 决定，不得交给 LLM。

第一张正式结果必须是 coverage。

随后统计：
ancestor_official_any
ancestor_degree_any
ancestor_high_official
elite_generations_count
strict_commoner_3g

按 tier / cohort / banner status / province / jiangnan_core 分组。

由于 D 层主要来自 1760–1798：
A/B/C/D 正式共同回归仅限 1760–1798。
1644–1820 只做 A/B/C 长时段趋势。

统计模型保持简单、可解释。

生成表、图、诊断与 P06.md。
commit 后停止。
```

---

## P07

```text
P06 已人工验收。

执行 P07：学术审计与稳健性。

完成：
1. coverage/missing sensitivity；
2. 多种寒门定义；
3. A 层人物 100% 权威来源核验；
4. LLM 样本随机 10% 独立重抽；
5. person duplication；
6. CGED-Q 季度重复；
7. 官职 tier；
8. 时间窗口；
9. regression denominator；
10. unknown 是否误编码成 0。

Coding 审计继续只用 OMP 中 OpenCode GO provider 的主力 V4.1 Flash，low thinking。

若低推理连续不能解决明确问题，只向我提出是否需要人工切换第二 Coding 模型；不得自动切换。

生成 P07.md 与 audit。
commit，停止。
```

---

## P08

```text
P07 已人工验收。

执行 P08 最终整理，不扩大研究范围。

生成：
README.md
CODEBOOK.md
METHODS.md
reports/final/FINAL_REPORT.md
最终 CSV/table/PDF figures

报告严格区分：
- 数据事实
- 统计关联
- 史学解释
- 局限

重点说明：
- 资料可见性偏差
- 高低层 coverage 差异
- CGED-Q 时间窗
- entity linkage 风险
- LLM 只抽事实字段、不决定寒门分类
- 结果不能自动解释成因果关系

运行全部测试。
生成 reproducibility 信息。
git commit 并 tag v0.1-one-day。

最后只给中文精炼验收摘要，然后停止。
```

---

# 二十、项目开始前的手动工作

只需做以下几项：

## 1. 建项目目录

```bash
mkdir -p ~/Projects/qing-elite-family-background
cd ~/Projects/qing-elite-family-background
git init
```

把本计划书保存为：

```text
OMP_PLAN.md
```

## 2. 确认 OMP 当前可运行

```bash
omp --version
```

以及你平常用来查看配置的：

```bash
omp config list
```

不需要运行 `opencode models`，也不需要安装独立 OpenCode 软件。

## 3. 检查 DeepSeek key

```bash
test -n "$DEEPSEEK_API_KEY" && echo "DEEPSEEK_API_KEY: OK" || echo "DEEPSEEK_API_KEY: MISSING"
```

不要：

```bash
echo "$DEEPSEEK_API_KEY"
```

## 4. DeepSeek 官方账户

建议保证余额至少：

```text
30 RMB
```

项目 hard cap 即 30 RMB。

## 5. 检查基础环境

```bash
git --version
python3 --version
uv --version
omp --version
```

如果缺 `uv`，手工安装 `uv`。

Python 库不要一个个手装，让 P00 创建环境。

## 6. CGED-Q

暂时不要提前下载。

P01 先尝试正常获取。只有站点要求浏览器交互时再由你手工下载。

---

# 二十一、第一版明确不做

在 v0.1-one-day 完成前禁止扩展到：

- 道光以后完整晚清；
- 全量《清史列传》OCR；
- 全量《清代官员履历档案全编》OCR；
- 姻亲网络；
- 母系家族；
- 整个清代县官总体；
- 社会网络中心性；
- 机器学习升迁预测；
- 知识图谱；
- 网页前端；
- 大型 RAG；
- 多 agent 复杂协作。

---

# 二十二、成功标准

第一版成功，不是“把清代所有官员查完”，而是：

1. 数据源真实可用；
2. 官员研究总体可复现；
3. 家世 codebook 明确；
4. structured 与 LLM 字段分开；
5. 正式 LLM 调用每条都能回源；
6. unknown 没有被误当寒门；
7. API 成本受硬限制；
8. OpenCode GO provider 的 Coding 路由只在项目 `.omp/` 内覆盖；
9. 低 reasoning 为默认；
10. 有 coverage 和 sensitivity；
11. 从数据 → 抽取 → 分类 → 统计 → 图 → 结论完整跑通；
12. 任何一个统计结果都能追溯到原始人物、来源和处理方法。

---

# 二十三、技术依据（2026-09-13 核查）

1. OpenCode 官方将 OpenCode Go 定义为一种 provider，而不是必须采用的独立 agent workflow；其当前模型清单包括 DeepSeek V4.1 Flash。  
   https://opencode.ai/v2/docs/providers  
   https://dev.opencode.ai/docs/go/

2. DeepSeek 官方 API 支持显式关闭 thinking；OpenAI-compatible Chat Completions 中可通过 `extra_body={"thinking":{"type":"disabled"}}` 控制，复杂任务可开启后使用低 reasoning。  
   https://api-docs.deepseek.com/guides/thinking_mode/  
   https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/

---

**计划书结束。**
