# T15-A Implementation Plan

Trellis/executing-plans with repository overrides; existing jj, one scoped review.
Goal: add real browser acceptance over existing durable core workflows. Spec:design.md.

- [x] 后端测试服务：参考现有integration工厂，先验证显式启动门/固定fixtures，再接真实app+workers；只注入模型替身。监听18080，输出与cleanup遵守design。代理独立实现，主agent负责集成。
- [x] 前端仅开发依赖@playwright/test固定版本与Chromium；测试Vite端口18081代理18080，保留原开发默认；`test:e2e`显式启动测试服务，不复用其他进程。现有Vitest仅src收集。
- [x] 浏览器测试先写用户路径：`page.getByLabel('对话附件（可选）').setInputFiles(...)`（实际label先核实），输入检查文本→发送；使用服务器状态/DOM等待待审，reload再审核并检查报告；短视频复用同路径，检查证据帧/时间；第二图完成后选择两份比较。修复可复现问题，每个修复先失败回归。
- [x] 真实服务故障回归：运行test_image_workflow_faults_live.py、test_video_workflow.py、test_human_review_recovery.py，相关opt-in且排除live_api；不重启共享服务。不为已有充分证据重复重型模型实验。
- [x] 主agent浏览截图/HTTP与持久结果，进行一次独立审查；最终backendpytest/Ruff、frontendtest/build和浏览器验收；必要限制、依赖安装来源和执行命令写短report/README，更新路线图/Session28/jjcheckpoint，无推送。
