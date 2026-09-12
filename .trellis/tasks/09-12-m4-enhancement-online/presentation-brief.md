# T12-B 增强对照页面与报告

先读 AGENTS.md/governance、Trellis overrides 与 backend/frontend conventions。已批准增强在线切片，不改检测/VLM/工作流。写范围仅 `backend/src/pipe_inspector/contracts/reports.py`、`api/reports.py`、`api/inspection.py`、`reports.py`、`backend/tests/test_reports.py`、新增 `backend/tests/test_enhancement_api.py`、`frontend/src/` 与本目录 `presentation-report.md`。不编辑其他后端契约/配置/存储/工作流；不嵌套委派、不操作 VCS、不装包/启动服务/调用模型。

主代理已经提供 contracts/enhancement.py 的 EnhancementResult/EnhancementReview/EnhancementDetail。Result 有 input_asset/output_asset(kind=enhanced_image,parent=input)、kind、model_id/version、source_region(原图尺寸+实际ROI)、quality_before/after、review_required=true。Review 有 quality_effect=improved|unchanged|degraded|uncertain、conflicting_finding_ids UUID tuple、additional_anomalies（现有 AdditionalAnomaly 原图坐标）、summary。Detail 为 {task_id,result:null|Result,review:null|Review}。检查点分别 `enhance`（Result JSON）与 `enhancement_review`（Review JSON直接对象），不读/返回 config pin（含服务器路径）。

1. GET /api/tasks/{task_id}/enhancement：确认任务存在，返回 EnhancementDetail（没跑过 null；各中间阶段可读），验证 result.input_asset==task.input_asset；非法落盘不能静默展示成别的任务。可加读取 helper 避免报告/详情两处重复，但只在上述允许文件内。旧 fixture repository 缺 load_step 时请给 fixture 补真实方法，不在生产中用 getattr 绕过契约。
2. TaskReport 增加 enhancement/enhancement_review 可选字段；校验输入对应任务，review不能没有result。报告API读取同一两个检查点；旧结果字段默认null保持兼容。HTML 显示独立原图/增强图、source ROI、模型版本、质量前后及辅助复核摘要，强调派生图供复核、不改原结论；CSP只增加img-src 'self'。显示input/output资产链接，不能呈现服务器本地路径、原始LLM消息或Key；用户字符串继续HTML转义。现有parent_task_id仍表示单次关联补证（包含旧追加复检），措辞可泛化但不要改其链接/身份。
3. 前端 typed network boundary 校验新Detail，加入 EnhancementPanel 与 App 按需展开入口（可用于waiting_review以及终态，处理时显示已存结果/暂未生成）；不要为所有历史卡片增加自动请求/轮询。显示原图与增强图、source ROI、模型版本、quality_effect/summary；ROI用原图坐标标识，可SVG矩形覆层，不画猜测框。SR与原图不等大，说明原图ROI是处理范围。正在处理阶段沿task.snapshot.stage=tools，active_tool 为 dehaze/deblur/super_resolution/enhancement_review，显示中文名。网络错误不能显示成功，切换任务/折叠后旧请求不能覆盖当前结果，重新展开读取持久内容。
4. 测试先红后绿：后端null/已存结果/中间状态/报告parent及转义/read-only；前端实际 HTTP boundary 的合法/非法字段、展开结果、加载失败、切换/恢复，不mock掉新组件本身。不测试文案逐字不变。Run focused pytest and `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend test`、build。root跑完整后端测试和独立审查。

本轮只有图片增强；UI不要宣称自动触发、视频增强、已恢复真实细节或精度提高。新检查顺序原图detect/verify→GPU enhance→双图辅助review；已有图片任务追加补证保留parent原结果。缺ROI的SR由对话澄清，不需要新增人工画框编辑器/参数平台。

自审后写 presentation-report.md：文件、red/green命令和结果、任何风险；返回短状态。若接口有关键疑问先询问root，不扩展写范围。
