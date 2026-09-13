# T16 本机运行交付记录

2026-09-13；负责人huicod；基线`25e49c62`，本地jj change `urlozrsx`。T16范围完成，首版全项验收未完成。

## 交付

使用 [运行手册](../../../RUNNING.md) 的 `scripts/app.py build|start|status|stop|restart`，固定管理同机API、CPU和GPU worker。
构建网页与API同源，服务器地址`http://127.0.0.1:8000`；自己电脑需按手册建立SSH转发。
仅生命周期命令加锁，build须停机串行执行。worker正常排空，停止不取消workflow、不删除数据、不停止PG/Temporal。
启动失败只清理本次子进程；退出等待30秒后仍存活则报错并保留记录，不自动强杀。

## 实际验收

- 后端完整套件：**1048 passed / 137 skipped，25.07s**；跳过项未计作通过。Ruff通过。
- 前端：**84 passed / 11 files**，`app.py build`的TypeScript/Vite构建通过。
- 最终真实Chromium及原生PG/Temporal验收通过：[生命周期JSON](../../../var/evaluations/2026-09-13-delivery-final/lifecycle.json)、[浏览器JSON](../../../var/evaluations/2026-09-13-delivery-final/browser.json)、[启动截图](../../../var/evaluations/2026-09-13-delivery-final/01-running.png)、[重启截图](../../../var/evaluations/2026-09-13-delivery-final/02-restarted.png)。
- 首次start约2.02秒，重复start约0.96秒且三个PID不变；stop约0.54秒，应用全停而PG/Temporal保持ready；再次start约2.06秒、restart约2.43秒，三个PID均更换。
- 同源JS/CSS均200，浏览器无页面错误；空会话`abd970e1-39e1-4857-b092-76755f9248c6`重启后ID和创建时刻保留，tasks/turns仍为空。
- 完成时API/CPU/GPU PID分别39498/39497/39496，总ready=true；应用留运行。此为当时状态，之后用status查询。

本轮启动前检查业务任务及Temporal Running均为0；仅显式执行已有init_storage.py补齐缺失表。
没有上传检查、提交对话任务、模型推理、付费API或人工审核；保留三个测试空会话和所有本轮证据，不清理用户数据。
这次证明运行入口与空会话持久化，不替代既有真实任务恢复/模型验收，也不产生新的准确率结论。

## 修复与审查

首次完整测试发现动态加载脚本未模拟CLI导入路径，修复fixture后完整通过。
两次早期浏览器验收分别因时间字符串时区表示、误把独立turns接口当SessionDetail字段而失败；
纠正测试契约后完整重跑通过。失败现场保留在`var/evaluations/2026-09-13-delivery`及`-retry`，不能算通过。
独立只读审查发现并关闭两项Important：启动失败清理宽限不足；PG进程存活被误作业务库就绪。
现清理统一30秒，status分别显示running/ready，检查应用DSN及必需表；单元测试隔离真实配置和PG。
最终无剩余Critical/Important。未新增依赖、进程监督框架、哈希体系或远端推送；真实.env未修改。

## 恢复入口与下一步

先读本记录、RUNNING.md和Session30。沿用Trellis唯一任务账本、jj本地检查点；检测/VLM复检/融合及模型不变，开放补检停用。
下一步核对T15剩余功能覆盖、故障矩阵和视频成本，并做用户界面验收；T14-B仍需标准编号/版本及PDF或官方来源。
视频增强/几何、自动关联增强仍未接入；几何仅像素和相对量。不要将T16完成等同于整套产品交付完成。
