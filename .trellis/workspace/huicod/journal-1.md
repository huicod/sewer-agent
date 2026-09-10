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


## Session 7: Qwen API 接入与 T06-A 基础能力

**Date**: 2026-09-10
**Task**: Qwen API 接入与 T06-A 基础能力

### Summary

Qwen共享Key接入与有界请求已实现；北京文本probe认证失败，等待真实Base URL。T06-A解析计划和证据报告已实现，304测试通过，独立复核通过；T06-B未实现。

### Main Changes

- 用户明确模型qwen3.8-max；.env保留DASHSCOPE_API_KEY并追加非敏感角色配置，0600且忽略入库；首次真实文本请求authentication_failed，无图片上传或跨地域重试。
- T05本地检查点d1565d95，真实API待验；当前T06稳定jj change xovuunkz。解析器向模型发送Schema，服务端限制工具计划；JSON/HTML报告仅读取终态证据。

### Git Commits

| Hash | Message |
|------|---------|
| `74e22a4a9f3b` | (see git log) |

### Testing

- [OK] 最终后端+真实本地PG/Temporal：304 passed,4 skipped；Ruff和diff检查通过。跳过真实GPU、两个付费API及共享服务重启，未宣称这些通过。

### Status

[OK] **Completed**

### Next Steps

- 先读当前T06任务的implement.md/runtime-report.md/review.md；切片A不重做。T06-B接消息持久化、Temporal对话与关联操作、聊天UI；完整首版Agent尚未交付。
- 等待Key对应的Base URL/去掉Key的调用示例后，少量有界文本与原图请求完成真实联调；不要把Key发到猜测地址，不自动推送GitHub。


## Session 8: Qwen 实测通过与 T06-B1 持久对话闭环

**Date**: 2026-09-10
**Task**: Qwen 实测通过与 T06-B1 持久对话闭环

### Summary

SDK、真实 Qwen 文本/原图/对话联调通过；T06-B1 对话上传、图片任务、证据回答与恢复已交付，独立审查通过；B2 关联补充复检待实现。

### Main Changes

- T02/T05 已完成工程验收；T06-A 不重做，B1 稳定 jj change zmoplozu，SDK 证据检查点 6b8c4942；未推送 GitHub。
- 对话 Temporal 解析/分派/结束检查点、固定身份与显式复检计划；前端附件/旧任务互斥，查询只读证据。项目 .venv 已装 OpenAI/DashScope SDK，生产仍用 httpx。

### Git Commits

| Hash | Message |
|------|---------|
| `56e3298ed279` | (see git log) |

### Testing

- [OK] 后端真实本地 PG/Temporal 回归 348 passed / 6 skipped（25.48s）；前端 26 passed，类型检查/构建通过，Ruff/diff clean；独立审查最终 PASS。
- [OK] 另行真实 Qwen 对话 1 passed（3.50s）；SDK 示例、结构化文本和选定原始 CCTV ImageReviewer 通过。额外 FS 建议待人工确认，非精度结论。

### Status

[OK] **Completed**

### Next Steps

- 先读 .trellis/tasks/09-10-m1-conversation/conversation-report.md、backend/README.md 与总体计划；下一步仅 B2 关联补充复检，再 T07/T08 视频，不把 B1 称完整首版。
- 无需再问 Key/Base URL；旧认证失败已解除但历史原因未定。Key 仅 .env，视频效果验收仍缺真实 CCTV 视频。API/worker/Web 按 README 启动，仅本机；不自动推送。


## Session 9: T06-B2 关联复检完成与视频阶段交接

**Date**: 2026-09-10
**Task**: T06-B2 关联复检完成与视频阶段交接

### Summary

T06-A/B1/B2 工程验证完成；对话支持一次关联 VLM 复检，冻结原证据且不重复检测，下一步 T07 视频。

### Main Changes

- 稳定 jj change mkomtpts；父 B1 70a4af21；本轮未推送 GitHub，Trellis 仅保存当前任务和必要交接。
- 原子关联与预算、verify-only Temporal 工作流、原 OTHER/冲突保留、父报告链接；复用现有适配器，未增依赖/模型/哈希。

### Git Commits

| Hash | Message |
|------|---------|
| `52b111a3f4e3` | (see git log) |

### Testing

- [OK] 最终后端398 passed/6 skipped（51.47s），包含真实PG/Temporal；前端27 passed及构建通过；Ruff lint/diff通过。
- [OK] 独立Sol审查与修复复核通过；配置变化保留原待审，生产共享verifier在API前检查策略版本。未新增付费请求或GPU推理。

### Status

[OK] **Completed**

### Next Steps

- 恢复先读 .trellis/tasks/09-10-m1-conversation/supplement-report.md 和总体开发计划；T06已完成，不重做。开始T07视频解码、事件聚合、证据选帧，再T08长任务恢复/T09人工审核。
- PostgreSQL/Temporal运行；关联表已初始化；smoke CPU worker已停，API/GPU/Web按README启动。现有Qwen配置可用，真实CCTV视频效果验收仍需用户样例。


## Session 10: T07 视频初查完成与复核增益评估待数据

**Date**: 2026-09-10
**Task**: T07 视频初查完成与复核增益评估待数据

### Summary

视频逐帧初查、事件/证据核心与配对评估工具通过验证；真实准确率增益尚未验证，等待留出划分或独立标注图；下一开发 T08。

### Main Changes

- 稳定 jj change mzwqotpq，父检查点63e00934；原检测模型与 Qwen 配置沿用，无新依赖或付费请求，未推送 GitHub。
- 真实PTS流式解码、检测器解耦、跨块聚合/重放、短暂事件保留和原帧证据；视频仍为pending，网页/Temporal视频执行留在T08。
- 评估分开原始框、保留候选和自动确认；失败与待审显式计数，不完整固定集禁止输出比较，恢复冻结非敏感配置。

### Version Control

本地 jj change `mzwqotpq` 保存实现与交接；未推送 GitHub。

### Testing

- [OK] 最终全后端460 passed/6 skipped，62.75s，含真实PG/Temporal和视频GPU；Ruff、相关格式与diff检查通过。
- [OK] 独立审查问题全部修复并主Agent复验：未满尾批丢帧、子集指标误报、续跑配置缺项；合成视频只证明集成，不是准确率验收。

### Status

[OK] 工程阶段完成；真实复核增益评估等待数据。

### Next Steps

- 恢复先读 .trellis/tasks/09-10-m2-video-core/runtime-report.md 和总体计划；T01–T07不重做，下一步T08长视频工作流、进度、取消和断点恢复。
- 真实增益试验仍待用户原split_manifest.csv或明确未参与训练的标注图片；无真实正常集/真实CCTV视频，不重分割冒充test，不宣称VLM已提高准确率。
