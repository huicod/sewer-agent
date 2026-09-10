# 后端开发入口

当前已接通图片检测、条件 VLM 复检，以及持久对话＋附件入口：对话可启动图片检查、追问已存证据、获取报告和追加一次关联复检。Qwen 文本与原图复检已真实联调通过；视频执行、人工审核等待仍在后续阶段。总范围见 [总体开发计划](../project_management/DEVELOPMENT_PLAN.md)，验收证据保存在 `.trellis/`。

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

2026-09-10 已验证 qwen3.8-max 结构化文本、所选原始 CCTV 图片的 `ImageReviewer`、真实对话工作流。旧 authentication_failed 已不再复现，但未证明其历史原因。图片复检接受 AJ 候选，并提出一项额外 FS 建议，后者仍需人工确认；这些是接口/工程验证，不是准确率验收。证据见 [Qwen 联调记录](../.trellis/tasks/09-09-m1-image-workflow/qwen-live-success.md)。

用户提供的 OpenAI Responses 与原生 DashScope 示例也已跑通。可选 SDK 通过 `.venv/bin/python -m pip install -e '.[qwen-sdk]'` 安装；生产保留已验证的 httpx Chat Completions 客户端，无需切换框架。旧 Responses `/api/v2/apps/protocols/compatible-mode/v1` 路径已被[官方文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)标记不再维护，不作为新生产默认地址。

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
- 此手动入口只持久入队（queued/pending），文本原样保存、不解析。通过下述显式启动接口执行基础图片检查；需要按文本规划操作时，使用“检查助手”及对话接口。
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

