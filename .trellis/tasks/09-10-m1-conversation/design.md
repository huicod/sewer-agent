# T06 设计边界
采用“模型提议意图、服务端生成计划”。不让 LLM 自由生成执行代码或无限工具循环。查询/解释/导出与新建检查、追加操作分离；TaskPlan 的 detect-first 约束只用于新检查，不用于每一条聊天消息。

切片 A：
1. 新 ConversationIntent 契约：inspect / ask / report / supplement / clarify / refuse；有限 action、关注五类、JSON/HTML 格式，不接收工具路径和任意参数。纯函数编译新检查计划，拒绝当前不可用能力；读请求不产生 TaskPlan。
2. IntentParser 复用现有 JSON 客户端；历史是有限的 user/assistant 文本，不能注入 system 角色。无内部重试、无工具执行；解析失败沿用安全错误码。
3. 报告端点 GET /api/tasks/{id}/report?format=json|html 仅读取终态快照和调用记录；运行中返回 report_not_ready。HTML 全部转义、无脚本，链接仅由服务端 AssetRef 生成；JSON 是版本化包裹，非模型自由摘要。内容包含运行状态、结论、覆盖、原始证据和工具失败/跳过记录。

切片 B1 已接通消息表及 request_id 幂等键、会话内选定任务边界、固定 workflow ID 和 Activity 检查点、多轮上下文、Web 对话入口。图片新检查的显式复检意图覆盖默认跳过策略；查询/报告只引用存储。切片 B2 已实现一次关联复检：原图片任务 completed/partial 后才能追加；子任务原子保存原检测及源快照，独立 verify/finalize，不改写旧终态。失败占用次数、子任务不能继续追加；前后实质冲突及原 OTHER 保持待审。复用现有存储约定，不预建通用插件框架。

接口检查：解析模块依赖既有 TaskPlan 与 JSON 客户端；报告只读 TaskRecord/ToolCall。B1 存储/UI 分开写入，根 Agent 整合 API/Workflow、验证并操作 jj。T05 真实原图 API 联调已通过；这不代表模型精度验收。
