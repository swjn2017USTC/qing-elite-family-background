# Qing Elite Family Background

> An evidence-aware, reproducible micro-study of family capital and career trajectories among Qing officials.

本仓库的当前公开版本是 **v0.3 `cohort-and-kin-network`**。它公开研究设计、代码、汇总结果、审计与复现元数据；不公开原始数据库、扫描件、逐人级派生表、原文摘录或 API 凭据。

## 研究问题与状态

主问题是：在相近的制度进入资格内，家庭的政治—教育资本及更广泛的宗族精英嵌入，与职业轨迹和最高官位之间有何**描述性关联**？

v0.3 将原先“按最终官位回溯家世”的设计，改为“标准化家世来源 → 家族资本（exposure）→ 职业轨迹（outcome）”。它扩展了亲属范围（直系三代加旁系长辈）、使用 CGED-Q 官方 `person_id`，并将职业位置建成任命事件面板。

当前 release gate 为 **PASS**，但 PASS 表示工程与证据门禁通过，**不等于研究问题已经得到肯定回答**。最终报告的结论是：在可合法自动获取的资料范围内，家世可观测性与双侧可辨识样本均不足，不能可靠估计家族资本与职业轨迹的关联。因此本版只发布描述性覆盖、样本流和职业面板结果，不做回归或因果叙述。

## 关键发现（必须连同限制引用）

- 链接 1,723 人后，有职业记录 1,697 人、有家世指标 1,592 人；进入分析框架的 1,204 人中，仅 65 人的家世暴露可定义（5.4%）。
- 双侧可观测仅 17 行，低于预设 200 行门槛；缺失率 0.99，且 9 个 exposure × outcome 单元格均小于 5。因此分析被自动降级为 `descriptive_only`。
- 早期/中期清代 A/B/C 扩展中，家世可观测率为 27.74%；但样本框和变量定义不同，不能将其与主 cohort 合并估计。

这不是“没有关联”的结论，而是“现有可观察资料无法回答该关联问题”的结论。`unknown` 不等于无家世、无任官、无功名或平民；本项目不生成 `is_commoner` 一类标签，也不做因果识别或“清代是否 meritocracy”的判断。

完整结果与边界见 [v0.3 最终报告](reports/upgrade_v03/FINAL_REPORT_V03.md) 和 [研究契约](V03_RESEARCH_CONTRACT.md)。

## 方法与模型边界

1. **来源与设计**：按 source × cohort × region × credential 定义样本框，明确区分家世暴露侧与职业结果侧的来源，避免由高官史料可见性反向制造关联。
2. **实体与亲属**：CGED-Q 官方 `person_id` 为主；链接采用风险分层、held-out precision 校准和人工复核队列。亲属证据为四态 `positive / explicit_negative / unknown / conflict`，缺失不可转为否定。
3. **职业面板**：以任命段、官品、行政层级、中央/地方路线和任命状态描述职业历程；`highest_tier` 只保留为历史版本的来源偏差基准。
4. **门禁与审计**：发布前检查样本流、来源覆盖、链接质量、派生数据完整性、图表与哈希；不满足数据门槛时停止模型估计。

| 用途 | 模型/渠道 | 边界 |
| --- | --- | --- |
| 仓库开发、测试、普通审计 | `opencode-go/deepseek-v4.1-flash` | 研发辅助，不产出正式研究数据。 |
| 结构化史料抽取 | DeepSeek 官方 API，`deepseek-flash` | 本地 Python 直接调用；thinking 默认关闭、JSON 输出；仅抽取可核验字段。 |
| 扫描件 OCR pilot | PaddleOCR-VL-1.6 官方服务 | 未达属性抽取阈值时降级为 identity-only。 |

模型不决定“寒门/官宦”或史学结论；所有正式结论必须服从证据状态、审计与预先声明的门槛。

## 数据获取与发布边界

所有原始数据只在本地只读使用，下载者应自行确认许可和再分发条款。完整版本、URL、日期与 SHA256 见 [RELEASE_MANIFEST.json](RELEASE_MANIFEST.json) 和 [data/raw/manifest_v03.json](data/raw/manifest_v03.json)。

