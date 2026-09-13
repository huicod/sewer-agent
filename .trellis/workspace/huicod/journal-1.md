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


## Session 11: T08 持久视频闭环与真实数据配对验证

**Date**: 2026-09-10
**Task**: T08 持久视频闭环与真实数据配对验证

### Summary

T08 工程验收完成。两段真实视频4259帧、SIGTERM恢复和有界复检已验证；42图配对评估未证明整体准确率提升。代码与证据采用 jj change nxyslwxv，未推送。

### Main Changes

- 接通视频对话/启动、原子分块检查点、精确PTS恢复、事件证据、有界VLM、明确取消接受边界和前端进度；复用既有Temporal/PG/检测器，无新训练或框架。
- 用户提供的数据已按固定seed选择；SEDD仅PL/BLOCK评分，Sewer2403可能训练重叠。原始观测和结果在var/evaluations/2026-09-10，来源与数值见T08 data-report.md。
- 独立审查三项问题已回归修复：事件审计不完整误报正常、终态后scan写入、最终提交阶段取消竞态。

### Git Commits

(No commits - planning session)

### Testing

- [OK] 后端513 passed / 7 skipped；前端32 passed，TypeScript/Vite build通过；Ruff、相关格式和diff检查通过。
- [OK] 真实561帧视频在cursor248处SIGTERM，原workflow从248恢复至560；71不重复块，2候选事件，4API调用，partial/inconclusive。
- [OK] 真实3698帧视频完整执行：463块，397观测聚合77候选片段，83证据帧，4API调用；1397.01秒，partial/defects_found，保留画质与复检缺口。
- [OK] 42图共39次真实Qwen复检且0失败。源域12图原检测F1 0.846，复检自动确认0.667；SEDD外部30图PL/BLOCK自动确认无增益，新增命中仅在pending队列。

### Status

[OK] **Completed**

### Next Steps

- 开始T09：人工审核API/页面、持久等待恢复与事件时间轴；不将本阶段partial临时包装成人工审核已完成。
- 完整首版其余YOLOE、增强、分割/相对深度等按T10–T16顺序继续；准确率校准需人工分歧审查与独立五类集，不重跑本次小样本追分。


## Session 12: Sewer2403 图片复检 v2 与扩展配对验证

**Date**: 2026-09-11
**Task**: Sewer2403 图片复检 v2 与扩展配对验证

### Summary

用户暂停视频和 SEDD，优先图片。v2 已完成；36 张新图自动确认 F1 0.511→0.691，仍低于 YOLO 0.817。12 张旧图另作回归。未推送、未部署。

### Main Changes

- 为五类增加视觉特征、易混淆项和不确定性规则；BLOCK 明确 CJ/JG/ZW；保持检测权重、阈值、API 模型与最终决策规则不变。
- 固定 seed20260911 选36新图，排除旧12图分组；v2复用完全相同的YOLO框/质量观测，保存Prompt、逐图回答、配对指标和正反案例。

### Git Commits

| Hash | Message |
|------|---------|
| `jj vktuuypp` | (see git log) |

### Testing

- [OK] 43项相关测试通过；后端446 passed/75 skipped；Ruff及diff检查通过，独立审查无生产代码阻断问题。
- [OK] 71次真实API调用：新图v1/v2各31次、旧图v2共9次；v1一次schema失败，v2均成功，未重试追分。双方无失败35图自动F1 0.558→0.638。

### Status

[OK] **Completed**

### Next Steps

- 恢复先读 .trellis/tasks/09-11-image-review-v2/report.md 与总体计划；视频、SEDD、T09暂缓，不重做既有阶段。
- 图片仍需局部证据/重复框处理与FS、BLOCK分歧人工核查；不要将提示词指令当作硬约束，也不能宣称整体已优于YOLO。API配置无需再提供。


## Session 13: 图片召回保护与独立扫描受控实验

**Date**: 2026-09-11
**Task**: 图片召回保护与独立扫描受控实验

### Summary

默认保留候选复核并防止VLM抹掉YOLO；独检36图召回85.3%→88.2%但FP8→82，零检测补回0，未提升为默认。96次实测完成，视频/SEDD/T09暂停，未部署推送。

