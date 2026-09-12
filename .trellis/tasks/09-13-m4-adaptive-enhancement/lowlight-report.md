# 低照度模型适配与 Sewer2403 小样本对照

日期：2026-09-13。结论只用于本任务的模型接入选择，不代表公开基准复现，也不代表
管网缺陷准确率提升。

## 在线选择

在线 pin 采用 `darkir-384` / `darkir-384-hf-8a73ebf2.pt`（官方 DarkIR-m，width
32，3,321,638 参数）。理由是它明确针对低照度下同时出现的照明、噪声和模糊恢复，
比只做低照度增强的候选更贴近 CCTV 场景；在本机 640×640 稳态推理也更快。此选择仍
须受 Agent 的明确退化证据和后端预算约束，普通或已经明亮的图不应执行。

HVI-CIDNet 保留为离线比较实现，不加入默认在线 registry。当前小样本没有检测或人工
标注复核，不能据此声称 DarkIR 提高识别率。

## 来源、权重与许可证

- DarkIR 代码：[官方 cidautai/DarkIR](https://github.com/cidautai/DarkIR)，revision
  `0910291157ad04f210481a002cf6bb480f6a7f18`，MIT。权重来自官方 Hugging Face
  `Cidaut/DarkIR` revision `8a73ebf271c513dfda9d1df444762d8aa0b42138` 的
  `DarkIR_384.pt`，本地名 `darkir-384-hf-8a73ebf2.pt`，13,397,397 bytes；模型卡
  标注 CC-BY-4.0。checkpoint 必须含 `params`，343 个 tensor，以 `strict=True`
  加载。
- HVI-CIDNet 代码：[官方 Fediory/HVI-CIDNet](https://github.com/Fediory/HVI-CIDNet)，revision
  `eb43d7d91e9a336c66856824ff9e4603ae41f408`，MIT。权重来自官方 Hugging Face
  `Fediory/HVI-CIDNet-Generalization` revision
  `51481ef2546f870060c43eb6d6525399f5b3d2d3` 的 `pytorch_model.bin`，本地名
  `hvi-cidnet-generalization-51481ef2.bin`，7,972,041 bytes；模型卡标注 MIT。
  checkpoint 为 191 个 tensor 的直接 state dict，以 `strict=True` 加载。
- 两份代码均以最小推理副本放在 `_enhancement_vendor`；来源、revision 和完整许可证
  记录在 `LICENSES.md`。没有加入新包，没有 import-time 下载。HVI 使用作者推荐的
  generalization 权重，`gamma=1.0`、`alpha=1.0`，没有 GT mean 调整。

## 对照方法

GPU 为 NVIDIA GeForce RTX 3080 Ti 12 GB，PyTorch 2.8.0+cu128。使用同一组六张
既有 Sewer2403 原图，不合成暗化：

1. `sample-0025/original.jpg`（640×640，所选集合中平均亮度最低）
2. `sample-0005/original.jpg`（640×640，有黑边和远处暗区）
3. `sample-0010/original.jpg`（1920×1080，管道中心暗区）
4. `sample-0030/original.jpg`（640×640，混合强光/暗区且颜色已经饱和）
5. `sample-0009/original.jpg`（640×640，遮挡和局部暗区）
6. `sample-0016/original.jpg`（1920×1080，远处暗区但近处已明亮）

输入、两种输出、逐图 metadata、总 manifest 和静态 gallery 位于
`var/evaluations/2026-09-13-adaptive-enhancement/lowlight-models/`。原图和输出由代理做了
可视检查，尚未经用户人工验收。每个输出保持 RGB、原始空间尺寸和有限像素。

## 运行结果

| 分辨率 | DarkIR 延时 | DarkIR peak allocated | HVI 延时 | HVI peak allocated |
|---|---:|---:|---:|---:|
| 640×640 首次调用 | 1247.1 ms | 730.6 MiB | 1189.0 ms | 517.1 MiB |
| 640×640 后续三次 | 47.6–47.8 ms | 739.5 MiB | 90.5–95.9 ms | 517.1 MiB |
| 1920×1080 两次 | 245.5–722.9 ms | 3625.4–3625.7 MiB | 443.4–446.5 ms | 2498.3 MiB |

首次调用包含 CUDA/cuDNN 初始化，不能当作稳态延时。peak 是同一进程中两模型均已
驻留时的 `torch.cuda.max_memory_allocated`，不是独占显存或并发容量保证。两模型在
12 GB GPU 上均完成全分辨率离线对照；在线 adapter 仍遵守现有 1,048,576 输入像素
上限，因此 1080p 在线请求需要明确 ROI，不能悄悄缩放整图。

代理可视对照中，最暗的 `sample-0025` 两模型都提升了暗部可见性；DarkIR 保留了较强的
局部纹理和中心暗区层次。已经偏亮或照明不均的样本会出现明显副作用：两者在
`sample-0010`/`sample-0016` 都可能把明部推得过亮，DarkIR 在已饱和的
`sample-0030` 进一步放大颜色。这支持“只在明确影响判读的低照度证据下运行并辅助
复核”的产品边界，不支持按全图平均亮度自动触发。

## 验证与限制

- `test_lowlight_models.py` 覆盖 registry pin、懒加载、严格 key、奇数尺寸、RGB、有限
  输出和 unload/reload；DarkIR 与 HVI 的实际 checkpoint 均已严格加载。
- 用同一真实权重和固定随机输入逐像素比较 vendored 与对应官方 revision：DarkIR 和
  HVI-CIDNet 的最大绝对差均为 `0.0`；HVI 只把官方 `eval_hf.py` 的 8 倍数 padding/
  crop 移入 `forward`，以保证任意输入尺寸不变。
- 实际 `LocalEnhancementModel("low_light", ...)` 在 `sample-0025` 上完成 GPU 推理，
  返回 RGB 640×640，并在 `unload()` 后清空模型引用。
- 定向回归：低照度与既有增强相关 48 tests passed；相关 Ruff 检查通过。
- 小样本没有成对正常曝光 GT、缺陷级人工判读或下游 detector/VLM 复算。画面增亮、
  无参考指标或主观纹理变化均不等于缺陷准确率提升。DarkIR 的颜色增强在某些摄像机
  源上较激进，所有新增或冲突证据必须保持待审，原图证据不可覆盖。
