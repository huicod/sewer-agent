# Compare VLM-only and YOLO26m-guided review on frozen tests

## Goal

Run full sewer2403 and SEDD test sets through independent VLM and YOLO26m-guided VLM, save resumable evidence, galleries and per-class comparison

## Requirements

- 在冻结的 merged_five_v1 test 上比较两组完整图片流程：
  1. 独立 VLM：原图及其多视图，不提供 YOLO 候选；
  2. YOLO26m best（第54轮）+ guided VLM：YOLO候选、原图和候选局部图交给既定复检与融合。
- 两组均覆盖 sewer2403 562 张和 SEDD 44 张；SEDD 只对 PL/BLOCK 评分，其他类别保留展示但不计入FP/FN。
- 复用固定 test 标注、IoU≥0.5 的同类一对一匹配；保存每张图的 VLM 原始结构化结果、融合结果、请求证据和可视化；支持逐样本checkpoint续跑。
- 使用 .env 中现有 Qwen VLM API；不读出或写入密钥，不调用LLM、不修改数据/线上权重、不部署。

## Acceptance Criteria

- [ ] 两组各完成606张，结果和请求状态可复核，失败样本明确记录。
- [ ] 输出按来源/类别的 TP/FP/FN、Precision/Recall/F1 及相对 raw YOLO 的变化，明确人工标注局限。
- [ ] 生成两组逐图图库，并完成 API、融合、计分和产物一致性检查。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
