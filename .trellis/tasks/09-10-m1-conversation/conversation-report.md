# T06-B1 交接与验收（2026-09-10）

本文件保留 B1 历史验收；最新 B2/T06 完成交接见 [supplement-report.md](supplement-report.md)，以下 B2 待办已由后续阶段完成。T06-A 及旧认证失败记录保留为历史。总体路线图仍仅维护 `project_management/DEVELOPMENT_PLAN.md`。

## 已交付

- 网页检查助手：文本＋可选新附件或已有任务，持久记录、刷新恢复、阶段轮询、歧义重试沿用原请求及资产；附件与旧任务二选一，防止身份冲突。
- PostgreSQL 对话表＋Temporal `ConversationWorkflow`：规划→分派→持久结束；每会话一条活跃消息，意图/计划/分派检查点，重复启动使用固定 workflow ID。
- 新图片检查复用既有五类检测器和图片工作流。解析出的 verify 请求强制复检；必要复检失败保留 partial。延迟启动即使遇到已终结任务，也验证首次固定的计划，不能悄悄跳过所需操作。
- 追问/报告引用保存的状态、候选、覆盖与 VLM 说明，不重复检测或 VLM；只读问句仍需 LLM 解析。对话说“已启动”不表示关联检查已完成。
- 未部署工具、视频执行、缺证据及 supplement 明确说明限制。T06-B2 关联补充复检尚未实现，T06 保持 in_progress。

## 验证与修复

- 最终后端包含真实本地 PostgreSQL/Temporal 回归：348 passed、6 skipped；跳过 GPU、共享服务重启与 4 个付费 API 项。这些跳过项不计为本次通过。
- 新对话真实 PG/Temporal 两用例：HTTP 启动图片→追问→报告→重新打开存储；丢失分派提交确认；worker 替换后复用意图检查点；最终存储失败只重试落库，不重跑模型/工具。
- 前端 26 passed，TypeScript/Vite 构建通过；Ruff、diff 检查通过。新增行为按 TDD 记录 RED/GREEN；未做完整浏览器视觉验收。
- 真实 qwen3.8-max 对话最终 1 passed（3.50s，输出上限 512，关闭思考），仅引用合成已存结果，不启动 GPU/VLM。初次失败为 ask 携带 actions：诊断确认跨字段规则未出现在 Schema 提示中，补充规则并固定 `conversation-v2` 后通过；保留严格拒绝，不静默修改模型输出。
- SDK/原始 CCTV 图片真实联调另见 `../09-09-m1-image-workflow/qwen-live-success.md`（检查点 6b8c4942）。原图额外 FS 为人工待确认建议；工程通过不是模型准确率提升证据。旧认证失败已不再复现，不能归因为安装 SDK。
- 独立审查最终 PASS：附件/任务冲突按回归修复；终态计划核对补充复核通过。两项范围疑问已按实际架构澄清：语义工具选择可能误判；原始上传为无鉴权单用户全局资产，并非多用户私有媒体。任务/关联任务必须同会话。

## 恢复入口与下一步

- 测试代码锚点 `56e3298ed279`；稳定 jj change `zmoplozu`，父 SDK 证据 `6b8c4942`、更早 T06-A `499519c2`。最终文档快照可由 jj log 查询，不循环修改自引用哈希；未推送 GitHub。
- `.venv` 已装 openai 3.11.0 / dashscope 1.27.4；生产保留 httpx Chat Completions。LLM/VLM 使用 `.env` 的 Qwen 配置；无需再索要 Key/Base URL，禁止输出密钥。
- 本机业务表已追加初始化。PostgreSQL/Temporal 保持运行；API、CPU/GPU worker 和网页按 `backend/README.md` 启动，仅绑定本机，不视为已开机自启/公网部署。
- 下一步只推进 B2：关联补充复检、原始证据复用、旧终态不改写、限制追加次数；之后 T07/T08 视频。真实 CCTV 视频仍待用户提供，合成视频可用于工程测试。
- 语义模型并非绝对正确；远端成功但本地未保存时可能重复计费。未实现完整视频、用户取消、人工审核等待、增强/分割/深度、成本计量；不新增第二编排器、不训练检测器、不做文件/模型哈希。
