# YOLO26 + YOLOE + VLM 管网缺陷检测系统架构

> 历史研究参考：本文含 SEDD 三类等旧假设，不作为当前实施依据。请以[主方案](../VLM_YOLO_ARCHITECTURE_AND_RESEARCH_SUMMARY.md)第 9～10 节和[开发计划](../../project_management/DEVELOPMENT_PLAN.md)为准。

> 版本：2026-08-24  
> 范围：SEDD 三类缺陷 `crack / root / deposit`，面向在线检测、VLM 复核、少样本增强、精细分割、尺寸量化和自动报告。  
> 论文与代码综述见：[YOLO + VLM 管网调研总览](./summary_yolo_vlm_pipe_inspection.md)。

## 1. 架构结论

本项目不应把 YOLOE 或 VLM 当作 YOLO26 的替代品，而应构建一个**双检测器、双速度环、事件驱动复核**系统：

- **YOLO26** 是固定类别主检测器，承担每帧高频检测；
- **YOLOE** 是按需运行的第二候选源，扫描完整帧或高分辨率切片，补充 YOLO26 可能完全漏掉的候选；
- **ByteTrack/BoT-SORT** 将逐帧检测变成轨迹和缺陷事件，负责时序稳定与去重；
- **VLM** 对已经存在的候选做语义复核、易混类比较、证据描述和拒识，不承担毫米级量测，也不应作为每帧检测器；
- **分割与几何模块**产生精细边界和真实尺寸；
- **LLM**只根据结构化事实、标准条款和证据生成报告；
- **状态机**拥有最终业务决定权，任何 VLM 输出都不能绕过校验直接报警或控制机器人。

最关键的数据流关系是：

```text
YOLOE 与 YOLO26 在“候选生成层”逻辑并列；
YOLOE 是否运行，由后面的事件路由器反馈控制；
YOLOE 产生的新候选必须重新进入候选融合、跟踪和 VLM 复核流程。
```

## 2. 当前项目基线与架构目标

### 2.1 已有基线

当前本地实验归档位于 [`serverData/yolov26`](../serverData/yolov26/)，任务为三类目标检测：

```text
0 crack
1 root
2 deposit
```

当前最优配置为：

```text
YOLO26s + imgsz=960
```

验证集结果：

| 指标 | 数值 |
|---|---:|
| Precision | 0.751 |
| Recall | 0.715 |
| mAP50 | 0.745 |
| mAP50-95 | 0.414 |

逐类主要问题：

| 类别 | 当前表现特征 | 架构需要补什么 |
|---|---|---|
| crack | Recall 约 0.717，严格框指标仍受边界影响 | 多帧证据、精细分割、标注一致性 |
| root | Precision 约 0.698，误报相对明显 | VLM 易混类与难负例复核 |
| deposit | Precision 约 0.827、Recall 约 0.685 | YOLOE 补候选、少样本增强、召回优先路由 |

这些是验证集结果，不等于最终 test、脏环境或生产性能。相关证据见 [`EXPERIMENT_SUMMARY.md`](../serverData/yolov26/EXPERIMENT_SUMMARY.md)。

### 2.2 新系统目标

新架构需要同时回答六个问题：

1. 如何保留 YOLO26 的速度和已有能力；
2. 如何减少 `root` 误报和类别混淆；
3. 如何找回 `deposit` 等类别的完全漏检；
4. 如何从逐帧框得到稳定、唯一的缺陷事件；
5. 如何得到像素级边界、真实尺寸和严重度；
6. 如何生成可追溯、不可随意编造的检测报告。

## 3. 完整在线架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│ 1. 采集层                                                           │
│ CCTV / 机器人相机 / 云台 / 双目或 RGB-D / LiDAR / 里程计与 SLAM      │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. 帧接入与质量控制                                                   │
│ 时间同步 → 质量评分 → 原图保存 → 选择性去雾/去模糊/超分 → 环形帧缓存 │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
                 ┌───────────────────────────┐
                 │ 3A. YOLO26 高频主检测器   │
                 │ 三个固定类别、每帧运行     │
                 └─────────────┬─────────────┘
                               ▼
                 ┌───────────────────────────┐
                 │ 4. 候选标准化与一级跟踪    │
                 │ NMS/坐标统一/质量标记       │
                 │ ByteTrack 或 BoT-SORT      │
                 └─────────────┬─────────────┘
                               ▼
                 ┌───────────────────────────┐
                 │ 5. 轨迹与事件聚合          │
                 │ track_id / 去重 / 最佳帧    │
                 │ 多帧投票 / 里程与钟点位置   │
                 └─────────────┬─────────────┘
                               ▼
                 ┌───────────────────────────┐
                 │ 6. 不确定性事件路由器      │
                 └──────┬────────┬───────────┘
                        │        │
           已有框但不确定│        │疑似完全漏检/周期扫描
                        ▼        ▼
          ┌────────────────┐  ┌────────────────────────┐
          │ 7A. VLM ROI复核 │  │ 3B. YOLOE 第二检测器   │
          │ ROI+上下文+多帧 │  │ 原图/整帧/切片+提示     │
          │ 定义+原型+负例  │  │ 输出新框/类别/掩膜      │
          └───────┬────────┘  └──────────┬─────────────┘
                  │                      │
                  │                      └── 新候选回流到第4层
                  │
                  ├──────── 证据不足 ───────> 主动选帧/变焦/重拍/人工
                  ▼
          ┌──────────────────────────────────────────────────────────┐
          │ 8. 多源融合、校准、拒识和状态机                           │
          │ YOLO26 + YOLOE + VLM + 时序 + 图像质量 + 几何一致性       │
          └───────────────────────────┬──────────────────────────────┘
                                      ▼
          ┌──────────────────────────────────────────────────────────┐
          │ 9. 精细分割                                               │
          │ YOLO26-Seg / SAM / Grounded SAM2                          │
          └───────────────────────────┬──────────────────────────────┘
                                      ▼
          ┌──────────────────────────────────────────────────────────┐
          │ 10. 几何量化                                              │
          │ 标定深度/双目/LiDAR/已知管径 → 长宽面积、堵塞率、变形率   │
          └───────────────────────────┬──────────────────────────────┘
                                      ▼
          ┌──────────────────────────────────────────────────────────┐
          │ 11. 结构化事实与证据库                                    │
          │ JSON Schema + 原图/ROI/掩膜/深度/轨迹/模型版本/人工记录    │
          └───────────────────────────┬──────────────────────────────┘
                                      ▼
          ┌──────────────────────────────────────────────────────────┐
          │ 12. LLM 报告与审核                                       │
          │ 标准检索 → 报告草稿 → 规则校验 → 人工审核 → PDF/Word/GIS  │
          └──────────────────────────────────────────────────────────┘
