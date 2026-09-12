# T12-B 原子增强检查点

先读 AGENTS.md/governance/Trellis overrides。这是已批准图片增强闭环的一项，不是新平台。只允许修改 `backend/src/pipe_inspector/storage/inspection.py`、为连接复用必要时修改 `storage/repository.py` 的 save_asset 提取 helper、`backend/tests/integration/test_enhancement_storage.py` 和本目录 `storage-report.md`。不嵌套委派，不操作 jj/Git，不装包，不动其他文件；不启停共享服务。主代理并行做契约/工作流，另一个范围做前端/报告。

实现 `InspectionRepository.save_enhancement(task_id: UUID, result: EnhancementResult, call: ToolCall) -> dict`，固定 step=`enhance`，返回 winner 的 EnhancementResult JSON。现有 contracts/enhancement.py 可直接使用。不加表；任务行锁、派生资产登记、成功调用和 step checkpoint 必须一事务。先读已有 checkpoint，重试/晚到的新随机输出 UUID 不登记第二份资产、不改 winner 调用。原图文件不可删除；事务失败后未登记 bytes 保留待诊断，不能在提交状态不明时删文件。

首写校验：task/input 同归属；input资产原图尺寸与 result.source_region 一致；result 输出新派生资产且 parent 对应；call task/step/status=succeeded/tool_name=enhance/resource=gpu/input_assets=(input_ref,)/output_assets=(output_ref,)/model_version 一致。检查已有 run 的 `config.enhancement` pin：`version=enhancement-online-v1`，`options={kind,roi_xyxy}`，`model_id`、`model_version`、`call_id` 与 result/call 对应；idempotency_key 为 `{run_id}:enhance`。按 options 全图或 floor/ceil ROI 校验 source_region；去雾/去模糊尺寸保持，SR ×4。异常/跨任务调用拒绝，不留部分资产或成功调用。读取 winner 时至少校验 task/call的逻辑身份，允许重试新结果 UUID 与winner不同，不将其当新成功。

TDD：复用现有 test_inspection_storage.py fixture，可通过显式导入 fixture；先运行红（本地 PG 若未运行，先编写并使用定向无需连接检查或请求 root 启动；不要把 skip 当 red）。正常后覆盖持久 roundtrip、不同随机输出的重复提交、两线程并发赢家、晚期 call 身份冲突事务回滚、错误 task/input/parent/ROI/model/pin/status 拒绝。真实测试需 `SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_enhancement_storage.py`，每例独立 test-owned schema。Ruff 只检查你修改的文件。

root 将协调启动现有 PG/Temporal，收到可用消息后你可运行该隔离测试。不运行全量后端套件。自审后写 storage-report.md，说明 red/green 命令结果、文件、风险。root 统一整合及一次独立审查。
