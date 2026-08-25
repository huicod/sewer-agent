# 交付文件清单

整理日期：2026-08-25

## 1. 总体规模

生成本清单前，交付目录包含 49 个文件，合计约 2.322 GiB。其中 Sewer2403 三卷压缩数据为 2.230 GiB；其余代码、权重、实验产物和文档约 94 MiB。

## 2. YOLO26

### 代码

| 文件 | 用途 |
|---|---|
| `yolov26/code/prepare_sewer2403_yolo.py` | VOC 标注转 YOLO；支持 17 类和五类；按源视频分组划分 |
| `yolov26/code/train_detect.py` | Ultralytics YOLO26 检测训练入口 |
| `yolov26/code/eval_detect.py` | val/test 检测评估 |
| `yolov26/code/sweep_thresholds.py` | 部署置信度阈值扫描 |
| `yolov26/code/visualize_yolo_labels.py` | YOLO 标签可视化检查 |
| `yolov26/code/requirements.txt` | Python 依赖 |

### 数据

| 文件 | 字节数 | 说明 |
|---|---:|---|
| `yolov26/data_archives/sewer2403.z01` | 1,073,741,824 | 多卷压缩包第 1 卷 |
| `yolov26/data_archives/sewer2403.z02` | 1,073,741,824 | 多卷压缩包第 2 卷 |
| `yolov26/data_archives/sewer2403.zip` | 246,516,568 | 多卷压缩包主文件/第 3 卷 |

原始数据约 19,049 张图片，使用 VOC XML 标注。交付包保存压缩包，不重复保存解压图片和重新生成的 YOLO hardlink 数据。

### 权重与实验

| 路径 | 状态 |
|---|---|
| `yolov26/pretrained/yolo26s.pt` | YOLO26s 通用预训练权重 |
| `yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/` | 完整：best/last、args、results、曲线、混淆矩阵、训练日志、验证预测图 |
| `yolov26/experiments/sewer2403_17class_reference/` | 不完整：只有 args 和训练日志；README 明确记录缺失项 |

五类完整实验目录共 25 个文件，约 60 MiB。五类 `best.pt` 是当前交付包的默认部署与继续实验起点。

### YOLO26 文档

- `yolov26/README.md`：服务器解压、数据转换、复训和测试命令；
- `yolov26/docs/sewer2403_5class_experiment_report.md`：五类和 17 类结果、逐类分析与后续建议；
- `yolov26/experiments/sewer2403_17class_reference/README.md`：17 类产物缺口。

## 3. VLM 复检

`vlm_review/README.md` 是面向当前 Sewer2403 的实施摘要。`vlm_review/research/` 保存七份调研：

| 文件 | 重点 |
|---|---|
| `architecture_yolo26_vlm_pipe_inspection.md` | 双速度环、事件复检、长视频粗到细、接口和平台架构 |
| `summary_yolo_vlm_pipe_inspection.md` | 管网 VLM/YOLOE/跟踪/分割/量测/报告总览 |
| `summary_vlm_enhanced_yolo_detection_survey.md` | 在线复核、补召回、伪标签、蒸馏和域适应 |
| `summary_detection.md` | 检测器与开放词表检测背景 |
| `summary_multimodal_pipe_anomaly_detection.md` | RGB-D、双目、线激光、LiDAR 和真实量测 |
| `summary_pipe_dirty_data_preprocessing.md` | 模糊、污渍、水雾、过曝等退化与鲁棒训练 |
| `summary.md` | VideoPipe/多标签视频分类背景及边界 |

未复制完整论文 PDF 语料或外部模型权重；研究文档内保留了论文、官方代码和工程链接。

## 4. 明确未包含

- 旧 SEDD 三类训练数据和实验矩阵；
- RT-DETR 数值不稳定实验；
- Sewer2403 17 类 checkpoint 和完整曲线，因为本机原本就没有同步；
- VLM、YOLOE、ByteTrack/BoT-SORT 的第三方仓库和权重；
- 已部署的 VLM review service——当前交付的是架构与研究材料。