```

### 3.1 两个速度环

```text
快速环：帧接入 → YOLO26 → 跟踪 → 事件更新
慢速环：路由器 → YOLOE/VLM/分割/量测/报告
```

快速环不能等待 VLM。慢速环出现超时、显存不足或服务不可用时，YOLO26 检测与视频记录仍应继续运行。

### 3.2 YOLOE 的准确位置

YOLOE 在模型职责上与 YOLO26 并列，都是候选框生成器；在调度上由事件路由器按需触发。

```text
图像/高分辨率切片
       ├──> YOLO26：高频固定类别检测
       └──> YOLOE：低频开放词表/视觉提示检测

两者输出
       └──> 统一候选池 → 跟踪/关联 → VLM复核 → 状态机
```

不能把 YOLOE 只接到 YOLO26 的 ROI 后面，否则 YOLO26 没有框时，YOLOE 也拿不到 ROI，无法补回完全漏检。

## 4. 各模块设计

### 4.1 采集、同步与帧缓存

每一帧应形成统一 `FramePacket`：

```json
{
  "camera_id": "cctv_01",
  "frame_id": 18425,
  "timestamp_ms": 1787529600123,
  "image_uri": "...",
  "pipe_id": "P-001",
  "chainage_m": 12.40,
  "camera_pose": null,
  "depth_uri": null,
  "quality": {
    "blur": 0.21,
    "brightness": 0.44,
    "glare": 0.17,
    "occlusion": 0.08
  }
}
```

建议保留最近 3–10 秒的环形帧缓存。路由器触发后，YOLOE 和 VLM 从缓存读取原图、增强图和邻帧，不阻塞相机主线程。

### 4.2 质量评估与选择性增强

只在明确退化时触发去雾、去模糊和超分辨率，原因是增强网络也可能制造纹理，导致裂缝误报。

处理规则：

- 原图永远保留，不能被增强图覆盖；
- YOLO26 默认先在经过标准 resize/normalize 的原图上运行；
- 质量低于阈值时，同时生成增强版本作为第二证据；
- 如果原图与增强图检测冲突，进入 VLM/人工复核，而不是自动相信增强图；
- 报告证据应优先显示原图，并标注增强版本仅用于辅助识别。

可借鉴 PipeInspector 的去雾、去模糊、超分辨率链，但采用门控触发。

### 4.3 YOLO26 主检测器

职责：

- 每帧检测 `crack / root / deposit`；
- 输出框、类别、置信度和推理版本；
- 为 tracker 提供连续候选；
- 在服务降级时独立完成最基本的告警。

标准输出：

```json
{
  "source": "yolo26",
  "model_version": "yolo26s_img960_e100_best",
  "frame_id": 18425,
  "bbox_xyxy": [312.0, 148.0, 487.0, 296.0],
  "class_id": 2,
  "label": "deposit",
  "score": 0.38
}
```

部署时不要只有一个全局阈值，应从验证集研究逐类阈值：`deposit` 可偏召回，`root` 可偏精度。但阈值只负责候选分层，不能替代模型改进。

### 4.4 跟踪与缺陷事件聚合

ByteTrack/BoT-SORT 不改变单帧 YOLO mAP，主要改善视频级稳定性：

- 为同一缺陷分配 `track_id`；
- 恢复短暂低置信度或一两帧漏检；
- 合并重复报警；
- 为 VLM 选取最清晰、最近和分数最高的 1–3 帧；
- 支持轨迹级类别投票和置信度聚合。

管道场景是“相机运动、缺陷固定”，普通 IoU ByteTrack 可能因为框位移和尺度变化而断轨。选型建议：

| 阶段 | 建议 |
|---|---|
| MVP | ByteTrack，快速验证事件聚合 |
| 正式系统 | BoT-SORT 或 ByteTrack + 全局运动补偿 |
| 有机器人位姿 | 加入里程计/SLAM、管道里程和钟点方位关联 |
| 高精度去重 | 图像特征、光流、几何位置和轨迹共同匹配 |

真正的 `defect_event_id` 不应只等于 `track_id`。同一物理缺陷可能因断轨产生多个 track，需要结合里程、钟点位置和视觉特征二次合并。

### 4.5 不确定性与事件路由器

路由器不是大模型，而是确定性规则加可选的小型校准器。它决定是否调用慢模型。

主要触发条件：

1. YOLO26 分数位于“保留但不接受”的中间区间；
2. 类别是 `deposit` 等低召回类；
3. 类别是 `root` 且需要控制误报；
4. 同一轨迹跨帧类别或置信度波动过大；
5. 轨迹中断后又重新出现；
6. 原图与增强图结果冲突；
7. 深度、点云或图像异常分数明显，但 YOLO26 没有框；
8. 每隔固定帧数或固定里程执行一次少样本类扫描；
9. VLM 返回 `need_more_context`；
10. 用户或人工审核员主动请求重新分析。

建议使用三个候选区间，具体阈值必须在验证集上标定：

```text
score >= τ_accept        稳定轨迹可直接进入确认候选
τ_review <= score < τ_accept  送 VLM 复核
τ_keep <= score < τ_review    只在有时序/YOLOE/几何证据时保留
score < τ_keep           默认丢弃，但不影响周期性 YOLOE 扫描
```

路由器还要实施背压：限制每秒 VLM/YOLOE 任务数，相同 `track_id` 的重复任务合并，只保留最新或最佳证据。

### 4.6 YOLOE 第二候选源

YOLOE 的核心任务是**提高候选召回**，不是生成解释。

输入模式：

- 完整原图：找回 YOLO26 完全没有提出框的缺陷；
- 高分辨率切片：用于细裂缝、小面积沉积；
- 大上下文 crop：围绕轨迹中断或深度异常区域；
- 文本提示：缺陷类别名和形态描述；
- 视觉提示：专家确认的参考框或掩膜。

首轮不需要训练 YOLOE，应先验证：

```text
YOLOE 新增的候选中，有多少是真正的 YOLO26 false negative？
```

YOLOE 输出统一转换为与 YOLO26 相同的 `Detection` Schema。随后：

1. 与 YOLO26 候选做 IoU/类别/视觉特征匹配；
2. 匹配已有轨迹则补充证据；
3. 未匹配则建立 `tentative_track`；
4. 新轨迹需要连续帧、VLM、几何或人工证据之一才能确认；
5. YOLOE 单帧输出不能直接成为正式缺陷事件。

### 4.7 VLM 语义复核器

VLM 的核心任务是**提高候选的类别判断和误报控制能力**。

输入证据包：

```text
整帧原图
+ YOLO/YOLOE ROI
+ 向外扩展的 context crop
+ 同一轨迹最佳 1–3 帧
+ 原图与可选增强图
+ YOLO26/YOLOE 初始类别和分数
+ 管材、管径、里程、钟点位置
+ 三类缺陷专家定义
+ 正例视觉原型
+ 接缝、反光、水渍、污物等难负例
```

VLM 只输出受限 JSON：

```json
{
  "review_job_id": "R-001",
  "track_id": 37,
  "is_defect": true,
  "canonical_label": "deposit",
  "evidence": [
    "沉积区域位于管底",
    "边缘不规则且跨相邻帧保持稳定"
  ],
  "confuser": "water_reflection",
  "confidence_raw": 0.78,
  "abstain": false,
  "need_more_context": false,
  "need_reimage": false
}
```

边界：

- 不让 VLM 自由输出任意新类别；
- 不采用 VLM 给出的自由文本坐标作为最终框；
- 不用模型自报置信度直接做最终阈值，必须在验证集校准；
- 不让 VLM 根据单目图像猜毫米尺寸；
- JSON 解析失败只重试一次，仍失败则进入人工或使用 YOLO-only 结果。

推荐第一版冻结 Qwen3-VL，只做 prompt 与视觉原型实验；积累足够的人工审核样本后，再考虑 LoRA/QLoRA。

### 4.8 多源融合与校准

第一版应采用可解释规则或逻辑回归，而不是直接训练复杂融合网络。

可用特征：

- YOLO26 类别分数；
- YOLOE 类别分数与候选来源；
- VLM 复核标签和经校准分数；
- 轨迹长度、稳定性和多帧一致率；
- 图像质量；
- 正例文本/视觉原型相似度；
- 难负例相似度；
- 深度/几何异常一致性。

一种研究表达为：

```text
S(c) = α · s_yolo26(c)
     + β · s_yoloe(c)
     + γ · s_vlm_calibrated(c)
     + η · temporal_consistency
     + μ · geometry_consistency
     - δ · hard_negative_similarity
