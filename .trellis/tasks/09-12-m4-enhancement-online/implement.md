# T12-B Implementation Plan

Trellis 管理唯一任务状态；使用 TDD 和有隔离范围的并行实现，按仓库覆盖规则只做一次集成审查，不建立 Superpowers ledger/第二套工作区。基线 e1638a63，jj vxkrkvwz。

## 1. Root：契约、准入与对话

- [x] `contracts/enhancement.py` 增加 EnhancementOptions(kind, roi_xyxy=None)、EnhancementReview(quality_effect, conflicting_finding_ids=(), additional_anomalies=(), summary)、EnhancementDetail(task_id,result=None,review=None)。ConversationIntent/TaskPlan 增加可选 enhancement 字段；旧无参数 enhance 意图可读，但不能执行。用新增 `test_enhancement_online.py` 测试非法参数/超分缺ROI/计划与参数相符。
- [x] `agent/intent.py` 的增强步骤移到原图 verify 之后，明确一工具及 ROI 语义；`workflow/enhancement_config.py` 固定权重及参数；`workflow/start.py` 为新图片/补证准入冻结 pin，视频拒绝增强，运行中参数不可换。对话版本增加；Dispatcher 显式增强支持、缺输入澄清、同 request_id 沿用已准入内容，其他未部署动作仍拒绝。

## 2. Storage 独立范围

- [x] `InspectionRepository.save_enhancement(task_id: UUID, result: EnhancementResult, call: ToolCall) -> dict`：任务行锁；校验归属、父资产/模型/输入输出 pin；已有 enhance checkpoint 返回已有 payload，不产生第二次成功登记；否则资产+调用+checkpoint 一事务。无新表，写范围限 storage/inspection.py 与独立 tests/integration/test_enhancement_storage.py。
- [x] 先写失败测试，再实现；独立测试 schema 覆盖成功/重复/并发、错误回滚和跨任务引用拒绝。不启动/停止共享服务，由 root 调度真实 PG 运行。

## 3. Root：可恢复执行与辅助复核

- [x] `workflow/enhancement_activities.py` 提供 prepare_image_enhancement、enhance_image、review_image_enhancement，分别冻结基线、调用 T12-A runner、调用新辅助 Reviewer。每步先读 checkpoint，工具记录含固定 ID/attempt/预算，原子提交调用 `save_enhancement`。
- [x] `agent/enhancement_review.py` 实现双图请求与严格响应校验，`agent/enhancement_fusion.py` 仅保留/标记原发现和添加待审；测试原高置信框不被擦除、辅助质量不能补正常、非法框/假引用拒绝、失败不完整。
- [x] `workflow/image.py`、`supplement.py` 按新 pin 条件串行执行三个新 Activities；`activities.py` final 读取固定 enhancement_source 基线及辅助结果，必需工具失败不完成。旧流程命令不变；worker 注册 GPU/CPU Activities。
- [x] 测试故障后缓存恢复、版本变化拒绝、先后顺序、人工等待和最终写库重试。新增真实 PG/Temporal 隔离测试，不使用第二套调度器。

## 4. Presentation 独立范围

- [x] `contracts/reports.py`、`api/reports.py`、`api/inspection.py`、`reports.py` 提供详情及报告；字段对应第 1 步接口，不暴露 pin 路径。报告显示原图和派生图、ROI、版本、质量/复核；HTML 仅放行同源图片。
- [x] frontend/src 的类型、校验、API、EnhancementPanel、App 对照入口及相关测试。页面按需查询详情，处理 null/失败/加载/任务切换，不为全部旧任务自动轮询。显示原图坐标 ROI、实际处理阶段，不宣称工具已成功或准确率提升。
- [x] 定向前后端红绿测试、全部前端测试和构建；不改 VLM、工作流或存储。

## 5. Root 集成与交接

- [x] 全量后端 pytest/Ruff，前端 tests/build，PG 并发/原子验证，Temporal 同队列替换 worker 后恢复；单独至多 3 次真实 API 调用及一个本地 GPU 增强端到端样例，输入和产物落在新 var/evaluations/2026-09-12-enhancement-online 目录。API 出错先查落盘/调用状态，不无限重试。
- [x] 一次只读独立审查，修复实际问题并验证；更新 README/.env.example/必要 conventions、任务报告、总体计划、journal，保存本地 jj 检查点，不推送。

接口交叉检查：root 产出 Options/Review/Detail → storage 和 presentation 只消费；storage 产出 save_enhancement → root Activity 消费；root 保存 enhance/enhancement_review checkpoints → presentation API 读取。并行文件不重叠。原 VLM/融合文件不在写范围；新增融合仅用于显式增强分支。
