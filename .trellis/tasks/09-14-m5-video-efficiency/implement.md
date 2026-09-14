# T15 视频扫描提速实施

Trellis为唯一任务/进度账本，沿用已批准总体计划；使用Superpowers的TDD、委派和一次独立审查技巧，jj覆盖Git提交/worktree指令，不创建第二份账本。
规范：本任务`prd.md`/`design.md`、总体计划第5节全覆盖与恢复协议、`.trellis/spec/backend/conventions.md`。

## 全局约束

- 不改图片VLM、融合、主检测器参数、视频PTS/覆盖、事件与恢复策略；不恢复开放补检。
- 不新增依赖、API、配置、工作流命令或付费请求；持久证据仍为PNG。
- 主Agent负责真实GPU前后实验、文档、服务操作及jj；实现者不操作服务/GPU实验/版本控制，不创建子代理。

## Task 1：临时BMP与PNG证据分离

只修改以下五文件及本目录`implementer-report.md`：

1. `backend/src/pipe_inspector/vision/video_decode.py`
2. `backend/src/pipe_inspector/vision/video_scan.py`
3. `backend/tests/test_video_decode.py`
4. `backend/tests/test_video_scan.py`
5. `backend/tests/business_restart_worker.py`

先补测试并运行观察预期失败，再实施：`decode_video_chunks(..., frame_format="PNG")`仅接受PNG/BMP，未知格式在临时文件创建前拒绝；PNG保持`compress_level=1`，BMP不压缩，后缀与编码一致。初查`scan_video`显式BMP，提取证据保持默认PNG。测试替身识别`frame-<index>.png`/`.bmp`。
测试使用实际文件验证相同RGB像素、PTS、默认PNG与BMP、未知格式不留下目录；验证扫描临时BMP/最终PNG和既有完整覆盖、恢复、清理测试。只跑相关CPU测试；主Agent统一跑完整回归。

## 协调与验证

- 主Agent在任何生产代码改动前启动旧代码真实视频扫描，记录旧代码已加载，然后实现者可开始。
- 相同源视频、checkpoint、device、imgsz=960、conf=0.25、chunk=8，各一次旧/新扫描；现有`scripts.video_scan.run_scan`保存观察和证据，不另建benchmark框架。
- 对照每帧index/PTS/质量/检测、事件（忽略随机ID），记录耗时和限制；0付费VLM。
- 一次独立范围审查、完整后端/Ruff、现有非破坏性视频工作流恢复测试。必要时仅重载空闲应用，不重启Temporal/PG。
- 总体计划写明剩余四组，保存本轮报告/Session34，完成任务并建立jj检查点；不推送。

预检：单实现任务的格式、扩展名、扫描/证据消费者契约一致；主Agent只写文档/实验输出，与实现者五文件无重叠。原PNG默认测试仍适用，不要求改写历史资产。