```

参数只在 validation 上拟合。测试集不得用于调阈值、prompt 或权重。

### 4.9 业务状态机

建议状态：

```text
CANDIDATE
  ├──> TRACKING
  ├──> REVIEW_PENDING
  └──> REJECTED

TRACKING / REVIEW_PENDING
  ├──> CONFIRMED
  ├──> NEEDS_REIMAGE
  ├──> HUMAN_REVIEW
  └──> REJECTED

CONFIRMED
  └──> SEGMENTED
       └──> QUANTIFIED
            └──> REPORTED
```

最终状态由确定性规则控制：

- 高置信、稳定多帧且模型一致，可自动 `CONFIRMED`；
- YOLO26 与 VLM 冲突，进入 `HUMAN_REVIEW`；
- 新 YOLOE 候选没有第二证据，保持 `CANDIDATE`；
- 证据不足且可控制相机，进入 `NEEDS_REIMAGE`；
- 报告只能读取 `CONFIRMED/QUANTIFIED` 事件。

### 4.10 主动选帧、变焦与重拍

如果只有离线 CCTV：

- 在同一 track 前后帧中选择最清晰、面积最大、遮挡最少的帧；
- 必要时对局部高分辨率重新解码，而不是使用低清预览帧。

如果机器人可控：

- VLM/路由器输出 `need_reimage`；
- 规划器根据安全约束控制减速、靠近、云台和照明；
- 重新拍摄后返回候选生成与复核链；
- VLM 只能提出“需要更多证据”，不能直接绕过运动安全控制。

这一路线可借鉴 VISION 与 PipeEye。

### 4.11 精细分割

分割模型只对最终候选或待量测候选运行：

| 条件 | 推荐方案 |
|---|---|
| 有足够 mask 标注 | YOLO26-Seg，适合部署 |
| 当前只有 bbox | SAM/Grounded SAM2 预标，人工修正后训练领域分割器 |
| YOLOE 已输出可用 mask | 作为候选 mask，仍需质量校验 |
| 离线高质量标注 | Grounding DINO + Grounded SAM2 |

分割解决的是边界问题，不能直接产生真实尺寸。

### 4.12 几何与尺寸量化

真实尺度的可靠来源按项目条件选择：

1. 线激光/结构光；
2. 标定 RGB-D 或双目；
3. LiDAR/ToF 与相机外参标定；
4. 已知管径和圆柱模型约束；
5. 单目 metric depth 只作候选估计，并必须现场校准。

输出应同时保存数值与不确定度：

```json
{
  "method": "stereo_calibrated",
  "length_mm": 124.0,
  "max_width_mm": 5.8,
  "area_mm2": 410.3,
  "uncertainty_mm": 1.6,
  "calibration_id": "CAL-2026-08-01"
}
```

量化逻辑：

- crack：掩膜骨架三维测地长度、法向最大宽度；
- root：侵入长度、截面占比；
- deposit：沉积截面积、堵塞率和沿管道长度；
- deformation：拟合截面与设计圆形的偏差率。

### 4.13 结构化事实、标准与 LLM 报告

报告生成前先形成权威 `DefectEvent`：

```json
{
  "event_id": "E-20260824-0037",
  "pipe_id": "P-001",
  "track_ids": [37, 41],
  "canonical_label": "deposit",
  "event_confidence": 0.84,
  "chainage_m": 12.4,
  "clock_position": "6 o'clock",
  "proposal_sources": ["yolo26", "yoloe"],
  "review_status": "confirmed",
  "measurement": {},
  "evidence_uris": [],
  "model_versions": {},
  "human_review": null
}
```

报告链：

```text
DefectEvent
  -> 检索适用标准与等级规则
  -> 规则引擎计算可确定的严重度
  -> LLM 生成文字说明与维修建议草稿
  -> Schema/数值/引用校验
  -> 人工审核
  -> PDF/Word/GIS/资产系统
