# qing-elite-family-background — 项目工作约定

本文件是 OMP 在本仓库内工作时自动加载的项目级 AGENTS.md。
研究计划以仓库根目录 `OMP_PLAN.md` 为唯一权威来源；本文件不复制研究参数，只规定工作方式。

## 1. 项目定位

- 清代（1644–1820）官僚精英家世与仕进 micro-study；地方基层对照主要使用 CGED-Q JSL 1760–1798。
- 第一版只回答描述性问题，报告一律使用 association / selection / composition 一类表述。
- **不做因果识别。**
- 一切以「先跑通全流程，再迭代精度」为准；小、快、低成本、可复现。

## 2. 阶段门禁

- 只执行用户在当前对话中明确指定的 P 阶段；不得自行进入下一阶段。
- 每个阶段结束必须产出 `reports/checkpoints/Pxx.md` 并 commit，然后**停止等待人工验收**。
- 不擅自扩大研究问题，不擅自增加数据源，不擅自增加模型。
- 阶段边界不允许"顺手做完"下一阶段的东西。

## 3. 汇报语言与形式

1. 全程**中文**向用户汇报；代码、变量、命令、文件名、模型 ID、路径保留英文。
2. 不输出私有 chain-of-thought；只给可审核的过程摘要（做了什么、依据什么、结果如何、哪里失败）。
3. 每个阶段汇报必须包含以下七项：
   - 当前 P 阶段；
   - 已完成事项；
   - 数据规模；
   - 当前 coverage；
   - 失败项；
   - 影响研究结论的方法决策；
   - 下一步人工验收内容。
4. 无该阶段适用的内容时，明确写 `n/a（原因）`，不得留空。

## 4. 模型与渠道

- **Coding / 仓库操作 / 测试 / 普通审计**：OMP 内已配置的 `opencode-go` provider，主力模型 `opencode-go/deepseek-v4.1-flash`，thinking `low`（见 `.omp/config.yml`）。
- **正式史料数据生产**：只能由本地 Python（`src/qing_elite/llm/`）调用 DeepSeek 官方 API，`model=deepseek-flash`，`thinking=disabled`，JSON output。
- 禁止安装、配置或调用独立的 opencode CLI/TUI。
- 禁止用 OMP 内的 `deepseek` provider 生产正式研究数据。
- 第一版不建立多模型 coding 编队；只有单个任务可临时升档，且必须在汇报中说明理由与范围。

## 5. 数据纪律

- `unknown != 0`；`missing != commoner`；`NA` 不得填 0。
- 不使用模型常识补史料缺口；没有证据一律 `null`。
- 原始数据文件只读；不在研究过程中自动更新数据源；大原始数据库不提交 Git。
- 任何正式 DeepSeek 批处理前，必须先输出预计 input/output tokens 与 RMB。

## 6. 凭据与审计

- `DEEPSEEK_API_KEY` 只存在于 shell 环境；不写入 Python、YAML、`.omp/`、日志、Git。
- 汇报中只允许说明 key 是否存在，绝不输出真实值（含部分值）。
- 正式数据生产的每次 DeepSeek 调用都写 `audit/llm_calls/calls.jsonl`，禁止记录 API key、auth header 或完整环境变量。

## 7. 成本护栏

- 未到 P04 不产生正式批量 API 费用。
- 软预算 20 RMB：停止启动新 batch，输出警告与剩余任务数。
- 硬预算 30 RMB：立即停止所有新请求并保存 checkpoint。
- 禁止"先跑完再核算"。