### Main Changes

- 两种图片模式及关联复检固定模式/Prompt/融合/视图版本；独检明确归一化坐标、本地裁剪映射；原检测全保留、冲突和新发现待审。
- 新增来源归因，原命中恢复与VLM真新增分列、零检测补回单列；固定36+12两轮完整保留，未重跑GPU、未改样本。

### Git Commits

| Hash | Message |
|------|---------|
| `jj moqulzpv` | (see git log) |

### Testing

- [OK] 后端479 passed/75 skipped；Ruff和diff检查通过；独立审查修复版本准入与视图pin遗漏。
- [OK] 真实Qwen96次：像素诊断轮17个schema失败、归一化轮0；只恢复1个错类目标（PL→BLOCK），原YOLO命中40个全部保留。
- [未通过] PG未启动，真实工作流集成准备失败；没有重启服务。错误堆栈意外含本地DB密码，已告知轮换建议，不存凭据。

### Status

[OK] **Completed**

### Next Steps

- 先读 .trellis/tasks/09-11-image-recall/report.md；当前默认candidate_review，independent_discovery仅显式实验。勿重跑本轮API追分。
- 优先零检测PL/FS、实例范围/定位、误报与重复候选分析；保留候选复核职责，不推进视频/T09或扩大模型栈。


## Session 14: YOLO 引导复核负结果与48图全量实物证据

**Date**: 2026-09-11
**Task**: YOLO 引导复核负结果与48图全量实物证据

### Summary

完成候选局部图引导复核、保守实验融合与固定36+12图测试；P/R未共同提高，不替换默认。全部原图、阶段PNG/SVG及真实API输入响应留档。

### Main Changes

- jj ruxvkskl：新增实验复核/缓存评估/全量图库；默认worker、环境和模型权重不变。
- 主集YOLO29/8/5→融合18/29/16；回归11/3/1→9/12/3。48请求记录、47有效响应、1网络失败无重试。
- 主集17/18源FS框与字幕区域重叠，已核对XML非绘图坐标错误；原标注不改，需人工核验。详细交接 .trellis/tasks/09-11-guided-review/report.md；实际证据 var/evaluations/2026-09-11-guided/index.html。

### Git Commits

(No commits - planning session)

### Testing

- [OK] 后端511 passed / 75 skipped；Ruff/格式/diff通过；全部48图原始字节、192张PNG尺寸及离线评分一致。

### Status

[OK] **Completed**

### Next Steps

- 先人工核验少量类别/字幕重叠/框范围分歧，再建立人工核验固定集；无已证实的联合精度增益，视频/SEDD/T09仍暂停。


## Session 15: 置信度分层复核与固定48图新实测

**Date**: 2026-09-11
**Task**: 置信度分层复核与固定48图新实测

### Summary

高分原框硬保护，低分仍可鉴别，整图继续补漏。21个高分预测全保留；原标注命中27→34，未补回YOLO原漏目标，不替换默认。完整PNG/HTML/API证据已保存。

### Main Changes

- jj kqysvuqx：Prompt v2、confidence fusion v2、阈值pin/边界回归、图库拟改类别可见；基线8c8dfb59。
- 用户确认主集7/11/22原高分应保留、17/18低分删除可接受；本轮17删除、18不确定保留，7响应schema失败回退；未改源GT。
- 报告 .trellis/tasks/09-11-confidence-review/report.md；证据 var/evaluations/2026-09-11-confidence/index.html。旧响应离线重放与新API实测分开，全部旧产物保留。

### Git Commits

本地 jj change `kqysvuqx`（包含实现与验证）；未推送远端。

### Testing

- [OK] 后端526 passed / 75 skipped；47项相关测试通过；独立审查无阻断问题，Ruff/格式及diff检查通过。
- [OK] 48份请求、47有效响应、1 schema失败无重试。主集24/34/10，回归10/13/2（TP/原标注未匹配/FN）；48原图、192PNG尺寸及离线汇总核对一致。

### Status

[OK] **Completed**