```

LLM 不得补写缺失尺寸；没有量测时必须写“未量测”或“待复核”。

## 5. 关键数据接口

### 5.1 核心对象

| 对象 | 生产者 | 消费者 | 关键字段 |
|---|---|---|---|
| `FramePacket` | 采集层 | YOLO26、帧缓存、质量模块 | `camera_id/frame_id/timestamp/image_uri/pose/quality` |
| `Detection` | YOLO26、YOLOE | 融合器、tracker | `source/bbox/label/score/model_version` |
| `Track` | tracker | 路由器、事件聚合 | `track_id/detections/best_frames/stability` |
| `ReviewJob` | 路由器 | VLM 服务 | `track_id/roi/context/frames/prompts/metadata` |
| `ReviewResult` | VLM | 校准器、状态机 | `label/evidence/confuser/abstain/need_reimage` |
| `MaskResult` | 分割器 | 几何模块 | `mask_uri/area_px/quality_score` |
| `Measurement` | 几何模块 | 状态机、报告 | `method/value/unit/uncertainty/calibration_id` |
| `DefectEvent` | 状态机 | 报告、GIS、人工审核 | `event_id/status/location/evidence/versions` |

### 5.2 幂等与追踪

- `frame_id + model_version` 唯一确定一次模型推理；
- `review_job_id` 防止相同轨迹重复调用 VLM；
- 所有派生结果保存上游对象 id；
- prompt、原型库、阈值、校准器和标准规则均需要版本号；
- 人工修订不能覆盖原输出，应追加审核记录。

## 6. 部署架构

### 6.1 单机器人或单路 CCTV 的 MVP

```text
边缘设备
  相机接入
  质量评分
  YOLO26 TensorRT
  ByteTrack
  环形帧缓存
  本地事件路由器
        │
        ▼
工作站/服务器
  YOLOE 服务
  Qwen3-VL 服务
  SAM/分割服务
  几何量测
  SQLite/PostgreSQL + 文件存储
  报告服务与审核界面
```

第一版不必直接引入 Kafka。单路系统可以使用有界内存队列或 Redis Streams，配合 SQLite/PostgreSQL 保存任务状态。

### 6.2 多机器人或多路视频

```text
多个采集节点
  -> 视频/帧消息总线
  -> GPU 推理池（YOLO26）
  -> 轨迹与事件服务
  -> 优先级任务队列
  -> YOLOE/VLM/SAM 推理池
  -> 事件数据库与对象存储
  -> 报告/GIS/监控平台
```

如硬件长期绑定 NVIDIA，可评估 DeepStream 管理多路视频、推理和跟踪；VSS 适合更大规模的视频检索和语义理解，但不应在尚未验证单路算法收益前成为第一阶段依赖。

### 6.3 资源优先级

从高到低：

1. 视频采集与原始证据落盘；
2. YOLO26 快速环；
3. 跟踪和事件状态；
4. 高风险/高不确定事件的 VLM；
5. YOLOE 周期扫描；
6. 分割和量测；
7. 非实时报告生成。

资源紧张时应降低慢速任务频率，不能降低快速环的稳定性。

## 7. 离线训练与数据闭环

```text
真实视频与人工标注
        │
        ├──> YOLO26 检测训练
        │
        ├──> 正例/难负例原型库
        │
        ├──> VLM 复核评测集
        │
        └──> 分割与量测标注子集
                 │
在线系统错误回收 │
FP/FN/冲突/拒识  │
        └────────┴──> 人工审核后的困难样本库
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
       YOLO26 再训练    VLM LoRA/校准     Prompt/原型更新
              │               │                │
              └───────────────┴────────────────┘
                              ▼
                       固定测试集回归评估
