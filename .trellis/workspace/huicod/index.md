# Workspace Index - huicod

> Journal tracking for AI development sessions.

---

## Current Status

<!-- @@@auto:current-status -->
- **Active File**: `journal-1.md`
- **Total Sessions**: 32
- **Last Active**: 2026-09-14
<!-- @@@/auto:current-status -->

---

## Active Documents

<!-- @@@auto:active-documents -->
| File | Lines | Status |
|------|-------|--------|
| `journal-1.md` | ~1071 | Active |
<!-- @@@/auto:active-documents -->

---

## Session History

<!-- @@@auto:session-history -->
| # | Date | Title | Commits | Branch |
|---|------|-------|---------|--------|
| 32 | 2026-09-14 | T15-D 真实视频资源与API用量验收完成 | jj opxksrwn | `-` |
| 31 | 2026-09-14 | T15-C 故障矩阵与回执丢失浏览器验收完成 | jj stvsvnru | `-` |
| 30 | 2026-09-13 | T16 本机一体化运行与交付入口完成 | jj urlozrsx | `-` |
| 29 | 2026-09-13 | T15-B 真实图片浏览器联调与资源记录完成 | jj ztuwslnm | `-` |
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

- 恢复先读 Session32 与 `.trellis/tasks/09-14-m5-video-cost/report.md`；T15-D完成，jj `opxksrwn`。两次真实视频共1311帧/165块、13次Qwen请求/57533tokens，估算0.622020元；第二段8帧在发送前预算拒绝，两次均待审。原图/JSON在var/evaluations/2026-09-14-video-cost；实测2.05/2.22fps，扫描效率与非管内片段预算消耗仍待优化。完整后端1054/145默认跳过，Ruff与限定审查通过；不改产品策略/模型/.env/依赖，不推送。测试自有workflow/schema已清理，文件保留，应用127.0.0.1:8000持续ready。下一步T15剩余全项、业务服务中断、长视频效率/有效片段及用户界面验收；T14-B仍缺规范，首版未整体交付。

- Sessions are appended to journal files
- New journal file created when current exceeds 2000 lines
- Use `add_session.py` to record sessions
