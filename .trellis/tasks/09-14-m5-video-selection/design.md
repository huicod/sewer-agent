# 视频有效证据选择

旧策略按时间顺序取每个事件最多3帧，再放周期审计；预算不足时前段事件和同事件多帧会挤掉后段/阴性审计。单靠亮度清晰度不能区分清晰草地和管内，因此采用确定性均衡候选池＋有界视频专用VLM场景判断。新模型、全视频VLM逐帧分析和基于猜测剔除整段均不采用。

## 流程和界限

全帧初查/聚合不动。新任务固定`video_selection_version=video-selection-v1`；无该pin的旧任务完全沿用原选择。现有`prepare_video`在CPU worker内完成候选池、原PNG证据提取、场景批次和最终选择；不新增Workflow命令。

- 候选仍为需要复核的事件证据＋周期审计；最多64帧、且不超过2×原复检预算。候选池和最终选择均为审计预留约四分之一，再用空闲名额补齐；按时间均衡选取，不取早期前缀。优先为一个事件分配完整所需证据组，再考虑下一个事件，避免许多事件各复检一帧却全部无法确认；选组时兼顾时间、单帧事件和共享帧去重，不能只堆积早期三帧事件。
- 场景判断仅在候选至少4帧、预算允许且未强制逐候选复检时启用；每批最多16张512像素长边预览，最多4批。输出仅`pipe/outside/unassessable/unknown`＋简短原因；缺陷、暗管、局部遮挡不可因预览不清直接归无效，应归unknown。
- 仅整组所需证据均明确outside/unassessable才延后该事件组；pipe优先，混合/未筛完整的组及unknown仍有资格，不能因一帧不清就延后整个事件。强制复检不使用场景延后。所有原事件及证据仍保存，最终裁决继续基于原图详细复检；预览不计作ImageReview，既有缺失复核/审计仍待审。
- 场景物理语义批次数从`video_review_budget`扣除；剩余名额用于原ImageReviewer。传输重试单列。场景错误保存错误码和unknown回退，不宣称已过滤/已复核。
- 每批`video_scene:<batch>`与调用记录原子save_step；最终`video_evidence.selection`保存选帧、原因/统计。缓存优先，不覆盖已存选择。取消/持久化异常必须传播。
- 原有save_step只允许成功/跳过；场景失败缓存使用显式`allow_error_checkpoint=True`才接纳failed/uncertain并原子保存，其他调用默认约束不变。
- Sync Activity中调用异步API使用`asyncio.run`；请求完整墙钟上限20秒（不只HTTP分阶段timeout），批前后和准备预览时心跳，低于现有30秒heartbeat门限。调用结构复用现有API client，不改图片Prompt。客户端可注入供测试/真实用量记录。

## 前后端契约

`VideoSceneAssessment`: `frame_index:int>=0, scene:pipe|outside|unassessable|unknown, reason:非空<=300字`。
`VideoSceneBatch`: `assessments`，最多16项；调用方验证索引恰好覆盖输入、无重复/越界。
`VideoReviewSelection`: `version=video-selection-v1, candidate_count:int>=0, pool_frames:int[], review_frames:int[], deferred_count:int>=0, unreviewed_event_count:int>=0, unreviewed_audit_count:int>=0, scene_calls:int[0,4], scene_errors:str[], assessments:VideoSceneAssessment[]`。pool最多64项；review为pool子集；计数及索引唯一性校验。deferred_count包含未进候选池者，不伪造其场景；unreviewed计数表示所需详细复检未完整入选，不等于已经尝试后失败。
现有`GET /api/tasks/{id}/video`的`evidence.selection`为可选新增字段；旧载荷缺字段仍兼容。页面显示已选详细复检帧数而非“已完成”，场景建议与候选真值分开，并显示延后/失败；不新增接口。

## 验证范围

只读设计复核确认：旧完整147.9秒视频budget32只覆盖到72.16秒，15张周期审计全未入选；真实井外帧29和模糊帧330均被本地质量规则判adequate，故需语义辅助。沿用video-review-v1裁决并独立固定新selection版本即可区分旧选择，不更换裁决版本。每批缓存须匹配run的版本/源资产/有序帧索引/模型及Prompt pin，不使用哈希。

先用固定合成事件做TDD，再用T15-D已存真实帧做有限API筛选对照。长视频选完整`videos-2/W114_W115_20260610142556.mp4`（751.163秒），非剪辑，不加速播放；真实主检测/工作流＋有界API，单独记录完整覆盖和未复检项，不计算缺少真值的准确率。延长现有验收harness须额外显式长视频开关，上限900秒，默认60秒不变。
