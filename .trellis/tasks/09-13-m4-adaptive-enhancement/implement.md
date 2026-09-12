# T12-C Implementation Plan

Trellis 控制状态；使用 writing-plans 的分步验证方法和 TDD，不建立第二套计划。需求/边界见同目录 prd.md、design.md。代码在本 jj change 中按不重叠文件范围并行；root 负责契约、工作流、存储与整合。

## 1. Root：契约与选择闭环

- [x] 在 `test_adaptive_enhancement.py` 先验证 `ConversationIntent(kind='inspect', actions=('enhance',), auto_enhancement=True, message='按需检查')` 能生成条件增强计划；默认关闭、显式互斥、视频/自动关联拒绝、未允许不新增调用。
- [x] `contracts/enhancement.py` 增加 LOW_LIGHT、EnhancementProposal（options/degradation/evidence/inspection_impact/confidence）、EnhancementSelection（status/reason/proposal/options）；conversation/planning 增加 bool。intent prompt 明确仅本次许可、不把低分当触发；dispatcher/start 固定候选与安全准入。
- [x] 新 `agent/enhancement_selection.py`、`workflow/enhancement_selection.py`：独立单图选择请求与严格策略，缓存选择，按原图尺寸校验。测试清晰图跳过、低光选工具、uncertain/工具不匹配/越界/预算拒绝，捕获实际发送的图片和 Schema。
- [x] workflow/image 仅 auto pin 新命令；selected 才衔接既有三步增强，skipped 不跑 GPU；失败 partial。storage/inspection 在锁内解析持久化选择和固定候选，验证后沿用已有原子保存。

## 2. 独立低照度适配切片

- [x] 写范围限 vision/enhancement.py、vision/enhancement_models.py、vision/_enhancement_vendor、独立 lowlight tests 和模型获取/离线对照脚本。先红绿测试 RGB/尺寸/非有限值/权重键/奇数尺寸，再复用本机依赖接 DarkIR 与 HVI 可用实现，不修改契约/工作流。必要兼容修复：旧 evaluation/enhancement 保持原三种工具，不随新 registry 暗中扩展。
- [x] 查官方代码和权重、记录来源与许可证；下载只进 var/models/enhancement。真实 GPU 少量 Sewer2403 对照，产物保存在本轮新目录。报告质量与时延，发现不兼容及时反馈，不扩大依赖或自行调整主 Prompt。

## 3. 展示与恢复验证

- [x] root 定义 selection 契约；presentation 独立切片发布详情/报告和前端，显示选择中/选中/跳过理由及低照度名称，保持原对照面板按需查询。测试旧报告可读、跳过、选择中及低照度结果。
- [x] 定向 pytest → 全后端 pytest/Ruff；前端 tests/build；新增真实 PG 写入拒绝/缓存/原子验证和 Temporal 自动选择恢复，不重启共享服务。
- [x] 保存六图模型对照与实际 7 次 API 调用证据；一次只读独立审查，修复选择失败错误降级原复核的问题。总体计划、主方案、README、Trellis report/journal 和本地 jj 记录随本次交接保存，不推送。
