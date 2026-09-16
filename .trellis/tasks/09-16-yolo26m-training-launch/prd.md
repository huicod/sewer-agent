# Review and launch YOLO26m training in tmux

## Goal

Verify launch prerequisites, detach training in tmux and hand off without waiting for completion

## Requirements

- 用户授权：启动前review无阻塞问题后，用tmux运行正式训练，确认正常推进即交接，不等待训练结束。
- 沿用已批准的YOLO26m/960/batch16/100epoch上限/30GiB allocator配置和merged_five_v1；不改划分、不调用API、不替换线上权重。
- 复核当前资源、数据/权重入口、输出目录及checkpoint设置，必要时安装用户指定的tmux。

## Acceptance Criteria

- [x] 启动条件满足，实际训练进程在独立tmux会话内推进。
- [x] 提供会话名、日志/产物路径，持久化启动交接；无需训练完成或指标达标。

## Notes

- tmux保证终端断开不影响进程，不保证服务器关机后自动续跑；恢复用checkpoint。