### Next Steps

- 先核验主集14/15/21/28与回归6的低分改类/删除及部分未标注新增，不继续无界重跑或据当前数据追阈值。
- 视频/SEDD/T09仍暂停；未部署、改环境或推送；worker默认不变。


## Session 16: T09 人工审核持久闭环与视频证据时间轴

**Date**: 2026-09-11
**Task**: T09 人工审核持久闭环与视频证据时间轴

### Summary

用户接受当前图片优化，恢复原计划。完成 T09 审核 API/页面、真实等待/恢复、视频候选时间轴；本轮无付费模型调用，默认复核策略和历史实验不变。

### Main Changes

- 原自动 final 保留为审核前检查点；人工决定、前后快照原子落库；请求幂等、审核取消串行、旧 pinless 不重开。
- 新任务 human-review-v1；页面原图/原框/视频时间证据与完整批次操作；独立审查后修复请求大小不一致和同任务页面缓存不刷新。
- 报告 .trellis/tasks/09-11-m2-human-review/report.md；总体计划与 backend/README.md 已更新。

### Git Commits

| Hash | Message |
|------|---------|
| `jj tmkruxqq` | (see git log) |

### Testing

- [OK] 后端564 passed/81 skipped；前端52 passed及构建通过；Ruff全项目及本次31个Python变更格式检查通过。
- [OK] 真实PG/Temporal隔离集成17 passed/1 live_api deselected；停worker后CPU恢复审核/取消、丢提交响应重试、HTTP确认OTHER/重复提交与报告均通过，模型为确定性替身。

### Status

[OK] **Completed**

### Next Steps

- 下一任务T10：首个YOLOE开放候选适配器及条件触发；无条件双跑、训练和新评测不在本阶段。先读T09报告与DEVELOPMENT_PLAN，不再沿用Session15的暂停决定。
- 本轮只启动原有loopback PG/Temporal；产品API/CPU/GPU worker未启动，public业务表未迁移，实际升级先init_storage再启动服务。未推送；浏览器视觉验收待后续联调。


## Session 17: T10 条件式开放候选与真实 GPU 验证

**Date**: 2026-09-11
**Task**: T10 条件式开放候选与真实 GPU 验证

### Summary

完成可替换 YOLOE、条件选择/预算/恢复及待审融合；默认关闭，下一步 T11。

### Main Changes

- 检查点复用、阶段工具记录、只增不删；三项集成审查问题已修复。
- 官方模型置于 var/models/yoloe，仅安装 CLIP/ftfy/regex；未改 .env、无付费 API、未启动产品服务或推送。

### Git Commits

| Hash | Message |
|------|---------|
| `jj prnozqso` | (see git log) |

### Testing

- [OK] 后端636 passed/84 skipped；真实PG/Temporal12 passed；前端52 passed/build通过；Ruff与diff检查通过。
- [OK] 固定sewer2403两图GPU冒烟；原始JSON/四张框图在var/open-scans/2026-09-11-t10-b8h4j412。并非准确率验收。

### Status

[OK] **Completed**

### Next Steps

- T11 小型固定集比较主检测/VLM/开放候选增量，人工区分漏标与误报；恢复先读T10 report.md。


## Session 18: T11 固定集增量评测与实图交付

**Date**: 2026-09-11
**Task**: T11 固定集增量评测与实图交付

### Summary

48图+8实帧固定重放，工程评测完成；开放模型未证明增量召回，仍默认关闭。

### Main Changes

- 新增固定清单校验、检查点恢复、阶段指标、图库及可定位人工审核CSV；修正YOLOE读取无扩展名媒体。
- 复用55成功VLM缓存及1历史错误，新增API请求0；未改.env、安装依赖、启动产品服务或推送。

### Git Commits

| Hash | Message |
|------|---------|
| `jj yvnxyszk` | (see git log) |

### Testing

- [OK] 后端667 passed/84 skipped；真实PG/Temporal开放扫描3 passed；Ruff与定向恢复通过。
- [OK] 56样本：32扫描成功、24未触发、13候选待审；21/21高置信原框保留，开放层无新增原标注召回；完整证据在var/evaluations/2026-09-11-incremental/verified。

