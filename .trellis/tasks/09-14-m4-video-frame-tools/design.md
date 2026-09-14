# 最小设计

复用`ImageSupplementWorkflow`及原工具/面板，不另建工作流。新任务`operation=supplement_review`，仅新增不可变`frame_source` pin；旧任务命令不变。

公共`TaskRecord.frame_source`可选，字段固定为`{video_asset:AssetRef, frame_index:int, timestamp_seconds:float}`；task.input_asset为FRAME，parent_task_id为真实视频任务。无发现也能显示来源，现有报告含task故自然携带。run pin含version=`video-frame-tool-v1`、parent_task_id、video_asset、frame_asset、frame_index、timestamp_seconds、width、height、operation（enhance/segment/relative_depth/relative_measure）。

准入：独立`sewer_video_frame_tools`表，唯一(parent_task_id,frame_asset_id,frame_asset_version,operation)，task_id唯一；原图片一次supplement表不改。父行锁内验证同会话、原始VIDEO、waiting_review/completed/partial、FRAME.parent及`video_evidence.frames`唯一匹配、维度/帧号/时间与已存FrameObservation一致。request优先返回冻结child，不重建模型pins。Turn已有task_id+input_asset组合，只给有效video/FRAME组合开窄口，不允许任意derived。

原子保存task/run/quota/detect/source/frame_source：detect是去掉locator的既有ImageObservation；source.task仍真实父视频；frame_source checkpoint保存pin、原帧观察/可选video_verify、单帧decision。decision复用既有decide_image_recall，finding ID固定到child，evidence补准确locator；覆盖只取此帧，绝不复制整片结论。enhancement/geometry baseline和错误回退优先此冻结decision；新增增强建议也带locator。缺帧复检不冒充已审。

默认输入校验仍只接受原图；FRAME仅在完整pin+checkpoint匹配下用于工具及原子落库。分割/相对测量要求该帧像素ROI；第一版不接受frame工具finding_id（安全说明请提供ROI），整帧depth无需框。不自动追加verify/auto增强，不链式处理派生图。

UI选帧把parent task与FRAME绑定到当前提交，切换附件/会话/任务清除，重试身份包括FRAME ref。源视频显示时间线，FRAME子任务只展示来源和既有增强/几何面板；不能因有locator就请求其/video接口。报告说明仅分析此帧，不改变整片审核结论。
