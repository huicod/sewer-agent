# Journal - huicod (Part 1)

> AI development session journal
> Started: 2026-09-09

---



## Session 1: T01 harness and backend bootstrap

**Date**: 2026-09-09
**Task**: T01 harness and backend bootstrap

### Summary

Session summary was not supplied.

### Main Changes

- Trellis huicod initialized; automatic Git commits disabled; jj isolated change

### Local Version Control

Local jj change `tyusnslv`, checkpoint `f5a0deb2`; no remote push.

### Testing

- [OK] pytest: 3 passed; ruff: all checks passed; independent review passed

### Status

[OK] **Completed**

### Next Steps

- T02 real detector and durable workflow validation; live API configuration pending


## Session 2: T02 local adapters and durable runtime checkpoint

**Date**: 2026-09-09
**Task**: T02 local adapters and durable runtime checkpoint

### Summary

T02 remains in progress: local contracts and real restart recovery passed; real checkpoint execution approval and live API configuration remain pending. jj change tsksxomp; no remote push.

### Main Changes

- Detector/API adapters; native PostgreSQL+Temporal; scoped restart probe; backend runbook

### Local Version Control

Local jj change `tsksxomp`; no remote push.

### Testing

- [OK] 50 passed, 3 skipped; Ruff clean; explicit restart suite 4 passed

### Status

**In progress** — local checkpoint saved; real model/API validation pending.

### Next Steps

- Obtain informed checkpoint trust approval and configure provider/model/base URL locally, then complete T02 before T03


## Session 3: T03 contracts complete and trusted GPU smoke passed

**Date**: 2026-09-09
**Task**: T03 contracts complete and trusted GPU smoke passed

### Summary

User authorized trusted checkpoint loading: GPU AJ smoke passed. T03 contracts and detector replacement completed and independently approved. T02 live API remains pending; next T04.

### Main Changes

- Validated task/asset/finding/tool contracts, shared pixel geometry, explicit detector lifecycle; jj change msmpusxk

### Git Commits

| Hash | Message |
|------|---------|
| `154f7d02` | (see git log) |

### Testing

- [OK] 145 passed, 3 skipped; Ruff clean; independent scoped review approved

### Status

[OK] **Completed**

### Next Steps

- T04 upload/media validation, persistent task API/progress and minimal Web shell; configure real LLM/VLM API separately

## Session 4: 首版 Agent 定位与压缩前交接

**Date**: 2026-09-09  
**Status**: 进度整理完成；开发仍停在 T04 planning。本节为最新交接，前面会话中的待办以本节为准。

### 首版目标与当前差距

- 首版最终是通过对话和 CCTV 图片/离线视频上传使用的任务型 Agent：理解任务、受约束地选择工具、执行检测并基于证据回答。不是仅交付固定检测流水线，也不是通用自主 Agent 平台。
- 原方案已经覆盖上传＋文本任务，但多轮追问、引用既有结果和追加工具操作尚未充分落入任务规格；继续 T04/T06 前补齐必要的会话—任务—证据关联和验收，不重新设计整套架构。
- LLM/VLM 均调用 API；本地运行后端与视觉模型。Temporal 仍是唯一持久编排器。聊天断开不能等同于取消长任务；解释现有结果与启动新工具操作需要明确区分。
- 当前只有健康检查 HTTP 接口，尚无上传、任务、聊天接口和前端；TaskPlan 契约/API 客户端不是已经完成的 Agent 执行循环。

### 已完成与验证证据

| 任务 | 最新状态 |
|---|---|
| T01 | Trellis huicod、jj/Git 工作规则、Python 环境和后端骨架完成 |
| T02 | 检测器/API 适配器、本机 PostgreSQL＋Temporal、重启恢复探针完成；真实 LLM/VLM 联调仍待配置 |
| T03 | 资产、Finding、TaskPlan、TaskSnapshot、ToolCall 契约与可替换检测器生命周期完成，独立审查通过 |
| T04 | 仅建立任务目录/PRD/上下文模板，未实现上传、持久任务接口或界面 |
| T05–T16 | 未启动；生产图片/视频工作流及 Agent 工具执行循环尚未实现 |

- 上一实现阶段全套结果：145 passed、3 skipped；Ruff 通过。此次只整理文档，未重跑代码测试。
- 用户已知情授权现有 best.pt 加载，真实 GPU 单图推理成功：AJ，score 0.93179；记录 var/fixtures/detector-smoke.json（不入库）。无需重复询问该权重授权；该样例不是精度验收。
- 真实恢复专项曾为 4 passed：测试工作流可在 worker、Temporal、PostgreSQL 重启后继续人工等待；不代表生产业务工作流已完成，也不保证所有外部副作用 exactly-once。
- 3 个默认跳过项为显式启用的恢复测试和两个真实 API 测试；恢复测试已单独运行，真实 API 尚未运行。

### 继续开发入口与约束