### Status

[OK] **Completed**

### Next Steps

- 先读T11 report.md与review-resolution.md；下一任务T12图像增强/质量门控，自动增强无收益时保持关闭；准确率仍需人工真值。


## Session 19: 开放补检替代模型实测完成，VLM保持不变

**Date**: 2026-09-11
**Task**: 开放补检替代模型实测完成，VLM保持不变

### Summary

MM-GDINO Base与OWLv2 Large完成固定48图本地对比；模型效果未获替换验收，T12继续暂停。

### Main Changes

- 新增离线HF适配器和同图对比入口，保留VLM/主检测器/融合；无需回退，无新增VLM请求或推送。
- 完整三模型逐图证据：var/evaluations/2026-09-11-open-alternatives/index.html；详情见任务report.md。

### Git Commits

| Hash | Message |
|------|---------|
| `jj nzqxsnsw` | (see git log) |

### Testing

- [OK] 后端695 passed/84 skipped；Ruff及一次集成审查/定向修复复审通过。
- [OK] MM/OWL各27次GPU扫描成功、21未触发、0失败；新增待审96/715，新增原标注匹配0/1，新增原YOLO漏检命中均0。

### Status

[OK] **Completed**

### Next Steps

- 先读09-11-open-detector-alternatives/report.md并人工查看sample-0042、0004、0022；不自动启用补检或恢复T12。


## Session 20: 开放补检退出在线，保留VLM主链路及历史证据

**Date**: 2026-09-12
**Task**: 开放补检退出在线，保留VLM主链路及历史证据

### Summary

用户决定弃用YOLOE/OWLv2/MM-GDINO额外补检；保留代码及实验，不整仓回退。

### Main Changes

- 新任务/对话拒绝开放补检，正式worker仅保留旧检查点读取；主检测器、VLM和融合未改。
- 主方案及唯一总体计划同步收敛，详情见09-12-retire-open-detectors/report.md。

### Git Commits

| Hash | Message |
|------|---------|
| `jj oprqxzlv` | (see git log) |

### Testing

- [OK] 后端711 passed/84 skipped；Ruff通过；一次独立审查发现的旧计划入口P2已用四例红绿测试修复。
- [OK] 真实PG/Temporal恢复测试未重跑，服务当前停止；没有新GPU推理、API请求、删除或推送。

### Status

[OK] **Completed**

### Next Steps

- T12尚未开发；继续原计划时不要重新启用开放补检。部署需统一新版API/CPU/GPU worker。


## Session 21: 恢复 T12：三种增强工具与原图对照完成，在线集成待续

**Date**: 2026-09-12
**Task**: 恢复 T12：三种增强工具与原图对照完成，在线集成待续

### Summary

T12-A 完成，保留五类/VLM/融合及开放补检退出在线的边界；T12 整体未完成。

### Main Changes

- 统一派生资产/ROI 坐标映射、保守自动关闭门控、DehazeFormer-MCT/Restormer/Real-ESRGAN 三种真实本地适配器；仅新增可选 einops。
- 完整图库和 JSON 在 var/evaluations/2026-09-12-enhancement；详见 09-12-m4-enhancement-tools/report.md 与 model-report.md。无新 API 调用、服务启动、删除、回退或推送。

### Git Commits

| Hash | Message |
|------|---------|
| `jj nnvxzzxz` | (see git log) |

### Testing

- [OK] 后端 753 passed/84 skipped；Ruff 与 diff 检查通过；一次独立审查无 P1/P2。
- [OK] 三种 GPU 冒烟成功，三张 Sewer2403 的 9 次增强全部成功；累计工具耗时 12.782 秒。不代表检测精度提升；过暗仍未解决，超分可能平滑细纹。

### Status

[OK] **Completed**

### Next Steps

- 继续 T12-B：受约束对话、GPU Activity/持久检查点、阶段展示、报告和辅助复检。自动增强仍关闭；不要调整 VLM Prompt/融合或重新启用开放补检。


## Session 22: T12-B 图片增强对话与持久闭环完成

