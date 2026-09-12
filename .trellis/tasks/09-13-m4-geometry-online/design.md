# T13-B 设计与冻结接口

复用图片/关联工作流、checkpoint表和串行GPU队列；不建第二调度器，不把GPU放HTTP请求。T13-A模型/量化语义不变。

## Root：契约、对话与准入
`contracts/geometry.py`: GeometryOptions(operation: Literal[segment,relative_depth,relative_measure], roi_xyxy: tuple[FiniteFloat,FiniteFloat,FiniteFloat,FiniteFloat]|None=None, finding_id:UUID|None=None)。ROI/finding互斥；缺区域可解析，分割准入时澄清。GeometryDetail(task_id:UUID, segmentation:SegmentationResult|None=None, depth:RelativeDepthResult|None=None, measurements:GeometryMeasurements|None=None)。
ActionKind新增RELATIVE_DEPTH=relative_depth；TaskPlan/ConversationIntent.geometry可选。geometry与动作一致，无增强混用。compile顺序 detect→verify→segment/relative_depth/relative_measure→summary/report；relative_measure自动补segment前驱。旧计划geometry缺省None。
`workflow/geometry_config.py`: build_geometry_config(settings,options,check_files=True)返回 {version:geometry-online-v1,options:...,segment:pin|null,depth:pin|null}，pin={model_id,model_version,checkpoint,device,call_id}。Settings.geometry_models_dir=var/models/geometry,geometry_device=cuda:0。validate_geometry_input(asset,options,task=None)验证原始图片/像素，返回原图ImageRegion或None；finding从task.snapshot.findings中找，必须唯一匹配原图region。新检查不接受finding_id（尚无本任务发现）。
Starter在新/关联run.config写geometry。关联保留operation=supplement_review/force_review=True历史准入契约，geometry分支实际不调用VLM。已有request先复用固定run，不重新模型准入。
只有已有任务、当前turn没有input_asset却被解析成新建几何inspect时澄清，不允许以意图分类绕过关联额度；真正新检查需本条上传/指定原图。

## Runtime：持久执行
新workflow/geometry_activities.py: GeometryActivities(settings,repo,media,segmenter_factory=None,depth_factory=None)，复用T13-A工具。prepare_image_geometry保存geometry_source={decision:InspectionDecision,input_asset:AssetRef,prompt:ImageRegion|null}；新图baseline由原detect/verify得出，关联从source冻结父task映射finding ID，不重审父结论。源finding在冻结父task找，prompt只保存一次。
GPU活动名segment_image和estimate_relative_depth；checkpoint分别geometry_segment/geometry_depth，payload={result:模型结果JSON,measurements:GeometryMeasurements JSON|null}。segment带mask像素统计；relative_measure的depth步骤组合同源mask统计，独立depth的measurements=null。ToolCall.step_id同checkpoint，tool_name为segment / relative_depth（测量时为relative_measure），每工具独立pin.call_id。失败存安全代码，不泄露路径。
InspectionRepository.save_geometry(task_id,step,result,call,measurements=None)：同事务锁task，验证run.pin/callID/step/幂等键/任务源/形状/parent/来源prompt/测量一致性，存2派生资产+call+checkpoint。已提交检查点返回已有资产，冲突不覆盖。实现可放storage/geometry.py，由InspectionRepository薄方法转发；不建表。
新workflow/geometry.py的run_image_geometry(run)：CPU prepare→所需segment GPU→所需depth GPU，timeout180s、预算有限重试；无geometry key不增加命令。image.py原verify后接入，supplement.py独立geometry分支。activities.finish_image读取冻结baseline保留原发现/覆盖，工具缺失/失败则partial及tool_error，禁止转human gate掩盖必需失败；原detect/VLM失败仍按原逻辑，不能因geometry配置变成正常。
注册scripts/inspection_worker.py；GPU并发仍1，缓存检查先于模型/环境校验。

## Presentation：API与页面
GET /api/tasks/{id}/geometry -> GeometryDetail；loader读两个checkpoint的result/measurements并验归属，优先depth测量否则segment测量，旧任务全null。TaskReport新增geometry:GeometryDetail|None并验task/source；JSON/HTML用实际资产，不公开本地模型路径。
frontend沿用EnhancementPanel模式，新增GeometryPanel/类型/边界校验、工具阶段名称和可展开面板；原图/框/掩膜/叠加、depth白近黑远、NPY链接、像素/全图占比/相对单位。区分错误与无结果，切task/关闭丢弃过期请求。显示finding_id供对话引用，不新增编辑器。

## 并发所有权
Root: contracts/geometry.py,planning.py,conversation.py；agent/intent.py,conversation.py；workflow/start.py,geometry_config.py；config.py；tests/test_geometry_conversation.py,test_geometry_admission.py；文档与真实GPU/API验证。
Runtime: workflow/geometry.py,geometry_activities.py,image.py,supplement.py,activities.py；storage/geometry.py,inspection.py；scripts/inspection_worker.py；新tests/test_geometry_workflow.py,test_geometry_online.py,integration/test_geometry_storage.py,test_geometry_workflow_live.py。
Presentation: api/inspection.py,reports.py；contracts/reports.py；顶层reports.py；frontend/src必要文件；新tests/test_geometry_api.py,test_geometry_reports.py。
允许只读其他文件。不嵌套委派/VCS/GPU/真实API，主agent整合并实测。
