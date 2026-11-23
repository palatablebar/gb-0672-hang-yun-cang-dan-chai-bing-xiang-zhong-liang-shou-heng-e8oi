# 航运舱单拆并箱与重量守恒

给货代单证员使用。主提单与分提单拆并形成有向无环关系，件数和净毛重逐层守恒。

八个业务模块：舱单采信、包装映射、拆并编组、危险品检查、净毛重核验、报关对账、版本封存、超期用箱。

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

访问 http://localhost:18241 。启动直接载入现场脏批。切换干净样例和旧编码样例可对照核验。干净批42个节点、6个主单，件数240、净重2400kg、毛重2640kg；现场脏批包含父级缺失、依赖环、危险品不相容及1kg毛重差异，封存状态为待处理。

各模块页面对应 `/<模块英文名>`，各模块执行入口为 `POST /api/<模块英文名>`，不传参即可独立演示。完整运行 `POST /api/runs` 支持 `dataset`（dirty/clean/legacy）、`csv_text` 与 `overrides` 参数。批次历史 `GET /api/runs`，详情 `GET /api/runs/{run_id}`。

同输入与规则版本生成相同批号，只保留一条批次。复核入口 `POST /api/runs/{run_id}/review` 提交当前 `revision`，陈旧提交409。归档包只有一个下载入口 `GET /api/export/{run_id}`，含证据和运行摘要。原始隔离在页面同屏显示。

SQLite使用命名卷，容器重启后保留历史。要重建演示可在Compose中改用新的卷名称，保留旧卷。

源码在 `app/`，样例在 `sample_data/`，测试在 `tests/`；定义见 `docs/DATA-DICTIONARY.md`，模块和边界测试对应关系见 `docs/DEPTH.md`。输入上限2MB，属于离线复核工具。

采信页支持上传或粘贴CSV。完整源字节与参数随批次持久化，跨页通过run_id读取同一结果。`POST /api/runs/{run_id}/rerun` 读取该批不可变输入快照重新计算，规则未变时批号不变。切换另一示例则创建或复用另一批次。

## 超期用箱责任账

港口按进场/离场计时，承运人按提空/还箱回执计时，两套时点同屏留痕。样例清册在 `sample_data/detention_shares.csv`（箱↔主单拆并共用）与 `detention_events.csv`（用箱节点与回执闭合状态），费率、免费箱期、口径容差与结算策略在 `context.json` 的 `detention` 节。

- 正式责任三闸门：拆并关系闭合、还箱回执闭合、两口径差异在容差内可互相解释。任一不满足只写“争议暂记”，不计正式金额、不可随舱单封存。
- 结算策略：`explainable`（默认，只结算双方可解释箱，已拆并清楚的箱先定责）或 `whole_container`（整柜等待回执闭合，任一争议则全柜暂记，块码 `WAIT_WHOLE_CONTAINER`）。
- 同一只箱费用只计一次；多主单共用按件数最大余数法分摊，分摊合计严格等于箱总额。
- `POST /api/detention/{run_id}/ledger` 生成正式账（幂等）；`/receipts` 补证回执、`/shares` 补证拆并关系（支持 `replace_master_bill` 零件数墓碑），同一回执重复提交不新增版次不新增费用。
- 箱期或拆并关系变化只重算受影响箱，其余箱沿用上一版；证据链 `detention_evidence` 只追加不删除。生成在单事务内完成，失败回滚旧版责任账。版次历史 `GET /api/detention/{run_id}/generations`。
- 导出封存件中 `detention_ledger.json` 只含可封存正式责任箱；已生成正式账时另附 `detention_ledger_official.json` 与完整 `detention_evidence.json`。
