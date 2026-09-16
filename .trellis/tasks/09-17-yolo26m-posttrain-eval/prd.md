# Evaluate completed merged-five YOLO26m training

## Goal

Audit completed training; evaluate fixed held-out data, source/class metrics, old-model reference, gallery and local detector smoke without deployment or API calls

## Requirements

- 检查训练终止原因、最佳轮次、loss和checkpoint完整性。
- 使用冻结的606张test：新best做mixed、sewer2403五类、SEDD仅PL/BLOCK标准评估；不按测试结果调参或重新选权重。
- conf=0.25、IoU匹配0.5、960输入，比较新/旧模型逐图结果；按名称映射类别而非假定ID相同。旧模型可能见过新sewer测试图，只作诊断参考。
- 保存逐图框/分数、原标注与新旧预测可视化、汇总表；测试本地检测适配器兼容性。
- 不调用VLM/LLM API、不重新训练、不修改在线权重或数据划分、不推送。

## Acceptance Criteria

- [x] 训练审计、固定测试集及分来源指标可追溯。
- [x] 实际检测图片与逐图数据可供人工复查，兼容性和相关回归完成。
- [x] 记录效果与剩余限制，更新进度；不把训练成功等同于业务效果达标。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
