# T15-B 设计

这是既有验收入口的有限扩展，不创建另一个产品部署器。相比复制整套live测试服务，扩展browser_server的可选模型装配，保持默认替身、HTTP/PG/Temporal与清理路径共用。相比直接使用常驻业务库，保留唯一schema和queue，避免混入真实用户任务。

## 固定接口
- `SEWER_RUN_BROWSER_LIVE=1 SEWER_RUN_LIVE_API=1 SEWER_LIVE_IMAGE=/absolute/image.jpg npm --prefix frontend run test:e2e`。Playwright沿用18080/18081及SEWER_BROWSER_OUTPUT，live只收集live.spec.ts，默认只收集core.spec.ts。
- 后端仍要求SEWER_RUN_BROWSER_TEST=1。live双开关缺一不得加载真实模型/API；SEWER_LIVE_IMAGE要求现有绝对图片路径。源图复制到`service/fixtures/image.jpg`供浏览器真实上传，并保存source metadata，非合成夹具。
- `backend/tests/browser_live.py`负责live模式装配/记录，必要测量仅放test utilities。沿用ImageReviewer、IntentParser、EnhancementReviewer与UltralyticsDetector、本地SAM/Depth/超分；只增加记录和预算，不改模型响应/Prompt。
- API包装器使用真实httpx传输和生产client，只保存脱敏messages（image_url替换标记）、经过schema校验的结果及白名单usage整数，不保存Authorization、key、图像base64或供应商完整错误体。请求发送前消耗共享8次额度，超限non-retryable安全错误；失败请求也计入。无自动重跑付费套件。
- 模型装配向browser_server提供原detector/reviewer/parser/starter与额外CPU/GPU activities（enhancement/geometry及supplement），复用已有workflow。几何/增强不得改变原检测证据；全部GPU activity串行。
- 最终输出除现有service-summary外，保存`live-execution.json`（provider_calls、模型名、GPU分配/保留峰值、计费未知项），每个task的tool calls和相关步骤检查点JSON，失败也保存。不得将报告里待审状态改成完成。

## 浏览器场景
同一sample-0022源图运行两个新任务（不是两个独立精度样本）。第一条使用既有验证prompt：先五类检测和VLM，原图[280,66,549,350]分割并relative_measure。第二条先检测/VLM，再[150,50,406,306]超分及辅助复核。预计2+3次API。
页面等待真实任务到completed/partial/waiting_review，再读取并验证所需工具均成功；failed或必需工具缺失须失败。展开几何/增强面板，滚动使图片实际加载，检查naturalWidth>0与证据GET200，保存局部截图与JSON；刷新后仍读取同一任务。不提交任何人工审核。终态报告仅在真实可用时保存，否则明确待审核。

## 所有权/后续
后端agent仅改browser_live.py、browser_server.py及相关测试；主agent负责Playwright/live.spec.ts、实际GPU/API运行、截图检查、成本查询、文档与VCS。一个只读独立review。T16、规范检索、长视频压测及新模型策略不纳入本切片。

实现中确认原前端build仅类型检查src，未覆盖e2e；新live测试曾误用task.task_id（应为snapshot.task_id）。因此test:e2e增加执行前类型检查及固定@types/node开发依赖，不改业务依赖。

实测调整：首跑几何成功，第二条新检查被意图模型错误澄清，累计3次API。
保留失败记录；只修正IntentParser中几何组合限制的作用域，明确inspect允许verify+enhance，
不改看图复检Prompt/融合规则。浏览器拆成两个可独立筛选的场景，只补跑增强，避免重复几何付费。
因此本轮证据来自两次启动，不能把首跑记为整组通过或声称修订后的两场景整组已重跑。

费用参照2026-09-13查询的[官方qwen3.8-max北京原价](https://help.aliyun.com/zh/model-studio/qwen3-8-max)：输入12元/百万tokens，输出36元/百万tokens，普通缓存命中输入1.5元/百万tokens。本机LLM/VLM均为dashscope.aliyuncs.com、qwen3.8-max。测试usage可用于标价估计，不能充当账户实际扣费（套餐、优惠及缺失回执均另计）。