**Date**: 2026-09-12
**Task**: T12-B 图片增强对话与持久闭环完成

### Summary

显式图片增强、冻结原结论、原子资产检查点、双图辅助复核、UI报告和恢复验收完成；下一步T13，首版仍未整体完成。

### Main Changes

- 新图片按detect/verify后增强，原任务单次关联增强不重跑原检测；自动/视频增强关闭，原VLM与开放补检停用决定不变。
- 修复未知定位被填ROI及同请求重试重做部署准入；需求、设计、原图对照和交接保存在T12-B任务。

### Git Commits

| Hash | Message |
|------|---------|
| `jj vxkrkvwz` | (see git log) |

### Testing

- [OK] 后端797passed113skipped；Ruff/diff通过；前端63passed及build通过。
- [OK] 隔离PG/Temporal最终33passed1deselected；真实上传/对话/检测/SR/Qwen共3API1增强，28.08秒通过，文件保留在var/evaluations/2026-09-12-enhancement-online。

### Status

[OK] **Completed**

### Next Steps

- T13提示式分割/相对深度：限定原图坐标、仅像素与相对量，不无依据输出毫米值。先读T12-B report.md及唯一DEVELOPMENT_PLAN.md；基础服务运行，产品API/worker未常驻。


## Session 23: T12-C 按需增强与低照度恢复完成

**Date**: 2026-09-13
**Task**: T12-C 按需增强与低照度恢复完成

### Summary

默认不增强；本次对话许可后独立VLM有据选择一次工具或跳过，持久选择和原图证据保留。新增DarkIR在线与HVI六图对照；两次真实HTTP/Temporal样例合计7QwenAPI+1GPU。后端829/116跳过、前端69/build、真实PGTemporal31通过；修复选择失败降级原确认状态。报告 .trellis/tasks/09-13-m4-adaptive-enhancement/report.md；下一步T12-D候选与缺陷保真比较再T13，无推送。

### Git Commits

| Hash | Message |
|------|---------|
| `jj skwvwmyl` | (see git log) |

### Status

[OK] **Completed**


## Session 24: T12-D 增强候选实图比较完成，暂不替换在线模型

**Date**: 2026-09-13
**Task**: T12-D 增强候选实图比较完成，暂不替换在线模型

### Summary

八图六模型比较已交付；48 次增强、72 次检测、40 张对齐局部图。无候选获得在线替换依据，下一步回到 T13。

### Main Changes

- 新增离线 Concertormer RealBlur-J、ConvIR-L Haze4K、HAT-L x4 及分组图册，保留原图、模型输出、真实检测框/JSON、耗时显存。原 VLM/融合、检测器及 T12-C opt-in 在线策略不变。
- ConvIR 在暗光 sample-0025 严重失真，官方代码与同一权重定点复现；HAT 在 sample-0003 局部未检出 PL。未将执行成功或检测分数当成缺陷恢复成功，不替换默认模型。
- 恢复入口 .trellis/tasks/09-13-m4-enhancement-candidates/report.md；图册 var/evaluations/2026-09-13-restoration-candidates/index.html；模型仅存 var/models/enhancement，不推送媒体或权重。

### Git Commits

| Hash | Message |
|------|---------|
| `jj lmuslmlo` | (see git log) |

### Testing

- [OK] 最终后端 866 passed / 116 skipped（20.80s），Ruff backend/scripts 与 diff 检查通过；10 个 HTML 本地链接有效。一次独立审查及针对性测试修复匹配/局部图/卸载错误呈现。
- [OK] 固定实验全部本地 GPU：48 次增强及 72 次检测成功，0 执行/卸载失败；另做三候选 smoke 和两次 ConvIR 定点前向。无 API、新依赖、训练或哈希校验。前端及服务集成未重复运行。

### Status

[OK] **Completed**

### Next Steps

- 按 project_management/DEVELOPMENT_PLAN.md 启动 T13 提示式分割与相对深度；保留视频/自动关联增强和浏览器人工验收未完成状态，不宣称第一版完工。


## Session 25: T13-A 分割与相对深度工具及六图实测完成

