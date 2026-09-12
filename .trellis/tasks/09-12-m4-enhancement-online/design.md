# T12-B 图片增强在线集成

沿已批准方案，采用既有图片/关联复检工作流的可选扩展，不建设第二个任务引擎或工具微服务。新检查顺序为 detect → verify（原图）→ prepare_enhancement（冻结基线）→ enhance（GPU）→ enhancement_review（原图与增强图 API）→ finish → 必要时人工等待。无增强 pin 的旧任务不产生新命令。关联补证复用 supplement 的单次准入和 source 快照，增强分支只执行后三个工具，不再次检测或重做原图 VLM。

参数 EnhancementOptions 只含 kind 与可选 roi_xyxy；超分缺框由对话澄清，真正执行时校验原图尺寸和 T12-A 像素上限。每个任务只允许一种增强；复杂多工具、视频增强、未部署能力先明确不执行，不能忽略用户部分要求。模型映射、设备、180 秒 GPU 超时、最多 3 次传输/存储尝试由服务端固定；OOM/缺权重/坏输出非重试错误。自动路径不启用。

增强 pin 包含 online version、请求、固定模型文件名/ID/本地路径/设备、调用 UUID 及辅助 Prompt 版本。恢复无 checkpoint 时验证当前配置与 pin 相同；有 checkpoint 时读取原证据而不偷偷重跑。原始资产、派生资产、调用和 step 统一沿用现有表，以事务和任务行锁提交增强结果；晚到重试读取赢家，不登记另一成功产物。GPU/API 成功但持久化前进程故障仍可能重算/重复计费，不承诺 exactly-once，也不新增哈希体系。

辅助复核输出 EnhancementReview：quality_effect、conflicting_finding_ids、additional_anomalies（原图坐标）、summary。请求含原图、增强图、实际 ROI、固定原图基线 findings 和五类视觉描述；不修改现有 VLM Prompt。校验引用 ID、坐标及 ROI；新异常一律待审，原图已有发现不删除/改类/改框/改分，只有辅助冲突转待审。原图覆盖/画质/阴性审计不由增强提升；工具失败保留基线但结果 partial/inconclusive，必要工具未完成不进入可误点为完成的人工等待。

对照 API GET /api/tasks/{id}/enhancement 返回 {task_id,result,review}，未生成字段为 null；不暴露服务器路径。TaskReport 增加可选 enhancement/enhancement_review，旧报告仍可读。网页新增按需展开的对照面板，显示服务端阶段、原图和派生图/ROI、复核摘要；HTML 报告同样引用同源媒体。

本轮只补齐图片增强闭环，视频仍使用现有处理流程；视频增强/分割/深度不顺手加入。验证以替身单元测试、独立 PG schema/Temporal 队列故障恢复和受控真实调用分层进行；无新依赖、无公开部署、无原实验覆写/回退/推送。
