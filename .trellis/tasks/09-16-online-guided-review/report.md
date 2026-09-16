# 在线图片接入已认可引导复核

日期：2026-09-16；变更：`jj ysywtovm`；交付分支：`fix/online-guided-review`。初版 `main` / `v0.1.0-prototype` 保持 `b1173b1b`。

## 实际修改

- 新图片任务默认 `guided_review`：每张一次原始 VLM 复核，输入原图＋YOLO 类别/分数/坐标 JSON＋最多 8 个候选上下文原生裁剪；零框仍复核原图。没有额外画框、增强或开放检测器。
- 与旧离线共用 `GuidedImageReviewer` / `yolo-guided-review-v2` / `candidate-context-native-v1` 和 `agent/guided_fusion.py` 的 `guided-confidence-v2`，没有重新优化 prompt、阈值或检测模型。
- ≥0.85 原类别、框、分数保留；冲突待人工。低分明确拒绝/改类生效；拒绝的 finding 保留 rejected 审计。明确、定位有效、非重复且不冲突的五类新增可纳入融合；图像质量足够时明确接受/改类/新增由规则确认。OTHER、不确定及冲突仍人工审核。VLM 新增 finding.score=null，离线占位值 1.0 不是可信度。
- verify 检查点保存原始 `review` 和 `fusion.predictions/changes`。融合预测与待审建议分开，不能把所有 findings 都计作已确认缺陷。增强/几何工具复用同一结论；关联操作沿用冻结父结论。
- 已保存旧任务按其 candidate/discovery 版本继续，不因新默认值失效或自动重算；混合/未知版本及模型/API 漂移仍拒绝。视频及 Temporal 命令序列不变。

## 验证

- TDD：主线最初 7 项目标测试失败、12 通过，覆盖原先缺失的线上 guided、版本兼容及可选工具基线；修正后通过。
- `SEWER_RUN_STORAGE_TEST=1 .venv/bin/python -m pytest backend/tests -q --tb=short`：**1257 passed / 48 skipped**，77.11 秒。真实 PostgreSQL 使用测试专属 schema 并清理；未运行有单独开关的实时模型、Temporal 故障/重启等测试。
- `.venv/bin/ruff check backend` 通过。独立只读审查及其 45 项定向测试通过，无阻塞发现。
- `.venv/bin/python .trellis/tasks/09-16-online-guided-review/replay.py`：旧实验 paired 36＋regression 12，**48/48** 的框、类别、分数和处理动作与保存结果逐项一致，在线 Finding 的选中投影也一致，含原有 1 次复核失败回退。**0 新增模型调用**。
- 回放产物：`var/evaluations/2026-09-16-online-guided-parity/summary.json`；未改旧图片/实验结果。该验证证明实现一致性，不是新准确率实验。
- 真实在线 reviewer 的本地 HTTP 替身测试覆盖原图/裁剪数、归一化坐标还原、零框补漏、高分保护、低分删除及缓存重放。当前本机非秘密设置核实为 qwen3.8-max、thinking=false、4096 输出 token、60 秒、8 MiB，与旧实验一致；未实调付费 API。

## 交接边界

服务持续暂停（API/CPU/GPU/Temporal 停止，PostgreSQL 保留），未重启人工验收。恢复后新建图片任务才使用新默认；原有结果/人工审核等待不被覆盖。前端并排展示 YOLO/VLM 独立结果仍是已知待办，本次未扩展界面。SEDD 上 YOLO 外域漏检没有通过训练或降阈值改变。

按用户授权仅推送独立分支，保留初版标签；密钥、`.env`、模型、上传图片及 `var/` 不进入 GitHub。回退部署可选初版标签；旧模式仍支持显式配置，但新 guided 任务不能交给不支持其版本的旧 worker。
