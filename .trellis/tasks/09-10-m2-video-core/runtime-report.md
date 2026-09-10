# T07 交接：视频初查与复核增益评估

2026-09-10；主 Agent 负责集成与 jj；稳定 change `mzwqotpq`，父检查点 `63e00934`。Trellis 为唯一任务记录，未推送 GitHub。

## 已实现与验收边界

- `vision/video_decode.py` 使用现有 FFmpeg 4.4.2 单次顺序解码，以整数 PTS × time_base 保留时间，不按 FPS 猜算；临时 PNG 按批次清理，错误与无完整帧超时显式退出。
- `contracts/video.py`、`vision/video_events.py` 保存可序列化聚合状态；同类框 IoU/时间间隔关联，保留单帧事件，跨块重放不重复计数。每事件最多 3 张证据，另按 10 秒窗口选择审计帧；原始像素坐标不改写。
- `vision/video_scan.py`、`scripts/video_scan.py` 复用 DetectorAdapter 的批量或单图接口；保存逐帧 JSONL、候选/覆盖 manifest，第二次仅解码提取少量原帧，不重复检测。检测/解码中断保留已完成批次；候选为空也只能 `pending`。
- `evaluation/review.py`、`scripts/review_eval.py` 已实现同图配对指标和有界显式运行入口，复用线上质量门控、条件 VLM 和决策。原始框、保留待审候选、自动确认分别评分，不把弃权/转人工造成的精确率变化当作实际纠错。

T07 是初查核心，不是最终视频闭环：网页/对话视频执行、Temporal 分块检查点/恢复/取消属于 T08；视频语义复核及人工事件审核随后接入。CLI 不支持断点续跑，源文件需保持不变。事件表示连续可见片段，不是物理缺陷身份识别。

## 本轮验证

- 最终全后端：`SEWER_RUN_STORAGE_TEST=1 SEWER_RUN_IMAGE_WORKFLOW_TEST=1 SEWER_RUN_CONVERSATION_TEST=1 SEWER_RUN_VIDEO_GPU_TEST=1 .venv/bin/pytest -q backend/tests`，`460 passed, 6 skipped`，62.75 秒。包含真实本机 PG/Temporal 与视频 GPU；跳过未开启的付费 API、独立单图 GPU 和共享服务重启用例。
- 单独真实视频 GPU 冒烟：`SEWER_RUN_VIDEO_GPU_TEST=1 .venv/bin/pytest -q -s backend/tests/integration/test_video_gpu.py`，`1 passed`。已知 CCTV 图片合成 4 帧，现有权重逐帧检出 AJ，聚为 1 个连续事件，提取 3 张原帧；只证明解码→GPU→聚合→证据集成，不是视频准确率或吞吐验收。
- 新视频单元/CLI 测试覆盖 CFR/VFR/大起始 PTS、短暂一帧异常、跨块重放、质量证据、失败部分覆盖、清理及禁止覆盖旧输出。配对评估使用手算框和替身验证，不调用真实 API。
- Ruff 与两个 CLI `--help` 通过；前端本轮无变更。本轮未新增依赖、权重、API Key，也未新增付费请求。
- 独立 Sol 审查完成，所列问题均已修复并经主 Agent 最终回归：损坏视频先交付尾批已完整解码帧再报错；不完整固定集只输出覆盖并非零退出，不输出子集指标；评估续跑检查影响结果的 VLM 参数、质量版本和强证据决策阈值。均有先失败后通过的回归证据。无剩余必修项。

## 准确率增益：尚不能下结论

只找到 Sewer2403 原分卷数据包，含 19,049 对图片/XML；历史派生 train/val/test 目录和 `split_manifest.csv` 均缺失。已有 P=0.636/R=0.592/mAP50=0.582 是历史 validation、不同阈值的结果，不能冒充本轮配对基线或独立 test。

五类映射后的空标签包含其他原始缺陷，不能当真正正常；也未找到真实 CCTV 视频。已向用户索要原划分清单或明确未参与训练的带标注图片。尚未选定/运行 48 张试验，不重新随机分割并称其为留出集。

收到可核验数据后，按标注而非模型输出固定小样本，使用相同图片、conf=0.25/imgsz=960、匹配 IoU=0.5，对照五类 P/R/F1、FP/FN、待审与失败数；Qwen 继续用现有配置。小样本仅能提供方向性证据。最小 manifest/命令见 [evaluation-report.md](evaluation-report.md)。

## 恢复入口与下一步

1. 读总体开发计划、本文件和评估说明；T01–T07 工程验证已完成，不重做。当前任务保留 `in_progress` 仅追踪真实增益试验等待数据；后续开发可新建 T08 活动任务，保留该等待事项。
2. 下一开发切片 T08：把视频核心接入现有 Temporal 与对话分派，设计已提交块/聚合状态的幂等落库、阶段进度、取消和恢复；不另建编排器。需明确恢复解码位置与证据资产持久化，不能把本地 CLI 的可序列化 reducer 当作已完成长任务恢复。
3. 收到用户数据后优先跑小规模真实配对试验，再如实报告变好/变差/无法定论；视频效果验收另需真实视频。
4. 本机 PostgreSQL/Temporal 沿用既有服务；API/CPU/GPU/Web 按 backend/README.md 显式启动，仅绑定本机。无需再问 Key/Base URL，不输出 `.env` 内容。
