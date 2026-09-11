# T10 技术边界与接口

顺序：detect → verify → select_open_scan → open_scan（条件式、GPU）→ finish/fusion → 原有人工审核。缺省不配置；仅带 open_scan 配置的新运行新增 Activity。补充复检子任务仍只做原有 VLM 复检。

图片优先显式请求，其次质量足够且无框，或 VLM 有 additional_anomalies / need_more_context。视频仅使用已保存证据帧：显式请求每事件一张、周期负结果审计帧、VLM 疑似漏报帧；每事件最多一次，默认最多 8 帧，受总工具预算约束。保存 open_plan；未覆盖/失败阻断完整负审计结论，不修改原确认缺陷。

## 固定接口与写入隔离

- `contracts/open_scan.py`: `OPEN_SCAN_VERSION='open-scan-v1'`, `OPEN_VOCABULARY_VERSION='sewer-open-v1'`; `OpenCandidate(label: DetectionLabel|Literal['OTHER'], name: NonEmptyStr, score: Probability, bbox_xyxy: tuple[FiniteFloat,...4])`; `OpenDetectionBatch(detector_id, model_version, vocabulary_version, width, height, candidates: tuple[OpenCandidate,...])`，原图坐标校验；`OpenScanEvidence(batch, asset: AssetRef, frame_index: int|None=None, timestamp_seconds: float|None=None)`。
- `agent/open_scan.py`: `merge_open_candidates(decision: InspectionDecision, *, task_id: UUID, scans: Sequence[OpenScanEvidence], incomplete: bool=False) -> InspectionDecision`。同源同帧同类 IoU≥0.5 的非 rejected finding 去重；其余候选 uuid5 稳定 ID、source=open_detector、needs_review，绝不删改旧 finding。incomplete 使 negative_audit_complete=False / needs_review；保留原 confirmed 与覆盖失败。
- `vision/open_detector.py`: Protocol `OpenDetectorAdapter` (`detector_id`, `model_version`, `vocabulary_version`, `predict_image(path: Path)->OpenDetectionBatch`, `unload()`); `YOLOEOpenDetector(checkpoint: Path, text_encoder_path: Path, *, device='0', image_size=640, confidence=0.25)`，构造无 I/O，lazy load。缺陷专用固定英文词表映射五类与根侵入/渗入 OTHER，不含普通物体。编码器显式本地路径，缺包/文件则失败，不隐式下载。CPU 编码，GPU 推理，返回 CPU 数据。
- 模型代理仅写适配器/其测试；规则代理仅写上述契约、融合/其测试；根代理负责 Settings、运行固定、工具可用性、Activities、工作流、finish、worker 注册及集成。

复用 run_steps / ToolCall，无新增存储系统。有限模型重试，成功结果复用，崩溃窗口仍是 at-least-once。GPU 并发为 1，调用前卸载主模型、后卸载开放模型。真实模型仅验证运行/坐标/资源，准确率对比留 T11。
