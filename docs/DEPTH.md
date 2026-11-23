# 深度登记 航运舱单拆并箱与重量守恒

规模：中型。使用者：货代单证员。核心规则：主提单与分提单拆并形成有向无环关系，件数和净毛重逐层守恒。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 舱单采信 | 执行并复核舱单采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 包装映射 | 执行并复核包装映射 | `POST /api/packaging` | `app/modules/packaging/rules.py` | `tests/test_packaging.py` |
| M3 拆并编组 | 执行并复核拆并编组 | `POST /api/grouping` | `app/modules/grouping/rules.py` | `tests/test_grouping.py` |
| M4 危险品检查 | 执行并复核危险品检查 | `POST /api/hazard` | `app/modules/hazard/rules.py` | `tests/test_hazard.py` |
| M5 净毛重核验 | 执行并复核净毛重核验 | `POST /api/weight` | `app/modules/weight/rules.py` | `tests/test_weight.py` |
| M6 报关对账 | 执行并复核报关对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M7 版本封存 | 执行并复核版本封存 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |
| M8 超期用箱 | 生成责任账、回执补证、增量重算 | `POST /api/detention` | `app/modules/detention/rules.py` `app/detention_store.py` | `tests/test_detention.py` `tests/test_detention_store.py` `tests/test_detention_api.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码回退 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_legacy_source` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_source` |
| TRAP-04 | 分段表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_segment_header` |
| TRAP-05 | 缺列隔离 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_column_isolation` |
| TRAP-14 | 编号冲突 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_conflict_all_isolated` |
| TRAP-18 | 空态三分 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_three_empty_states` |
| TRAP-15 | 吨与包装换算 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_pallet_real` |
| TRAP-07 | 空值哨兵 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_sentinel_real` |
| TRAP-06 | 全角千分位 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/packaging/rules.py` | `tests/test_packaging.py::test_fullwidth_decimal` |
| TRAP-20 | 拆并依赖环 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/grouping/rules.py` | `tests/test_grouping.py::test_cycle_real` |
| TRAP-21 | 缺失父级 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/grouping/rules.py` | `tests/test_grouping.py::test_missing_parent_real` |
| TRAP-31 | 两口径时点错位 | 直接按港口时间向承运人计费 | 承运人提空/还箱回执为正式依据，港口口径容差交叉解释 | `app/modules/detention/rules.py` | `tests/test_detention.py::test_carrier_basis_overdue_and_fee` |
| TRAP-32 | 未闭合回执定责 | 争议箱写成正式责任 | 三闸门校验，未闭合只入争议暂记且不可封存 | `app/modules/detention/rules.py` | `tests/test_detention.py::test_open_return_receipt_blocks_formal` |
| TRAP-33 | 同箱重复计费 | 多主单各算一遍导致总额翻倍 | 箱费用只计一次，最大余数法分摊且合计守恒 | `app/modules/detention/rules.py` | `tests/test_detention.py::test_fee_counted_once_and_largest_remainder` |
| TRAP-34 | 补证重复入账 | 重试回执产生新费用 | receipt_id 幂等，同内容重试不新增版次 | `app/detention_store.py` | `tests/test_detention_store.py::test_idempotent_same_receipt_no_new_fee` |
| TRAP-35 | 全量重算覆盖已定责箱 | 箱期变化冲掉无关注释与金额 | 只重建受影响箱，其余箱原样复制上一版 | `app/detention_store.py` | `tests/test_detention_store.py::test_receipt_close_only_recomputes_affected_box` |
| TRAP-36 | 生成失败污染责任账 | 半成品覆盖旧版 | 单事务写入，异常回滚保持旧版 active | `app/detention_store.py` | `tests/test_detention_store.py::test_build_failure_restores_previous_ledger` |
| TRAP-37 | 补证删除历史 | 新关系物理覆盖旧映射 | 零件数墓碑 + 证据只追加 | `app/detention_store.py` | `tests/test_detention_store.py::test_share_relation_repair_uses_tombstone` |

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。
