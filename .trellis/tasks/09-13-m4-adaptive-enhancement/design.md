# T12-C 已确认方案的实施边界

采用现有工作流可选扩展；不采用全图强制预处理或无限工具循环。`ConversationIntent` / `TaskPlan.auto_enhancement` 默认为 false，与显式 `enhancement` 互斥。只有本次文本明确允许按需增强才置 true；compile 生成一个条件 enhance 步骤。自动补证暂明确告知新建图片检查，避免默默转换用户操作。

新图片准入固定 `auto_enhancement` 配置：选择策略/Prompt 版本、选择调用 ID、当时可用的增强模型 pins。原流程 detect → verify 不变；仅自动 pin 存在时增加 `select_image_enhancement` Activity。选择器独立发送原图、质量线索和原检测/复核结果，不修改原 VLM。响应为一种退化、明确/不明确证据、影响判读的理由和一个可选 Options；后端检查退化与工具匹配、真实尺寸、像素预算和冻结可用模型。没有有用建议就保存 skipped，不执行 GPU/辅助复核。

选择结果与 API 调用原子保存为 enhancement_selection checkpoint。选择成功时返回派生运行参数（加入已固定的 enhancement pin）给既有增强命令；不覆写准入配置。Storage 的增强写入校验从固定候选加已持久化选择恢复有效 pin，禁止任意模型/ROI 注入。断点重试优先读选择和结果检查点，不重新选择；旧无 auto pin 工作流不增加命令。选择/执行失败输出 partial，不让工具缺失被人工误点成完成。

`EnhancementDetail.selection` 可选字段公开安全的选择摘要（status/reason/proposal/options），不公开模型服务器路径；报告同时保存摘要。新增 low_light kind 接已有 GPU adapter、派生资产及双图辅助 VLM。当前仅一个本地低照度模型为在线默认，另一候选可在离线对照验证；模型替换仍在服务端固定，不由 VLM 指定权重。

测试替身验证分支、原证据保护、调用数与缓存；真实 PG/Temporal 验证选择+增强事务和恢复。新增独立 var/evaluations/2026-09-13-adaptive-enhancement 证据目录，先少量真实低照度模型图像，再至多 12 次真实 Qwen API 验证选择和辅助复核；不得把小样本辅助判读变成精确率提升结论。12GB 显存不够时先缩小明确 ROI/比较可运行候选，不能悄悄换假增强或改原图。