```

各模型的训练策略：

| 模块 | 第一阶段 | 数据足够后的阶段 |
|---|---|---|
| YOLO26 | 保留当前 best baseline | 清洗标注、少数类增强、蒸馏后再训练 |
| YOLOE | 不训练，先测文本/视觉提示 | 只有证明候选互补后才领域微调 |
| VLM | 冻结模型，优化 prompt/原型 | 用人工复核数据做 LoRA/QLoRA |
| 融合器 | 规则或逻辑回归 | 小型 MLP/校准器，保持可解释 |
| 分割器 | SAM 预标 + 人工修正 | 训练 YOLO26-Seg 或领域分割器 |

少样本 `deposit` 可增加 MIRAGE 式合成数据实验，但合成图只能进入训练集，必须用真实验证/测试集判断收益。

## 8. 模块作用与指标边界

| 模块 | 主要可能改善 | 不能宣称的效果 |
|---|---|---|
| YOLO26 | 固定类单帧检测与速度 | 通用开放词表能力 |
| ByteTrack/BoT-SORT | 事件稳定、短时漏检、去重、多帧证据 | 单帧 mAP 提升 |
| YOLOE | 候选 recall、完全漏检审计、少样本参考检测 | 自动保证最终 precision |
| VLM | 候选分类、误报控制、易混类解释、拒识 | 没有候选时自动恢复全部漏检；精确几何 |
| 分割器 | 边界、mask IoU、量测基础 | 真实尺度 |
| 深度/三维 | 尺寸、位置、堵塞率、变形率 | 类别语义解释 |
| LLM 报告 | 报告效率、格式一致性、知识检索 | 改善检测 mAP |

## 9. 实验与消融设计

### 9.1 固定基线

```text
E0：YOLO26s-960 best.pt
```

报告 image-level 检测指标和当前三类逐类结果。

### 9.2 增量实验

| 实验 | 新增模块 | 要验证的问题 |
|---|---|---|
| E1 | YOLO26 + tracker | 事件重复率、轨迹稳定性和短时漏检是否改善 |
| E2 | E1 + VLM ROI 复核 | `root` precision、易混类 macro-F1 是否改善 |
| E3 | E2 + 专家文本 | 详细缺陷定义是否优于只有类别名 |
| E4 | E3 + 正例视觉原型 | 视觉参考是否提高少样本识别 |
| E5 | E4 + 难负例 | 接缝、反光、水渍误报是否下降 |
| E6 | E5 + 多帧证据 | 轨迹级结果是否优于单帧复核 |
| E7 | E6 + YOLOE | `deposit` 和总体 candidate recall 是否提高 |
| E8 | E7 + 分割 | 框定位与 mask 质量是否改善 |
| E9 | E8 + 几何 | 尺寸 MAE 与不确定度是否满足工程需求 |
| E10 | E9 + LLM report | 报告时间、字段正确率和无依据陈述率 |

### 9.3 指标体系

检测层：

- mAP50、mAP50-95；
- per-class AP、precision、recall；
- candidate recall/AR@N；
- false proposals per image。

事件层：

- event precision/recall/macro-F1；
- 重复事件率；
- 轨迹断裂率和 ID switch；
- 从首次可见到确认的延迟。

VLM 层：

- 固定候选上的 macro-F1；
- `root/deposit/crack` confusion matrix；
- abstain rate、人工转交率；
- ECE/Brier score；
- P50/P95 latency 和触发率。

量测与报告层：

- 长度、宽度、面积、堵塞率 MAE；
- 不确定度覆盖率；
- 报告字段正确率；
- 无依据陈述率；
- 人工修改字段数和处理时间。

### 9.4 数据划分

必须按管段、站点或完整视频分组，不能把同一缺陷的相邻帧分散到训练和测试。Prompt、视觉原型、阈值和校准器只能使用 train/validation，最终 test 一次性评估。

## 10. 故障与降级策略

| 故障 | 降级行为 |
|---|---|
| VLM 超时/宕机 | 保留 YOLO26 结果为 `review_pending`，快速环继续 |
| YOLOE 不可用 | 停止补候选，记录当前召回能力降级 |
| JSON 不合法 | 固定 schema 重试一次；再次失败转人工 |
| 原图与增强图冲突 | 保存两者并进入复核，不能自动覆盖原图 |
| tracker 断轨 | 依据里程/钟点位置/特征做事件级二次合并 |
| 深度缺失 | 尺寸写 `not_measured`，禁止 LLM 猜测 |
| 模型冲突 | 保持 `HUMAN_REVIEW`，不自动生成正式等级 |
| GPU 队列拥塞 | 合并相同轨迹任务，优先高风险事件，降低 YOLOE 周期频率 |

## 11. 分阶段实施

### Phase 0：固定真实基线

- 固定 `YOLO26s + 960 + best.pt`；
- 补齐正式 test 和 dirty validation；
- 保存真实数据 split、模型版本和阈值；
- 建立 FP/FN/类别混淆错误库。

### Phase 1：YOLO26 + 事件跟踪

- 接入视频推理、环形帧缓存；
- 先使用 ByteTrack，验证轨迹稳定和去重；
- 对移动相机加入全局运动补偿；
- 形成 `Track` 和 `DefectEvent` Schema。

交付物：稳定事件列表、最佳帧、轨迹级基线。

### Phase 2：VLM 复核

- 冻结 Qwen3-VL；
- 建立三类专家文本、正例图和难负例库；
- 对同一批候选做单帧/多帧和不同 prompt 消融；
- 实现校准、拒识和人工审核。

交付物：VLM review service、结构化 JSON、`root` 误报改善证据。

### Phase 3：YOLOE 补召回

- 首先零样本运行，不训练；
- 分别测类别名、专家文本和视觉提示；
- 与低阈值 YOLO26 在相同候选预算下比较；
- 新候选回流 tracker 和 VLM。

交付物：YOLOE candidate service、`deposit` recall 消融。

### Phase 4：分割和尺寸量化

- SAM 预标并人工修正 mask 子集；
- 训练/评估领域分割器；
- 完成相机与深度传感器标定；
- 输出尺寸及不确定度。

交付物：mask、三维量测、量测误差报告。

### Phase 5：报告与平台

- 事件数据库、证据存储和审核 UI；
- 标准/规则检索；
- LLM 报告草稿、Schema 校验和人工签发；
- GIS 或管段资产档案关联。

## 12. 论文、专利与软著的统一产出

### 12.1 论文主线

建议主线：

```text
面向长尾管道缺陷的
YOLO26 快速检测 + YOLOE 互补候选
+ 文本/视觉/难负例原型 VLM 复核
+ 轨迹级不确定性路由
```

核心学术问题是：在固定测试集和受控计算预算下，这套系统能否同时改善少样本召回、易混类 precision 和事件级 macro-F1。

### 12.2 专利点

- 基于置信度、图像质量、轨迹稳定和跨模型分歧的 VLM/YOLOE 调度；
- 文本原型、正例视觉原型和难负例原型联合复核；
- YOLOE 新候选回流跟踪并通过多证据确认；
- VLM 不确定性驱动最佳邻帧选择、云台变焦或主动重拍；
- 检测、三维量测和标准规则约束报告生成。

### 12.3 软件著作权模块

- 视频接入与质量控制；
- YOLO26 实时检测；
- 缺陷跟踪与事件聚合；
- YOLOE 补充候选；
- VLM 复核与提示原型管理；
- 分割和尺寸量化；
- 事件状态机与人工审核；
- 标准化报告和 GIS/资产管理。

## 13. 最终推荐版本

近期最可行、最值得验证的系统是：

```text
YOLO26s-960 高频主检测
  + ByteTrack/BoT-SORT 轨迹和事件聚合
  + Qwen3-VL 事件级 ROI/多帧复核
  + YOLOE 对疑似漏检与 deposit 周期补扫
  + YOLO26-Seg/SAM 精细掩膜
  + 标定深度或三维几何量测
  + Schema/状态机约束的 LLM 报告
