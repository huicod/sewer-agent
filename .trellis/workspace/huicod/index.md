# Workspace Index - huicod

> Journal tracking for AI development sessions.

---

## Current Status

<!-- @@@auto:current-status -->
- **Active File**: `journal-1.md`
- **Total Sessions**: 28
- **Last Active**: 2026-09-13
<!-- @@@/auto:current-status -->

---

## Active Documents

<!-- @@@auto:active-documents -->
| File | Lines | Status |
|------|-------|--------|
| `journal-1.md` | ~932 | Active |
<!-- @@@/auto:active-documents -->

---

## Session History

<!-- @@@auto:session-history -->
| # | Date | Title | Commits | Branch |
|---|------|-------|---------|--------|
| 28 | 2026-09-13 | T15-A 核心链路与浏览器验收完成 | jj myszupvl | `-` |
| 27 | 2026-09-13 | T14-A 历史证据比较与报告完成 | jj prpustlr | `-` |
| 26 | 2026-09-13 | T13-B 对话几何工具持久闭环完成 | jj qllqnxks | `-` |
| 25 | 2026-09-13 | T13-A 分割与相对深度工具及六图实测完成 | jj kwvrmtsk | `-` |
| 24 | 2026-09-13 | T12-D 增强候选实图比较完成，暂不替换在线模型 | jj lmuslmlo | `-` |
| 23 | 2026-09-13 | T12-C 按需增强与低照度恢复完成 | jj skwvwmyl | `-` |
| 22 | 2026-09-12 | T12-B 图片增强对话与持久闭环完成 | jj vxkrkvwz | `-` |
| 21 | 2026-09-12 | 恢复 T12：三种增强工具与原图对照完成，在线集成待续 | jj nnvxzzxz | `-` |
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

- 恢复先读 Session 28 与 `.trellis/tasks/09-13-m5-core-acceptance/report.md`。T15-A 核心浏览器验收已完成，jj `myszupvl`：真实HTTP/PG/Temporal上的图片/短视频对话上传、待审刷新、表单审核、报告/时间线/比较通过，模型与媒体明确为合成替身，0付费API。证据 var/evaluations/browser/run-1789248229831-265724；后端1023通过137跳过、前端84/build、浏览器1和既有真实恢复8通过。T14-A保存比较已完成；T14-B缺规范文档，T15剩余真实模型/UI、故障及成本验收和T16部署待续。首版未整体交付；原检测/VLM/融合/增强/几何不变，开放补检停用，无推送。

- Sessions are appended to journal files
- New journal file created when current exceeds 2000 lines
- Use `add_session.py` to record sessions