# 实施与验证报告

## 实施内容

- 默认融合策略切换为 `guided-conservative-v1`：YOLO 原始框是召回底线；VLM 的 reject/revise 不再自动删除或改类，新增异常默认进入待人工审核队列。
- 高置信度候选保留原类别、原框和原分数；VLM 可报告同一区域的 `coexisting_labels`，但不覆盖原候选。
- `guided-confidence-v2` 保留为显式历史重放策略，历史变更记录字段保持兼容，避免旧缓存被静默改义。
- 前端、HTML 报告和后端证据契约展示共存建议，同时兼容没有该字段的旧记录。
- 新增 `pipe_inspector.evaluation.guided_replay`，只读取已保存的 review/observation 状态，不调用 VLM。

## 606 张缓存重放

输入：`var/evaluations/2026-09-18-vlm-yolo26m-comparison/yolo26m_guided/state.json`。

| 策略 | TP | FP | FN | Precision | Recall | F1 | provider attempts |
|---|---:|---:|---:|---:|---:|---:|---:|
| 原始 YOLO | 501 | 230 | 260 | 0.6854 | 0.6583 | 0.6716 | — |
| `guided-conservative-v1` | 501 | 230 | 260 | 0.6854 | 0.6583 | 0.6716 | 0 |
| `guided-confidence-v2`（历史） | 477 | 441 | 284 | 0.5196 | 0.6268 | 0.5682 | 0 |

保守策略当前不宣称自动提升精度/召回；它先消除旧融合对高置信度检测的破坏，并把 VLM 分歧转为可审计、可校准的待审核建议。后续若要自动采纳 reject/revise/addition，需要基于人工确认集增加门控阈值和逐类校准。

## 验证

- 后端：`1161 passed, 159 skipped`。
- 前端：`123 passed`；生产构建 `tsc --noEmit && vite build` 通过。
- Ruff 静态检查通过；`git diff --check` 通过。
- 两次重放均为 `provider_attempts=0`，没有读取或修改 API key、模型权重或数据集。
