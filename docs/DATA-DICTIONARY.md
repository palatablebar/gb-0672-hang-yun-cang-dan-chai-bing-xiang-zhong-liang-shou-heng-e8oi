# 舱单数据定义

id是主单、分组单或箱货明细唯一字符串编号，parent为空表示主单。节点申报件数、净重和毛重是其完整子树的申报总量，校验时只加直接子节点，避免多层重复计数。包装carton=1件，pallet=10件；重量kg与t统一为kg。件数必须非负整数，净重非负且毛重不得低于净重。
拆并结构采用每节点最多一个父级的有向图；自环、长环、缺父级均阻断封存，保留环路径。不会以平面求和替代逐层校验。
危险品按明确分类与UN编号检查，3类与5.1类禁止同箱；未知分类和缺失编号需复核。此演示规则只覆盖文中三类，不替代运输合规审批。
报关总量只汇总主单，与context.json内的申报件数、净重和毛重逐项比较。重复同值折叠，重复编号冲突全部隔离。原始计数=采纳+隔离+空行+重复表头+重复同值行。
封存需所有图、重量、危险品和采信问题处理完毕。数值均Decimal，对外字符串保留精度，编号不转整数。

## 超期用箱责任账（M8 detention）

两份清册：`detention_shares.csv` 字段 container/master_bill/pieces/note，描述箱被哪些主单拆并共用及件数；`detention_events.csv` 字段 container/event/event_time/receipt_id/actor/closed，event 取 GATE_IN、GATE_OUT、PICKUP、RETURN，时间必须带时区偏移，closed 仅 yes/no。

占用天数按起止时刻整24小时向下取整。港口口径=GATE_OUT-GATE_IN；承运人口径=RETURN-PICKUP，作为正式责任起算依据，港口口径仅用于交叉解释。超期天数=max(0,承运人计时天-免费箱期)，箱费用=超期天数×日费率。两口径天数差×24小时超过 basis_tolerance_hours 记 BASIS_UNRECONCILED。

正式责任三闸门同时满足才入账：①箱的全部主单映射均存在于本批图根节点（否则 RELATION_NOT_CLOSED）；②PICKUP/RETURN 回执齐全且 RETURN closed=yes（缺失或未闭合分别记 PICKUP/RETURN_RECEIPT_MISSING/OPEN）；③两口径可互相解释。争议箱 entry_status=争议暂记、container_fee=0.00、sealable=false，不写入正式责任金额。

结算策略 explainable 只把可解释箱转为正式责任；whole_container 在任一箱存在块码时把全部箱降级暂记并加 WAIT_WHOLE_CONTAINER。多主单分摊用最大余数法，各主单 bill_fee 之和严格等于 container_fee，同箱费用全局只计一次。

正式账独立持久化于 SQLite 的 detention_* 表，按 (run_id,generation) 版本化，仅最新版 active。补证回执按 receipt_id 幂等覆盖；拆并关系补证按 (container,master_bill) 覆盖，被替换关系以 pieces=0 墓碑保留不物理删除。每次重算只重建受影响箱，未受影响箱从上一版原样复制；证据表 detention_evidence 只追加。构建异常整体回滚，旧版保持 active。
