# T15：视频临时帧提速

2026-09-14；jj `syylpnmz`；基线 `67630d8d`。范围是无损初查性能切片，非有效片段筛选或首版全项验收。

## 定位与改动

同一真实视频前32帧的解码＋质量分析约12.02秒，其中PNG保存累计7.27秒（编码7.14秒），质量分析3.21秒；FFmpeg原始帧读取约0.42秒。计时包含不同层级，不能将包含关系的数值直接求和。
四帧RGB内存探测（均值/帧，包含全部像素回读比对，不使用哈希）：

| 编码 | 保存秒 | 读取秒 | 文件MB | RGB一致 |
|---|---:|---:|---:|---|
| PNG，压缩1 | 0.2163 | 0.0644 | 2.510 | 是 |
| PNG，压缩0 | 0.0912 | 0.0349 | 6.224 | 是 |
| BMP | 0.0044 | 0.0059 | 6.221 | 是 |

因此仅让初查的受控临时块使用BMP，默认解码和持久证据继续PNG；保持像素、PTS、主检测器、质量阈值、事件/VLM/融合策略不变，不降低帧率。默认8帧1080p临时块约从20MB增至50MB，仍逐块清理，不缓存完整视频。

## 前后实跑

输入为 `var/datasets/videos-1/W90-W89_20260611151716.mp4`，完整22.401953秒、1920×1080、561帧；同一现有五类权重，GPU0、imgsz960、conf0.25、chunk8。
使用现有 `scripts.video_scan.run_scan`，各一次独立进程；旧代码加载后才开始生产代码编辑。计时包括初查、首次模型加载和PNG证据提取/写出，不包括上传、数据库/Temporal、文本解析或VLM。因此不能与T15-D端到端总时间直接作提速对比。
原始输出根目录：[2026-09-14-video-efficiency](../../../var/evaluations/2026-09-14-video-efficiency)。

| 指标 | 原PNG初查 | 新BMP初查 |
|---|---:|---:|
| 扫描＋证据生成秒 | 237.610 | 59.505 |
| 完整处理帧 | 561 | 561 |
| 候选事件 / PNG证据帧 | 2 / 5 | 2 / 5 |
| 解码 / 证据错误 | 无 / 无 | 无 / 无 |

本次约3.993倍提速、耗时减少74.96%。全部561条原始逐帧记录（检测类别、分数、框、质量和PTS）精确一致；聚合结果仅归一化两次运行各自随机生成的源资产UUID及事件ID前缀后精确一致，没有忽略分数或框差异。5张证据的元数据、PNG格式和全部RGB像素一致。
对照记录：[comparison.json](../../../var/evaluations/2026-09-14-video-efficiency/comparison.json)；旧/新原始逐帧结果：[baseline](../../../var/evaluations/2026-09-14-video-efficiency/baseline/observations.jsonl)、[optimized](../../../var/evaluations/2026-09-14-video-efficiency/optimized/observations.jsonl)；完整事件见对应`manifest.json`。
证据示例：[PL原帧](../../../var/evaluations/2026-09-14-video-efficiency/optimized/frames/000000096.png)、[FS原帧](../../../var/evaluations/2026-09-14-video-efficiency/optimized/frames/000000520.png)。两次均0付费调用，未重跑VLM，因此本轮不提供新的复检精度结论。

各方案只跑一次同源短视频，不作统计显著性或长视频吞吐保证。没有降采样/分辨率变化；当前最重要的后续仍是有效片段筛选和代表性长视频验收。

## 验证

- TDD：格式契约先失败5项，实施后相关43项通过；见[实现证据](implementer-report.md)。
- 主Agent完整后端：`1066 passed / 146 skipped`（24.73秒）；`.venv/bin/ruff check backend scripts`通过。146项是默认外部条件跳过，不冒充实际运行。
- 另启用`SEWER_RUN_VIDEO_WORKFLOW_TEST=1`运行现有`integration/test_video_workflow.py`：`5 passed`（9.32秒），真实PG/Temporal及FFmpeg、模型/API替身，验证检查点、回执丢失、worker替换与缓存；未重复付费或重启共享持久服务。
- 独立审查：规格与质量均PASS，无发现；见[审查记录](review.md)。前端和API未改，不重复构建前端。
- 重载前运行workflow为0；仅重载应用API/CPU/GPU（PID153275/153274/153273），三者ready；PG/Temporal未重启（Temporal仍PID118993）。

## 交付边界

剩余四组工作及验收门槛已写入[总体计划4.1](../../../project_management/DEVELOPMENT_PLAN.md#41-距离首版完整验收的四组收尾工作)：视频效率/有效片段、剩余工具入口、规范检索、集成收口。现有图片交互可立即人工试用。
本轮0付费API、不训练、不新增依赖、不改已接受的图片复检策略、不恢复开放补检。规范检索仍需用户指定编号/版本及PDF或官方来源。
Trellis为唯一任务账本；TDD用于验证无损和清理/恢复契约，独立审查和最终验证用于确认改动；stop-that-shit约束只处理已测得的瓶颈，不扩建框架。