1. 阅读主方案 vlm_review/VLM_YOLO_ARCHITECTURE_AND_RESEARCH_SUMMARY.md 第 9–10 节、唯一总体计划 project_management/DEVELOPMENT_PLAN.md，以及当前 .trellis/tasks/09-09-m1-intake/。
2. 在 T04/T06 规格中明确对话入口和后续交互的分工，再完成 T04 小设计、相关测试与实现。T04 当前只承诺持久入队，不能显示检测已执行；生产执行属于 T05 及后续任务。
3. 等待非敏感的 LLM/VLM 供应商、模型、Base URL 信息；密钥仅由用户在服务器 .env 配置。真实 CCTV 视频仍待提供，机械测试可先用合成媒体。
4. 不训练/优化 YOLO 权重，不新增第二套编排器、不做文件/模型哈希、不扩建开发管理平台。正常结论需覆盖、画质和负结果审计成立；其他类别先人工确认；无标定不输出真实尺寸。
5. 后端命令和服务限制见 backend/README.md。项目 .venv 复用系统 torch/CUDA；PostgreSQL 数据在 /var/lib/postgresql/14/main，Temporal 仅本机监听；不要打印 .env/凭据。前端依赖尚未安装。
6. 进度保存在 Trellis，路线图只维护总体计划；后续每阶段更新状态、证据和下一步。只由主 Agent 操作 jj，不自动推送。

### 本地版本锚点

- T01：tyusnslv / f5a0deb2；T02：tsksxomp / e52516ab；T03：msmpusxk / 5d42d0d0。
- 当前 T04 change：wtkqtltt，整理前快照 c4c6c21e；本次交接随该 change 保存，用 jj status 查看最新快照，避免文档自引用不断改号。
- GitHub 私有仓库 huicod/sewer-agent 仅首次资料已推送；上述后续开发保存在本地，未自动同步远端。

## Session 5: T04 验收完成与 T05 交接

**Date**: 2026-09-09
**Status**: T04 完成、独立审查 APPROVED；下一步 T05 planning。旧会话的待办以本节为准。

- 已交付会话关联的图片/视频上传、文本提交、PostgreSQL 持久任务与查询、原始文件下载、中文 React/TS 界面。重复提交使用 request_id，刷新恢复 session；当前任务明确 queued/pending，不调用检测器或伪造 Agent 回答。
- 验收：后端 176 passed / 12 skipped、Ruff 通过；另行真实 PG/HTTP 9 passed；前端 11 passed，类型检查/构建通过。已修正 MOV MIME、错误响应格式、重试错用文件和 2xx JSON 校验问题。审查规格/质量均通过，无阻塞项。
- 证据归档：.trellis/tasks/archive/2026-09/09-09-m1-intake/ 的 report.md、review.md、storage-report.md、frontend-report.md。T04 change wtkqtltt，验收代码快照 cb8b2464；最终文档/归档快照见 jj log（无代码变化）。
- 当前入口：.trellis/tasks/09-09-m1-image-workflow/，以及唯一总体计划 project_management/DEVELOPMENT_PLAN.md。T05 尚未实现：图片五类初查、质量分析、条件 VLM/负结果审计、规则融合和生产 Temporal 工作流。
- T06 承担多轮追问、既有证据解释和关联工具操作；T04 已提供 session/task/asset 基础，不需重建整套架构。现有 TaskPlan 是完整检查计划，不能导致每个追问都重跑 detect。
- 本机业务表已初始化；FFmpeg 4.4.2 已安装；.tools/node/bin 为隔离 Node 24.21.0/npm 11.19.0。运行、配置及测试命令见 backend/README.md 和 frontend/package.json。
- 本次临时 uvicorn/Vite 联调进程已停止，Temporal/PostgreSQL 未停止；后续按运行说明启动。未重复可信权重推理或付费 API，未上传真实媒体到外部 API。
- 已知限制：缺真实 LLM/VLM 配置与 CCTV 视频效果资料；首帧校验不代表全视频无损；尚未做视觉浏览器验收。开发态 StrictMode 可能多留一个空会话；崩溃/歧义 DB 提交可能留未引用原始字节，不自动删除。
- API 密钥仅放服务器 .env，不粘贴到聊天/仓库。模型训练、第二套调度器、文件/模型哈希、公共部署和管理平台均不在本阶段范围。
- GitHub 仍只有首次资料推送；后续开发全部保存在本地 jj，未经授权不自动推送。


## Session 6: T05 图片工作流与 API 待验交接

**Date**: 2026-09-10
**Task**: T05 图片工作流与 API 待验交接

### Summary

T05 已接通图片检测与条件复检、本地 GPU/Temporal 验证通过；独立审查及恢复修复已通过，真实 VLM 待配置。

### Main Changes

- jj change mxopyywt；父检查点 T04 0c6d664b；本轮未推送 GitHub。
- 已实现显式图片启动、检测与质量门、VLM全图审核、持久结果/工具记录、页面状态和证据；连续对话仍属T06。

### Git Commits

(No commits - planning session)

### Testing

- [OK] 真实GPU生产工作流1 passed（AJ、defects_found、verify skipped）；真实Temporal恢复1 passed，HTTP到Temporal再到结果查询1 passed。
- [OK] 最终后端248 passed/4 skipped，前端15 tests和构建通过，Ruff clean；真实Temporal故障注入验证通过，独立复审PASS，详见当前任务runtime-report/review。

### Status

[OK] **Completed**

### Next Steps

- T05实现与本地审查通过；下一步配置真实VLM并做受控样例联调，再进入T06。不重做T04，不把mock当模型效果验收。
- VLM首选qwen3-vl-plus，备选qwen3-vl-flash；需服务商/模型ID/地域/Base URL，Key只写本机.env。
