# 执行与文件分工

主Agent控制Trellis/jj、整合、用户进度、相关集成与一次审查。先RED再最小实现，无新依赖/模型/付费调用，禁止nested delegation。

1. 后端Agent：contracts/intake.py及必要新frame contracts；storage/video.py、storage/conversation.py、storage/inspection.py、storage/geometry.py及必要frame helper；workflow/start.py、enhancement/geometry_config.py、enhancement/geometry/supplement_activities.py及必要baseline helper。自有新unit/storage集成测试，不改主Agent/前端文件。先给出`admit_frame_tool(parent_task_id,submission,*,enhancement=None,geometry=None)`接口；保持旧FRAME拒绝默认，冻结单帧source/pins和原子额度。相关PG验证使用独立测试schema。报告backend-report.md。
2. 前端Agent：frontend/src的App、VideoTimeline、types/validators及相关测试；仅新增选帧绑定/来源提示，不新建panel。公共shape依design固定；精确帧纳入retry identity，附件/会话/任务切换清理，FRAME不调/video。RED/GREEN及typecheck（tsc --noEmit，勿生成额外tsbuildinfo）；报告frontend-report.md。
3. 主Agent：agent/conversation.py、agent/intent.py、reports.py与各自新测试；显式所选帧路由starter，不放宽全视频/auto/任意derived；已准入重试优先固定child。补真实PG/Temporal选帧工具闭环和必要浏览器断言，完整回归＋一次独立Spec/Quality审查。修改初始化只添加新表，不重启PG/Temporal；生产构建重载等所有活动安全结束后统一做。

预期证据：来源/预算/幂等拒绝与成功、frame-local baseline和零原检测命令、工具缓存恢复、报告/UI单帧边界。真实模型已T12/T13验收，不反复付费证明相同模型；本任务检验新工程入口。

实施时确认的必要连接点：后端Agent可在`workflow/activities.py`的decision→final之间，仅对已验证frame_source补齐新增增强建议的帧定位。否则原融合生成的新EvidenceRef缺时间；不改原图或融合判断逻辑。
