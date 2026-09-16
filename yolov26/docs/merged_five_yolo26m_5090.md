# YOLO26m / RTX 5090：训练配置与调试余量

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
| Batch | **固定8**；当前Ultralytics训练内验证自动为16 |
| 轮次 / 早停 | 最多100epoch；patience=30；best.pt按验证表现选择，不认定第100轮最好 |
| 优化器 | MuSGD；显式固定，旧YOLO26s训练日志的auto实际也选MuSGD |
| 学习率 | lr0=0.01、lrf=0.01；cos_lr=false，基础终值0.0001（参数组可有倍率） |
| 动量 / 权重衰减 | momentum=0.9 / weight_decay=0.0005 |
| Warmup / 名义batch | warmup_epochs=3 / nbs=64，稳定阶段梯度累积约8步；预热期由框架调整 |
| 混合精度 | AMP=true，真实AMP检查通过 |
| CPU | 主进程Torch/OMP/MKL线程4；DataLoader训练workers4，当前框架验证workers8 |
| 显存预算 | PyTorch allocator上限16GiB；启动时要求GPU空闲至少18GiB |
| 缓存 / 编译 | cache=false、compile=false |
| 增强 | mosaic=1.0，最后10轮关闭；scale=0.5、translate=0.1、左右翻转0.5；不旋转/上下翻转；HSV0.015/0.7/0.4；mixup/cutmix关闭 |
| 随机种子 | 20260916；deterministic=true |
| 保存 | 每轮last/best，额外每10轮checkpoint；plots=true |
| 正式产物目录 | `var/training/merged_five_yolo26m_960_5090/`，权重在`weights/` |

环境已经支持5090的sm_120，CUDA矩阵运算、真实前向/反向/MuSGD更新通过，不需要更换Torch。
100epoch是训练上限而非最优轮数承诺。SEDD仅有PL/BLOCK可映射真值，后续正式报告需分来源评估，不能只解释混合mAP；详见[数据说明](../../.trellis/tasks/09-16-merged-five-dataset/report.md)。

## 显存预检证据

`var/training_preflight/2026-09-16-yolo26m/result_saved.json`和`probe_saved.log`：

- 从**训练集内部**固定取64张框较多的图片；完成8个训练batch及验证batch16的完整短程回合，并验证checkpoint保存/回读。测试集未使用，小子集指标不作为准确率结果。
- 采用配置中的YOLO26m、960、batch8、AMP、MuSGD、16GiB allocator预算。为限制时间，回调在第8个训练batch后停止；预检关闭plots，与正式训练的报告绘图不同。
- 另一独立进程保留**10GiB模拟显存占用**，同时持续运行现有YOLO26s的960单图预测。
- 训练进程峰值allocated **9.510GiB**，峰值reserved **12.271GiB**；并发进程峰值reserved **10.201GiB**（包含模拟占用）。采样到的整卡最低空闲 **7.599GiB**。
- 并发期间55次单图预测成功，端到端调用中位数约33.8ms、P95约40.3ms；仅此探针场景，不是完整网页/API/VLM响应时间。探针总计约21秒，不据此精确推算完整训练时长。
- 首次预检关闭save后，训练/验证已通过，但库在结束时回读不存在的last.pt报错；修正为保存预检checkpoint后完整通过。失败记录`result.json`与`probe.log`保留；正式配置原本就是save=true。预检权重不用于部署或正式训练初始化。

## 如何为调试留资源

不使用AutoBatch，避免按空闲整卡自动扩大批量。固定batch8的实测缓存峰值距离16GiB预算还有约3.7GiB余量；整卡另外规划至少约12GiB供推理和运行时使用。

16GiB是[PyTorch缓存分配器限制](https://docs.pytorch.org/docs/2.8/generated/torch.cuda.memory.set_per_process_memory_fraction.html)，**不是硬件显存分区、全部CUDA分配上限或GPU算力配额**。达到该限制也会抛OOM，不能把“设了上限”理解成“绝不OOM”；防范依靠固定批量、实测余量与启动检查共同完成。

- 页面、上传、任务状态、CPU/API和单张检测/VLM联调可作为共存目标；LLM/VLM仍走外部API。此次验证的是本地单图推理，不是整套前后端并发验收。
- 高分辨率超分、增强、分割/深度或连续视频检测可能占用更多显存和算力，先与训练错峰；不要承诺所有视觉工具同时运行仍无OOM或无延迟。
- 如果后续实测资源不足，先停止新增重型调试任务；再经确认降batch至4重新预检，不优先降低960分辨率，不自动放宽显存预算。
- 当前容器CPU配额约25核、RAM上限90GiB；不缓存全部训练图，控制worker和线程。可用`nice -n 10`降低训练CPU调度优先级，但它不限制GPU算力。

## 使用命令

在`/root/autodl-tmp/server`查看实际参数，不加载模型或占用GPU：

```bash
.venv/bin/python yolov26/code/train_detect.py \
  --profile yolov26/configs/merged_five_yolo26m_5090.yaml --dry-run
```

**下面的命令仅在用户确认正式训练后执行：**

```bash
nice -n 10 .venv/bin/python -u yolov26/code/train_detect.py \
  --profile yolov26/configs/merged_five_yolo26m_5090.yaml
```

profile中的model/data/project相对仓库根目录解析；不能同时传旧CLI的batch/model等覆盖参数。
必须通过该profile入口启动，直接使用`yolo train`或旧CLI不会应用这里的资源限制。
此轮只保存配置及本地交接，未推送GitHub。
