# 推理机房扩容核算

项目按来源版本保存业务资料，已有 traffic、capacity、billing 环节。已有时段用量、静态机池和电价估算，未把高峰期限、迁移约束与收入承诺一起用于扩容选择。

运行与检查：

    python -m unittest discover -s tests -v
    python -m compileall -q compute_plan tests
    python -m compute_plan.cli data/example.json