**Date**: 2026-09-13
**Task**: T13-A 分割与相对深度工具及六图实测完成

### Summary

可复用原图框分割、相对深度和像素统计完成；六图12次工具成功，未接在线，下一步T13-B。

### Main Changes

- 新增SAM 2.1 Hiera Large与Depth Anything V2 Small本地适配器；本地官方权重、严格加载、单GPU串行，无API/新安装包/训练/哈希。
- 图册var/evaluations/2026-09-13-geometry-tools/verified/index.html；6张原图及提示框、掩膜/叠加、深度NPY/预览、实际JSON全部保留。恢复先读.trellis/tasks/09-13-m4-geometry-tools/report.md。

### Git Commits

| Hash | Message |
|------|---------|
| `jj kwvrmtsk` | (see git log) |

### Testing

- [OK] 最终后端918 passed/116 skipped（22.03s），Ruff backend/scripts及diff检查通过；独立核心审查两项问题已有失败用例后修复。
- [OK] 6分割+6深度全部成功，累计工具调用16.13s，另两次单模型smoke；逐张检查掩膜与深度，保留管口不等于缺陷面积、深度细节不足/遮挡伪影等限制，不宣称准确率提升。

### Status

[OK] **Completed**

### Next Steps

- T13-B：显式对话工具准入、原图框选择、串行GPU Activities、资产/调用原子检查点、进度和UI报告；验证常驻worker重复调用资源稳定性。主检测/VLM/融合与增强策略不变，开放补检停用，首版未整体完成；无推送。


## Session 26: T13-B 对话几何工具持久闭环完成

**Date**: 2026-09-13
**Task**: T13-B 对话几何工具持久闭环完成

### Summary

显式分割/相对深度接入对话、原子检查点、串行GPU与页面报告；下一步T14。

### Main Changes

- 恢复入口 .trellis/tasks/09-13-m4-geometry-online/report.md；原五类/VLM/融合不变，几何只作待复核证据。新检查在原图复检后执行；关联冻结父证据、不重跑检测/VLM、共享一次补证额度。
- 真实图册 var/evaluations/2026-09-13-geometry-online/live-0022-v2/index.html；成功链路26.43秒，两次QwenAPI及YOLO/SAM/Depth。首次意图误澄清已修复并保留失败记录，两轮共3API。无训练、新依赖、哈希或推送。

### Git Commits

| Hash | Message |
|------|---------|
| `jj qllqnxks` | (see git log) |

### Testing

- [OK] 最终后端967 passed/126 skipped（22.34s），前端78及build、Ruff和diff通过。独立审查两项边界与主审缺工具审核绕过问题均有回归修复。
- [OK] 真实PG/Temporal9 passed（13.28s）：掩膜回执丢失后复用、GPU worker替换后继续深度，保留空白图导致的人工等待，不伪造正常。6次同进程模型调用分配稳定约72MiB，非长期压力验收。

### Status

[OK] **Completed**

### Next Steps

- T14历史比较、版本化规范检索与报告完善；规范版本/条款待明确。T15整体/UI验收和T16部署待续，首版未整体完成。


## Session 27: T14-A 历史证据比较与报告完成

**Date**: 2026-09-13
**Task**: T14-A 历史证据比较与报告完成

### Summary

冻结两份已存检查，对话/页面/报告接通；真实PG/Temporal恢复及Qwen意图已验证，下一步规范输入。

### Main Changes

- 恢复入口 .trellis/tasks/09-13-m4-comparison/report.md。同会话已结束同媒体类型检查比较，七类逐审核状态计数；不重跑检测/VLM，不改源任务或预算，不推断真实缺陷变化。
- 证据 var/evaluations/2026-09-13-comparison/live-smoke；合成检查AJ确认1→2、另驳回1，实际Qwen意图与真实PG/Temporal worker替换通过。无新表/包/模型/哈希/推送。

### Git Commits

| Hash | Message |
|------|---------|
| `jj prpustlr` | (see git log) |

### Testing

