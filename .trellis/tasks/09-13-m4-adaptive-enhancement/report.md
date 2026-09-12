# T12-C 交接：按需增强与低照度恢复

2026-09-13；huicod；jj change `skwvwmyl`，基线 `d7afd3ac`。本切片已完成，原首版仍在开发。Trellis 是唯一任务状态；工作区与原实验保留，未推送 GitHub。

## 已实现

- 普通检查默认不增强。本次对话明确允许按需增强，才在原检测/原 VLM 后调用独立工具选择器：原图、质量线索、原发现 → 一种提议或放弃 → 后端校验 → 最多一次增强/双图复核。不是亮度/置信度阈值直接触发，也不更改已验收 VLM Prompt/融合。
- 固定可用模型与调用版本，选择/跳过原因先存 checkpoint；恢复读取已存选择，不重复询问。派生执行配置不改写原 run，GPU 结果仍按原图、选定 pin 和成功调用原子提交。
- 选择失败不能降级已成功的原图复核；保留原框/类/分数/coverage，任务 partial 且不能通过人工按钮假装补完失败工具。新增/冲突发现仍待审。
- 新增本地 DarkIR-m `low_light`；HVI-CIDNet 保留离线比较。原三模型/旧三工具评测可继续复现，未默默替换其他模型。来源/许可/实际显存见 [模型记录](lowlight-report.md)。
- 对照面板和 JSON/HTML 报告增加选择依据、跳过原因和低照度名称；旧详情无 selection 可读，原图不覆盖，无模型路径泄露。
- 自动增强仅新原始图片任务；显式关联增强照常可用。视频/自动关联补证先拒绝，未顺带开发分割或深度。

使用示例：“新建图片检查，允许根据实际画质按需选择一次增强，不需要就跳过”。需要指定暗光工具可说“检查图片并做暗光恢复辅助复核”。详见 [后端入口](../../../backend/README.md)。

## 实际输出与结果

[总入口](../../../var/evaluations/2026-09-13-adaptive-enhancement/index.html)；[六图 DarkIR/HVI 对照](../../../var/evaluations/2026-09-13-adaptive-enhancement/lowlight-models/index.html)。全部为既有 Sewer2403 原图，不合成暗化；有两张 1080p 离线全分辨率比较，在线仍保留 1,048,576 输入像素预算，超限需 ROI。

| 在线样本 | 真实选择 | API / GPU | 时长与结论 |
|---|---|---|---|
| sample-0025 | low_light，暗部妨碍判读 | 4 次 qwen3.8-max / 1 次 DarkIR | pytest 30.61 秒；辅助复核认为暗部可见性改善，无新增/冲突；BLOCK 0.8947、AJ 0.3169 原框/分数/待审状态保留 |
| sample-0016 | not_needed，当前区域可判读 | 3 次 qwen3.8-max / 0 次增强 | pytest 24.24 秒；明确跳过工具，沿用原检测/复核结果 |

在线目录保存 original、可选 enhanced、detect、verify、selection、冻结 baseline、enhance/review、任务、工具调用、阶段和实际 API 文本请求；图片字节未复制进请求 JSON。总计 7 次真实 API 和 1 次增强；不是模型替身。两例均经 HTTP 上传/自然语言 → 真实 Temporal/PG → 本地检测/API 执行。

样例的隔离数据库 schema 已删除、等待中的测试工作流已终止，文件保留；不伪造人工决策，也不是常驻产品会话。目录按会话日期命名，内部 UTC 时间戳保留服务器原值。

小样本观察不能推出检测精确率/召回率增益。低照度模型也会放大噪声/改变颜色和纹理，不能全图默认使用；没有把六图输出当成管网最优排名。人工缺陷复核与成对标注尚未完成。

## 验证与审查

- 最终后端：`.venv/bin/pytest -q backend/tests --tb=short`，**829 passed / 116 skipped**，14.13 秒；跳过项需要显式服务/模型/API 条件。
- `.venv/bin/ruff check backend scripts`、`git diff --check` 通过。
- 前端：69 tests passed，TypeScript/Vite build 通过；未进行浏览器人工视觉验收。
- 真实 PG/Temporal：增强事务、旧显式链路及新主动选择组合 **31 passed**（25.10 秒），包括选择 ACK 丢失/替换 worker 后继续和跳过分支；未重启共享服务。
- 上述两个真实在线 smoke 各 1 passed，付费次数如表；未为文档或审查重复调用。
- 一次独立只读审查发现选择器失败被当作原 VLM 失败，已用先失败后通过的测试修复；最终无未解决审查项。修复后原图确认状态保持不变，失败任务仍 partial。

## 进度与下一步

本轮无新包、训练、输入/模型哈希、原图/旧实验覆写、开放检测器恢复或远端推送。按 stop-that-shit 保持一个增强切片；TDD 约束了选择分支和失败保留原证据，验证与独立审查后形成 jj 本地检查点。

下一步见唯一路线图 [DEVELOPMENT_PLAN.md](../../../project_management/DEVELOPMENT_PLAN.md)：T12-D 小样本比较去模糊/去雾/超分新候选及缺陷保真，再回到 T13。低照度尚非最终最优选型，当前自动策略仍为用户 opt-in，不开全局默认。
