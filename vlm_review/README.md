# 管网缺陷 VLM 复检调研包

## 1. 当前推荐主线

```text
视频
  -> YOLO26 固定类别高频检测
  -> ByteTrack / BoT-SORT 跨帧关联
  -> DefectEvent 事件聚合与最佳证据选帧
  -> 事件路由器
       ├─ VLM：已有候选的语义复核、改类、拒识、请求更多上下文
       └─ YOLOE：整帧/高分辨率切片补候选，处理 YOLO26 完全无框的漏检
  -> 候选融合和再次跟踪
  -> 状态机校验
  -> 人工复核、量测和报告
```

快速 YOLO 环不能等待 VLM。VLM 和 YOLOE 应异步运行，最终报警、报告和机器人控制由外部状态机决定。

## 2. 为什么以事件而不是帧为单位

一次 VLM 复检的输入单位应是 `DefectEvent`。同一缺陷连续出现几十帧时，只提交一次包含最佳证据的小包，避免逐帧重复调用和结论抖动。

推荐证据包：

- 原始全帧；
- YOLO ROI；
- 带 1.5～2 倍上下文的 ROI；
- 同一 `track_id` 的前、中、后或最佳质量 1～3 帧；
- YOLO 类别、置信度和框；
- 管道材质、里程、钟点位和图像质量；
- 候选类别定义、正例原型和容易混淆的难负例。

VLM 输出应受 Schema 约束，至少包含：

```json
{
  "event_id": "...",
  "track_id": 0,
  "decision": "accept|revise|reject|abstain|need_more_context",
  "canonical_label": "PL|BX|FS|BLOCK|AJ",
  "evidence": [],
  "confuser": null,
  "needs_human_review": false
}
```

不要使用 VLM 自由文本坐标替换检测框，也不要让单目 VLM 猜毫米尺寸。

## 3. Sewer2403 当前复检优先级

五类模型中建议优先复检：

1. `BX`：样本少、召回与定位都弱；
2. `FS`：边界模糊，容易与污渍、湿壁和照明变化混淆；
3. `PL`：形态跨度大，小裂缝和不规则边界容易漏检；
4. `BLOCK` 与 `AJ`：当前相对可靠，可作为高置信视觉原型和误报对照。

17 类实验中 `AJ/TJ/CJ` 是相对可信的真实缺陷类别；`CQ` 指标虽高但验证实例只有 16 个，不应直接据此认定其泛化稳定。

## 4. 建议触发条件

- YOLO 分数位于待复核区间；
- BX、FS、PL 等弱类；
- 多帧类别投票不一致或轨迹不稳定；
- 原图与增强图结果冲突；
- 图像模糊、过曝、污渍、水雾或遮挡；
- YOLO26 与 YOLOE 候选冲突；
- 新类别探索、疑似无框漏检；
- VLM 返回 `need_more_context`，需要邻帧、变焦或重拍。

具体阈值必须在固定验证集上校准，不要直接使用模型自报置信度作为业务概率。

## 5. 第一轮最小实验

```text
E0  YOLO26 单帧基线
E1  YOLO26 + tracker + 事件聚合
E2  E1 + VLM ROI/上下文单次复核
E3  E2 + 同轨迹三帧证据
E4  E3 + 专家定义、正例原型、难负例
E5  E4 + abstain / need_more_context
E6  E5 + YOLOE 第二候选源
```

分别报告候选召回、最终事件 precision/recall、VLM macro-F1、拒识率、ECE、人工复核率、VLM 触发率、P50/P95 延迟和每小时视频成本。不能把 VLM 图像分类 macro-F1 与 YOLO 检测 mAP 直接比较。

## 6. 文档阅读顺序

1. `research/architecture_yolo26_vlm_pipe_inspection.md`：最新完整系统架构、长视频粗到细处理、事件接口和 MCP/平台边界；
2. `research/summary_yolo_vlm_pipe_inspection.md`：管网论文、工程组合、Prompt、实验矩阵和最终选型；
3. `research/summary_vlm_enhanced_yolo_detection_survey.md`：在线复核、补召回、伪标签、蒸馏和域适应专题；
4. `research/summary_detection.md`：目标检测路线背景；
5. `research/summary_multimodal_pipe_anomaly_detection.md`：RGB-D、双目、线激光、LiDAR 与真实量测；
6. `research/summary_pipe_dirty_data_preprocessing.md`：模糊、污渍、过曝和水雾等退化数据方案；
7. `research/summary.md`：VideoPipe 与多标签视频分类背景。

这些 Markdown 已包含论文、官方代码和工程链接。本交付包没有复制整套 PDF 语料，因为它们不是服务器运行 VLM 复检所必需的材料。
