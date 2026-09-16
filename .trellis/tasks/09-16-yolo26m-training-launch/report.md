# 正式训练启动交接

2026-09-16 14:21 UTC：用户批准review通过后tmux启动，不等待完成。启动任务完成，训练仍在运行。

- 会话`sewer-yolo26m`，pane进程13403，dead=0；第1/100轮已推进至至少64/682 batch。入口和配置基线为`7b609c44`，实际args.yaml确认m/960/batch16/workers8/AMP/MuSGD/save_period10。
- 日志`var/training_logs/merged_five_yolo26m_960_5090.log`，产物`var/training/merged_five_yolo26m_960_5090/`，checkpoint在`weights/`。查看方式见`yolov26/docs/merged_five_yolo26m_5090.md`。
- review：39相关测试通过；12,120图路径/标签配对、9,519已知组无跨split；训练框架扫描train10,908/val606均0损坏。官方通用m权重、全新输出目录，GPU空闲启动，CPU25核/RAM90GiB/shm45GiB/磁盘空闲56GB。当前GPU实测约21,437MiB，日志无OOM或异常。
- tmux原未安装，已安装Ubuntu仓库tmux3.2a（只新增此包，未升级环境）。断开终端不影响训练；机器关机需checkpoint恢复。
- 数据限制不变：BX较少，SEDD只补充PL/BLOCK；完成后分来源评估。不变更数据/超参数、不自动部署、不重启前后端、不调用API、不推送GitHub。不要重复启动；下一轮先检查会话和日志。
