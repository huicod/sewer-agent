# T12-A pretrained enhancement tools and conservative quality gate

## Goal

Three local pretrained enhancement adapters, original evidence and ROI mapping, conservative gate, small CCTV comparison

## Requirements

- 沿已批准 T12 交付工具切片 A；范围见 design.md。三种真实预训练模型通过同一 PIL RGB 适配器运行，限本地可信权重，保留原图和版本信息。
- 原始坐标 ROI 超分必须显式，增强图区域可映射回原图；缺模型、越界、非法结果与资源超限明确失败。
- 复用现有质量摘要，自动执行默认关闭，门控只作建议，不将低对比认定为雾或将分数提高认定为检测增益。
- 给出少量真实 CCTV 原图/增强图及 JSON 对照，不调用 VLM、不改变原检测/融合、不恢复开放补检。

## Acceptance Criteria

- [x] 契约、质量门控与安全工具 runner 的先失败后通过测试。
- [x] 三个真实预训练适配器，最小依赖、来源与许可、逐模型 GPU 冒烟。
- [x] 小样本图库和原始 JSON；全量后端回归、Ruff、一次独立审查。
- [x] 保存 Trellis/jj 和后续 T12-B 对话/Temporal/报告集成边界，不宣称 T12 整体完成。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
