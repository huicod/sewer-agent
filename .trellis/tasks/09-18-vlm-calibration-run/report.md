# VLM 校准验证报告

本页保留原框级评测（同类、IoU ≥ 0.5）。2026-09-23 新增
[图像级多标签复算与口径对照](image_level_report.md)，使用相同缓存与标注，
不替代本页结果，也不表示模型能力提升。

## 范围与协议

- Cohort: 60 张 group-disjoint validation images（sewer2403 37 张、SEDD 23 张）。
  该比例与完整验证集不一致，因此只作为校准诊断，不外推为最终测试集准确率。
- YOLO operating point: 已保存预测中只保留 `score >= 0.25`，避免把 mAP 的
  `conf=.001` 导出误当作线上阈值。
- VLM: Qwen API，每张图 1 次 guided review；失败样本安全回退到 YOLO。
- Fusion: 当前生产保守策略保留原始 YOLO 框；VLM-only additions 仅离线投影。
- 独立二次确认: 对第一轮产生的 23 个实际新增框各发起一次新的 guided request，
  只接受 `accept + original_presence=present + evidence_strength=clear`，不递归接收
  第二次请求中的新增异常。

## 结果

| source | view | P | R | F1 | TP / FP / FN |
|---|---|---:|---:|---:|---:|
| sewer2403 | YOLO / conservative | 65.52% | 62.30% | 63.87% | 38 / 20 / 23 |
| sewer2403 | 单次 VLM additions | 51.32% | 63.93% | 56.93% | 39 / 37 / 22 |
| sewer2403 | 独立二次确认 additions | 55.07% | 62.30% | 58.46% | 38 / 31 / 23 |
| SEDD | YOLO / conservative | 63.27% | 65.96% | 64.58% | 31 / 18 / 16 |
| SEDD | 单次 VLM additions | 57.41% | 65.96% | 61.39% | 31 / 23 / 16 |
| SEDD | 独立二次确认 additions | 59.62% | 65.96% | 62.63% | 31 / 21 / 16 |
| mixed（仅本 cohort） | YOLO / conservative | 64.49% | 63.89% | 64.19% | 69 / 38 / 39 |
| mixed（仅本 cohort） | 单次 VLM additions | 53.85% | 64.81% | 58.82% | 70 / 60 / 38 |
| mixed（仅本 cohort） | 独立二次确认 additions | 57.02% | 63.89% | 60.26% | 69 / 52 / 39 |

单次 additions 仅找回 1 个 raw 漏报，却增加 22 个 FP；独立二次确认筛掉了一部分
误报，但仍未找回新的 TP，并比保守基线多 14 个 FP。三种 view 都没有同时提高精确率和召回率，
因此没有将 additions 推入线上融合。

## 按类别观察（mixed cohort）

| 类别 | YOLO TP/FP/FN | 单次 additions TP/FP/FN | 二次确认 TP/FP/FN |
|---|---:|---:|---:|
| AJ | 10 / 2 / 2 | 10 / 3 / 2 | 10 / 3 / 2 |
| BX | 8 / 1 / 4 | 8 / 1 / 4 | 8 / 1 / 4 |
| FS | 5 / 5 / 8 | 6 / 7 / 7 | 5 / 6 / 8 |
| PL | 37 / 27 / 20 | 37 / 37 / 20 | 37 / 32 / 20 |
| BLOCK | 9 / 3 / 5 | 9 / 12 / 5 | 9 / 10 / 5 |

主要退化来自 PL/BLOCK 的 VLM-only additions；BX 没有新增收益。FS 的单次新增虽多找回
1 个样本，但同时新增 2 个 FP，二次确认后连该 TP 也没有保留。

## 运行完整性与证据

- 第一轮请求：60 次，59 次成功，1 次 `response_schema_invalid`；该样本使用 YOLO 回退。
- 第二轮请求：23 次，21 次成功，2 次 `response_schema_invalid`。
- 每张图/每个新增候选均 checkpoint；状态可从 `state.json` 继续执行。
- 无 Authorization header 或 API key 写入请求证据；图片以 request 目录下的本地 view 文件保存。
- 第一轮可视化结果：
  `var/evaluations/2026-09-18-vlm-calibration/live-60-conf025/gallery/index.html`
- 第一轮汇总：
  `var/evaluations/2026-09-18-vlm-calibration/live-60-conf025/summary.json`
- 独立二次确认汇总：
  `var/evaluations/2026-09-18-vlm-calibration/live-60-conf025-addition-confirmation/summary.json`

## 结论

当前最可靠的线上策略仍是“YOLO 框全部保留 + VLM 复核说明/待人工确认”，而不是自动采纳
VLM-only 新框。若要继续提升召回，应针对真实漏报建立人工确认的小型校准集，并单独训练或
校准“是否存在新增异常”的判别门；不能把本次同一模型自报的 `confirmed` 当作独立证据。
