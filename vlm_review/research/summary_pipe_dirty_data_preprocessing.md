# 雨污管道脏数据合成与图像预处理方案

## 1. 结论先行

针对雨污管道内的低光、雾气、水汽、运动模糊、反光、水渍和局部泥污遮挡，当前阶段最推荐的主路线不是“推理前统一修图”，而是：

```text
干净训练集
  -> 通过图像预处理与数据增强合成脏数据
  -> clean + dirty 混合训练
  -> 单独构建 dirty_val 做鲁棒性评估
  -> 真实推理阶段先用原图直接推理
```

这样做的目标不是让图像更好看，而是让检测模型在训练阶段就见过部署时会遇到的退化分布。

需要同时明确边界：

- **可恢复退化**：低光、局部过暗、雾气、水汽、噪声、轻中度模糊。这些适合通过合成增强纳入训练。
- **信息缺失**：积水完全覆盖、淤泥完全覆盖、强遮挡。这些不能靠预处理恢复，应在训练和评估中单独建模为低质量或不可判读情形。

## 2. 为什么优先做合成脏数据

现实问题本质上是训练分布和部署分布不一致。当前训练集如果主要是干净图像，模型就容易把“清晰、正常曝光、低遮挡”当成默认世界；一旦进入真实管道视频，Recall 往往会先掉。

图像增强在这里的作用，不只是扩大样本量，而是主动把部署时可能出现的退化条件注入训练集，让模型学习在这些扰动下仍保持判别能力。Albumentations 论文把数据增强定义为利用保持标签语义的输入变换来增加训练集多样性；ImageNet-C 和 AugMix 则说明，围绕常见退化做训练和评估，是提升模型抗腐蚀能力的有效路线。  
参考：

