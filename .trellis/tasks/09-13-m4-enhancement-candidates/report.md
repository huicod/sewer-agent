# T12-D 交接：增强候选与缺陷保真对照

2026-09-13；huicod；基线 `ee158b99`，jj change `lmuslmlo`。本轮只做候选适配和离线实图比较，不改线上模型、Agent 触发逻辑、原 VLM Prompt/融合或检测器。

## 结论与实际图片

暂不替换现有 Restormer / DehazeFormer-MCT / RealESR-general-x4v3。Concertormer 没有显示明确的缺陷级优势；ConvIR 在暗光反例上严重失真；HAT 没有改善本组局部检测保留。三者仅保留离线入口，不加入在线模型白名单。低照度 DarkIR 及用户 opt-in 选择策略沿用 T12-C。

- [图册总入口](../../../var/evaluations/2026-09-13-restoration-candidates/index.html)：结论及重点反例。
- [全部 8 图逐模型对照](../../../var/evaluations/2026-09-13-restoration-candidates/paired/index.html)：全图、对齐局部、原尺寸/增强输出、真实检测框。
- [完整结果 JSON](../../../var/evaluations/2026-09-13-restoration-candidates/paired/results.json)：原路径、尺寸/ROI 映射、模型版本、逐框分数、时间、显存和状态。每张图还有单独检测 JSON/PNG，可点击查看原尺寸。

## 比较设置与边界

固定 Sewer2403 历史原图 0003、0007、0011、0015、0017、0022、0025、0033，覆盖五类、既有 BX/AJ 案例及字幕/反光。来源、原始标注和选择理由在 [samples.json](samples.json)，推理前已冻结；不使用 SEDD、不合成模糊/雾、不补造标注。0033 没有五类标注，不等于正常。含暗光等错误工具控制，六模型全部强制离线运行，不是 Agent 自动选型实验。

去模糊/去雾使用共同的最长边不超过 960 的等比例分析图；1080p 先统一 LANCZOS 缩小，原文件另存，不拿缩小后的结果直接对比旧实验原尺寸结果。超分使用原图固定 320×320 ROI，输出 1280×1280；另有原始 ROI 和 bicubic ×4 控制。全图和 ROI 是不同任务，不能混算准确率。

所有控制/输出均用同一已有 YOLO26 权重，imgsz=960、conf=0.25；框回映原图，类别一致且 IoU≥0.5 才算保留，基线高分界限为 0.85。检测框保留/丢失/新增只是模型一致性，不是真值判断，也不以分数上升认定恢复成功。裁剪本身可能改变类别/框，不能归因超分模型。

## 实测结果

8 图 × 6 模型共 **48 次增强推理、72 次检测**，执行失败、卸载失败均为 0；无 Qwen API。本轮真实输出中有视觉失败，不能因程序成功就判定模型有效。

| 工具 / 模型 | 对控制图的框保留 | 基线高分框保留 | 单图中位耗时 | 峰值 allocated 显存 |
|---|---:|---:|---:|---:|
| 去模糊 / Restormer（现有） | 9/9 | 4/4 | 0.962 s | 2.364 GiB |
| 去模糊 / Concertormer RealBlur-J | 9/9 | 4/4 | 1.607 s | 4.635 GiB |
| 去雾 / DehazeFormer-MCT（现有） | 8/9 | 4/4 | 0.272 s | 0.214 GiB |
| 去雾 / ConvIR-L Haze4K | 7/9 | 3/4 | 0.407 s | 0.881 GiB |
| ROI 超分 / RealESR-general-x4v3（现有） | 4/5 | 1/1 | 0.708 s | 0.085 GiB |
| ROI 超分 / HAT-L x4 ImageNet | 3/5 | 1/1 | 2.978 s | 3.966 GiB |

显存为逐模型串行运行时 Torch 峰值分配，不是 nvidia-smi 整机占用；FP32。耗时包含 PNG 解码、增强、输出校验、PNG 编码及 CUDA 同步，首张还含惰性权重加载，不是纯前向或端到端 Agent 延迟。48 次增强此口径合计 57.812 秒；模型准备、YOLO 和人工检查不含在内。

仅按已有不完整全图标注：控制/Restormer/Concertormer/DehazeFormer 均为 TP=7、FP=2、FN=0（P=77.78%，R=100%）；ConvIR 为 TP=6、FP=1、FN=1（P=85.71%，R=85.71%）。这里“FP”仅指未匹配原标注，不是人工确认误报；ConvIR 少报不能当作有效精度增益。ROI 不计算全图 P/R。数据可能训练重叠，且仅 8 张有目的选样，不能宣称泛化性能或 VLM 复核准确率变化。

## 重点逐图观察（Agent 目视，非用户验收）

