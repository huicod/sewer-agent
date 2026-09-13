# T15-C 故障验收记录

2026-09-14；huicod；基线`c0514263`，jj `stvsvnru`。本轮新增验收测试，不改变检测/VLM/融合策略。
后端矩阵、浏览器实测和一次独立审查通过；本切片完成，T15全项尚未完成。

## 后端：实际持久化故障结果

七场景**7 passed / 20.51秒**。使用当前candidate_review及生产解析/工作流/存储，检测替身固定AJ、0.6分、原框(4,4,40,40)。
每行链接均为本轮保存的真实任务快照、调用尝试和检查点；“请求次数”指测试HTTP边界，不是付费请求。

| 注入故障 | 检测/复检次数 | 实际结局 |
|---|---|---|
| [超时后成功](../../../var/evaluations/2026-09-14-fault-matrix/timeout_then_success.json) | 1 / 2 | 第一次uncertain，第二次成功，AJ确认、defects_found |
| [429后成功](../../../var/evaluations/2026-09-14-fault-matrix/rate_limit_then_success.json) | 1 / 2 | 第一次failed，第二次成功，AJ确认、defects_found |
| [持续超时](../../../var/evaluations/2026-09-14-fault-matrix/persistent_timeout.json) | 1 / 3 | 三次uncertain后待审，AJ及原框保留，inconclusive |
| [持续429](../../../var/evaluations/2026-09-14-fault-matrix/persistent_rate_limit.json) | 1 / 3 | 三次failed后待审，AJ及原框保留，inconclusive |
| [非法JSON](../../../var/evaluations/2026-09-14-fault-matrix/invalid_json.json) | 1 / 1 | 不重试，待审、AJ保留，inconclusive |
| [Schema非法](../../../var/evaluations/2026-09-14-fault-matrix/invalid_schema.json) | 1 / 1 | 不重试，待审、AJ保留，inconclusive |
| [检测器OOM](../../../var/evaluations/2026-09-14-fault-matrix/detector_oom.json) | 1 / 0 | 检测失败后不调用VLM，failed/inconclusive |

六个复检场景均只检测一次，持续故障不保存成功复检检查点；任何失败场景均未输出normal。
OOM只在检测器边界抛torch.OutOfMemoryError，未耗尽显存，也不证明真实OOM后GPU恢复。

## 浏览器：实收任务后丢失回执

真实POST返回202后，只中断回到浏览器的响应。页面提示使用同一请求编号重发；重发后实查数据库投影只有一轮对话、一个任务，附件仅上传一次，刷新仍待审。
两次request_id均为`a115ae0a-f733-4957-a6a7-12d1a170a3f6`；会话`66064182-a450-4ab1-b1d2-bb90429da1f7`。
新增场景5.1秒；与既有core一起**2 passed / 28.3秒**。前端**84 passed**及TypeScript/Vite构建通过。

- [丢失回执页面](../../../var/evaluations/2026-09-14-fault-browser/screenshots/06-lost-response.png)
- [重发、刷新后恢复](../../../var/evaluations/2026-09-14-fault-browser/screenshots/07-retry-restored.png)
- [请求与实际记录](../../../var/evaluations/2026-09-14-fault-browser/lost-response.json)、[整组浏览器结果](../../../var/evaluations/2026-09-14-fault-browser/browser-results.json)、[服务记录](../../../var/evaluations/2026-09-14-fault-browser/service/service-summary.json)

整组为合成图片/1.2秒视频、模型替身和真实HTTP/PG/Temporal，4任务/5轮对话。
原core场景自动审核3项合成任务，新增场景不提交审核；结束时清理测试schema并终止1个自有待审workflow，截图不是常驻可继续审核的真实业务会话。

## 检查点与运行状态

既有`test_image_workflow_faults_live` **1 passed / 4.22秒**：检测已提交后丢完成回执、终态视图首次写失败，实际工具不重复，检查点修复最终视图。[JUnit记录](../../../var/evaluations/2026-09-14-fault-recovery.xml)
本轮初始应用和PG/Temporal均已停止，使用既有runtime/app入口恢复；恢复前public任务数与Temporal Running均为0。
应用留在服务器`127.0.0.1:8000`，当时API/CPU/GPU PID6059/6058/6057，总ready=true。操作见[运行手册](../../../RUNNING.md)。

## 总体验收剩余项

| 能力 | 证据与边界 |
|---|---|
| 图片/视频对话、报告、人工等待与时间线 | [T15-A](../09-13-m5-core-acceptance/report.md)及本轮core：合成模型、真实浏览器和服务；不代表检测准确率 |
| 真实图片检测/VLM、增强和相对几何 | [T15-B](../09-13-m5-live-browser/report.md)：真实单图两链路，失败与补跑分别保存 |
| 业务检查点及worker恢复 | 当前图片测试和既有T08/T09/T12/T13记录；不能把M0通用重启探针写成当前所有业务路径的数据库/Temporal中断全矩阵 |
| 部署入口与会话重启 | [T16](../09-13-m5-local-delivery/report.md)：同机启停与会话持久化；不含自动进程监督 |
| 尚待完成 | 规范编号/版本/真实资料、视频成本与人工真值、完整业务服务故障覆盖、用户最终界面验收；视频增强/几何与自动关联增强未接入 |

本轮没有真实供应商请求、模型推理、共享服务故障注入、.env变更或远端推送。模型异常注入验证工程处理，不测模型效果或物理显存恢复。

## 验证与保存

完整后端**1048 passed / 144 skipped（24.03秒）**，Ruff通过；144项含本轮已单独启用的7项集成测试，其余默认跳过项不计作本轮通过。
独立审查无Critical/Important/Minor问题；实际逐例JSON、浏览器截图和调用记录已复核。Trellis Session31保存交接，未推送远端。
没有产品逻辑修复。新增测试保护既有行为，不为制造失败而改动生产代码；Superpowers用于隔离实现/独立审查，stop-that-shit将范围限制在缺失的验收证据。
测试结束已查无test_image_/browser_acceptance_残留schema，public业务任务仍为0；测试文件和JSON保留，不改用户数据。
