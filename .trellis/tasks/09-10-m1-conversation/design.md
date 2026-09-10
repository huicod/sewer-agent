# T06 设计边界
采用“模型提议意图、服务端生成计划”。不让 LLM 自由生成执行代码或无限工具循环。查询/解释/导出与新建检查、追加操作分离；TaskPlan 的 detect-first 约束只用于新检查，不用于每一条聊天消息。

切片 A：
1. 新 ConversationIntent 契约：inspect / ask / report / supplement / clarify / refuse；有限 action、关注五类、JSON/HTML 格式，不接收工具路径和任意参数。纯函数编译新检查计划，拒绝当前不可用能力；读请求不产生 TaskPlan。
2. IntentParser 复用现有 JSON 客户端；历史是有限的 user/assistant 文本，不能注入 system 角色。无内部重试、无工具执行；解析失败沿用安全错误码。
3. 报告端点 GET /api/tasks/{id}/report?format=json|html 仅读取终态快照和调用记录；运行中返回 report_not_ready。HTML 全部转义、无脚本，链接仅由服务端 AssetRef 生成；JSON 是版本化包裹，非模型自由摘要。内容包含运行状态、结论、覆盖、原始证据和工具失败/跳过记录。

切片 B：消息表及 request_id 幂等键、会话内选定任务边界、固定 workflow ID 和 Activity 检查点；真实多轮上下文与调用审计；关联补充操作沿用原始证据，不修改旧终态。再将 A 接到 Web 对话入口。细化实现时复用现有存储约定，不预建通用插件框架。

接口检查：解析模块只依赖既有 TaskPlan 与 JSON 客户端；报告只读 TaskRecord/ToolCall，二者独立。根 Agent 拥有 API 注册、文档和 jj；子 Agent 仅写独立解析文件。T05 的真实性验收仍开放。