```

实施顺序必须是：

```text
固定基线
→ 跟踪与事件化
→ VLM 降误报和解混淆
→ YOLOE 补完全漏检
→ 分割与真实量测
→ 报告和平台化
```

这样每增加一个模块都能单独做消融，并能明确回答它改善的是单帧检测、候选召回、事件稳定、语义判断、几何量测还是报告效率。

## 14. VLM 是否单独进行图片复检

### 14.1 直接答案

会，但默认不是“每一帧都再调用一次 VLM”，也不是让 VLM 对整张图无条件重新检测。推荐做法是：

```text
YOLO26/YOLOE 产生候选
        ↓
tracker 将连续帧合并为同一个缺陷事件
        ↓
从事件中选择最佳 1～3 帧或 2～5 秒短片段
        ↓
对该事件单独提交一次 VLM 复检任务
        ↓
输出 accept / revise / reject / abstain / need_more_context
```

所以，VLM 的基本调用单位应当是 `DefectEvent`，而不是视频帧。对同一个事件，通常只做一次主复检；只有发生模型分歧、VLM 请求更多上下文、YOLOE 找到新候选或主动重拍后，才进行第二次复检。

### 14.2 VLM 实际看到的内容

不要只给 VLM 一个紧裁剪 ROI。一次复检应包含一个小型证据包：

- 原始全帧，用于理解管壁、接头、水面和相机姿态；
- YOLO26 或 YOLOE 的缺陷 ROI；
- 向外扩展 20%～50% 的上下文 ROI；
- 同一轨迹的最佳 1～3 帧，必要时附 2～5 秒短片段；
- 原图与选择性增强图，且必须标明哪张是增强结果；
- 检测类别、置信度、框坐标、轨迹稳定性和图像质量；
- 三类缺陷的专家定义、正例视觉原型和难负例；
- 管径、材质、里程、拍摄方向等已有元数据。

VLM 输出必须使用受约束的 JSON，而不是直接用自然语言覆盖检测结果：

```json
{
  event_id: evt_001,
  decision: accept,
  class_id: deposit,
  class_probability: 0.81,
  image_quality: medium,
  evidence_frames: [1832, 1840, 1847],
  need_more_context: false,
  reason_codes: [irregular_surface_accumulation, persistent_across_frames]
}
```

其中 `class_probability` 不能直接当成已经校准的统计概率，需要在验证集上进行温度缩放、阈值校准或单独学习融合器。

### 14.3 单图 VLM 和视频 VLM 的使用边界

| 情况 | 推荐输入 | 原因 |
|---|---|---|
| 图像清晰、框稳定、只是类别混淆 | 全帧 + ROI + 上下文 ROI | 单次图像复检成本最低 |
| 运动模糊、遮挡、反光 | 邻近 3～8 帧 | 可从邻帧找到更清楚证据 |
| 缺陷随相机移动才显现 | 2～5 秒短片段 | 需要运动和持续性信息 |
| 完全没有候选框 | YOLOE/低阈值 detector 先找候选 | 只看 ROI 的 VLM 无法恢复“无框漏检” |
| 所有帧都不可判定 | `abstain` + 人工/重拍 | 禁止强制分类 |

VLM 和 YOLOE 的作用并不相同：YOLOE 是补充候选框的开放词表检测器，主要解决“哪里可能漏了”；VLM 是事件级语义复核器，主要解决“这个候选到底是什么、是否足以确认”。

## 15. 10 秒～10 分钟视频的粗到细处理

### 15.1 输入契约

系统应明确支持三种入口：

1. `video`：执行固定缺陷类别的自动巡检；
2. `video + text`：文本定义本次检索目标、管段信息或检查策略，例如“重点检查沉积和树根侵入”；
3. `text`：查询已经入库的视频和事件，例如“查找 K12 管段最严重的沉积”，文本本身不能替代图像证据。

建议任务接口为：

```json
{
  video_uri: s3://inspection/K12.mp4,
  query: 重点检查沉积与树根侵入,
  inspection_profile: sewer_three_class_v1,
  pipe_metadata: {
    pipe_id: K12,
    diameter_mm: 800,
    material: concrete
  }
}
```

### 15.2 不要先把整段长视频交给 VLM

长视频应采用时序金字塔式的“粗扫—扩窗—精扫—复核”：

```text
原始视频 / 文本任务
        │
        ▼
解码、时间戳、里程/姿态对齐、环形缓存
        │
        ▼
Level 0：0.5～2 FPS 建立缩略图和质量索引
        │
        ▼
