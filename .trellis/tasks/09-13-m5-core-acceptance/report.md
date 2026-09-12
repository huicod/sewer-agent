# T15-A：核心链路与浏览器验收

2026-09-13；jj `myszupvl`；基线 `a3da206e3b17`。本切片完成，不代表 T15 全项或首版交付完成。
T14-B 缺规范编号/版本及真实来源，因此先执行不依赖规范的核心验收，不自行选择评级标准。

## 本轮交付与证据

新增开发用 Playwright 1.63.0 / Chromium Headless Shell 153.0.8010.12 和独立测试服务，
没有修改生产检测、VLM、融合、增强、几何或报告实现，也没有增加生产测试接口。
真实浏览器经 HTTP、PostgreSQL、Temporal 完成两次图片和一次短视频检查，
刷新保留人工等待，实际填写审核表单后完成报告；视频 6 帧聚合为一个 0–1秒事件，
两份已存图片报告再通过对话比较并打开 HTML 导出。

最终运行目录：`var/evaluations/browser/run-1789248229831-265724/`；此前成功和启动失败记录也保留。

- [图片待审页面](../../../var/evaluations/browser/run-1789248229831-265724/screenshots/01-image-waiting.png)
  / [审核完成](../../../var/evaluations/browser/run-1789248229831-265724/screenshots/02-image-reviewed.png)
- [短视频与证据时间线](../../../var/evaluations/browser/run-1789248229831-265724/screenshots/03-video-evidence.png)
- [对话比较页面](../../../var/evaluations/browser/run-1789248229831-265724/screenshots/04-comparison.png)
  / [HTML 报告截图](../../../var/evaluations/browser/run-1789248229831-265724/screenshots/05-html-report.png)
- [浏览器结果](../../../var/evaluations/browser/run-1789248229831-265724/browser-results.json)、
  [任务引用](../../../var/evaluations/browser/run-1789248229831-265724/browser-evidence.json)、
  [比较 JSON](../../../var/evaluations/browser/run-1789248229831-265724/comparison.json)、
  [持久记录及调用统计](../../../var/evaluations/browser/run-1789248229831-265724/service/service-summary.json)。

图片为 64×64 合成噪声，视频为 FFmpeg 生成的 1.2秒/6帧测试图案，均不是 CCTV 精度样本。
检测替身固定一个 0.65 分 AJ，VLM 替身弃权，浏览器以 `browser-fixture-reviewer` 确认，
审核理由明确标记为软件验收。两份图片确认 AJ 均为1，比较差值为0。
共3项任务、4轮对话、3份审核记录；检测替身8次、复检替身4次、意图替身4次，真实API调用0次。
浏览器流程19.8秒、含服务启动/结束共23.9秒；这不是模型延迟或长视频吞吐数据。

## 验证与隔离边界

- 完整后端 **1023 passed / 137 skipped**（23.81秒）；新增夹具测试10项包含在内。
  跳过项仍需显式真实服务、模型或API条件，不计作通过。Ruff backend/scripts通过。
- 前端 **84 passed**、TypeScript/Vite build通过；真实Chromium另 **1 passed**。
  已查看中文截图；未发现未捕获页面异常。自动操作与截图不替代用户主观界面验收。
- 既有真实PG/Temporal图片故障、视频工作流及人工等待恢复测试 **8 passed**（17.26秒），
  不调用付费API，不重启共享服务。浏览器刷新不是进程故障恢复的替代证据。
- 测试使用唯一 schema/队列、本机18080/18081、新输出目录；正常退出已保存全部记录，
  没有遗留运行中的自有workflow，删除本轮测试schema，保留文件与截图。
  本轮不强杀整机，不验证断电时测试夹具的清理保证；Temporal中已结束历史仍按服务保留策略保存。
- 一次独立审查发现测试退出时派发竞态和清理宽限不足；派发竞态已用正常/异常退出两项失败回归复现，
  改为两个worker均停止后再枚举终止自有workflow，外层退出宽限由15秒改为30秒。
  两项回归转绿，最终浏览器重跑通过，数据库检查无遗留 `browser_acceptance_` schema。

启动命令与字体资源见 [后端入口](../../../backend/README.md)。本机仅增加被忽略的浏览器、
浏览器配套FFmpeg和Noto中文字体，无系统包升级、模型训练、密钥修改或远端推送。
Superpowers用于测试/调试和一次独立审查；stop-that-shit将范围限定为验收夹具，未扩展产品框架。

下一步：推进T15剩余真实模型/UI联调、故障与资源/API成本验收及T16一体化运行交付；
T14-B仍等待用户指定规范文档。视频增强/几何、自动关联增强和用户最终验收的限制继续保留，
不把本轮的短视频替身测试写成全部功能或准确率已通过。
