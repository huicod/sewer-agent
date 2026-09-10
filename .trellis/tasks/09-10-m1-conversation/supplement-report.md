# T06-B2 交接与验收（2026-09-10）

这是当前恢复入口；B1 的 conversation-report.md 保留历史。T06-A/B1/B2 工程验证完成，下一步 T07 视频处理；并非完整首版或模型精度验收。

## 本轮交付

- 对话选定已完成/部分完成的原图片任务，可请求一次关联 VLM 复检；只支持 verify 加可选摘要/报告，混合未部署工具时全部不执行。
- PostgreSQL 原子保存子任务、parent_task_id、首次固定配置、原检测和源快照。同一 request_id 并发重试仍返回同一子任务；不同请求最多一个，失败也占用次数，不能建立子任务链。
- Temporal 独立 ImageSupplementWorkflow 仅 verify→finalize，不运行检测器，不改写原任务/报告。启动确认失败沿用同一身份；成功复检检查点可跨 Worker 恢复，最终落库可单独重试。已结束的复检不会因 /start 重做。
- 原 VLM 新增发现/OTHER 继续保留；两次实质判断不一致时待审核。原缺失/弃权的复检可被有效审核补足；复检失败不能得出正常。
- 页面显示关联复检及阶段，HTML 报告链接原任务，JSON 保留 parent_task_id；原检测器版本和调用记录留在原报告。未增加依赖、模型、通用插件框架或哈希校验。

## 验证与审查

- 最终命令：`SEWER_RUN_STORAGE_TEST=1 SEWER_RUN_IMAGE_WORKFLOW_TEST=1 SEWER_RUN_CONVERSATION_TEST=1 .venv/bin/pytest -q backend/tests`：**398 passed / 6 skipped，51.47s**。跳过 GPU、共享服务重启和 4 个付费 API 项，不计为本轮通过。
- 新存储用例 23 个覆盖事务回滚、并发同/不同身份、重开、预算与源证据限制；新真实 PG/Temporal 用例 3 个覆盖 HTTP 对话→复检→追问/报告、429 重试、分派存储失败、OTHER 保留、Worker 替换和最终写库失败。均未启动 GPU Worker，外部 API 使用可控替身。
- 前端 **27 passed**，TypeScript/Vite build 通过；Ruff lint 与 diff 检查通过。全仓格式检查存在历史格式差异，只整理本次新增/相关代码，未顺手重排无关文件；未做完整浏览器视觉验收。
- 按 TDD 做失败先行。独立 Sol 审查发现配置变化失败路径遗漏 prior OTHER，已通过参数化 Activity 测试复现并修复；保留冻结源发现。另在实际生产共享 verifier 中前置复检策略版本核对，测试确认不匹配时不调用 API。复核无剩余 Critical/Important 问题。
- 本轮未新增付费请求。Qwen 真实文本、原始 CCTV ImageReviewer 与对话联调沿用上一阶段已验证证据；此处不把替身测试说成准确率提升。

## 当前环境与恢复

- 本轮稳定 jj change：`mkomtpts`；父 B1 检查点 `70a4af21`。最终提交 ID 从 jj log 查询，不循环修改自引用哈希；未推送 GitHub。
- `scripts/init_storage.py` 已执行，增量创建关联表，无旧数据删除。生产 CPU Worker 在独立 smoke 队列启动注册成功后已停止；PostgreSQL/Temporal 保持运行，API/GPU/Web 未常驻。启动方式见 `backend/README.md`，仅绑定本机。
- LLM/VLM 继续使用 qwen3.8-max 与现有 `.env`；不再索要 Key/Base URL，不输出凭据。生产仍用 httpx Chat Completions。
- 下一步 T07：视频解码、逐帧主检测、跨块事件聚合和证据选帧；T08 再接长任务恢复，T09 接人工等待。真实 CCTV 视频仍待用户提供，可先用合成视频验证工程链路。
- 仍未交付视频执行、取消、人工审核入口、增强/分割/深度和成本计量。远端成功而本地未保存的窗口可能重复计费；不承诺 exactly-once 或模型准确率提升。
