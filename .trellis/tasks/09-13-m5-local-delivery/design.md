# T16 本机运行设计

这是已批准M5部署方案的应用进程装配，不改变业务架构。沿用本机原生服务。
方案比较：逐终端启动是现状；Vite常驻增加开发进程；选FastAPI挂载已构建dist，网页/API共用127.0.0.1:8000。
无公网监听/鉴权变更。SSH转发由用户本机执行。

## 固定接口与边界

- `scripts/app.py build|start|status|stop|restart`，固定repo根cwd及项目.venv，无任意命令配置。build使用本地Node/npm；start要求frontend/dist/index.html存在，不自动安装/构建/初始化数据库或修改.env。
- app.start复用runtime.start_temporal确保PG/Temporal（已初始化）；随后检查业务表、LLM/VLM配置及主权重存在（只读，无付费API/GPU推理）。启动GPU/CPU worker，再启动API；所有子进程脱离调用终端，日志在var/runtime/app，0600。
- API以现有uvicorn factory运行，固定127.0.0.1:8000；仅其环境设SEWER_WEB_DIST绝对dist路径。Settings.web_dist默认None，开发API行为不变。路由后挂载静态目录；不回退API未知路径为index.html，不暴露repo根/.env。
- worker CLI增加`--ready-file /absolute/path`，写JSON `{pid,kind}`。只有Worker.run开始且公开is_running为真后才写；退出删除自身marker。SIGTERM/SIGINT触发Worker.shutdown，不取消业务workflow。保持CPU4/GPU1与现有activity注册不变。
- app记录每角色pid、Linux /proc启动时刻、固定命令身份；start/status/stop/restart用fcntl锁防止生命周期CLI竞争。build不在锁内，须在启动前串行执行，不支持在线无缝更新。停止前验证身份/启动时刻，拒绝PID重用/外来进程；记录不含凭据或完整环境。
- status包括各角色running/ready、PG/Temporal依赖、地址与日志目录；PG另用业务DSN及必需表只读检查报告ready，总ready要求依赖和三个应用组件均就绪。worker ready是本次进程成功进入运行，不是付费模型或所有工具已验收。进程退出不自动拉起；用户restart可恢复。
- start幂等复用健康自有进程，只补缺项；不匹配存活PID或外来8000端口拒绝。失败仅通知本次创建的子进程退出，最多等待30秒，超时保留记录并报错；保留已有进程及持久服务。
- stop先停止API接收新请求，再通知CPU/GPU正常退出；有界等待30秒，超时保留记录并返回非零，不SIGKILL。不停止PG/Temporal、不删除资产/表、不terminate workflow。restart仅在stop成功后start。

## 验证与所有权

实现agent独占scripts/app.py和backend/tests/test_app_control.py；主agent独占API/Settings静态挂载、inspection_worker.py及相关测试/文档。
主agent做真实同机启动、重复启动PID不变、网页资源/health可读、新建无任务会话、stop/start后会话可读与worker PID更换。
只创建标记为部署验收的空会话，不提交检查以避免消费API；启动前检查现有队列有无待执行任务，若有则先隔离验收队列。
测试结束保留可用本机应用供用户访问（仅当确认没有历史任务意外启动），记录URL和命令。
一次只读独立审查；不新增多层harness，T15完整覆盖/规范输入缺口仍保留。
