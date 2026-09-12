# T13-A 设计

已批准 T13 的新工具子系统切片：先接可复用工具，再改在线链路。相比 Grounded SAM 无需额外检测器；相比纯图册脚本，复用资产契约以供后续 Activities 使用。

`contracts/geometry.py` 定义显式 ImageRegion 框请求、掩膜/深度结果与像素相对量。可信调用者提供 AssetRecord，工具校验 ref、kind(image/frame)、尺寸与 4,194,304 像素上限。掩膜允许延伸出提示框，不以裁剪制造精度；空掩膜失败，质量分数不是缺陷正确率。

`vision/geometry.py` 提供 `MaskPrediction(mask: np.ndarray, score: float)`、SegmenterAdapter、RelativeDepthAdapter；`run_segmentation(request, source, media, adapter)`、`run_relative_depth(request, source, media, adapter)`、`measure_geometry(segmentation, media, depth=None)`。适配器返回原图 H×W bool 掩膜或 float32 深度，后者声明 larger_is=nearer/farther。`vision/geometry_models.py` 提供 LocalSegmenter/LocalRelativeDepth(checkpoint, device)，精确官方选型写 research/models.md。

分割保存二值 PNG mask 和原图叠加 PNG。深度保存 float32 NPY（禁 pickle）与单图归一化灰度 PNG，AssetKind.DEPTH_MAP；白近黑远。沿用 MediaStore UUID/version/source 新路径，不扩展上传格式。多个派生资产由后续调用者纳入数据库事务，本轮不宣称恢复已接通。

量化按真实掩膜计数：area_pixels、bbox 像素宽高、image_plane_fraction（分母为整幅图像），可选深度 q10/median/q90。该占比不等于横截面堵塞率；相对深度是任意单位，不能跨图比较。不同原资产/尺寸拒绝混算，增强图禁止量测。

`evaluation/geometry.py` 小样本 CLI 读取显式 manifest/模型路径，写新目录：原图、提示框、掩膜/叠加、深度、量化、失败、时间/显存和 HTML。模型串行，原实验不覆盖，不评估无真值的分割/深度准确率。