用户已选定 `qwen3.8-max`，LLM/VLM 两个角色均复用本地 `DASHSCOPE_API_KEY`；已验证 `https://dashscope.aliyuncs.com/compatible-mode/v1`。不自动切换地域或服务商。模型效果仍需固定集评估：[官方模型说明](https://help.aliyun.com/zh/model-studio/qwen3-8-max)。

配置示例见 `.env.example`。共享 Key 仅自动用于官方 HTTPS Qwen 地址；其他兼容服务需要明确设置对应角色的 Key，不能把共享密钥自动发往任意地址。`SEWER_API_ENABLE_THINKING=false` 与 `SEWER_API_MAX_OUTPUT_TOKENS=4096` 限制输出；参数在任务启动时固定。测试可临时覆盖上限为 256，正常工作流不自动使用测试值。

## 持久对话、关联复检与证据报告（T06）

- 升级后重新执行 `scripts/init_storage.py` 新增对话/复检关联表，并重启 API 与 CPU worker；GPU worker 命令不变。该操作不删除旧数据。网页“检查助手”支持文本、可选附件或已有任务；手动入队入口仍保留。
- `POST /api/sessions/{session_id}/turns` 接收 `request_id`、`text`、可选 `input_asset` / `task_id`，返回 202；同一路径 GET 恢复最近 50 条消息，加 `/{request_id}` 查询单条阶段。未指定任务时，沿用最近一次已完成对话的关联任务；超出保留窗口时应显式选择。
- PostgreSQL 保存原始消息、意图/计划/分派检查点；Temporal 依次规划、执行、结束。每会话最多一条活跃消息；启动确认失败可原样重发相同 request_id，刷新后也能用“确认启动”重试。终态不可被重试改写。
- `IntentParser` 使用最多 5 轮已完成历史（10 条消息）；服务端构建受约束计划，拒绝未部署工具。对话新检查始终执行五类；意图解析认定用户请求 `verify` 时不得跳过 VLM，其他情形沿用条件复检规则。文本识别仍可能误解意图、增加已允许工具的调用，不能承诺语义判断完全准确；必要复检失败保留 partial，不冒充完成。
- 回答“已启动”只表示关联检查已提交，不表示检查完成；检测阶段由关联任务显示。查询/解释引用保存的结论、发现、覆盖和复检说明，不重新跑模型；意图解析本身仍调用一次 LLM。无证据、视频执行或未部署工具均明确说明限制。
- `GET /api/tasks/{id}/report?format=json` 导出版本化 TaskReport（任务快照＋工具尝试）；`format=html` 查看转义后的中文证据报告。仅终态可导出；进行中返回 409。partial/failed/cancelled 不被包装为正常，报告保留待审、覆盖及工具错误。
- 报告仅查询存储，不调用模型、不改变旧结论；HTML 证据链接指向服务器媒体接口，不包含离线打包的原图。
- 选择已完成或部分完成的原图片任务，发送“请对这个任务追加一次 VLM 复检”：新建带 `parent_task_id` 的关联任务，复用原图及冻结的检测证据，不运行 GPU 检测、不改写原结果。页面和 HTML 报告可回到原任务报告，原检测版本/调用记录仍属于原任务。
- 每个原任务最多一个复检子任务；重复 request_id 沿用首次身份与配置，子任务不能再追加子任务，失败也占用次数。启动确认失败可对同一子任务重试 `/start`；已结束的复检不因 `/start` 重做。复检只需 CPU/API worker，允许使用当前配置的 VLM，但重试不得切换模型或参数。
- 追加操作仅允许 `verify`（可附带摘要/报告）；若同时要求尚未部署的分割/增强等工具，本次全部不执行。前后 VLM 实质判断冲突保留待审；原有 VLM 新增发现/OTHER 不因新回答遗漏而消失。缺失或弃权的原复检可由新的有效审核补足；复检失败不能得出正常。视频、人工审核等待及后置工具仍未交付，不能称完整首版已完成。
- 任务和对话关联必须属于同一会话；上传资产沿用本机单用户全局模型，没有上传者所有权或用户鉴权。这不是多用户数据隔离方案，仍只能绑定本机使用。

本地对话恢复测试：`SEWER_RUN_CONVERSATION_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_conversation_workflow.py -m 'not live_api'`。只使用隔离测试 schema/队列，不重启共享服务；另加 `SEWER_RUN_LIVE_API=1` 并选择 `-k real_qwen` 才会付费调用文本 API。真实测试使用合成已存证据验证查询分派，不代表正常样本验收。远端成功但尚未保存本地检查点的崩溃窗口仍可能重复付费，不能承诺 exactly-once。

关联复检的本地 PG/Temporal 验证：`SEWER_RUN_CONVERSATION_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_image_supplement_integration.py`；无需 GPU 或付费 API，覆盖限流重试、重复分派、保留 OTHER、Worker 替换及最终写库失败恢复。

## 视频初查核心（T07）

已提供独立的本地初查入口：逐帧解码并保留原始 PTS，调用可替换检测器，跨块聚合同类候选事件，选择少量原帧证据。当前网页/对话仍不执行视频；T08 才接入 Temporal 长视频进度、检查点、取消与恢复，本 CLI 不支持断点续跑。

```bash
.venv/bin/python scripts/video_scan.py \
  --source /absolute/path/to/cctv.mp4 \
  --checkpoint yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt \
  --output var/video-scans/example-001
```

输出目录必须不存在。`observations.jsonl` 记录已处理原帧，`manifest.json` 保存事件、模型/规则版本、实际覆盖与错误，`frames/` 保存选中的原帧 PNG。每批默认 8 帧，临时图片随批次清理；证据通过第二次仅解码扫描提取，不再次推理。超时是“没有完整新帧”的等待上限，不是整段视频的时长上限。

这是初查候选，不是最终复核结论：包括零候选在内均为 `pending`；解码或检测中断保留已处理结果并标记不完整。事件只表示连续可见片段，不保证重复路过同一缺陷仍是同一事件。真实 FFmpeg 时间戳/短暂单帧/跨块测试及现有权重 GPU 冒烟通过；合成视频不代替真实 CCTV 效果验收。

## 复核增益先导评估

`scripts/review_eval.py` 对固定标注图片使用与线上相同的条件复核规则，分开比较原始检测、复核后保留候选、自动确认结果的 P/R/F1、误报/漏报，以及待审/失败数量。默认只汇总已保存结果，显式 `--live` 才调用本地模型和 API，默认最多 48 张；已记录图片不重复调用。

目前缺失原训练划分清单和可核验的独立测试图，尚未运行真实配对试验，不能宣称复核提高准确率。五类空标签也不等于管道正常。数据要求、最小 manifest 与运行命令见[评估说明](../.trellis/tasks/09-10-m2-video-core/evaluation-report.md)。
