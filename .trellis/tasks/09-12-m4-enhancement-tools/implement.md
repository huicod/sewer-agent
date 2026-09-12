# T12-A Implementation Plan

Trellis 控制任务状态；按项目已批准计划直接实施，不创建第二套 ledger。基线 62783b47；root 使用现有隔离文件范围和 jj change nnvxzzxz。

1. Root：`contracts/enhancement.py` 提供 EnhancementKind、EnhancementRequest（AssetRef、kind、可选 ImageRegion）及 EnhancementResult（派生 AssetRecord、实际原图 ROI、模型信息、前后质量）。超分无 ROI 拒绝；输出映射用实际裁剪框与输出尺寸，不猜缩放。
2. Root：`agent/enhancement_policy.py` 的 choose_enhancement(quality, requested=None, roi=None) 只在显式请求允许执行，自动路径返回建议/跳过原因；quality.py 仅将类型注解扩为支持内存图片流，不改指标或阈值。`vision/enhancement.py` 的 run_enhancement(request, media, adapter) 保留源资产，限制像素，写新派生文件，校验 RGB/尺寸，返回统一结果。
3. 独立模型范围：`vision/enhancement_models.py` 的 LocalEnhancementModel(kind, checkpoint, device) 提供 model_id/model_version/scale、enhance_image(PIL.Image)->PIL.Image 与 unload()；仅必要官方 architecture vendoring，保留许可来源。不训练、不在推理中下载、不重装 Torch/CUDA。
4. Root：`evaluation/enhancement.py` 或同任务小脚本调用上述真实接口，输出原图/增强图、JSON、简单 HTML 对照；固定少量 Sewer2403 图。失败不换假算法。每模型分别记录成本和 GPU 运行结果。
5. 先写契约、门控、ROI映射/输出校验/不覆盖原图测试，跑红；实现最小代码后跑绿。模型范围用小张量网络/权重测试与真实权重冒烟；最后全量 `pytest backend/tests -q --tb=short`、Ruff、主代理 diff 与一次独立审查。更新 report、journal 和唯一 roadmap 的 T12-A 状态。

T12-B 后续接入消费：请求按白名单选 kind；执行器调用同一 runner，将结果资产和调用记录提交检查点；原图 VLM 结论不由增强图自动覆盖。该在线路径不是本切片验收项。
