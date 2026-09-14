# 视频证据选择实施计划

Trellis是唯一任务账本；按已批准计划使用SDD/TDD与一次独立审查，jj覆盖Git/worktree指令，无第二份进度账本。Spec：本目录prd/design及backend/frontend conventions。

## 全局约束

- 不改图片Prompt/复检/融合、主检测器/解码/事件聚合；不恢复开放补检，不引入新依赖或训练。
- 原始候选/PNG证据不得删除；场景预览不能自动通过缺陷、替代全图审计或填补未复检覆盖。
- 旧pin缺失保持原路径，缓存优先；场景筛选与详细复检共享原32语义预算，未知保留、异常不掩盖。
- 主Agent负责API/GPU实验、共享服务、文档和jj；实现者不操作这些，不嵌套代理。

## Task 1：后端选择闭环（实现者）

文件：新增`contracts/video_selection.py`、`agent/video_selection.py`、`workflow/video_selection.py`；修改`workflow/video_activities.py`、`workflow/start.py`；测试新增`test_video_selection.py`、`test_video_selection_activity.py`，必要时调整`test_inspection_start.py`。不动其他文件；发现必要调用方再向主Agent说明。
接口由design固定。新增`VideoSceneSelector(client, max_image_bytes=...)`的异步`classify(frames)`，帧参数使用原PNG Path及索引，明确原尺寸/受控预览；输出VideoSceneBatch。Activity层注入`scene_selector=None`，单独客户端构建，不借用或修改ImageReviewer。
确定性helper负责候选池和最终selection，按场景/质量/事件/时间选择；现场验证返回实际frame_ids，不能只断言常量。辅助Workflow模块承接场景调用和持久批次，VideoActivities仅接线，避免再扩展大类。

- [ ] 写失败测试：后段/不同事件/周期审计得到名额；低质单帧缺陷仍保留；预算0/1/32；unknown不当outside；force不延后；非法/重复/漏索引拒绝。
- [ ] 实施纯函数、结构化API边界（实际请求带Schema，预览不传指令/任意路径），测试捕获请求。
- [ ] 场景批次checkpoint/call、墙钟超时和失败unknown回退；测试结果保存后重进不再调用、错误保存、取消/DB异常传播。
- [ ] 新pin接线、旧无pin/已存evidence保持原样；prepare只返回review_frames；全部事件证据保存，详细验证仍使用原PNG。
- [ ] 运行相关CPU测试/Ruff，保存`backend-report.md`含RED/GREEN。主Agent统一完整回归。

## Task 2：页面与真实验证（主Agent）

前端`types.ts`、`validators.ts`、`VideoTimeline.tsx`及相关测试：以design契约显示selection，兼容旧载荷；先失败后实现。现有验收辅助`video_cost_live.py`及单测添加额外长视频准入；live components注入可记录的scene client，保存筛选详情与统计，不扩大付费上限。
旧T15-D证据有限真实筛选对照；新完整751秒真实视频运行，不重跑旧全部检测/VLM。主Agent记录证据和耗时，处理期间可继续独立收尾工作。

## 最终门

- [ ] 完整后端/Ruff、前端测试/build、真实PG/Temporal视频测试、新selection恢复测试；一次独立范围审查。
- [ ] 证据结果落盘、必要浏览器检查；空闲时重载应用、任务/总体计划/Session35、jj检查点。不推送。

预检：Task1生产/单测与Task2前端/harness不共享写文件；共享selection字段由design固定，Task2客户端需等待Task1接口可用。保持候选不删除与旧决定函数不变一致；场景失败是排序回退，不是已完成详细复检。
