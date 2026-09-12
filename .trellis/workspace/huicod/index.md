# Workspace Index - huicod

> Journal tracking for AI development sessions.

---

## Current Status

<!-- @@@auto:current-status -->
- **Active File**: `journal-1.md`
- **Total Sessions**: 20
- **Last Active**: 2026-09-12
<!-- @@@/auto:current-status -->

---

## Active Documents

<!-- @@@auto:active-documents -->
| File | Lines | Status |
|------|-------|--------|
| `journal-1.md` | ~673 | Active |
<!-- @@@/auto:active-documents -->

---

## Session History

<!-- @@@auto:session-history -->
| # | Date | Title | Commits | Branch |
|---|------|-------|---------|--------|
| 20 | 2026-09-12 | 开放补检退出在线，保留VLM主链路及历史证据 | jj oprqxzlv | `-` |
| 19 | 2026-09-11 | 开放补检替代模型实测完成，VLM保持不变 | jj nzqxsnsw | `-` |
| 18 | 2026-09-11 | T11 固定集增量评测与实图交付 | jj yvnxyszk | `-` |
| 17 | 2026-09-11 | T10 条件式开放候选与真实 GPU 验证 | jj prnozqso | `-` |
| 16 | 2026-09-11 | T09 人工审核持久闭环与视频证据时间轴 | jj tmkruxqq | `-` |
| 15 | 2026-09-11 | 置信度分层复核与固定48图新实测 | jj kqysvuqx | `-` |
| 14 | 2026-09-11 | YOLO 引导复核负结果与48图全量实物证据 | - | `-` |
| 13 | 2026-09-11 | 图片召回保护与独立扫描受控实验 | jj moqulzpv | `-` |
| 12 | 2026-09-11 | Sewer2403 图片复检 v2 与扩展配对验证 | jj vktuuypp | `-` |
| 11 | 2026-09-10 | T08 持久视频闭环与真实数据配对验证 | - | `-` |
| 10 | 2026-09-10 | T07 视频初查完成与复核增益评估待数据 | - | `-` |
| 9 | 2026-09-10 | T06-B2 关联复检完成与视频阶段交接 | `52b111a3f4e3` | `-` |
| 8 | 2026-09-10 | Qwen 实测通过与 T06-B1 持久对话闭环 | `56e3298ed279` | `-` |
| 7 | 2026-09-10 | Qwen API 接入与 T06-A 基础能力 | `74e22a4a9f3b` | `-` |
| 6 | 2026-09-10 | T05 图片工作流与 API 待验交接 | - | `-` |
| 5 | 2026-09-09 | T04 验收完成与 T05 交接 | jj wtkqtltt | - |
| 4 | 2026-09-09 | 首版 Agent 定位与压缩前交接 | jj wtkqtltt | - |
| 3 | 2026-09-09 | T03 contracts complete and trusted GPU smoke passed | `154f7d02` | `-` |
| 2 | 2026-09-09 | T02 local adapters and durable runtime checkpoint | - | `-` |
| 1 | 2026-09-09 | T01 harness and backend bootstrap | - | `-` |
<!-- @@@/auto:session-history -->

---

## Notes

- 恢复先读 Session 20 与 `.trellis/tasks/09-12-retire-open-detectors/report.md`。按用户决定，三个开放补检模型退出在线，保留离线代码、权重和实验；不整仓回退，主检测/VLM/融合未改。本地 jj `oprqxzlv`，711 后端测试通过、84 跳过，Ruff 通过；一次独立审查 P2 已修复。持久服务及产品进程当前未启动，未重跑真实恢复测试。T12 尚未开发，后续不重新启用开放补检；原实验融合也未切生产默认。

- Sessions are appended to journal files
- New journal file created when current exceeds 2000 lines
- Use `add_session.py` to record sessions