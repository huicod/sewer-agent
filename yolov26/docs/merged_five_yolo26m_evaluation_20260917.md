# YOLO26m 合并五类：训练后测试报告

2026-09-17。结论：训练正常完成，SEDD适配明显改善，但五类效果仍不均衡，**尚不能认定整体优于旧模型，不自动替换线上权重**。

## 训练与文件检查

- tmux `sewer-yolo26m`退出码0；84轮，4.070小时（results.csv累计14,651.7秒）。第54轮最佳，后续30轮未提高触发早停，不是崩溃。
- 全84行数值有限，日志无OOM/NaN/Traceback；train10,908、val606张扫描均0损坏。
- 第54→84轮训练cls loss从0.90383降至0.59758，但验证cls loss从1.29874升至1.48972，验证mAP50–95从44.166%降至42.293%。有后段过拟合迹象，采用best而非last合理。
- `best.pt`、`last.pt`各约44.1MB；周期文件`epoch0.pt`至`epoch80.pt`间隔10，9份齐全。CPU实际加载最新周期点，EMA/optimizer/scaler完整且优化器张量有限；未实际恢复训练。
- 周期命名为零基，epoch80对应已完成第81轮；它可用于从第82轮续训。正常结束的best/last已精简，没有完整优化器状态。
- `close_mosaic=10`原定在100轮上限的最后10轮关闭；84轮早停没有进入此阶段。这不是运行故障，亦不能单凭此点断言效果原因。

## 测试口径

冻结数据`var/datasets/merged_five_v1`，test共606图＝sewer2403 562＋SEDD 44，共761个标注框。未重划分、未用test选权重或阈值。

标准指标：新best、960、batch16、FP32、conf下限0.001，框架原生mAP；P/R为框架曲线上选取的工作点，不等同于固定0.25阈值。

固定工作点评估：复用项目真实`UltralyticsDetector.predict_image`，逐张原图、960、conf=0.25；复用`score_boxes`按同类、IoU≥0.5、置信度降序一对一匹配。FP/FN均相对于现有标注，不是人工确认的业务误报/漏报。

SEDD只评分PL/BLOCK，其他三类预测如有只展示、不计FP；sewer评价五类。新旧模型的names实读均为PL/BX/FS/BLOCK/AJ同序，但评分仍按名称。原生mixed mAP没有逐来源屏蔽未标注类别，因此以分来源结果为主，不能将其与屏蔽后的固定指标直接对照。

## 新模型标准mAP

| 数据 | 图数 | mAP50 | mAP50–95 |
|---|---:|---:|---:|
| 混合val：复现已选best | 606 | 65.38% | 44.10% |
| 混合test：参考 | 606 | 63.20% | 42.20% |
| sewer2403 test：五类 | 562 | 63.18% | 42.47% |
| SEDD test：PL/BLOCK | 44 | 76.11% | 42.35% |

验证重验与训练最终重验略有小数差异：本次FP32/batch16，原训练最终验证采用不同精度/批次；数值接近，不重新选择checkpoint。

## 固定conf=0.25：新旧模型对照

| 来源 / 模型 | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| sewer2403 / 旧s（非独立基线） | 606 | 124 | 86 | 83.01% | 87.57% | 85.23% |
| sewer2403 / 新m | 451 | 204 | 241 | 68.85% | 65.17% | 66.96% |
| SEDD / 旧s | 4 | 6 | 65 | 40.00% | 5.80% | 10.13% |
| SEDD / 新m | 49 | 27 | 20 | 64.47% | 71.01% | 67.59% |
| 混合 / 新m | 500 | 231 | 261 | 68.40% | 65.70% | 67.02% |

SEDD上的提升同时体现在P和R，但这是加入SEDD训练数据后的**同来源留出集适配**，不证明对第三方未见数据集也能泛化。44图样本有限。

