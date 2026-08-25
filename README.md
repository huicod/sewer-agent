# 管网检测服务器交付包

整理日期：2026-08-25

本目录用于把当前可用的 YOLO26 训练材料和 VLM 复检调研传回服务器。两部分相互独立：

```text
server/
├── yolov26/     # 较新的 Sewer2403 训练链路、数据压缩包、权重和实验记录
└── vlm_review/  # VLM 事件级复检、补召回、跟踪和报告调研
```

## 1. 本次 YOLO26 取舍

主版本采用 `Sewer2403 五类 YOLO26s + 960`，因为它是当前本地最新且产物完整的实验，包含 `best.pt`、`last.pt`、逐轮指标、曲线、混淆矩阵和验证集预测图。

同时保留 Sewer2403 17 类实验的训练配置与完整日志，用于读取逐类结果和后续复现。17 类的 `best.pt`、`results.csv` 和曲线当时没有同步回本机，因此没有伪装成完整 checkpoint 放入交付包。

旧的 SEDD 三类实验、RT-DETR 失败实验和早期 YOLO26 参数扫描没有复制进来。它们仍保留在原工作区，但不是这次服务器包的主线。

## 2. 上传方式

在 Windows PowerShell 中，从 `E:\cctv-lab` 执行：

```powershell
scp -P <端口> -r .\server root@<服务器IP>:/root/autodl-tmp/
```

完整包包含约 2.23 GB 的三卷 Sewer2403 压缩数据。若服务器已经有原始 `sewer2403/images` 和 `sewer2403/labels`，可以跳过 `server/yolov26/data_archives`，只上传其余目录。

上传完成后，在服务器检查：

```bash
cd /root/autodl-tmp/server
sha256sum -c SHA256SUMS.txt
```

## 3. 使用入口

- YOLO26 数据准备、训练和评估：`yolov26/README.md`
- VLM + YOLO 架构、方法、论文与实验路线总览：`vlm_review/VLM_YOLO_ARCHITECTURE_AND_RESEARCH_SUMMARY.md`
- VLM 复检方案与文档阅读顺序：`vlm_review/README.md`
- 文件清单和用途：`MANIFEST.md`

## 4. 边界说明

- `Sewer2403 五类`当前只有验证集结果，尚未完成独立测试集评估。
- 训练完成后的 Ultralytics checkpoint 已移除优化器状态，适合推理、评估和作为初始权重继续微调，不保证精确恢复原优化器进度。
- VLM 目录目前是架构与调研交付，不包含已经部署完成的 VLM 服务或模型权重。
- Ultralytics、VLM、YOLOE、跟踪器及论文代码的许可证需要在部署前分别复核。
