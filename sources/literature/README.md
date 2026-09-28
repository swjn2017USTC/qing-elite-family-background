# sources/literature — V0.3 现代学术文献库

框架来源：三通项目已验证的 `ACADEMIC_LITERATURE_PIPELINE_HANDOFF`。
**只复用框架与字段约定，不复用其 topic data 与研究结论。**

## 目录

```text
sources/literature/
├── registry/literature_registry.jsonl   全项目唯一登记表（LIT-NNNN）      [tracked]
├── registry/README.md                   字段 schema                      [tracked]
├── digests/LIT-NNNN.md                  每篇 core 一份 Paper Digest      [tracked]
├── queues/acquisition_queue.{md,jsonl}  人工获取门（human gate）          [tracked]
├── logs/parse-LIT-NNNN.json             解析 QC 报告                     [tracked]
├── public/                              许可清楚的 OA 全文本体           [gitignored]
├── private/inbox/                       机构授权全文（人工放入）          [gitignored]
├── private/normalized/                  完整提取文本                     [gitignored]
├── candidates/                          候选池（中间件）                  [gitignored]
└── manifests/                           OA manifest（按需）               [gitignored]
```

## 铁律

1. 同一篇论文全项目只登记一次（按 DOI，否则按归一化标题去重）。
2. `fulltext_verified: true` 只能由**成功解析**支撑；解析失败/文本过短一律回落 ABSTRACT/METADATA。
3. digest 是下游唯一二手来源：ledger、historiography、claim delta 只读 digest，不重读原文。
4. 不绕过登录/验证码/付费墙；机构授权文献进 `queues/acquisition_queue.*`，由人工放入 `private/inbox/`。
5. 二进制全文不入 Git（`.gitignore` 已排除 `public/*` 与 `private/`）；registry 只登记路径与 sha256。

## 命令

```bash
uv run python -m qing_elite.v03.lit.harvest --plan     # 检索计划
uv run python -m qing_elite.v03.lit.harvest            # 检索 → 候选池 → registry（带磁盘缓存）
uv run python -m qing_elite.v03.lit.acquire            # 取 OA 全文并解析（只取许可清楚的 OA 位置）
uv run python -m qing_elite.v03.lit.acquire --queue    # 重写人工获取队列
uv run python -m qing_elite.v03.lit.registry summary   # 统计与缺 digest 清单
uv run python -m qing_elite.v03.lit.registry validate  # schema 校验
```