- [OK] 后端1013 passed/137 skipped（24.66s），前端84和构建、Ruff及diff通过；一次独立审查无剩余Critical/Important。
- [OK] 真实PG存储20 passed（8.52s）；PG/Temporal比较及原对话恢复3 passed/1 deselected（10.65s）；实际Qwen比较冒烟1 passed（7.47s），冻结快照复用且源任务不变。

### Status

[OK] **Completed**

### Next Steps

- T14-B待用户提供规范编号/版本及PDF或官方来源，不自行评级；随后T15整体/UI/故障验收、T16部署交付。首版未整体完成；原检测/VLM/融合/增强几何不变，开放补检停用。


## Session 28: T15-A 核心链路与浏览器验收完成

**Date**: 2026-09-13
**Task**: T15-A 核心链路与浏览器验收完成

### Summary

真实Chromium上传对话、审核与报告通过；合成模型不计作准确率，下一步剩余整体验收和部署。

### Main Changes

- 恢复入口 .trellis/tasks/09-13-m5-core-acceptance/report.md；新增开发用Playwright和隔离测试服务，无生产业务代码或模型策略变更。图片/1.2秒视频合成夹具，真实HTTP/PG/Temporal/FFmpeg，0付费API。
- 证据 var/evaluations/browser/run-1789248229831-265724：5张截图、3任务报告、比较JSON及持久记录。Trellis为唯一生命周期，未推送/重启共享服务；测试schema已清理，文件保留。

### Git Commits

| Hash | Message |
|------|---------|
| `jj myszupvl` | (see git log) |

### Testing

- [OK] 后端1023 passed/137 skipped（23.81s），前端84及build、Ruff和diff通过；真实浏览器1 passed（23.9s），既有图片/视频/人工等待真实服务恢复8 passed（17.26s）。
- [OK] 一次独立审查发现清理时派发竞态及外层宽限不足；正常/异常退出两项失败回归后修复，夹具10项转绿，最终浏览器再跑通过。自动表单审核不代表用户对真实缺陷的审核。

### Status

[OK] **Completed**

### Next Steps

- T14-B仍待用户指定规范编号/版本及PDF或官方来源；推进T15剩余真实模型/UI、故障与资源/API成本验收及T16一体化交付。浏览器替身验收不证明准确率或长视频吞吐，首版未整体完成。


## Session 29: T15-B 真实图片浏览器联调与资源记录完成

**Date**: 2026-09-13
**Task**: T15-B 真实图片浏览器联调与资源记录完成
**Branch**: `-`

### Summary

真实YOLO/Qwen/分割深度/ROI超分经浏览器运行，保存失败与补跑；下一步T16。

### Main Changes

- 恢复先读 .trellis/tasks/09-13-m5-live-browser/report.md 与 var/evaluations/2026-09-13-live-browser/index.html。同一Sewer2403 sample-0022两真实任务；几何首跑成功，增强意图误澄清后仅补跑通过，未整组重跑。
- 仅修复IntentParser几何限制作用域，inspect允许verify+enhance；图像复检Prompt/融合/权重不改，candidate_review默认不变，开放补检停用。测试新增双开关、8请求预算、脱敏usage/显存记录与E2E类型检查；两个真实任务均待审，无人工提交。

### Git Commits

| Hash | Message |
|------|---------|
| `jj ztuwslnm` | (see git log) |

### Testing

- [OK] 最终后端1031 passed/137 skipped，前端84及build、Ruff通过；默认浏览器1 passed，真实首跑失败保留trace，增强筛选补跑1 passed。独立审查无剩余must-fix；未重复长视频或故障全矩阵。
- [OK] 6次实际Qwen请求，usage总15957tokens，标价估计约0.18元而非实际扣费；GPU分配峰值1.324GiB。两个任务工具前冻结发现与最终完全一致。测试schema已清理、GPU空闲，PG/Temporal保留运行；无远端推送。

### Status

[OK] **Completed**

### Next Steps

- T16一体化运行入口/交付说明，并核对T15剩余全项/故障/视频成本验收；T14-B等待用户规范编号、版本和来源。视频增强/几何、自动关联增强和用户最终界面验收限制仍在，首版未整体交付。