旧s可能见过本次新划分的sewer测试图片/近邻，其高数值只作诊断参考，不是公平的独立模型对照。不能据此单独断言m架构更差、混合训练必然有害，或只凭SEDD提高就宣称整体升级成功。

新m在sewer测试上的逐类表现：

| 类别 | GT框 | Precision@0.25 | Recall@0.25 | mAP50 |
|---|---:|---:|---:|---:|
| PL | 341 | 62.34% | 57.77% | 58.58% |
| BX | 16 | 38.89% | 43.75% | 40.99% |
| FS | 44 | 62.96% | 38.64% | 44.58% |
| BLOCK | 191 | 77.25% | 76.44% | 82.07% |
| AJ | 100 | 80.00% | 84.00% | 89.69% |

BX训练仅288张阳性图，test仅16图；FS训练677张阳性图。类别不均衡是已知风险，不能据此完成因果归因。全test仅2张显式空标注图，正常场景误报率仍未充分验证。

## 人工可查的图片与原始结果

全部结果在`var/evaluations/2026-09-17-yolo26m/`：

- [606张完整图库](../../var/evaluations/2026-09-17-yolo26m/gallery/index.html)：每图有`original.jpg`、`ground_truth.jpg`、`old.jpg`、`new.jpg`、`result.json`与HTML。没有VLM结果。
- [逐图框/分数/计数JSON](../../var/evaluations/2026-09-17-yolo26m/per_image.json)、[固定指标JSON](../../var/evaluations/2026-09-17-yolo26m/fixed_summary.json)；`new_*_test.json`含原生逐类AP。
- `new_mixed_test/`、`new_sewer_test/`、`new_sedd_test/`保留PR/F1曲线、混淆矩阵、批次预测图及原生低阈值预测txt；这些txt是conf≥0.001，不要误当图库的conf≥0.25。
- [SEDD裂缝例子00170](../../var/evaluations/2026-09-17-yolo26m/gallery/sedd__00170/index.html)：GT6框，旧0TP/1FP/6FN，新6TP/1FP/0FN。
- [sewer PL漏检例子](../../var/evaluations/2026-09-17-yolo26m/gallery/sewer2403__1002_jpg.rf.a9ea8533cd764e85da8bdb0f5c33424c/index.html)：旧命中1框，新漏1框。
- [FS人工复核例子](../../var/evaluations/2026-09-17-yolo26m/gallery/sewer2403__094605e757df1c330bf977313fd3c221/index.html)：旧命中、新漏检，但GT框落在左上大段叠加文字区域；从可见图像不能直接确认其缺陷。这说明部分分数差异需要检查标注语义，**本次保留原GT，不改标签或重算“修正分数”**。

## 软件与资源验证

- 83项相关测试通过，覆盖训练入口、合并数据、分组、检测适配器及框匹配计分；评估脚本Ruff通过。
- 新旧checkpoint各完成606次真实单图适配器调用＋4图批量调用；类别、尺寸、原图坐标契约通过，模型卸载成功。
- 新m单图调用（含读图/预处理/推理/结果转换，排除加载和首次预热）中位12.76ms、P95 27.88ms；旧s为12.33ms/27.06ms。串行GPU本地测量，不等同网页/VLM延迟。
- 周期权重有独立只读审计；训练日志/曲线数值、测试样本数与图片产物均核对。
- 初次评估在完成val后因临时导出脚本误读`model.validator`中止；查本地库源码后改用`on_val_end`采集seen，重跑四项评估和全部推理成功。初始日志/产物保留为`run.initial.log`、`initial_val_export_incomplete/`；正式证据为`run.log`与`completion.json`。

复现脚本：[evaluate.py](../../.trellis/tasks/09-17-yolo26m-posttrain-eval/evaluate.py)。已完成输出有覆盖保护；再跑另设独立OUT目录。不需要API Key。

建议先人工复核BX/FS和新旧分歧样本，再决定是否调整数据或训练。当前没有重训、调参、恢复服务、调用API或上线新权重。
