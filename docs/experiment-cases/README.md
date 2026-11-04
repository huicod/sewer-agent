# 视觉复检的逐图证据

首例展示 2026-11-02 工作台中 YOLO26s 候选被视觉复检接受的真实记录。后两例保留 2026-09-18 冻结测试集（606 张图）的 YOLO26m 历史自动融合结果。每例均使用本次检查保存的原始检测与复检结果，按同类、IoU ≥ 0.5、一对一匹配核对标注。点击图片可查看大图。

## Accepted crack

**破裂候选得到确认，命中框保留。** 原始样本 `sewer2403-4172_png.rf.fccf3ffaa2743770e3ebdd7199ebf7cd`，640 × 640 像素。任务 `2ff87e71-4546-46db-83a1-8037e7121974` 于 2026-11-02 完成，检测模型为 `sewer2403_5class_yolo26s_img960_e100/best`，复检模型为 `qwen3.8-max`，工具版本为 `yolo-guided-review-v4`。

| 数据集标注 | YOLO26s 初查 | 同次检查保存的复检融合 |
|---|---|---|
| [![原图上的一个破裂标注框](accepted-crack/ground_truth.png)](accepted-crack/ground_truth.png) | [![YOLO26s 检出的破裂框](accepted-crack/yolo_raw.png)](accepted-crack/yolo_raw.png) | [![VLM 接受候选后保留的破裂框](accepted-crack/predictions.png)](accepted-crack/predictions.png) |
| PL 标注 `[274,113,341,437]`。 | PL 分数 0.709432，框 `[244.7491,103.5368,348.4540,435.9802]`，IoU 为 0.626432。 | VLM 接受候选，理由为可见连续纵向细裂纹。保存结果保留原类别、框和分数，无新增候选。 |

初查和复检融合均为 1 个命中、0 个多报、0 个漏检。任务最终为 `completed`，发现项按规则确认为 `confirmed`。这张图说明候选核对与结果保留的流程，整体效果由各自评估集统计。

三张图以同一份归档原图和保存坐标调用项目既有图库绘制函数生成，均展示完整画面。原图与仓库[示例 01](../../example/01_wall_trace/image.jpg)逐字节一致，标注取自 [sources.json](../../example/sources.json)。[逐项证据 JSON](accepted-crack/evidence.json)包含原始检测、VLM 意见、实际融合、最终状态、模型版本和重新核算的匹配结果。该单任务示例单独列示，606 图 YOLO26m 指标仍采用对应冻结测试集。[实验工具](../../.trellis/tasks/09-18-vlm-yolo26m-comparison/run_comparison.py)

## Deformation

**变形候选被撤销。** 样本 `sewer2403__DF_53_png.rf.5e30f50de9eb202e294d8ffbd9e092e9`，原帧为 640 × 640 像素。对照图截取原图的 `x=[67,584), y=[52,548)`，保留完整标注框和检测框，省略周围像素。绿色遮挡来自原图库。

| 数据集标注 | YOLO26m 初查 | 当时的复检融合 |
|---|---|---|
| [![数据集标出一个变形区域](deformation/ground_truth.png)](deformation/ground_truth.png) | [![YOLO26m 的变形框](deformation/yolo_raw.png)](deformation/yolo_raw.png) | [![复检后画面没有预测框](deformation/predictions.png)](deformation/predictions.png) |
| 一个 BX（变形）框。 | BX 分数 0.831，与标注 IoU 为 0.617。 | 视觉模型将环形肋条和远端轮廓解释为正常波纹与透视，旧策略删除原框。 |

初查命中 1 个标注，融合后漏检 1 个。用后来的保守策略重放同一份复检意见，原框保留并转交人工判断。

## Obstruction

**新增堵塞框与标注错开。** 样本 `sewer2403__911ca174a34a5d7f1cd40e317c7519de`，原帧为 1920 × 1080 像素。对照图截取原图的 `x=[780,1200), y=[615,944)`，保留两个预测框与唯一标注框，省略周围的巡检信息叠字。

| 数据集标注 | YOLO26m 初查 | 当时的复检融合 |
|---|---|---|
| [![标注框圈出底部一处堵塞类物体](obstruction/ground_truth.png)](obstruction/ground_truth.png) | [![YOLO26m 框位于标注目标处](obstruction/yolo_raw.png)](obstruction/yolo_raw.png) | [![原框上方多出了一个堵塞类框](obstruction/predictions.png)](obstruction/predictions.png) |
| 一个 BLOCK（堵塞类）框。 | BLOCK 分数 0.374，与标注 IoU 为 0.551。 | 原框保留，另在上方加入一个“底部散落杂物”框，与唯一标注的 IoU 为 0。 |

按整图类别评分，两次都报对 BLOCK。按框评分，初查为 1 个命中，旧融合为 1 个命中、1 个未匹配预测。新增框的 1.00 是旧融合的排序占位值。仅凭这份标注无法判断现场是否确有额外杂物。保守重放保留原框，将新增框列为待审建议。[保守重放记录](../../.trellis/tasks/09-18-vlm-calibration/report.md)

后两例的原始图库保存在本地实验归档的 `var/evaluations/2026-09-18-vlm-yolo26m-comparison/yolo26m_guided/gallery/<样本 ID>/`。原图左上角为坐标原点，文中区间含起点、不含终点。摘录分别来自 `ground_truth.jpg`、`yolo_raw.jpg` 和 `predictions.jpg`，直接裁取其解码像素。框和标签是图库原有叠加，引用的检测分数、匹配和复检意见均按完整原帧及同目录的 `result.json` 核对。保守重放使用 `var/evaluations/2026-09-18-vlm-conservative-fusion/conservative.json` 中相同样本 ID 的记录。[复检差异分析](../../.trellis/tasks/09-18-vlm-yolo26m-comparison/fusion_diagnosis.md)
