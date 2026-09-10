# 后端开发入口

当前已完成 T03 统一契约、T04 会话/上传/持久任务入口，并接通 T05 图片检测与条件复检工作流；真实 API 联调遇到认证失败，待确认 Key 对应地址，T06-A 已有意图解析/计划编译基础及报告端点，连续对话仍待 T06-B 接入。总范围见 [总体开发计划](../project_management/DEVELOPMENT_PLAN.md)，活动任务与验收证据保存在 `.trellis/`。

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

`GET /health` 仅表示 API 进程存活，不代表检测器/API 供应商/数据库已就绪。T04 的会话、上传和任务接口见下节。

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

这只验证文本/图像请求与结构化返回的协议兼容，不验证缺陷识别准确率。2026-09-10 已使用用户指定的 qwen3.8-max 向北京官方地址发送一次有界文本请求，返回 authentication_failed；尚未上传 CCTV 图片，不能标记真实 LLM/VLM 验证通过。

## 检测器状态

`vision/detector.py` 对外提供 `DetectorAdapter`；业务层接收原图像素坐标、五类标签、分数和版本字段，不接收 Ultralytics 对象。契约测试与真实单图 GPU 推理均已通过。

用户已知情确认加载现有可信 `best.pt`；单图检出 AJ，score=0.93179，记录在忽略入库的 `var/fixtures/detector-smoke.json`。这只证明兼容，不代表精度验收。使用 `scripts/detector_smoke.py --help` 查看显式入口，不自动下载替代权重。

## 统一契约入口

`pipe_inspector.contracts` 提供 AssetRef、AssetRecord、Finding、TaskSnapshot、TaskPlan、ToolCall 及检测结果类型。UUID + version 关联证据，所有新契约拒绝额外执行字段；不包含数据库连接或文件路径解析。

```python
from pipe_inspector.contracts import TaskPlan

plan = TaskPlan.model_validate({
    "target_labels": ["PL"],
    "steps": [
        {"step_id": "initial", "action": "detect"},
        {"step_id": "review", "action": "verify", "depends_on": ["initial"]},
    ],
})
```

这里的 PL 是报告关注范围，基础检测仍覆盖五类。验证通过表示计划结构合法，不表示工具已安装、证据已确认或计划已执行；这些由后续工作流负责。

## 文件/文本任务入口（T04）

先启动本机持久服务，再显式初始化业务表（可重复运行；不会清空已有数据）：