Level 1：1～5 FPS 低成本粗扫
YOLO26 低阈值 + 图像质量 + 异常/视觉文本相似度
        │
        ▼
候选帧合并成重叠时间窗，并向前后扩展 1～3 秒
        │
        ▼
Level 2：只对候选窗按原分辨率、较高 FPS 精扫
YOLO26 + 可选分块推理 + tracker
        │
        ▼
Level 3：YOLOE 对疑似漏检窗或周期覆盖窗补扫
        │
        ▼
选择每个事件的最佳帧/短片段
        │
        ▼
Level 4：VLM 事件级复核
        │
        ▼
若仍不可判定：提高采样率重扫 / 人工 / 主动重拍
```

例如 10 分钟、25 FPS 的视频共有约 15,000 帧。若首轮按 1 FPS 扫描，只处理约 600 帧；随后仅对候选附近的数秒窗口恢复高 FPS。这样可以把 VLM 和高分辨率检测的预算集中到真正可疑的位置。

但 1 FPS 不是固定标准：持续不足 1 秒的事件可能被跳过，所以必须保留重叠窗口、周期覆盖帧、多尺度采样和无候选回退重扫，并在自己的视频上测事件级召回率。

若机器人具备轮速计、里程计或 SLAM，优先按“每移动固定距离”采样，而不是只按秒采样，避免车辆速度变化造成空间覆盖不均。

### 15.3 如何在模糊长视频中快速找到有用画面

核心不是把模糊帧全部删掉，而是先找到可疑时间窗，再在该窗口内选出“相对最好”的原始证据帧。每一帧可以计算：

- 清晰度：Laplacian variance、Tenengrad 或无参考图像质量模型；
- 曝光、暗区比例、过曝和高光反射；
- 遮挡、水花和镜头污渍；
- 运动模糊程度；
- YOLO 置信度、框面积和类别边际；
- 与相邻帧的轨迹一致性；
- 与用户文本或缺陷原型的视觉语义相似度。

候选事件内部可使用一个可学习或经验初始化的最佳帧得分：

```text
Q(frame) =
  a × sharpness
+ b × detector_score
+ c × target_area
+ d × temporal_consistency
- e × glare
- f × occlusion
- g × motion_blur
```

不要只选 `Q` 最高的一帧，还应保留 1 张全局上下文帧和 1～2 张时间上分散的证据帧，避免最高分帧恰好只显示缺陷的一部分。

若整个候选窗都比较模糊，则按以下顺序处理：

1. 回看前后 1～3 秒，寻找相机运动更慢或对焦更好的邻帧；
2. 从原始码流重新解码，避免缩略图、转码或二次压缩造成的信息损失；
3. 使用多帧一致性进行确认，而不是信任单张增强图；
4. 只对候选窗口触发去模糊、去雾或超分辨率，并保留原图作审计；
5. 若设备可控，提出减速、补光、重新对焦或回退重拍请求；
6. 仍无有效证据时输出 `insufficient_evidence`，不得强行生成缺陷结论。

选择性增强的定位应当是“改善候选窗的可读性”，不能把增强网络生成的纹理当作新缺陷证据。论文评估中要分别报告原图检测、增强图检测以及二者融合的结果。

### 15.4 视频索引与可查询性

每段视频入库时建议生成四类索引：

- `temporal_index`：时间戳、帧号、里程和相机姿态；
- `quality_index`：清晰度、曝光、反光、遮挡和运动模糊；
- `detection_index`：低阈值候选、轨迹、异常得分；
- `semantic_index`：关键帧/片段的视觉向量，用于文本检索。

这样文本查询“找沉积最严重且画面清楚的片段”可以先在结构化索引和向量索引中筛选，不必让 VLM 从头观看十分钟视频。

实现上可以先采用 FFmpeg/PyAV 或 GStreamer 解码，离线任务使用任务队列；只有验证多路实时吞吐后，再决定是否引入 NVIDIA DeepStream。管道视频通常没有自然场景切换，不能只依赖通用 shot detection，应结合质量变化、相机运动、里程和检测信号。

## 16. Harness 与插件化架构

### 16.1 可以做成随插随用，但要分成三层

最稳妥的方式不是把 YOLO、tracker 和 VLM 全部写进某一个 Agent Harness，而是采用三层插件化：

```text
第一层：确定性的 Inspection Core
视频解码、GPU 调度、检测、跟踪、状态机、融合、存储、审计
                         │
                         ▼
第二层：稳定的服务与工具接口
REST/gRPC + JSON Schema；长任务返回 job_id；MCP 暴露高层工具
                         │
                         ▼
第三层：可替换 Harness / UI
Codex Plugin / DeepSeek Harness Plugin / 自研工作台
负责理解文本意图、规划调用、审批、解释和报告流程
```

这样 Codex、DeepSeek 或普通 Web UI 都可以调用同一个管网检测核心；即使不运行 Agent，核心检测流程仍能独立完成任务。

### 16.2 内部算法插件接口

建议定义以下能力槽，而不是用具体模型名写死流程：

| 插件能力 | 默认实现 | 输入 | 输出 |
|---|---|---|---|
| `IngestPlugin` | FFmpeg/PyAV/GStreamer | 视频 URI/流 | 帧、时间戳、元数据 |
| `SamplerPlugin` | 时序金字塔采样器 | 帧流、里程 | 粗扫帧、候选窗口 |
| `QualityPlugin` | 规则 + IQA | 图像/窗口 | 质量分量和可用性 |
| `EnhancerPlugin` | 去雾/去模糊/超分 | 低质量候选窗 | 增强帧及溯源 |
| `DetectorPlugin` | YOLO26 | 图像/tiles | 固定类检测框 |
| `ProposalPlugin` | YOLOE | 图像、文本/视觉提示 | 补充候选框 |
| `TrackerPlugin` | ByteTrack/BoT-SORT | 检测序列 | track/event |
| `ReviewerPlugin` | Qwen3-VL | 事件证据包 | 结构化复核结果 |
| `SegmenterPlugin` | YOLO-Seg/SAM | 图像、框/点 | 缺陷 mask |
| `GeometryPlugin` | 标定深度/双目/LiDAR | mask、深度、标定 | 尺寸和不确定度 |
| `ReportPlugin` | LLM + 规则模板 | 结构化事实 | 报告草稿 |

每个插件清单至少包含：`name`、`version`、`capabilities`、输入输出 JSON Schema、同步/异步模式、GPU/内存要求、超时与重试、健康检查、模型和代码许可证。

配置文件可以替换具体实现：

```yaml
pipeline:
  sampler: temporal_pyramid_v1
  detector: yolo26s_960
  proposal: yoloe_11s
  tracker: bytetrack
  reviewer: qwen3_vl
  segmenter: yolo26_seg
  reporter: structured_llm_reporter
