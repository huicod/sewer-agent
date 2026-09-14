# 视频证据帧工具收尾

2026-09-14；基线`5f6bbdeb`；jj `sonwsnyp`。总体计划4.1第2组的剩余入口已完成，独立Spec/Quality审查通过，无未关闭的Critical/Important。

## 已交付

- 展开视频时间线，在默认折叠的证据帧区选定一帧，再通过对话请求一种显式增强或几何操作包。绑定实际父视频与原始FRAME，不任意解码时间点、不接受其他派生图、不自动增强整片。
- 沿用现有模型与ImageSupplementWorkflow；冻结单帧检测/复检基线，新增发现和配置变化回退均保留精确帧定位。不会重新扫描/复检原视频，不改父结论。
- 同会话来源、证据帧号/时间/尺寸、工具类型及独立额度由后端校验；每父视频/帧/操作一次，原图片一次补证额度不变。结果与调用原子落盘，重试复用冻结子任务及工具检查点。
- 页面、对话答复和HTML/JSON报告显示来源及单帧范围。分割/相对量化须显式像素ROI；相对深度可分析整帧。此入口不支持finding ID，不提供毫米尺寸或真实堵塞率。

## 验证

| 检查 | 结果与边界 |
|---|---|
| 全量后端 | 1127 passed / 159默认跳过，25.25秒；Ruff backend/scripts通过 |
| PG存储 | 显式启用40 passed，18.00秒；包含新帧准入与原图增强/几何兼容 |
| PG/Temporal工作流 | `SEWER_RUN_FRAME_TOOL_TEST=1`：2 passed，10.86秒；增强和分割→相对深度在落盘后故意丢回执，恢复不重复工具、不调用原始检测/复检，父检查及调用不变 |
| 前端 | 111 passed，TypeScript检查及生产构建通过 |
| 浏览器 | 核心场景1 passed，28.1秒；真实HTTP/PG/Temporal、合成模型/媒体，验证选帧引用和安全澄清；不冒充真实模型效果或浏览器内完成工具推理 |

[浏览器结果](../../../var/evaluations/2026-09-14-video-frame-tools/browser/)与[选帧截图](../../../var/evaluations/2026-09-14-video-frame-tools/browser/screenshots/06-selected-video-frame.png)已保存，截图已查看。实现与RED/GREEN细节见[后端](backend-report.md)、[前端](frontend-report.md)。

审查定点关闭：失败选帧请求切换任务后不再复用旧FRAME；提交中不能取消选帧；最终新增异常有同帧定位；配置变化不复制整片其他发现。没有改已接受的图片VLM Prompt/融合。

## 运行与后续

重载前确认Temporal没有运行中的工作流；停止受管API/CPU/GPU，执行现有幂等init_storage新增帧工具额度表，构建网页并重新启动。最终API/CPU/GPU及PG/Temporal均ready，持久服务未重启；入口仍为`http://127.0.0.1:8000`。

本次未新增模型、依赖、训练或付费实验，不推送远端。规范检索仍缺用户指定的编号/版本/PDF或官方来源；现有功能可以人工试用，但不宣称规范检索或首版全项正式验收已经完成。
