# T15-E 业务服务中断恢复验收

状态：完成，真实验收与一次独立审查通过。基线 jj `a53751b2`，本轮 change `wqqzlvlt`。

## 范围

真实 ImageInspectionWorkflow / VideoInspectionWorkflow、Temporal 和 PostgreSQL；合成图片、6 帧视频与确定性检测/复核替身。图片在人工等待、视频在首块提交后中断，同一次服务重启覆盖两种状态。不调用 Qwen，不改产品模型/策略，不把恢复通过当作准确率证据。

## 已核实的执行前环境

- 日常 API/CPU/GPU 正常运行，PID 6059/6058/6057；Temporal 2156，PG 14/main 主进程 2130。
- public 业务任务 0、运行 workflow 0；数据库仅 postgres / sewer_agent / temporal / temporal_visibility，非本次 psql 的客户端连接仅本项目 Temporal。
- 既有本机目标和专用集群守卫通过，恢复守卫 3 passed / 1 skipped，runtime 单元测试 4 passed。

## 实际结果

[原始 JSON](../../../var/evaluations/2026-09-14-business-restart-001/business-restart-summary.json)保存前后快照、原框、检查点、工具记录、进程和 workflow/run ID；[跨进程调用轨迹](../../../var/evaluations/2026-09-14-business-restart-001/call-trace.jsonl)可直接核对是否重算。输入在同目录 fixtures，派生资产在 assets，worker 日志和失败阶段记录亦保留。

| 检查 | 中断前 | 恢复后 |
|---|---|---|
| 图片 | waiting_review，AJ 框 (8,8,40,40)，检测/复核各 1 次 | 等待快照、detect/verify/final 检查点和工具记录完全一致；仍各 1 次 |
| 视频 | 2 帧、1 块 (0–1)，scan attempt=1 | 6 帧、3 块 (0–1,2–3,4–5)；后两块 attempt=2，帧 0–5 每帧仅检测 1 次 |
| 工作流 | 两个 inspection workflow/run ID | 两个 ID 及 run ID 均不变，没有创建替代任务 |
| 服务 | Temporal PID 2156；PG 启动 01:09:19.551533+08:00 | Temporal PID 118993；PG 启动 11:12:11.726054+08:00 |

首 worker PID 118718 被强制终止，恢复 worker PID 119057。视频首次扫描工具状态保留 uncertain/activity_interrupted，第二次 succeeded，使用同一逻辑 call_id；中断未被掩盖。视频完成后覆盖、质量和负样本审计均满足，输出 normal；这是合成无候选 fixture 的流程结果，不是真实管道的准确率结论。

图片恢复后才对合成 AJ 提交测试审核，进入 completed/defects_found。同一 request_id 再提交直接读取原审核记录，不重新连接 Temporal。没有审核用户真实图片。

真实用例 **1 passed / 37.88 秒**。服务停止、PG 重启和 Temporal 启动合计 **4.7925 秒**；Temporal 就绪之后至新 worker 完成视频并确认图片等待 **26.1061 秒**，不含人审耗时。保留生产 30 秒心跳超时，以上不是任意任务恢复 SLA。

## 验证和清理

- 后端完整测试 **1064 passed / 146 skipped（23.84 秒）**；默认跳过项不算本轮实际通过，重启用例单独启用如上。Ruff 通过；前端未改，不重复构建。
- 独立审查 `business_restart_review` 对规范符合性、代码质量和安全给出 PASS，无 Critical/Important。主 Agent 另核对全量改动、原始 JSON/调用轨迹和恢复后服务状态。
- 首次单文件命令在收集时因辅助模块短导入失败，尚未创建测试输出、任务或中断持久服务；联合测试路径曾掩盖此问题。改为包限定导入后，单独入口、联合守卫及完整收集均验证，再跑本次成功验收。仅测试代码修复，未修改生产功能。
- 清理仅删除本轮 `business_restart_*` 测试 schema；文件保留可重查。结束检查 public 任务 0、运行 workflow 0、测试 schema 0，无测试 worker/FFmpeg 残留。
- 日常应用已恢复 `127.0.0.1:8000`，API/CPU/GPU PID 119681/119680/119679，PG/Temporal 和总 ready=true。原会话/媒体未删除；没有 .env、依赖、模型或远端推送变更。

## 边界与下一步

本轮补齐当前图片等待和视频首块之后的真实服务恢复证据，不覆盖断电、磁盘损坏、每个 Activity 的全部崩溃窗口，也不承诺外部 API 零重复计费。增强/几何等已有 worker 恢复测试不能自动升格为全部服务故障矩阵通过。

继续 T15 视频扫描效率/有效片段筛选及剩余交付验收；T14-B 仍等用户指定标准编号、版本和 PDF/官方来源。自动关联增强、视频增强/几何、最终用户界面与全项验收未完成，首版尚未整体交付。图片 VLM 策略保持不变，开放补检停用。
