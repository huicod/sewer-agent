# T02 阶段交付 / 续跑记录

日期：2026-09-09；本地 jj change：`tsksxomp`。用户已知情确认可信权重执行，GPU 单图验证通过；T02 仍待真实 API 联调。

## 已实现与验证

- T01：Trellis huicod、jj 工作规则、虚拟环境、FastAPI 存活接口和配置已完成，独立审查通过。
- 可替换 DetectorAdapter、五类结构化结果、原图坐标校验、真实 class map 映射和显式单图 smoke 入口；契约测试不加载真实权重。
- OpenAI-compatible LLM/VLM 客户端：Schema 校验，超时/限流/服务错误分类，禁止带凭据跟随重定向，不在客户端叠加重试。供应商响应错误不转为正常结论。
- 原生 PostgreSQL 14.24 + Temporal 1.31.2 已部署并保持本机运行；最小 init/start/stop/status 管理入口、分离角色和 0600 凭据。
- 完整本地测试：`.venv/bin/python -m pytest backend/tests -q` → **50 passed, 3 skipped in 2.37s**；跳过项为真实 LLM、真实 VLM、需显式启用的重启测试。
- 代码检查：`.venv/bin/ruff check backend scripts/runtime.py scripts/detector_smoke.py` → **All checks passed!**
- 显式真实重启测试：`SEWER_RUN_RESTART_TEST=1 .venv/bin/python -m pytest backend/tests/integration/test_recovery.py -q -s` → **4 passed in 7.23s**。测试 worker 被杀、Temporal/PG 重启后仍等待审核，signal 后完成，detected/completed 两个检查点各写入一次。
- 独立审查指出测试目标隔离与失败恢复缺口，已修正并复审通过。此用例证明已确认 Activity 的 replay 不重复，不宣称提交后丢 ACK 时 exactly-once。

## 未完成项与准确边界

1. 权重阻碍已解决：用户获知反序列化风险后明确授权，现有 best.pt 单图 GPU 验证通过；720×576 原图检出一个 AJ，score=0.931791，完整加载+推理耗时约 3.78 秒。实际类映射 0:PL、1:BX、2:FS、3:BLOCK、4:AJ；产物 `var/fixtures/detector-smoke.json`。这不是吞吐或精度基准。
2. LLM/VLM 供应商、模型、Base URL 与服务器本地密钥待配置；真实 API 测试未执行。已提供显式 opt-in 联调测试，普通测试不付费、不上传图片。
3. 当前是基础设施和适配器，不是完整检测 Agent。T03 统一任务契约、图片业务链、文本规划、视频、审核 UI 及后置模型尚未实现。
4. 直接依赖固定在 pyproject.toml；复用基础环境 torch/CUDA，不宣称完整传递依赖/系统镜像锁定。GPU 单图兼容已确认，性能基线需代表性回归集。

## 下一步

- 权重 smoke 已通过，无需重复；用户已要求继续，先推进不依赖 API 供应商的 T03 契约。
- API 配置完成后受控验证 LLM 文本与 VLM 图像协议；密钥只填服务器 `.env`，不要发到聊天。
- 真实 API 配置后补齐 T02 验收；T03/T04 可独立推进，真实 CCTV 视频在 M2 验收前提供即可。
- 运行命令见 `backend/README.md`。本次只保存本地 jj/Trellis，未推送 GitHub；总体计划仍是唯一路线图。
