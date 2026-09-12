# 设计：离线对照，不改变在线链路

复用既有检测器、增强接口和 `evaluation.review` 匹配规则。新候选只由独立离线 factory 创建，在线 `ENHANCEMENT_MODELS` 与 run pins 不变。按工具建立同输入比较组，模型串行加载/卸载，随后统一执行 YOLO，避免同时驻留重模型。

`vision/restoration_candidates.py` 提供 `make_restoration_model(model_id, checkpoint, device)`，返回现有 EnhancementAdapter 形状（kind/model_id/model_version/scale/enhance_image/unload）；有限白名单，不接受动态代码模块。所需官方推理代码仅引入必要部分，记录许可证，严格权重加载。

`evaluation/restoration.py` 读取显式小样本 JSON（image、sample_id、roi、ground_truth、reason）和模型 JSON（model_id、kind、checkpoint），最多 12 张。保留原文件副本；去雾/去模糊使用共同的等比例最长边 960 分析图，ROI 超分使用原图上固定整数裁剪且不合成退化。保存完整原图与分析图尺寸/映射，检测坐标回映原图。超分的原始裁剪与 bicubic ×4 是控制，不作模型输出。GT 局部裁剪不进入全图 P/R 汇总，检测一致性仅是辅助指标。

图册逐图展示原图、控制、模型输出、检测框、缺陷局部细节和失败；原始 JSON 可下载，所有输出在新的 `var/evaluations/2026-09-13-restoration-candidates/` 子目录，不覆盖前次结果。检测类别/分数/IoU 对照与视觉观察分开，原证据不被删除。本轮离线工具试验不产生产品任务、人工审核记录或 API 费用。
