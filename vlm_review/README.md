# 管网检测 Agent 文档入口

更新：2026-09-09。

## 以哪份为主

1. [主方案：VLM_YOLO_ARCHITECTURE_AND_RESEARCH_SUMMARY.md](VLM_YOLO_ARCHITECTURE_AND_RESEARCH_SUMMARY.md)：需求与架构依据，第 9～10 节是已对齐的首版范围和运行协议。
2. [总体开发计划](../project_management/DEVELOPMENT_PLAN.md)：唯一里程碑/任务路线图；当前待用户批准。
3. [项目开发规则](../AGENTS.md)：开发边界、jj、Trellis 和 Skills 分工。

首版处理 CCTV 图片与离线视频，使用已有五类检测器，LLM/VLM 走 API；YOLOE、增强、分割和相对深度按需调用。工作流持久化、进度、故障恢复和人工审核是产品核心，不是附带脚本。

## 研究资料（不覆盖主方案）

- [管网论文与方法总览](research/summary_yolo_vlm_pipe_inspection.md)
- [VLM 增强检测专题](research/summary_vlm_enhanced_yolo_detection_survey.md)
- [画质退化与预处理](research/summary_pipe_dirty_data_preprocessing.md)
- [历史完整架构](research/architecture_yolo26_vlm_pipe_inspection.md)：含旧三类等假设，仅供按需查阅。

不要求开发前通读全部研究，也不把训练、蒸馏或额外传感器研究自动加入首版。
