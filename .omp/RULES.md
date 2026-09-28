# RULES — qing-elite-family-background（always apply）

以下规则在本仓库内始终生效，优先级高于一般工作习惯与默认 agent 行为。
编号用于审计引用；违反其中任何一条都必须在阶段汇报中作为失败项列出。

1. **语言**：向用户汇报一律用中文；代码、变量、命令、文件名、模型 ID 保留英文。
2. **不展示私有推理链**：只输出可审核的工作摘要（依据、动作、结果、失败、决策）。
3. **阶段边界**：只做用户当前指定的 P 阶段；完成后写 `reports/checkpoints/Pxx.md`、commit、停止等待人工验收；不得自动进入下一阶段。
4. **研究问题冻结**：不得扩大或改写研究问题；不得在未经用户确认的情况下新增数据源、样本层或变量。
5. **模型渠道**：Coding 统一使用 `opencode-go/deepseek-v4.1-flash`（thinking `low`）；正式史料 LLM 只能走本地 Python → DeepSeek 官方 API（`model=deepseek-flash`，`thinking=disabled`，JSON output）。
6. **禁止独立 opencode**：不得安装、配置或调用独立 opencode CLI/TUI。
7. **禁止 provider 混用**：不得用 OMP 内的 `deepseek` provider 生产正式研究数据；OMP 对话上下文不得进入正式数据生产输入。
8. **不做多模型编队**：不建立多模型 coding 编队；单任务升档需人工可见的理由，且不得进入 `max`。
9. **advisor / subagents**：默认关闭 advisor；subagent 并发上限 2，且不得升档 thinking。
10. **NA 纪律**：`unknown != 0`；`missing != commoner`；信息不足记 `NA`，禁止用 0 填充。
11. **证据纪律**：没有史料证据一律 `null`；禁止用姓氏、籍贯、同族常识或模型知识推定亲属。
12. **原始数据只读**：固定 release、登记 SHA256 与版本日期；不在研究过程中自动更新数据源；大原始数据库不提交 Git。
13. **凭据纪律**：`DEEPSEEK_API_KEY` 只在环境变量中；不写入代码、YAML、`.omp/`、日志、Git；汇报中不得输出真实值。
14. **审计**：正式数据生产的每次 LLM 调用写 `audit/llm_calls/calls.jsonl`（含 call_id、model、thinking_mode、prompt_version、input_sha256、tokens、estimated_cost_rmb、status、retry_count、timestamp）；禁止记录 API key / auth header / 完整环境变量。
15. **成本护栏**：未到 P04 不花批量 API 费用；软预算 20 RMB、硬预算 30 RMB；批处理前必须先输出预计 token 与 RMB；禁止"先跑完再核算"。
16. **输入最小化**：不得把整卷/整传原文塞给模型；只发送目标人物相关窗口（关键词定位 ±500–1000 汉字）。
17. **不编造**：不得报告未实际执行的命令、测试、下载或统计结果；无法完成的部分必须显式说明缺什么、已尝试什么。
