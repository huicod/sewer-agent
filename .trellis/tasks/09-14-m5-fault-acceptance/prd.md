# T15-C 故障矩阵与浏览器断线验收

## Goal

补齐T15关键失败分支的证据：API传输/响应错误、GPU异常、浏览器回执丢失；不改检测/VLM策略。

## Requirements

- 生产Workflow/Activities与真实PG/Temporal，candidate_review模式；仅模型/HTTP边界注入故障。
- 超时/限流有界重试，超时保存uncertain、明确限流保存failed；非法JSON/Schema与OOM不盲重试；失败不得变正常，已保存候选不丢。
- 浏览器已提交后丢失回执，同一输入重发复用request_id和asset，只有一轮对话/一个任务；刷新可恢复。
- 独立schema/队列、合成媒体、0真实API/GPU推理；不改.env/依赖/模型，不停共享服务/用户任务、不推送。
- 记录实际结果/截图和必要未完项，不把注入异常当成供应商真实中断或物理显存耗尽。

## Acceptance Criteria

- [x] 七种后端故障场景及既有检查点恢复实际通过，有落库结果证据。
- [x] Chromium回执丢失、重发、刷新通过，有截图和单任务记录。
- [x] 验证/独立审查、总体计划、Trellis与jj进度保存，见report.md。

## Notes

- 已批准M5的验收切片；T14-B规范资料、视频成本和用户最终界面验收仍待后续。