| 样本 | 可核查的变化 | 判断 |
|---|---|---|
| [0025 暗光 BLOCK](../../../var/evaluations/2026-09-13-restoration-candidates/paired/sample-0025/index.html) | 控制 BLOCK=0.895；ConvIR 输出出现大面积黑白饱和和彩色失真，检测为零；现有去雾仍保留 BLOCK=0.889 | 明确的错误工具反例，不应把暗光当成雾；不接入该候选 |
| [0003 细 PL](../../../var/evaluations/2026-09-13-restoration-candidates/paired/sample-0003/index.html) | 全图各去模糊/去雾结果仍检出 PL；原 ROI=0.489，bicubic=0.473，RealESR=0.519，HAT 无框。HAT 仍可见裂纹样线条，并非证明裂纹被擦除；RealESR 明显平滑纹理/锐化边缘 | 保留框是代理指标；未证实 HAT 有效，也不能将 RealESR 的平滑细节视为真实恢复 |
| [0011 BX](../../../var/evaluations/2026-09-13-restoration-candidates/paired/sample-0011/index.html) | 控制 BX=0.874，Restormer=0.840，Concertormer=0.809；全图形变轮廓和类别仍保留 | 基线高分框“保留”不要求增强后仍≥0.85；更锐利不等于更可靠 |
| [0022 AJ](../../../var/evaluations/2026-09-13-restoration-candidates/paired/sample-0022/index.html) | 全图 AJ 约 0.94，各恢复模型均保留；ROI 原始 0.889、bicubic 0.888、RealESR 0.861、HAT 0.870；开口轮廓仍可见 | 没有新增缺陷证据，超分不能替代原图判断 |
| [0033 模糊/反光](../../../var/evaluations/2026-09-13-restoration-candidates/paired/sample-0033/index.html) | Restormer 与 Concertormer 的洞口/水面仍明显模糊，后者局部纹理/色调有变化；全图均无五类框 | 没有看见明确的恢复或补漏收益；无五类框不等于正常 |

针对 0025 异常另做一次定点诊断：相同官方权重和未改写官方 ConvIR 代码同样产生失真，原始输出约 [-5848.62, 9525.22]，99.258% 数值超出 [0,1]。两实现原始输出最大差 0.013672，钳位编码后与已保存图最大像素差 1。故不是本地 PNG/坐标适配造成；失真的更深层原因未在本轮定位，不把“可能域不匹配”写成已证实原因。该诊断额外做两次单图 GPU 前向，不计入固定 48 次对照。

## 工程交接

新增独立 `vision/restoration_candidates.py` 和 `evaluation/restoration.py`，复用已有 RGB、尺寸限制、卸载接口和检测器。候选本地白名单、权重严格加载、无运行时下载；官方来源/版本/许可证见 [模型研究](research/models.md) 与 [Concertormer 记录](concertormer-report.md)。三个候选权重在 `var/models/enhancement/`；媒体/权重不入库，未安装新依赖。上游代码许可记录已保留，权重再分发许可尚未明确，不随项目打包分发。

复现需使用不存在的新输出目录（防止覆盖原实验）：

```bash
.venv/bin/python -m pipe_inspector.evaluation.restoration \
  --manifest .trellis/tasks/09-13-m4-enhancement-candidates/samples.json \
  --models .trellis/tasks/09-13-m4-enhancement-candidates/models.json \
  --output var/evaluations/restoration-new-run \
  --detector-checkpoint yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt \
  --device cuda:0
```

一次独立审查指出框保留的贪心匹配歧义、缺少对齐局部图及检测器卸载失败未汇总，按 TDD 修复；仅从已保存结果重建派生图册/匹配统计，不重跑 GPU。关于 HAT padding 的建议核对后未采纳：本版本官方 HATModel 明确使用 reflect，与适配器一致。

最终验证：`.venv/bin/pytest -q backend/tests --tb=short` **866 passed / 116 skipped，20.80 秒**；跳过项需要显式服务/API/模型条件。`.venv/bin/ruff check backend scripts`、`git diff --check` 通过；10 个 HTML 页面本地图片/链接均存在，40 张对齐局部图已生成。前端/线上工作流本轮未改，不重复付费 API、服务故障注入或前端验收。不做模型/输入哈希、训练、无限选型、开放补检恢复或 GitHub 推送。stop-that-shit 将本轮限制为三组离线比较，Superpowers 的测试/审查用于修复证据呈现问题；Trellis/jj 保存恢复点。

下一步回到唯一 [总体计划](../../../project_management/DEVELOPMENT_PLAN.md) 的 **T13：提示式分割与相对深度**。图册可供用户继续审阅，但无需先选出新增强模型才能推进。第一版 Agent 仍未全部完成。