- [Albumentations: Fast and Flexible Image Augmentations](https://www.mdpi.com/2078-2489/11/2/125)
- [Benchmarking Neural Network Robustness to Common Corruptions and Perturbations](https://openreview.net/forum?id=HJz6tiCqYm)
- [AugMix: A Simple Data Processing Method to Improve Robustness and Uncertainty](https://openreview.net/forum?id=S1gmrxHFvB)

## 3. 合成脏数据时应覆盖哪些退化

### 3.1 推荐优先级

| 优先级 | 退化类型 | 原因 |
|---|---|---|
| P0 | 低光、局部暗区 | 管道内部高频出现，且会直接压低纹理对比 |
| P0 | 运动模糊、轻度失焦 | 机器人移动、镜头抖动时常见，直接伤害裂缝和细边界 |
| P0 | 水渍、轻度反光、局部泥污遮挡 | 最贴近现场，决定模型能否适应“脏画面” |
| P1 | 雾气、水汽、镜头轻微污渍 | 对比度下降明显，适合加入鲁棒性训练 |
| P1 | 传感器噪声、压缩失真 | 低照度视频和传输链路常见 |
| P2 | 严重积水覆盖、严重淤泥覆盖 | 应更多用于质量门控和不可判读建模，不宜简单沿用原标签训练 |

### 3.2 各类退化的合成方式

| 退化类型 | 推荐实现 | 是否保持原标签 | 说明 |
|---|---|---|---|
| 低光 | `RandomGamma`、`RandomBrightnessContrast` | 是 | 先做最基本的亮度分布扰动 |
| 局部暗区 | `RandomShadow` 或自定义暗区 mask | 是 | 适合模拟管壁局部照明不均 |
| 运动模糊 | `MotionBlur` | 是 | 用于模拟机器人运动、镜头抖动 |
| 失焦模糊 | `Defocus`、`GaussianBlur` | 是 | 适合模拟对焦不准、镜头偏脏 |
| 雾气、水汽 | `RandomFog`、`AtmosphericFog` | 是 | 用于模拟整体对比度下降 |
| 轻度反光 | 高亮椭圆/条带 mask + alpha 混合 | 大多是 | 需控制范围，避免把缺陷完全盖住 |
| 水渍、镜头污渍 | `Spatter` 或自定义半透明污渍 mask | 大多是 | 更像前景污染，而不是普通天气 |
| 局部泥污遮挡 | 自定义不规则 blob mask + 纹理叠加 | 视遮挡程度而定 | 局部遮挡可保留标签，严重遮挡应改为低质量样本 |
| 噪声 | `GaussNoise`、ISO noise | 是 | 建议轻量使用，避免把训练集做得过脏 |
| 压缩失真 | `ImageCompression` | 是 | 模拟视频编码和链路损伤 |

Albumentations 官方文档已经提供 `MotionBlur`、`Defocus`、`CLAHE`、`RandomFog`、`Spatter`、`RandomRain` 等大量现成算子；如果需要模拟更贴近管道的泥污、水膜、镜头污染，可基于其自定义 transform 机制自己写 `ImageOnlyTransform`。  
参考：

- [Albumentations transform overview](https://albumentations.ai/docs/2-core-concepts/transforms)
- [Albumentations blur transforms](https://albumentations.ai/docs/api-reference/albumentations/augmentations/blur/transforms/)
- [Albumentations weather transforms](https://albumentations.ai/docs/api-reference/albumentations/augmentations/pixel/weather/)
- [Albumentations CLAHE documentation](https://albumentations.ai/docs/api-reference/albumentations/augmentations/pixel/color_basic/)
- [Creating custom Albumentations transforms](https://albumentations.ai/docs/4-advanced-guides/creating-custom-transforms/)

## 4. 标签策略：哪些脏图还能沿用原标签

### 4.1 可以直接沿用原标签的情况

以下属于像素级退化，不改变几何位置，原 YOLO 标签可直接复用：

- Gamma、亮度、对比度变化；
- Gaussian / Defocus / Motion blur；
- 雾气、水汽；
- 轻度噪声；
- 轻度压缩失真；
- 没有遮住缺陷主体的轻度反光和污渍。

### 4.2 需要谨慎的情况

对“遮挡类增强”不能机械地保留标签：

- 如果缺陷仍有主体可见，且人眼还能判断，标签可以保留；
- 如果缺陷大面积被覆盖，保留原标签会训练模型“看不见也要猜”，这会污染监督信号；
- 严重遮挡图更适合进入 `unreadable` / `low_quality` 辅助任务，或者只放入质量评估集，不直接作为正样本训练。

对当前项目，建议暂定规则：

```text
可见面积 >= 50%：保留标签
可见面积 20%~50%：人工抽检后决定
可见面积 < 20%：不作为普通检测正样本，转入低质量/不可判读集合
```

## 5. 离线合成与在线增强怎么配合

### 5.1 离线合成：用于可复现数据集和验证集

离线生成建议承担两件事：

1. 构造固定版本的 `dirty_train`；
2. 构造固定版本的 `dirty_val`。

推荐目录：

```text
runs_data/
  sedd_yolo/
  sedd_yolo_dirty/
    images/
      train/
      val/
    labels/
      train/
      val/
    metadata/
      dirty_manifest.csv
```

`dirty_manifest.csv` 至少记录：

```text
source_image
generated_image
degradation_type
severity
seed
label_policy
```

这样做的好处：

- 能人工抽查脏图质量；
- 能稳定复现实验；
- 能分退化类型统计 `mAP / Recall / F1`；
- 能追踪哪种合成方式真正有用。

### 5.2 在线增强：用于训练时增加多样性

在线增强适合放在训练 dataloader 里，每个 epoch 随机采样不同参数，继续扩大扰动覆盖面。它更适合：

- 亮度、对比度、Gamma；
- 轻度 blur；
- 噪声；
- 轻度压缩；
- 少量随机遮挡。

推荐关系：

```text
离线 dirty_val：固定，用于评估
离线 dirty_train：固定，用于可审计扩容
在线 augmentation：随机，用于训练期继续扩展分布
```

## 6. 推荐的脏数据生成配方

### 6.1 起步版配方

先不要做得太复杂，第一版建议只覆盖 6 类：

```text
low_light
motion_blur
defocus_blur
fog_haze
water_reflection
mud_occlusion_light
```

建议比例：

```text
clean: 60%~70%
dirty: 30%~40%
```

每张原图最多生成 1~2 张脏图，先防止数据集被极端脏图主导。

### 6.2 参数起点

| 类型 | 建议参数起点 |
|---|---|
| Gamma | `gamma_limit=(60, 120)` |
| Brightness/Contrast | `brightness_limit=0.2, contrast_limit=0.2` |
| Motion blur | `blur_limit=(3, 9)` |
| Defocus | 半径约 `2~5`，按所用版本参数名配置 |
| Fog | `fog_coef_lower=0.15, fog_coef_upper=0.45` 或弱到中等强度 |
| Noise | 轻量参数起步，不要先上强噪声 |
| Water reflection | 高亮 mask alpha `0.15~0.35` |
| Mud occlusion | 遮挡面积先控制在图像面积 `5%~20%` |

这些参数只是起点，最终应通过 `dirty_val` 上的分退化类型指标回调。

## 7. 参考实现思路

### 7.1 现成算子部分

以下代码用于说明组合思路。你们现有仓库 `videopipe_task1_boost/requirements.txt` 使用的是 `albumentations>=1.4`，而新版官方文档已经转向 AlbumentationsX；不同版本之间个别参数名会变化，正式实现时应按实际安装版本调整。你们项目里现有的 `MotionBlur`、`GaussianBlur`、`RandomGamma`、`CoarseDropout` 用法可作为本地风格参考。

```python
import albumentations as A

dirty_pixel_pipeline = A.Compose(
    [
        A.OneOf(
            [
                A.RandomGamma(gamma_limit=(60, 120), p=1.0),
                A.RandomBrightnessContrast(
                    brightness_limit=0.2,
                    contrast_limit=0.2,
                    p=1.0,
                ),
            ],
            p=0.35,
        ),
        A.OneOf(
            [
                A.MotionBlur(blur_limit=(3, 9), p=1.0),
                A.GaussianBlur(blur_limit=(3, 7), p=1.0),
            ],
            p=0.30,
        ),
        A.RandomFog(fog_coef_lower=0.15, fog_coef_upper=0.45, alpha_coef=0.15, p=0.15),
        A.GaussNoise(p=0.15),
        A.ImageCompression(quality_lower=55, quality_upper=95, p=0.15),
    ]
)
```

### 7.2 管道专用自定义部分

建议额外实现 3 个自定义模块：

1. `WaterReflectionOverlay`
   - 生成偏白、偏青灰的高亮椭圆或条带；
   - 重点分布在画面下部或水线附近；
   - alpha 混合，不直接覆盖整块区域。

2. `MudOcclusionOverlay`
   - 生成不规则 blob；
   - 颜色偏棕灰、黄褐或深灰；
   - 可叠加纹理；
   - 要记录遮挡面积，便于后续做标签策略判断。

3. `LensStainOverlay`
   - 半透明污渍、边缘模糊；
   - 可与局部 blur 叠加；
   - 更接近镜头污染而不是场景污染。

这三个模块更贴近雨污管道真实问题，现成天气增强通常只能部分代替，不能完全替代。

## 8. 与 YOLO 自带增强的关系

二者不冲突，但要避免重复加得过重。

YOLO 常见默认增强更偏通用训练扰动，例如：

- HSV；
- Mosaic；
- 缩放、平移、翻转；
- 少量模糊或颜色扰动。

你这里要补的是**场景退化增强**：

- 管道低光；
- 运动模糊；
- 水汽雾化；
- 水面反光；
- 泥污局部遮挡；
- 镜头污渍。

建议策略：

```text
保留 YOLO 的基础几何增强
降低过强的通用颜色增强
额外加入 sewer-specific dirty augmentation
```

第一版可从以下组合起步：

```text
mosaic=0.5~0.7
hsv_s=0.3~0.5
hsv_v=0.3~0.5
额外脏增强按本文方案加入
```

## 9. 数据增强在本任务中的正确用法

### 9.1 它应该解决什么

- 扩大训练分布；
- 减少模型对“干净画面”的依赖；
- 提升对低光、模糊、雾气、轻遮挡的鲁棒性；
- 为后续真实脏样本微调打底。

### 9.2 它不应该被用来做什么

- 不应该用来替代真实脏样本采集；
- 不应该把完全遮挡的样本硬当成普通正样本；
- 不应该为了“画面更花”而无节制叠加退化；
- 不应该只看训练集变大，而不看 `dirty_val` 指标是否提升。

### 9.3 推荐实验设计

至少做这 5 组：

```text
A. clean_train -> clean_val
B. clean_train -> dirty_val
C. clean + dirty_train -> clean_val
D. clean + dirty_train -> dirty_val
E. clean + dirty_train + online augmentation -> dirty_val
```

重点指标：

- `mAP50`
- `mAP50-95`
- `Recall`
- 各类缺陷单独 AP
- 各退化类型下的 Recall
- 严重遮挡样本上的误检率

如果 D/E 对 `dirty_val` 提升明显，同时 C 对 `clean_val` 没有明显下降，说明合成策略是有效的。

## 10. 可直接复用的开源论文与代码

### 10.1 直接用于脏数据合成和鲁棒训练

| 名称 | 作用 | 开源情况 |
|---|---|---|
| Albumentations | 大量现成图像增强算子，适合直接搭 dirty pipeline | 有开源实现；原始 `albumentations` 仓库已在 2025 年归档，当前官方文档转向 `AlbumentationsX`，使用时需注意许可证 |
| ImageNet-C / robustness | 常见退化基准，可参考其 corruption 类型与强度分层 | 官方代码公开 |
| AugMix | 通过组合增强提升鲁棒性，可作为训练策略参考 | 官方代码公开 |

参考：

- [Albumentations paper](https://www.mdpi.com/2078-2489/11/2/125)
- [Albumentations repository](https://github.com/albumentations-team/albumentations)
- [ImageNet-C robustness repository](https://github.com/hendrycks/robustness)
- [AugMix paper](https://openreview.net/forum?id=S1gmrxHFvB)
- [AugMix repository](https://github.com/google-research/augmix)

### 10.2 可作为“推理前增强对照组”的开源恢复模型

这些模型不用于合成脏图，而适合做后续 ablation，验证“先增强再检测”是否真有收益：

| 模型 | 适用问题 | 备注 |
|---|---|---|
| Zero-DCE | 低光增强 | 官方代码公开，但仓库声明仅限非商业用途 |
| Retinexformer | 低光增强 | 官方代码公开 |
| Restormer | 去模糊、去噪、去雨 | 官方代码公开 |
| MPRNet | 去模糊、去雨、去噪 | 官方代码公开 |

参考：

- [Zero-DCE repository](https://github.com/Li-Chongyi/Zero-DCE)
- [Retinexformer repository](https://github.com/caiyuanhao1998/Retinexformer)
- [Restormer repository](https://github.com/swz30/Restormer)
- [MPRNet repository](https://github.com/swz30/MPRNet)

### 10.3 管道领域论文：用于定义问题，不一定有公开代码

| 论文 | 对本方案的价值 |
|---|---|
| [Automatic defogging, deblurring, and real-time segmentation system for sewer pipeline defects](https://www.sciencedirect.com/science/article/pii/S0926580522004654) | 说明去雾、去模糊与下游分割需要联合评估 |
| [No-reference image quality assessment via degraded-content inference for sewer inspection images](https://www.sciencedirect.com/science/article/pii/S0926580525007678) | 说明低质量/不可判读识别本身值得单独建模 |
| [Structure-aware dehazing of sewer inspection images based on monocular depth cues](https://www.sciencedirect.com/science/article/pii/S1093968726003294) | 说明 sewer-specific 退化处理可能优于完全通用方案 |

这些论文的工程启发很强，但多数并没有提供可直接复用的完整代码，因此更适合作为设计依据，而不是实现起点。

## 11. 推荐落地顺序

1. **先实现 dirty dataset generator**
   - 用 Albumentations + 自定义 overlay；
   - 生成 `dirty_train`、`dirty_val` 和 `dirty_manifest.csv`。
2. **先跑 5 组基础 ablation**
   - 确认合成脏数据是否真的提高 `dirty_val` 指标。
3. **再引入真实脏样本**
   - 后续从真实视频中补 200-500 张标注图做微调。
4. **最后再考虑推理前增强**
   - 只有当 `dirty augmentation` 后仍在低光或模糊上明显掉点，才评估 Zero-DCE、Retinexformer、Restormer 等模型。

## 12. 对当前项目的建议版本

当前最适合你们直接推进的是：

```text
阶段 1:
  从现有 SEDD 训练集生成 dirty_train / dirty_val
  退化类型先覆盖:
    - low_light
    - motion_blur
    - defocus_blur
    - fog_haze
    - water_reflection
    - mud_occlusion_light

阶段 2:
  用同一套 YOLOv26 配置训练:
    - clean only
    - clean + dirty

阶段 3:
  分别评估:
    - clean_val
    - dirty_val
    - 每种退化类型单独 Recall / AP
```

只要 `clean + dirty` 在 `dirty_val` 上明显优于 `clean only`，且在 `clean_val` 上没有明显退化，这条路线就值得继续扩大。
