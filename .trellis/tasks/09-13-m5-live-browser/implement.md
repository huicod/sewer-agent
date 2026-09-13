# T15-B Implementation Plan

> 执行使用Superpowers executing-plans/TDD，Trellis项目覆盖规则控制任务，沿用jj工作区；一个独立审查。

Goal: live图片浏览器链路与可追溯资源记录。Architecture:扩展原测试服务的模型装配，复用持久链路。Stack:已有Python/HTTPX/Temporal/PG/React/Playwright。Spec:design.md。

- [x] 后端agent先写准入/8次预算/usage脱敏失败用例，再实现browser_live.py与browser_server可选装配、资源输出。默认夹具回归保留；agent未调用真实API/GPU。
- [x] 主agent实现真实浏览器场景、testMatch双开关和类型检查；无HTTP mock、人工提交或固定sleep，实际图像/JSON/截图保留。
- [x] 默认浏览器通过；live首跑几何完成、增强意图误澄清，保留失败。修正意图说明后仅补跑增强，共6请求，不重复几何。未启动规范检索。
- [x] 检查截图/冻结原证据/usage/GPU；官方标价估计与真实账单分开。一次独立审查无剩余must-fix，最终后端1031/137跳过、前端84/build、Ruff通过；report.md、Session29和jj ztuwslnm交接。
