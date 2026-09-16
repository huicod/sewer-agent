# YOLO26 Sewer2403 服务器材料

## 新训练配置（2026-09-16，尚未正式启动）

用户指定YOLO26m＋RTX5090，使用合并五类数据；配置为960、固定batch8、AMP、最多100epoch、16GiB PyTorch显存预算，预留调试余量。
参数、真实显存预检和启动方式见[当前训练配置](docs/merged_five_yolo26m_5090.md)。
后文YOLO26s及旧数据划分/AutoBatch命令属于历史材料，不是本次重训入口；线上现有权重仍为YOLO26s。

## 1. 当前主版本

```text
模型：YOLO26s
数据：Sewer2403
任务：五类目标检测
输入：960 x 960
训练：100 epochs，AutoBatch=11，workers=12
训练环境记录：Python 3.12.3，torch 2.8.0+cu128，RTX 5090 32GB
```

五类定义：

| 类别 | 含义 | 来源标注 |
|---|---|---|
| PL | 破裂 | PL |
| BX | 变形 | BX |
| FS | 腐蚀 | FS |
| BLOCK | 堵塞类 | CJ 沉积 + JG 结垢 + ZW 障碍物 |
| AJ | 支管暗接 | AJ |

当前 `best.pt` 的验证集结果：Precision 0.636、Recall 0.592、mAP50 0.582、mAP50-95 0.389。该结果不是独立测试集结果。

## 2. 目录

```text
yolov26/
├── code/                  # 数据转换、训练、评估、阈值扫描和标签可视化
├── data_archives/         # Sewer2403 三卷压缩包，ASCII 文件名便于上传
├── pretrained/            # YOLO26s 通用预训练权重
├── experiments/
│   ├── sewer2403_5class_yolo26s_img960_e100/  # 完整五类实验
│   └── sewer2403_17class_reference/           # 17 类配置和日志，缺 checkpoint
└── docs/                  # 五类实验报告
```

## 3. 安装环境

建议先安装与服务器 CUDA 匹配的 PyTorch，再安装本目录依赖：

```bash
cd /root/autodl-tmp/server/yolov26
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r code/requirements.txt
```

`requirements.txt` 只约束 `ultralytics>=8.3.0` 和 `PyYAML>=6.0`。历史训练实际使用 Ultralytics 8.4.46；若需要严格对照，建议固定该版本并记录新环境的 CUDA、torch、Ultralytics 和 GPU 信息。

## 4. 解压原始数据

三卷文件必须放在同一目录：

```text
data_archives/sewer2403.z01
data_archives/sewer2403.z02
data_archives/sewer2403.zip
```

服务器安装 7-Zip 后执行：

```bash
mkdir -p data_raw
7z x data_archives/sewer2403.zip -odata_raw
find data_raw -maxdepth 2 -type d
```

压缩包内层目录名称为 `市政下水雨污管道缺陷数据2403`。可以重命名为 ASCII 路径：

```bash
mv "data_raw/市政下水雨污管道缺陷数据2403" data_raw/sewer2403
```

转换脚本要求源目录中直接存在 `images/` 和 `labels/`。

## 5. 生成五类 YOLO 数据集

先做只读解析和划分检查：

```bash
python code/prepare_sewer2403_yolo.py \
  --source data_raw/sewer2403 \
  --profile five \
  --out runs_data/sewer2403_5class_yolo \
  --group-mode video \
  --dry-run
```

确认统计正常后正式生成：

```bash
python code/prepare_sewer2403_yolo.py \
  --source data_raw/sewer2403 \
  --profile five \
  --out runs_data/sewer2403_5class_yolo \
  --group-mode video \
  --image-mode hardlink \
  --overwrite
```

默认使用 `seed=3407`、`train/val/test=0.8/0.1/0.1`，并尽量把同一视频的帧放在同一划分中，避免视频近邻帧泄漏。

## 6. 复训命令

```bash
python code/train_detect.py \
  --model pretrained/yolo26s.pt \
  --data runs_data/sewer2403_5class_yolo/sewer2403_5class.yaml \
  --epochs 100 \
  --imgsz 960 \
  --batch -1 \
  --device 0 \
  --workers 12 \
  --patience 30 \
  --project runs_detect \
  --name sewer2403_5class_yolo26s_img960_e100
```

若要从现有最佳模型继续微调，把 `--model` 改为：

```text
experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt
```

这属于以已有权重初始化的新训练，不是恢复原优化器状态。

## 7. 独立测试集评估

```bash
python code/eval_detect.py \
  --model experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt \
  --data runs_data/sewer2403_5class_yolo/sewer2403_5class.yaml \
  --split test \
  --imgsz 960 \
  --device 0 \
  --workers 12 \
  --project runs_eval \
  --name sewer2403_5class_test
```

建议下一步先完成该 test 评估，再进行 BX/FS 类别均衡、管道退化增强和困难负样本实验。

## 8. 17 类材料边界

`experiments/sewer2403_17class_reference` 保留了完整训练日志和 `args.yaml`。日志可恢复总体及逐类验证指标，但本机没有同步该实验的 `best.pt`、`results.csv`、混淆矩阵和曲线，因此不能直接部署 17 类 checkpoint。
