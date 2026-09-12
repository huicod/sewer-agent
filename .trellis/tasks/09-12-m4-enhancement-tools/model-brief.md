# 模型适配器独立任务

读本文件和 design.md；root 同时负责契约、runner、quality gate、图库，文件范围不重叠。

## 唯一交付与接口

新增 `backend/src/pipe_inspector/vision/enhancement_models.py`，接口：

```python
LocalEnhancementModel(kind: str, checkpoint: Path, device: str = "cuda:0")
# kind is dehaze / deblur / super_resolution (StrEnum also accepted)
# attributes: kind, model_id: str, model_version: str (checkpoint.name), scale: int (1/1/4)
enhance_image(image: PIL.Image.Image) -> PIL.Image.Image  # RGB, no source mutation
unload() -> None  # release GPU model reference/cache
```

只读取本地权重，懒加载，torch.load(weights_only=True, map_location='cpu')，strict state dict，不在推理中下载/安装。缺文件、错权重、非法图像/输出或不可恢复资源错误明确失败，不返回假锐化/插值。输入/输出 [0,1] 有限值，模型结果转 RGB PIL；不用半精度冒险改默认。单次输入上限 1,048,576 像素；SR 使用明确的小 ROI（上游 runner 强制），tile 控制显存。返回尺寸严格为输入×scale。

## 已查证第一方实现

- DehazeFormer-MCT：作者官方 Space IDKiro/DehazeFormer_Demo，https://huggingface.co/spaces/IDKiro/DehazeFormer_Demo/raw/main/models/dehazeformer.py 及 models/__init__.py 的 MCT 别名；权重 https://huggingface.co/spaces/IDKiro/DehazeFormer_Demo/resolve/main/saved_models/dehazeformer.pth?download=true （5.93MB），键 state_dict。RGB [0,1]→[-1,1]，MCT basenet resize256，输出[-1,1]。MIT。get_coord 原实现CPU linspace需要 x.device/x.dtype 修正；全局曲线语义不可外部切块，FP32。
- Restormer motion：https://github.com/swz30/Restormer/releases/download/v1.0/motion_deblurring.pth （104.7MB），键 params；官方代码 https://raw.githubusercontent.com/swz30/Restormer/68dc6ac472db26f16361150cb7a96a1bc87da93f/basicsr/models/archs/restormer_arch.py 。默认3→3 dim48 blocks[4,6,6,8],refine4,heads[1,2,4,8],ffn2.66,biasFalse,WithBias,dual_pixelFalse。MIT。RGB[0,1] reflect pad multiple8，极小图片反射不足需合法padding；tile512 overlap32，crop回原尺寸。唯一新增可选依赖 einops==0.8.1，先核实PyPI第一方可用再安装到项目.venv --no-deps，不改变torch/CUDA。
- Real-ESRGAN realesr-general-x4v3：https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth （4.89MB），优先params_ema否则params；官方63行SRVGG https://raw.githubusercontent.com/xinntao/Real-ESRGAN/a4abfb2979a7bbff3f69f58f58ae324608821e27/realesrgan/archs/srvgg_arch.py ，3→3 feat64 conv32 upscale4 PReLU。移除BasicSR registry import/decorator，保留BSD-3-Clause版权。tile256+pad10裁剪上下文，RGB[0,1]输出x4。生成纹理不是缺陷/量测真值。

允许 vendoring 到 `vision/_enhancement_vendor/`（原作者许可与来源必须随代码）；使用 apply_patch 创建/修改源文件，不构建BasicSR框架。不创建哈希体系。可下载上述公开权重到 `var/models/enhancement/`，不存在才创建，不覆盖已有文件。仅本任务三个模型，不接触其他模型或.env。

## 验证/交付

独占写入：上述adapter、vendor目录、`backend/tests/test_enhancement_models.py`、本任务 `model-report.md`；可操作 `.venv` 的唯一 einops 安装和忽略入库的 `var/models/enhancement/`，模型冒烟图片/JSON只放 `var/evaluations/2026-09-12-enhancement-model-smoke/`。其余文件由root所有，特别不要改pyproject、契约、runner、workflows和状态文档。

先写并运行失败的边界/加载/shape测试再实现。可以小torch模型替身测适配器生命周期，但三个真实权重必须各跑至少一张原CCTV小图/ROI（用 var/evaluations/2026-09-11-confidence/paired/gallery/sample-0007/original.jpg），保存输出及设备/耗时/尺寸。模型间串行卸载，root在你结束前不跑GPU。官方源代码按固定revision记录，额外需要的patch逐项说明。跑所负责测试和Ruff并自审。不要提交、推送、嵌套委派、启动服务或调用VLM。

model-report.md 记录来源/许可、真实权重路径、必要补丁、红绿测试/真实GPU结果、失败和未知项；返回简短状态和路径。一次独立审查由root统一安排。
