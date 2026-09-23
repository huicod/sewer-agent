# Design

- 公共字段 `inspection_mode: Literal["yolo_only", "yolo_vlm"]`，默认 `yolo_vlm`。放在 TaskSubmission/TaskRecord 与 TurnInput/TurnRecord，已有 JSON 缺失字段按默认读取。PostgreSQL 持久层和幂等比较显式包含它。
- 由后端冻结到 run.config；不是 LLM 自选的工具参数，也不复用内部 guided/candidate review mode。旧 run 无字段按旧流程执行，保护 Temporal 历史。图片 pure mode 跳过 verify 和 VLM 配置依赖。
- 纯 YOLO 直接保存原框和原分数。质量合格的检测可由独立 detector-only 规则确认，报告必须明确未复检；零框不设置 negative_audit_complete，无必要人工阻塞。保持既有 NORMAL 约束，不修改融合代码。
- 确认来源增加 `detector_only_rules`，不冒充 VLM/人工确认。图片证据增加 `inspection_mode` 和 `review_status`（completed/skipped_by_mode/unavailable），前后端同步字面量并兼容历史缺省字段。
- 视频/增强/几何组合保持既有 VLM 路径；纯 YOLO 下拒绝不支持的组合。已完成任务的询问/报告可正常使用，追加动作不可悄悄改变父任务模式。
- 前端两个提交入口投影同一字段，重试身份含模式；既有任务显示保存的模式。视频不开放纯 YOLO 选择，并清楚提示。
- 离线模块 `evaluation/image_level.py` 只读 calibration state（及可选 confirmation state）。输出 raw_yolo、conservative、experimental、confirmed_additions 四投影，后者仅在提供二次确认输入时出现，缺失或错误确认不接受且显式计数。按 source 显式 mask 后按 label set 计数；空集合等于空集合算整图匹配，不能当阳性 TP。
- 输出含源文件路径、口径、样本数、失败计数、逐图集合/TP/FP/FN、汇总和原 IoU=.5 框级统计。生成物在 var/evaluations 下，不覆盖原实验文件。

Existing evidence: ../09-18-vlm-calibration-run/image_level_report.md. 60 图 raw TP59/FP1/FN11，experimental 61/15/9，confirmed 59/11/11；原框级 raw69/38/39。
