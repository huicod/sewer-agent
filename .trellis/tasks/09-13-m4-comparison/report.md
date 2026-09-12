# T14-A：历史证据比较与报告

2026-09-13；jj `prpustlr`；基线 `403fc646befc`。T14-A 已完成；Trellis 控制本轮任务，T14-B/T15/T16 仍待续。

## 交付范围

对话可通过页面选择或本条文本参考 UUID，比较同会话两个不同的已结束检查（completed/partial），
支持图片—图片、视频—视频。当前任务可沿用最近已完成对话关联任务；新上传媒体需先检查。
五类＋OTHER＋未定类分别统计已确认、待定、待人工复核、已驳回，并保留两份原始结果快照、
源资产、发现证据及覆盖限制，导出独立 JSON/HTML 比较报告；不会覆盖原任务报告。

比较是独立受约束对话动作，不进入检测计划或追加补证，不调用 YOLO/VLM/GPU，不改源任务、
发现、审核或预算。意图解析仍使用 LLM；服务端拒绝猜测/跨会话/冲突引用与混用其他工具。
首次比较在锁定对话行后原子保存到现有检查点，重试复用首次快照，不重读后来源状态。
沿用现有 Temporal dispatch activity，无新表、依赖、调度框架或训练。

这里只比较**保存记录**：没有拍摄位置配准、逐缺陷物理匹配或可靠拍摄时序，不推断真实新增、
消失、维修效果，也不靠计数判正常；相对深度值不做跨图比较。T14-B 仍需用户指定规范编号、
版本及真实 PDF/官方来源，不自行选择评级标准。

## 可复现证据

- [实际 Qwen 意图](../../../var/evaluations/2026-09-13-comparison/live-smoke/intent.json)：识别为
  compare＋export_report，参考来自页面而非编造 UUID。
- [冻结比较](../../../var/evaluations/2026-09-13-comparison/live-smoke/comparison.json)、
  [HTML 示例](../../../var/evaluations/2026-09-13-comparison/live-smoke/report.html)、
  [恢复结果](../../../var/evaluations/2026-09-13-comparison/live-smoke/verification.json)。
  两项检查是明确标记的合成测试记录：AJ 已确认 1→2，另有已驳回1，确认差值应为+1而不是+2。
  这些不是新的准确率实验，不代表真实缺陷变化。
- 真实 PostgreSQL/Temporal：比较提交后、dispatch 完成保存前故意丢失响应，退出首个 worker，
  新 worker 从检查点恢复完成。首次快照 attempt=1，完成对话 attempts≥2；源任务保持不变，
  无检测 run/ToolCall，无重复意图解析。模型替身版3.75s，实际 Qwen 意图版7.47s，均1项通过。
  这是 worker 实例替换，不是强杀整机或重启共享服务；未保存意图时仍可能重复 API 计费。

测试只使用本轮隔离 schema/队列，结束删除测试 schema；文件证据保留。HTML 使用原服务器
资产链接，没有打包媒体；测试会话不是常驻产品数据。现有对话恢复回归另有2项通过。

## 最终检查与交接

- 最终后端：**1013 passed / 137 skipped**（24.66s）；跳过项需要显式服务、模型或API条件，
  不计作通过。Ruff backend/scripts、差异空白检查通过。
- 前端：**84 passed**，TypeScript/Vite build通过；这是DOM/API边界测试，不是浏览器人工视觉验收。
- 真实PostgreSQL比较及对话存储 **20 passed**（8.52s）；真实PG/Temporal比较及既有对话恢复
  **3 passed / 1 deselected**（10.65s，排除另一个付费测试）；实际Qwen比较冒烟另行通过如上。
- 一次独立跨层审查无剩余Critical/Important项。主审过程中修复了文本参考无法展示报告、
  比较提交但关联ID未写入时错误拒绝读取，以及OTHER字符串导出异常，并保留回归用例。

使用前需重启 API/CPU worker并更新前端，不修改真实.env，不自动重启常驻产品服务或推送远端。
代码与进度通过 jj/Trellis 保存。Superpowers的TDD、恢复验证和审查用于准入与快照边界；
stop-that-shit 将改动限定在本切片，没有增加哈希、模型或通用管理框架。

下一步先完成 T14-B 规范输入对齐和引用检索，再 T15 整体端到端/UI/故障验收与 T16 部署说明。
原五类检测/VLM/融合、图片增强与几何策略不变，开放补检保持停用。
