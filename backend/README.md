# 后端开发入口

当前为 M0 基础设施与适配器阶段，不是已完成的图片/视频产品。总范围见 [总体开发计划](../project_management/DEVELOPMENT_PLAN.md)，活动任务与验收证据保存在 `.trellis/`。

## 本服务器环境

- Python 3.12.3；项目 `.venv` 以 `--system-site-packages` 复用服务器的 torch 2.8.0+cu128、torchvision 0.23.0+cu128，不重装 CUDA。这不是完全隔离环境。
- 直接 Python 依赖版本固定在 `pyproject.toml`；未声明所有传递依赖及系统包完全可复现。Ultralytics 8.4.46 已通过现有权重单图 GPU 兼容验证。
- PostgreSQL 14.24 使用原生 `14/main` 集群；Temporal Server 1.31.2 位于忽略入库的 `.tools/temporal/`。当前无 Docker/systemd；本脚本尚不是新服务器的一键安装器。
- Temporal 仅监听本机，不启用鉴权，不能直接暴露公网。数据库业务角色和 Temporal 角色分离。

以下命令均在项目根目录执行。

## 开发与测试

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend scripts/runtime.py scripts/detector_smoke.py
.venv/bin/uvicorn pipe_inspector.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

当前 HTTP 接口只有 `GET /health`，表示 API 进程存活，不代表检测器/API 供应商/数据库已就绪。

## 本机持久化服务

本服务器已完成初始化；日常只需 `start` / `status`。`init` 会管理固定的项目数据库和角色，不应在已有同名业务库的其他服务器上盲目执行。

```bash
.venv/bin/python scripts/runtime.py start
.venv/bin/python scripts/runtime.py status
.venv/bin/python scripts/runtime.py stop
```

`stop` 仅停止本脚本确认身份的 Temporal 进程，不停止 PostgreSQL。数据位置：

- PostgreSQL：`/var/lib/postgresql/14/main`。因为 postgres 用户不能穿过 `/root`，没有把数据强行移入项目或修改 `/root` 权限。
- Temporal 配置、PID、日志和数据库凭据：`var/runtime/`；原始/派生媒体预留 `var/assets/`。
- `.env` 的运行时管理区由脚本维护；凭据文件权限为 0600，不入库。不要在聊天或日志中粘贴内容。

以下验证会杀死测试 worker 并重启本机 Temporal 与 PostgreSQL；仅允许本项目专用测试服务，运行前必须停止其他业务任务：

```bash
SEWER_RUN_RESTART_TEST=1 .venv/bin/python -m pytest backend/tests/integration/test_recovery.py -q
```

该测试已证明人工等待跨重启恢复、已确认完成的 Activity 不因重放再次写入。它不是完整业务工作流，也不证明所有崩溃窗口下 exactly-once；后续业务落库仍需唯一键及幂等提交。

## LLM/VLM 配置与真实联调

两者均通过 API；当前适配器明确面向 OpenAI-compatible `chat/completions`，不是所有供应商的通用协议。按 `.env.example` 在服务器现有 `.env` 中补充各自的 Base URL、模型和密钥，不要覆盖运行时生成的数据库配置。

默认测试不调用付费 API，也不上传图片。供应商配置完成且明确选择可上传图片后，才运行：

```bash
SEWER_RUN_LIVE_API=1 SEWER_LIVE_IMAGE=var/fixtures/sewer2403-sample.jpg \
  .venv/bin/python -m pytest backend/tests/integration/test_live_api.py -q
```

这只验证文本/图像请求与结构化返回的协议兼容，不验证缺陷识别准确率。当前真实联调未执行。

## 检测器状态

`vision/detector.py` 对外提供 `DetectorAdapter`；业务层接收原图像素坐标、五类标签、分数和版本字段，不接收 Ultralytics 对象。契约测试与真实单图 GPU 推理均已通过。

用户已知情确认加载现有可信 `best.pt`；单图检出 AJ，score=0.93179，记录在忽略入库的 `var/fixtures/detector-smoke.json`。这只证明兼容，不代表精度验收。使用 `scripts/detector_smoke.py --help` 查看显式入口，不自动下载替代权重。
