# T12-B 交接：图片增强接入对话与持久工作流

2026-09-12；huicod；jj change `vxkrkvwz`，基线 `e1638a63`。图片增强在线闭环及恢复补丁已验收。原 VLM Prompt、主检测及原融合不变；YOLOE/OWLv2/MM-Grounding-DINO 继续退出在线，不整仓回退。

## 功能与边界

- 新图片：对话/上传 → 原图检测与 VLM → 冻结基线 → 显式增强 → 原图/增强图辅助 VLM → 结果或人工等待。无增强参数的旧任务不产生新工作流命令。
- 原图片可追加一次关联增强，与原来的追加复检共用一个补证额度；复用冻结父任务证据，不重跑检测或原图 VLM，不修改父任务。一次一种去雾、去模糊或局部超分；超分缺像素 ROI、视频/派生图片、越界/超预算或模型缺失先明确说明。
- GPU 串行，模型与参数固定；派生资产、成功调用、检查点在同一事务登记。提交后响应丢失读已有产物；提交前崩溃仍可能重算/重复计费，不承诺 exactly-once，不新增哈希系统。
- 增强建议不能删除、改类、移动或改分原发现；冲突与新增疑似异常待人工确认。无法定位保持空坐标。原图覆盖、画质和阴性审计不因增强分数提高而升级。必需工具失败输出不完整状态，不送入可误点完成的人工等待。
- 页面按需展开原图/增强图、实际 ROI、质量、模型及辅助说明；阶段显示正在执行的增强类型。详情 API 可读中间结果，终态 JSON/HTML 报告带相同证据。

自动增强仍关闭；本轮未实现视频增强、分割、相对深度或额外模型，不新增依赖，不训练模型。前端组件测试与构建不等于浏览器人工视觉验收。

## 真实样例（不是准确率实验）

[对照入口](../../../var/evaluations/2026-09-12-enhancement-online/index.html)；[逐阶段执行记录](../../../var/evaluations/2026-09-12-enhancement-online/execution.json)；[任务快照](../../../var/evaluations/2026-09-12-enhancement-online/task.json)。

使用前轮 Sewer2403 `sample-0022/original.jpg`（720×576），原图 ROI `[150,50,406,306]`，Real-ESRGAN general x4v3 输出 1024×1024。测试从 HTTP 上传和自然语言请求进入真实 Temporal/PG，包含现有权重初查与本机 GPU 超分。

实际 **3 次 qwen3.8-max API**：文本意图、原图复检、双图辅助复核；**1 次增强推理**。记录约 27.19 秒，pytest 全过程 28.08 秒。增强阶段约 1.12 秒（阶段观测差，非纯推理基准）。辅助复核表示可判读性改善，无新增/冲突；原基线 AJ 0.9409 与 FS 建议保持不变，任务仍为 `waiting_review/inconclusive`，没有将建议自动确认。

目录保留 `original.jpg`、`enhanced.png`、上传/对话请求、`detect.json`、`verify.json`、`enhancement_source.json`、`enhance.json`、`enhancement_review.json`、工具调用和阶段记录。仅删除隔离测试 schema 并终止其等待中的测试工作流，不假造人工决定；结果文件保留，但不是常驻产品会话。密钥未落盘到证据或仓库。

这一例证明工具链真实可用与证据保留，**不证明准确率、召回率提高**；未重新扩大数据集或修改原实验。

## 验证与审查

- `.venv/bin/pytest -q backend/tests`：797 passed、113 skipped（13.68 秒）；跳过项需显式服务/模型/API 条件，不混算为通过。`.venv/bin/ruff check backend scripts`、`git diff --check` 通过。
- 前端：63 passed；TypeScript/Vite build 通过。
- 真实 PG 原子/并发与既有对话/关联复检：30 passed、1 个付费项 deselected（29.76 秒）。
- 新增真实 Temporal/PG 恢复：3 passed（8.15 秒）；覆盖完整顺序、增强提交后丢确认/更换 worker、模型只执行一次、冲突人工等待继续、关联任务复用冻结父证据。
- 恢复补丁后的最终合并服务回归：33 passed、1 个付费项 deselected（37.72 秒）；包含上述 PG/新旧对话/关联复检/增强恢复，无付费调用。
- 真实付费/本地 GPU：1 passed（28.08 秒），上述 3 次 API + 1 次增强，未再次付费重跑。
- 一次只读集成审查发现“无法定位却填整个 ROI”问题；已用先失败后通过的测试修复。主代理检查前端/API/报告对齐。实现细节和各切片测试见同目录 storage/conversation/presentation-report。
- 另补同一 request_id 接纳后的恢复：重试先找已存在任务，再验证不可变身份，不重做模型部署准入；即使模型文件后来不可用也不遗失原链接/配置。新请求仍检查模型。定向回归 80 passed；真实 PG 同一子任务/固定 run 保留通过，终态重试不再误称“尚未完成”。

## 恢复入口与下一步

操作和复现：[backend/README.md](../../../backend/README.md)；唯一路线图：[DEVELOPMENT_PLAN.md](../../../project_management/DEVELOPMENT_PLAN.md)。继续 T13 提示式分割/相对深度，先限定输入、原图坐标和非绝对量测，再接真实模型与现有工具链；保持当前 VLM 和开放补检停用决定。自动增强是否启用须有另行域内效果依据。

本轮按 Trellis 保存需求、设计、测试与交接；按 stop-that-shit 限制在图片切片、一次审查和受控调用内。本地 jj 保存，不推送 GitHub。未改 `.env`、原始媒体、已有权重或旧实验；本轮启动的基础 PostgreSQL/Temporal 保持本机健康运行，产品 API/CPU/GPU worker 不常驻。
