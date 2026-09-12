# Retire online open-detector supplementation; preserve VLM and history

## Goal

保留 YOLOE / OWLv2 / MM-Grounding-DINO 代码、模型和实验，但退出在线调用，不整仓回退。用户已授权选择处理方式；VLM、主检测器及融合逻辑不改。

## Requirements

- 新图片/视频任务无 open_scan 配置或预算预留；遗留环境变量不能启用。
- 对话不推荐这三个模型；规划及直接启动拒绝显式开放补检，不静默忽略要求。
- 正式 worker 只保留同名历史 activity 的只读兼容处理：返回已完成检查点，未完成补检以非重试 tool_retired 结束，不运行模型。旧 workflow 命令顺序、schema 和历史报告保留。
- 离线脚本保留手动运行能力；不删权重/图库，不改历史结果，不新增依赖/API 调用，不推进 T12。

## Acceptance Criteria

- [x] 修改 test_open_scan_wiring.py、新增退休 worker 回归，先验证入口禁用测试失败。
- [x] 修改 agent/intent.py、agent/conversation.py、workflow/start.py；只读兼容 activities 替换 scripts/inspection_worker.py 的原注册。
- [x] 历史测试显式构造旧 run pins；验证禁用、检查点和失败不误报正常。相关/全量后端 pytest 与 Ruff 通过。
- [x] 简要更新 .env.example、backend/README.md、主方案和唯一总体计划；独立只读审查与修复完成，journal/本地 jj 检查点同批保存，不推送。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
