# T06-A 解析子任务（先读本文件）
唯一目标：实现可测试的对话意图解析与服务端合法计划编译，暂不接 HTTP/DB/Temporal/UI。
允许写：backend/src/pipe_inspector/contracts/conversation.py、backend/src/pipe_inspector/agent/intent.py、backend/tests/test_conversation_intent.py、本目录 planner-report.md。只读参考 contracts/planning.py、agent/api_client.py、contracts/common.py。不改现有契约、不加依赖。根 Agent 独占集成和 VCS。

接口与要求：
- ConversationIntent 继承 ContractModel，kind enum inspect/ask/report/supplement/clarify/refuse；target_labels 默认现有全部 DetectionLabel、唯一非空；actions 为 ActionKind 元组、唯一，最多 8；outputs 默认 json/html、唯一非空；message 1..2000 是提议/说明而非有事实权威的答案。无路径、任意 params、model 或预算字段。
- ask/report/clarify/refuse 不能携带 actions；supplement 必须非空且不可含 detect；inspect actions 仅表示额外请求（不含 detect）。
- compile_inspection_plan(intent, *, available_actions) -> TaskPlan | None：非 inspect 返回 None，无检测副作用。inspect 服务端注入 detect 首步；每步串行依赖；强制 verify 策略节点（实际由质量/候选策略决定调用，不能作为 skip-normal 漏洞），末尾 summarize/export_report；请求 relative_measure 自动补 segment。请求不可用 action 时抛安全 IntentPlanningError(code='tool_unavailable')，不得静默删除。focus 只改报告标签，不过滤检测五类。沿用已有 TaskPlan 和预算，不建立另一套执行器。
- IntentParser(client).parse(text, history=()) 异步调用现有 client.generate 一次，输出 ConversationIntent。text <=4000，history最多10条，每条 role仅 user/assistant、content <=4000、禁止额外字段；超过上限拒绝，不暗中截断。采用契约约束历史输入；测试伪造 system 角色不会发 API。
- system prompt 含五类中文含义，OTHER待人工，CCTV only，无标定不能量毫米/体积；当前能力由调用方验证，LLM 不能自由执行；明确输出 JSON。无任意 shell/文件/网络工具。不要仅用关键词替代 LLM。
- 单测 RED -> GREEN：合法新计划、只读无计划、依赖/预算、无效 intent、未知动作、不可用动作拒绝、历史注入/长度、mocktransport非法JSON不假成功、一次请求且无内部重试。

边界：此切片是“解析及计划基础”，未接产品对话，不得宣称完整 Agent 已上线；不运行真实 API、不读取 .env。工具 exec 需要 require_escalated。宿主 apply_patch Update 会 bwrap失败：用 apply_patch Add 创建新文件即可，不重复绕圈；仅如确需改自己文件，全读后 Add 完整保留。
读 AGENTS.md 与适用规范，TDD；不嵌套委派，不执行 jj/Git 可变操作。报告记录精简 RED/GREEN 命令、结果、未知项。