```

“随插随用”仍然需要 adapter：不同 detector 的预处理、标签映射、tensor 格式、阈值和 TensorRT engine 并不相同，不能只换一个模型文件就假定行为一致。

### 16.3 提供给 Agent 的高层工具

MCP 或其他工具层不应传输逐帧视频或负责 GPU 内循环，而应暴露稳定的任务级接口：

```text
inspect_video(video_uri, query?, inspection_profile) -> job_id
get_job_status(job_id) -> progress, stage, warnings
search_video(video_id, text_query, filters?) -> temporal_windows
list_events(job_id, filters?) -> DefectEvent[]
review_event(event_id, reviewer_profile?) -> VLMReview
measure_event(event_id) -> Measurement
generate_report(job_id, report_profile) -> report_id
request_human_review(event_id, reason) -> review_ticket
```

长任务返回 `job_id`，Harness 只轮询或订阅状态；视频 URI、证据帧和中间结果保存在对象存储/数据库中。这样不会因为一次 Agent 对话中断而丢失十分钟视频的处理状态。

### 16.4 DeepSeek Harness 和 Codex 的接入方式

截至 2026-08-24，DeepSeek Harness 官方仍将项目标为 developer preview，并强调模型、工具、skills、sessions、sandboxes、storage、loops、scheduling 和 UI 等均可插件化；其开放代码采用 MIT 许可证，但官方也提示 API 仍在快速演进。因此它适合作为研究和编排适配层，不宜成为视频实时内循环的唯一依赖。[DeepSeek Harness 官方页面](https://deepseek.com/harness/en/)；[DeepSeek Harness GitHub](https://github.com/deepseek-ai/deepseek-harness)

Codex Plugin 可以包含 Skills、MCP server 或二者：Skill 描述巡检工作流和调用规则，MCP server 提供受控工具和结构化返回。若要构建独立操作台，还可以通过 Codex App Server 接入会话、审批、流式事件和工具调用；但实时检测、GPU 调度和事件状态机仍应留在 Inspection Core 中。[Codex Harness 官方介绍](https://openai.com/index/unlocking-the-codex-harness/)；[Plugins in Codex](https://help.openai.com/en/articles/20001256-plugins-in-codex/)

推荐最终打包为：

```text
pipe-inspection-core/
  services/              # 可独立部署的确定性核心
  schemas/               # Job/Event/Review/Measurement JSON Schema
  plugins/               # 算法插件与 adapter
  mcp-server/            # 面向 Harness 的高层工具
  codex-plugin/
    skills/pipe-inspection/
    .codex-plugin/plugin.json
  deepseek-adapter/      # 调用同一组服务接口
```

### 16.5 Agent 权限与安全边界

- Agent 可以提交检测、复核、检索和报告任务，但不能直接篡改单帧事实记录；
- LLM 不能越过 Schema、融合器和事件状态机直接判定最终缺陷；
- `request_reimage` 只能形成建议，机器人运动由安全控制器或人工批准；
- 视频中的字幕、标牌和 OCR 文本均视为不可信输入，不能当成系统指令；
- 所有插件调用记录版本、输入摘要、输出、耗时和失败原因，保证论文复现实验和专利实施例可审计；
- 报告必须引用 `event_id`、证据帧和量测结果，不能补写数据库中不存在的事实。

## 17. 更新后的完整端到端流程

综合视频输入、粗到细定位、YOLO26、YOLOE、VLM 和插件化之后，完整流程应调整为：

```text
视频 / 视频+文本 / 已入库视频的文本查询
                    │
                    ▼
任务解析、InspectionProfile、视频解码与元数据对齐
                    │
                    ▼
时序金字塔采样 + 质量/语义/低阈值检测索引
                    │
                    ▼
候选时间窗合并、前后扩窗、周期覆盖与回退重扫
                    │
                    ▼
候选窗原分辨率精扫
YOLO26 固定类别检测 + ByteTrack/BoT-SORT
                    │
                    ▼
轨迹聚合为 DefectEvent + 最佳帧/短片段选择
                    │
                    ▼
不确定性路由器
      ┌─────────────┼────────────────┐
      │             │                │
      ▼             ▼                ▼
VLM 事件复核    YOLOE 补充候选    人工/主动重拍
类别/真假/拒识   无框漏检/周期扫描  证据仍不足
      │             │                │
      │      新候选回流 tracker      │
      └─────────────┴────────────────┘
                    │
                    ▼
融合、校准、拒识与事件状态机
                    │
                    ▼
分割 → 深度/标定/三维几何量化
                    │
                    ▼
结构化事实、证据包、人工审核
                    │
                    ▼
LLM 标准化报告 + 资产/GIS 关联
```

其中最快的高频路径仍是 YOLO26；YOLOE 和 VLM 都是被路由器触发的慢路径。Harness 位于整个流程外层，负责把用户的文本意图转为任务、调用工具和组织报告，不能替代视频预处理、逐帧检测、GPU 调度、融合器和事件状态机。