| 来源 | 用途 | 获取地址 |
| --- | --- | --- |
| CBDB SQLite | 人物、亲属、功名与职官 | [CBDB 发布信息](https://raw.githubusercontent.com/cbdb-project/cbdb_sqlite/master/latest.json)；[v20260926 ZIP](https://huggingface.co/datasets/cbdb/cbdb-sqlite/resolve/main/history/cbdb_202609/cbdb_20260926.zip) |
| CGED-Q JSL | 职业事件面板、籍贯与出身 | [Harvard Dataverse 数据文件](https://dataverse.harvard.edu/api/access/datafile/14184134)；[数据集 DOI](https://doi.org/10.7910/DVN/GMQWVZ) |
| 《江南寧蜀同官錄·金奎光》 | OCR pilot 的扫描件来源 | [Wikimedia Commons 文件](https://upload.wikimedia.org/wikipedia/commons/5/58/WZLib-DB-143494_%E6%B1%9F%E5%8D%97%E5%AF%A7%E8%9C%80%E5%90%8C%E5%AE%98%E9%8C%84%E9%87%91%E5%A5%8E%E5%85%89.pdf) |
| 《清史稿》 | 仅作自由文本补缺 | [中文维基文库](https://zh.wikisource.org/wiki/%E6%B8%85%E5%8F%B2%E7%A8%BF) |

公开仓库不含上述原件，也不含逐人级的链接、亲属、职业事件、LLM/OCR 输出、人工复核或全文摘录。公开的表和图均为汇总结果。

## 复现

需要 Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)。先按 manifest 下载、校验并置入本地 `data/raw/`，再运行：

```bash
git clone https://github.com/swjn2017USTC/qing-elite-family-background.git
cd qing-elite-family-background
uv sync
uv run python -m qing_elite.v03.pilot.frame
uv run python -m qing_elite.v03.pilot.run
uv run python -m qing_elite.v03.linkage.run
uv run python -m qing_elite.v03.kin.run
uv run python -m qing_elite.v03.career.run
uv run python -m qing_elite.v03.analysis.run
uv run python -m qing_elite.v03.extension.run
uv run python -m qing_elite.v03.release --build-docs
uv run python -m qing_elite.v03.release
uv run pytest
```

公开快照不携带逐人级 v0.2 linkage baseline，因此包含该基线的 U06R legacy comparison 需要从授权原始数据在本地重建；`scripts/recover_v02_artifacts.sh` 只适用于保有私有历史的研究副本，不能在 GitHub 的公开无历史快照中使用。`DEEPSEEK_API_KEY` 仅从 shell 环境变量读取，绝不写入仓库。外部 API、OCR 服务及上游来源可能随时间变化；请以 [REPRODUCIBILITY.md](REPRODUCIBILITY.md) 的版本、种子、阈值和已知外部约束为准。

## 目录

```text
config/v03/             研究契约、来源优先级、亲属本体、审核与职业配置
src/qing_elite/v03/     设计、抽取、链接、亲属图、职业面板、分析、门禁与发布代码
reports/upgrade_v03/    U03R–U11R 阶段报告、最终报告和汇总图表
audit/v03/              阶段门禁、对抗审计和发布记录
sources/literature/     可公开的文献登记、摘要与检索队列（不含受限全文）
data/raw/manifest_v03.json  原始来源元数据与校验值（不含原始文件）
```

## 关键文档

- [v0.3 最终报告](reports/upgrade_v03/FINAL_REPORT_V03.md)
- [v0.3 研究契约](V03_RESEARCH_CONTRACT.md)
- [来源优先级与访问纪律](V03_SOURCE_PRIORITY.md)
- [发布清单](RELEASE_MANIFEST.json)
- [复现记录](REPRODUCIBILITY.md)
- [v0.2 后评估](reports/upgrade_v03/V02_POSTMORTEM.md)

当前未单独声明代码许可证；第三方来源始终按其各自条款使用。
