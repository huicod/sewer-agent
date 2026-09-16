# YOLO26m / RTX5090 配置交接

2026-09-16。配置调整完成，正式训练未启动；线上服务保持暂停，原YOLO26s与VLM策略未改。
唯一参数/运行说明：[训练配置与显存预检](../../../yolov26/docs/merged_five_yolo26m_5090.md)。
配置：`yolov26/configs/merged_five_yolo26m_5090.yaml`；本地change `tlqvooko`。

训练入口新增`--profile`/`--dry-run`，保留旧CLI；固定整数batch，不允许旧默认参数或缩写覆盖绕过。
路径相对仓库根目录；启动前检查本地数据/权重、单卡device0、显存空闲18GiB，设置CPU4线程和PyTorch16GiB allocator预算。
当前为YOLO26m/960/batch8/AMP/MuSGD/最多100轮/patience30；数据保持Session43版，不重新划分。

## 验证

- 17个入口与资源门测试；加已有数据/分组测试共39 passed。Ruff lint/format与diff检查通过。
- 新版profile实际dry-run及Ultralytics `get_cfg`校验通过；真实GPU上资源门通过，allocator fraction约0.51026，对应16GiB，Torch线程4。
- RTX5090 32607MiB、Torch2.8.0+cu128/Ultralytics8.4.46；sm120及CUDA运算已确认，不需更新依赖。
- 短程64图（仅来自train）/8训练batch/验证batch16/保存及回读通过；峰值allocated9.510GiB、reserved12.271GiB。
- 同时另进程占10GiB并跑旧YOLO960单图，训练活动窗口55次成功，采样最低整卡空闲7.599GiB。
- 预检证据：`var/training_preflight/2026-09-16-yolo26m/result_saved.json`、`probe_saved.log`；早先save=false收尾回读失败的记录保留，原因及修正见运行说明。探针权重不是正式训练产物。
- 独立只读审查通过。该任务不修改应用包，未运行无关前后端全量回归。预检不是完整网页或全工具并发验收，不宣称任意并发永不OOM。

## 下一步边界

等待用户正式训练启动确认；之后使用profile入口，不用README中的历史AutoBatch命令。
网页/CPU/API及轻量单图调试可共存；大型增强、超分、分割、长视频错峰。16GiB仅是PyTorch allocator上限，不是GPU算力隔离或全部CUDA硬分区。
模型与数据大文件均被Git忽略；只保存本地代码/文档版本，未推送、未恢复服务。
