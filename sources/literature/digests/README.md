# Paper Digest — 格式与纪律

每篇 `tier = core` 一篇 `LIT-NNNN.md`。digest 是下游**唯一二手来源**：ledger、historiography、
claim delta 只读 digest，不重读原文。机器校验 `src/qing_elite/v03/lit/registry.py:check_digest_sections`。

## header（必须）

```text
- literature_id: LIT-0007
- title / authors / year / venue / doi:
- evidence_level: FULLTEXT | ABSTRACT     ← 我们实际引用到的层级，不得高于实际
- access: <route> (<local path 或 OA url>)
- digest 生成者: <agent/model> / <date>
```

`evidence_level: ABSTRACT` 时，所有 locator 一律写 `abstract`：**不得伪造页码或章节**。

## 九个正文章节（标题逐字出现）

```text
## THESIS
## PRIMARY MATERIALS USED
## METHOD / INFERENCE
## KEY CLAIMS + LOCATORS        ← 每条带 locator；ABSTRACT 级写 `abstract`
## KEY COUNTERCLAIMS
## WHAT PAPER REJECTS / REVISES
## LIMITATIONS                  ← 样本、年代、地域、推断强度，诚实写
## RELEVANCE TO V03 CLAIMS      ← 对应 V0.3 exposure/outcome 变量
## NEW LEADS
```

## 禁止

- 把摘要里没有的内容写进 KEY CLAIMS；
- 把"作者没有说的话"写成作者主张；
- 在没有全文的情况下声称页码/图号；
- 用 digest 代替原文做逐字引文（引文必须回原文 span）。
