# T15-C 故障验收设计

复用PG/Temporal及Playwright隔离夹具，边界注入故障而业务编排/持久化真实运行。不破坏共享服务、不另建harness。
后端新建test_fault_matrix.py，复用test_image_workflow.case独立schema/合成图片，Settings改candidate_review后固定run_config。
检测替身给低置信度AJ；MockTransport驱动真正OpenAICompatibleClient/ImageReviewer，不mock决策/数据库/Workflow。
七场景：timeout后成功、429后成功、持续timeout、持续429、非法JSON、Schema不合法、torch.OutOfMemoryError（只抛异常，不占GPU）。
断言落库次数/状态、uncertain、检查点、失败不是normal且保留候选；成功明确accept候选。
SEWER_FAULT_OUTPUT可选写仓库var/evaluations新目录下逐场景JSON，含任务/调用/检查点/次数，不含密钥或原始远端响应；重跑用新目录。
既有检测提交回执丢失/终态视图修复用例复跑一次；共享服务重启及物理OOM不在本轮制造。

浏览器新增faults.spec.ts：route.fetch实发POST确认202后route.abort丢回执；重发比较payload，GET验证仅一轮对话/一任务，刷新后仍待审。
默认无付费E2E包含新场景，live筛选不变。保存错误和恢复截图，不为真实CCTV提交审核。
实现agent独占backend/tests/integration/test_fault_matrix.py；主agent独占frontend/e2e/faults.spec.ts、playwright.config.ts和文档。产品缺陷先复现再明确修复范围。
Trellis唯一账本、jj原工作区、分文件并行、一次全切片独立审查；T14-B规范/视频成本/用户最终界面验收仍待续。