```bash
.venv/bin/python scripts/runtime.py start
.venv/bin/python scripts/init_storage.py
.venv/bin/uvicorn pipe_inspector.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

另一个终端启动界面（本机隔离 Node 位于 .tools/node，未替换系统 Node）：

```bash
PATH="/root/autodl-tmp/server/.tools/node/bin:$PATH" npm --prefix frontend run dev -- --host 127.0.0.1
```

通过 SSH 转发本机 5173 端口后浏览；Vite 同源代理 /api 到 127.0.0.1:8000。当前无鉴权，不直接绑定公网。

- POST /api/sessions 建立会话；GET /api/sessions/{id} 恢复该会话的提交与状态。
- POST /api/assets 接收 multipart 的 file 字段，返回 AssetRecord；GET /api/assets/{id}/versions/1 取元数据，加 /content 取原始字节。
- POST /api/tasks 接收 request_id、session_id、input_asset、instruction；GET /api/tasks/{id} 查询。重复 request_id 与相同内容返回原任务，内容不同返回 409。
- 提交后仍先持久入队（queued/pending），不自动运行。T05 通过下述显式启动接口执行图片检查；文本已保存但尚未解析执行，连续对话和文本工具规划在 T06 接入。
- 原图 JPEG/PNG 全量校验；视频 MP4/MOV/AVI/MKV 使用 FFmpeg 校验元数据与首帧。整段视频的解码缺口在后续检测覆盖中记录；首帧通过不是整段完整性保证。
- 默认单文件 1 GiB、画面 4000 万像素、媒体探测 20 秒；环境变量见 .env.example。上传依赖系统 ffmpeg/ffprobe，不使用客户端 MIME 或扩展名判断内容。
- PostgreSQL 保存会话/资产/任务元数据，本地 var/assets 保存原始文件。两者需一起备份。进程在文件写完、元数据提交前崩溃可能留下未引用文件；本阶段不自动清理，避免误删证据。

实际存储集成验收（仅创建/删除随机命名的测试 schema，不重启数据库或 Temporal）：

```bash
SEWER_RUN_STORAGE_TEST=1 .venv/bin/python -m pytest backend/tests/integration/test_storage.py backend/tests/integration/test_intake_http.py -q
```

媒体单元测试也使用 ffmpeg 构造短视频；没有安装媒体工具的环境不能宣称视频验收通过。

## 图片工作流（T05）

在既有服务启动后，重新执行一次业务表初始化以新增运行、工具记录和检查点表，然后在两个终端分别运行 worker：

```bash
.venv/bin/python scripts/init_storage.py
.venv/bin/python scripts/inspection_worker.py cpu
# 另一个终端；同机只运行一个 GPU worker，内部并发固定为 1
.venv/bin/python scripts/inspection_worker.py gpu
```

- 页面“启动图片检查”或 POST /api/tasks/{id}/start 显式启动，返回 202。相同任务重复点击沿用固定工作流 ID 与首次落盘配置；启动请求超时可重试，不另建任务。视频当前返回 409；终态重处理需新建任务。
- GET /api/tasks/{id} 的 snapshot 包含实际阶段、重试次数、结论和证据；GET /api/tasks/{id}/calls 查看每个工具的各次尝试。成功 Activity 结果落检查点，可跨 worker 重启复用。
- 原图检测和质量评估后，零检测、低于 0.85 的候选、FS/BX 或低质量会触发 VLM 全图审核。其他高置信候选可跳过复检。质量阈值和分数阈值只是首轮工程策略，尚未校准，不能当作缺陷概率或精度保证。
- 正常必须满足画质、全图检测覆盖和完成的全图阴性审核。OTHER、强证据冲突或不能判断的候选为待复核；T05 暂以 partial 保存，T09 才提供真正的人工等待/审核流程。
- VLM 传输最多 3 次尝试；鉴权/配置/结构错误不重试，超时/网络失败标记 uncertain。检测端的可恢复传输失败最多重试 3 次；本地最终落库可耐久重试，但不会再执行检测或 VLM。供应商可能已处理请求，不能保证无重复计费。当前只记录次数与状态，未计量真实 token/金额。
- VLM 使用原图（默认最多 8 MiB），不暗中缩放、去雾或增强。超过限制或复检失败会显示不完整结果，保留检测证据，不报告正常。增强模型接入属于 T12。
- 恢复时要求检测器/标签空间及规则、Prompt、VLM 配置版本一致；不要原地覆盖同版本权重。此阶段未实现视频恢复、用户取消或进程开机自启；对应 T07/T08/T16。

用户已选定 `qwen3.8-max`，LLM/VLM 两个角色均复用本地 `DASHSCOPE_API_KEY`。精确 Base URL 仍需与 Key 所属服务商/地域匹配；当前北京官方地址认证失败，未自动切换地域。官方模型说明支持视觉输入，但本项目实际效果仍待验证：[Qwen3.8-max](https://help.aliyun.com/zh/model-studio/qwen3-8-max)。

配置示例见 `.env.example`。共享 Key 仅自动用于官方 HTTPS Qwen 地址；其他兼容服务需要明确设置对应角色的 Key，不能把共享密钥自动发往任意地址。`SEWER_API_ENABLE_THINKING=false` 与 `SEWER_API_MAX_OUTPUT_TOKENS=4096` 限制输出；参数在任务启动时固定。测试可临时覆盖上限为 256，正常工作流不自动使用测试值。

## 对话规划基础与证据报告（T06-A）

- `IntentParser` 将有限历史与用户文本转换为严格意图；`compile_inspection_plan` 由服务端生成工具顺序、保留复检策略节点并校验当前可用工具。查询/报告等只读意图不生成新的检测计划。当前解析模块尚未接到产品消息入口，上传文本仍按 T05 保存而不解析执行。
- `GET /api/tasks/{id}/report?format=json` 导出版本化 TaskReport（任务快照＋工具尝试）；`format=html` 查看转义后的中文证据报告。仅终态可导出；进行中返回 409。partial/failed/cancelled 不被包装为正常，报告保留待审、覆盖及工具错误。
- 报告仅查询存储，不调用模型、不改变旧结论；HTML 证据链接指向服务器媒体接口，不包含离线打包的原图。
- T06-B 尚需消息持久化、幂等对话处理、追加关联操作及聊天界面；本切片不能替代这些完整 Agent 交互。

相关测试：`backend/tests/test_conversation_intent.py`、`test_reports.py`；真实 PostgreSQL 报告回读随 `SEWER_RUN_STORAGE_TEST=1` 执行。
