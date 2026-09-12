# 开放补检退出在线：交接

日期：2026-09-12；本地 jj change：`oprqxzlv`，基线 `b90355c1`。

## 决定与实现

采用保留代码/证据、停用正式调用，不回退整仓。三个模型的域内补检收益不足，用户决定舍弃该分支；不因此删除已完成的对话、恢复、人工审核等工程能力，也不继续搜索替代补检模型。

- 在线主链路仍为：五类主检测 → 条件 VLM（含负结果审计）→ 规则融合 → 必要人工审核/报告。主检测器可替换性、VLM Prompt、检测参数和融合策略未改；只调整了对话意图提示，不推荐已停用工具。
- 新图片/视频 run 无 `open_scan`，旧 `SEWER_OPEN_*` 环境配置不能启用它；规划器与启动配置入口拒绝显式开放补检。视频不再预留开放扫描预算，恢复原 VLM 选帧上限 32。
- 正式 worker 使用 `RetiredOpenScanActivities`，不注册原开放模型执行器。同名历史 activity 只返回已完成检查点；未完成项以非重试 `tool_retired` 失败，最终保留不完整/待审状态，不伪装正常。Workflow 命令顺序、旧 run pins、Finding 来源与历史报告不改。
- YOLOE 原适配器、OWL/MM 仅离线适配器、手动实验脚本、权重和全部图库保留。`var/evaluations/2026-09-11-incremental/` 与 `var/evaluations/2026-09-11-open-alternatives/` 未改写。

用户接受的 `guided-confidence-v2` 仍是既有实验策略，未在本次切换为生产默认；既有 worker `candidate_review` 同样未改。

## 验证与边界

- TDD：入口/旧配置/worker 退役测试修改前失败，修改后通过。相关测试 31 项通过（覆盖旧分支顺序、旧检查点、未完成项不能正常、真实 worker 注册入口）。
- `.venv/bin/python -m pytest backend/tests -q --tb=short`：**711 passed, 84 skipped**。
- `.venv/bin/ruff check backend scripts/inspection_worker.py`：通过。
- 84 项显式集成/真实服务测试仍跳过，本轮不宣称完成真实 Temporal 历史重放或服务重启验证；旧实现集成用例已改为显式历史 pins。运行检查显示 PostgreSQL/Temporal 未启动，API/worker 亦未运行；未启动任何产品或持久服务。
- 没有新 GPU 推理、付费 API 请求、依赖安装、真实 `.env` 修改、资产删除或远程推送。部署需统一启动新版 API/CPU/GPU worker，不能混用旧执行器。

主方案与唯一总体计划已按此收敛。T12 尚未开发，本轮不推进增强、分割、深度或视频效果优化。

## 一次独立审查与修复

审查确认正式 worker 无开放模型执行路径，发现一项 P2：旧 run 已有相同开放计划时，显式再次提交该计划没有在入口拒绝。主代理先用图片/视频、未终态/已终态四个案例复现失败，再将统一检查前移至 `InspectionStarter.start` 入口；四例及全量测试通过。不带新计划的历史恢复仍保留。审查意见已逐项处理，未新增审查循环。
