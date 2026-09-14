# T15-E 设计

新增测试专用 harness，生产模块原则上不改。两个任务使用实际业务 workflow/activities/repository，仅检测/复核外部边界用替身。复用现有恢复测试模式，不安装另一套服务。

测试子进程注册图片/视频 CPU/GPU Activities（GPU 串行）。图片先到 human_review；6 帧合成视频每块 2 帧，在 repository 首块提交后测试钩子阻塞。主测试确认 PG 快照/检查点后杀自己创建的 worker，停止托管 Temporal、重启准确定位的 PG 14/main、启动 Temporal；新 worker 从历史/检查点续跑。

用测试目录 JSONL 记录跨进程实际模型调用，不能只计新进程内存。图片必须仍等待且不重算，视频剩余四帧完成，最终 6 帧/3 块。保持生产 30 秒心跳超时。恢复后只审核合成图片并检查幂等，不替用户审核真实数据。

守卫：复用 M0 本机地址/集群检查，要求 app.py 的三组件停止、无其他 running workflow；中断前再核对仅有本轮两个 workflow。双开关 SEWER_RUN_BUSINESS_RESTART_TEST=1 和 SEWER_RUN_RESTART_TEST=1，输出必须是新的 var/evaluations 子目录。失败也恢复 runtime，不运行 init，不输出凭据，不删除历史证据。

主 Agent 独占日常 app 停复与唯一重启验收，子 Agent 仅写测试/离线检查。同一次中断覆盖两种业务阶段，减少无意义重复重启。
