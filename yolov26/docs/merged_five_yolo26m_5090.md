# YOLO26m / RTX 5090：整卡训练配置

2026-09-16。配置和短程显存预检完成，**正式训练尚未启动**。
配置唯一入口：[merged_five_yolo26m_5090.yaml](../configs/merged_five_yolo26m_5090.yaml)。
原线上YOLO26s权重不替换，数据划分不变；本次不重启前后端服务。

## 当前参数

| 参数 | 值 |
|---|---|
| 模型 | YOLO26m，官方COCO通用预训练`yolov26/pretrained/yolo26m.pt` |
| 数据 | `var/datasets/merged_five_v1/data.yaml`，PL/BX/FS/BLOCK/AJ |
| train / val / test | 10,908 / 606 / 606 |
| GPU / 框架 | RTX5090，nvidia-smi总显存32607MiB；Torch2.8.0+cu128 / Ultralytics8.4.46 |
| 输入 | imgsz=960，训练960×960，不启用多尺度 |
| Batch | **固定16**；当前Ultralytics训练内验证自动为32 |
| 轮次 / 早停 | 最多100epoch；patience=30；best.pt按验证表现选择，不认定第100轮最好 |
| 优化器 | MuSGD；显式固定，旧YOLO26s训练日志的auto实际也选MuSGD |
| 学习率 | lr0=0.01、lrf=0.01；cos_lr=false，基础终值0.0001（参数组可有倍率） |
| 动量 / 权重衰减 | momentum=0.9 / weight_decay=0.0005 |
| Warmup / 名义batch | warmup_epochs=3 / nbs=64，稳定阶段梯度累积约4步；预热期由框架调整 |
| 混合精度 | AMP=true，真实AMP检查通过 |
| CPU | 主进程Torch/OMP/MKL线程8；DataLoader训练workers8，当前框架验证workers16 |
| 显存预算 | 整卡供训练；PyTorch allocator上限30GiB；启动时要求GPU空闲至少30.5GiB |
| 缓存 / 编译 | cache=false、compile=false |
| 增强 | mosaic=1.0，最后10轮关闭；scale=0.5、translate=0.1、左右翻转0.5；不旋转/上下翻转；HSV0.015/0.7/0.4；mixup/cutmix关闭 |
| 随机种子 | 20260916；deterministic=true |
| 保存 | 每轮更新last，达到最佳验证表现时更新best；save_period=10；plots=true |
| 正式产物目录 | `var/training/merged_five_yolo26m_960_5090/`，权重在`weights/` |

环境已经支持5090的sm_120，CUDA矩阵运算、真实前向/反向/MuSGD更新通过，不需要更换Torch。
100epoch是训练上限而非最优轮数承诺。SEDD仅有PL/BLOCK可映射真值，后续正式报告需分来源评估，不能只解释混合mAP；详见[数据说明](../../.trellis/tasks/09-16-merged-five-dataset/report.md)。

## 显存预检证据

`var/training_preflight/2026-09-16-yolo26m-full/result.json`和`probe.log`：

- 从**训练集内部**取128张框较多的图片，验证资源检查复用其中64张；完成8个训练batch、完整验证及checkpoint保存/回读。测试集未使用，指标不能作为准确率结果。
- 实际训练batch16、验证batch32，AMP检查通过，MuSGD更新正常，8个loss均有限，无OOM或自动降批。训练workers8、验证workers16、主线程8与配置一致。
- 峰值allocated **18.490GiB**，峰值reserved **20.311GiB**；总计约21秒。不据此精确推算全量训练耗时或保证所有数据批次的峰值。
- 回调在8个训练batch后停止，预检关闭plots；没有模拟占用或并发推理。预检权重不用于部署或正式训练初始化。
- 之前的batch8并发调试预检保留在`var/training_preflight/2026-09-16-yolo26m/`，仅作历史证据，原资源预留方案已被用户撤销。

## 资源与checkpoint注意事项

不使用AutoBatch，固定batch16。用户已选择前端调试与训练错峰，不再为前端预留显存。32GB标称容量不能直接当作32GiB可分配容量；30GiB预算保留CUDA运行时余量，短测缓存峰值距离预算约9.7GiB，不为用满显存而盲目增大batch。

30GiB是[PyTorch缓存分配器限制](https://docs.pytorch.org/docs/2.8/generated/torch.cuda.memory.set_per_process_memory_fraction.html)，**不是硬件显存分区、全部CUDA分配上限或GPU算力配额**。达到该限制也会抛OOM，不能把“设了上限”理解成“绝不OOM”；防范依靠固定批量、实测余量与启动检查共同完成。

- 训练期间不并发启动其他GPU重任务。若全量训练仍OOM，先检查其他占用，再降低batch并重新预检，不自动放宽显存预算。
- 当前容器CPU配额仍约25核、RAM上限90GiB；用户调整配额后，正式启动前重新核实，不能把宿主机208核亲和性当成容器配额。cache=false避免整集缓存。
- 当前Ultralytics8.4.46使用零基epoch：save_period=10生成`epoch0.pt`、`epoch10.pt`、`epoch20.pt`等，实际对应完成第1、11、21轮，间隔10轮。每轮另有`last.pt`，最佳指标更新`best.pt`。
- 已检查周期快照包含optimizer和scaler恢复状态。正常结束时框架会精简last/best；需要完整续训状态时保留周期快照，不要只保留部署用best。当前剩余磁盘约56GB，足以容纳本轮周期权重，但正式启动前仍检查剩余空间。

## 使用命令

在`/root/autodl-tmp/server`查看实际参数，不加载模型或占用GPU：

```bash
.venv/bin/python yolov26/code/train_detect.py \
  --profile yolov26/configs/merged_five_yolo26m_5090.yaml --dry-run
```

**下面的命令仅在用户确认正式训练后执行：**

```bash
.venv/bin/python -u yolov26/code/train_detect.py \
  --profile yolov26/configs/merged_five_yolo26m_5090.yaml
```

profile中的model/data/project相对仓库根目录解析；不能同时传旧CLI的batch/model等覆盖参数。
必须通过该profile入口启动，直接使用`yolo train`或旧CLI不会应用这里的资源限制。
此轮只保存配置及本地交接，未推送GitHub。
