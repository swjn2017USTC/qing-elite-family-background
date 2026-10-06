# U06R linkage benchmark

- 生成时间：2026-09-27T14:55:46+00:00
- 上游参考：`bruceyyu/ML-Chinese-record-linkage` @ `79fec7e5b7c16a9bb852b00c19dd4f969881d849`（CC BY-NC 4.0 (LICENSE.txt); README badge says CC BY-SA 4.0 — treat the stricter）
- CGED-Q release：`data/raw/cgeq/cgedq_jsl_public_1760-1912_personid_2026-08-28.tab` sha256 `92d74b4dbca2de3a…`（与 v0.1 冻结哈希一致：True）

## 1. 评测面（U02 gold，从 git 历史恢复）

- 对数 419（正例 120 / 负例 299；train 331 / held-out 88）
- 来源：recovered from git history: data/processed_v02/linkage_gold.parquet @ bd64fba

## 2. 三套 matcher（held-out）

| matcher | 接受的 pair | held-out precision | held-out recall | 备注 |
| --- | --- | --- | --- | --- |
| deterministic | 314 | None | None | 必须至少一条非姓名证据 |
| ML (logit) | — | 1.0 | 1.0 | 阈值 0.5；331 训练对 |
| Splink (v0.2 设置) | 353 | — | — | ok: {'blocking_rule': 'l.name_norm = r.name_norm', 'em_converged': True, 'scored_pairs': 401} (gold pairs scored: 353/419) |

### agreement pattern

| pattern | pairs |
| --- | --- |
| `det` | 197 |
| `det+ml` | 117 |
| `none` | 103 |
| `ml` | 2 |

## 3. auto-accept 区域（门槛 0.99）

- ML：**PASS**，阈值 0.35，precision 1.0，recall 1.0，覆盖 0.2841
- deterministic：**FAIL**，best available {'threshold': 0.5, 'precision': 0.3731, 'true_positive': 25}
- 灰带 abstention：[0.3, 0.7] 覆盖 0.0341，带内 precision 0.0

## 4. validator 与聚类

- chronology 冲突 117；geography 冲突 63；career transition 冲突 0
- 聚类：{'records': 618, 'components': 301, 'multi_record_components': 618, 'singletons': 0, 'size_distribution': {2: 576, 3: 30, 4: 12}, 'max_component_size': 4}
- 时间连通性：{'components_checked': 301, 'gap_violations': 13}

## 5. active learning

| round | labelled | best threshold precision | coverage |
| --- | --- | --- | --- |
| 0 | 40 | 1.0 | 0.2841 |
| 1 | 65 | 1.0 | 0.2841 |
| 2 | 90 | 1.0 | 0.2841 |
| 3 | 115 | 1.0 | 0.2841 |

- 人工新增标注：0 / 预算 300

## 6. 官方 id 与旧 dedupe 的关系

- release：1261658 条记录，132361 个 official person_id，缺 id 记录 92302；跨期同 id 515 人
- crosswalk：{'legacy_ids': 12682, 'relations': {'same_id': 11400, 'merged_by_v02': 1282}, 'official_ids_matched': 12682, 'merged_groups': 986, 'max_group_size': 8}

## 7. risk 与人工队列

- risk 分布：{'LOW': 279, 'MEDIUM': 140}
- 人工队列条数：102（无固定比例抽样）
