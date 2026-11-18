# 推理机房扩容核算

项目按来源版本保存业务资料。链路:traffic(时段负载)→ capacity(机池容量)→ billing(地区电价)→ scenario(同一窗口核对容量、时限、能耗与合同收入)→ compare(方案对比与归因)。

## 资料种类

- `load`:时段请求量,可带 `requests_low`/`requests_high` 预测区间
- `pool`:机池容量;`plan`:扩容方案(`plan` 名、窗口、地区、`add_capacity`、`unit_cost` 采购单价)
- `sla`:类别利用率上限(延迟承诺);无 sla 的类别记为无时限承诺
- `price`:地区电价;`facility`:机房 PUE、空闲冗余比例、闲置功耗比
- `contract`:合同单请求收入;`route`:跨地区调度单价(可选 `max_requests` 上限)

## 证据规则

- 资料以 (kind,id,revision) 存档,同版本内容冲突即拒绝;只有 known_at 之前收到的版本参与核算。
- 容量约束、任务时限、地区电价与合同收入在同一时间窗口核对:窗口内先按时限优先级分配本地容量,未满足部分按调度单价从低到高跨地区调配,再核算能耗(含冷却与闲置)与收入。
- 缺失的计量或收入证据一律记为待核并把相关金额置为 null,不用估值补齐;缺失证据不会被补成正利润。任何合计只有在全部证据齐备时才给出数值。

## 方案对比

`compare` 给出已批准方案与新方案的成本、服务能力与不确定范围差异,并分三段归因:

- `evidence`:同一已批准方案在 before/after 两个知识时点的差异(预测、电价等版本变化引起)
- `plan`:同一 after 证据下新方案与已批准方案的差异(方案本身引起)
- `total`:两者合计;`evidence_changes` 列出两个时点间实际变化的资料版本,供投资会定位变化来源

运行与检查:

    python -m unittest discover -s tests -v
    python -m compileall -q compute_plan tests
    python -m compute_plan.cli data/example.json
    python -m compute_plan.cli data/example.json --scenario mix-spill
    python -m compute_plan.cli data/example.json --compare
