# 后端开发入口

当前已接通对话＋附件、图片/视频检测与复检、报告、关联复检、人工审核等待/恢复和视频证据时间轴，并新增 T10 条件式开放候选扫描（默认关闭）。用户接受的图片实验融合未切为 worker 默认。总范围见 [总体开发计划](../project_management/DEVELOPMENT_PLAN.md)，当前交接见 [T10 记录](../.trellis/tasks/09-11-m3-open-candidates/report.md)。

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

## 条件式开放候选（T10）

YOLOE 只作为 `OpenDetectorAdapter` 的首个实现，不替换五类检测器。运行顺序是检测 → VLM → 条件选择 → 开放候选 → 融合/人工审核。图片仅在显式 `open_scan`、画质足够的无框结果或 VLM 疑似漏报时补扫；视频复用保存的证据帧，每事件最多一次，默认最多 8 帧。配置缺省时不新增调用，显式请求会被拒绝，不假装已执行。

本服务器已准备以下官方资产（均不入 Git）：

- `var/models/yoloe/yoloe-26s-seg.pt`：[官方权重](https://github.com/ultralytics/assets/releases/download/v8.4.0/yoloe-26s-seg.pt)。
- `var/models/yoloe/mobileclip2_b.ts`：[官方文本编码器](https://github.com/ultralytics/assets/releases/download/v8.4.0/mobileclip2_b.ts)。
- `.venv` 已安装固定 revision 的 Ultralytics CLIP、ftfy 6.3.1、regex 2026.9.3；可选依赖组为 `.[gpu,open-scan]`，现有 torch/torchvision/Ultralytics 未升级。

要启用，在现有 `.env` 中同时配置 `.env.example` 的 `SEWER_OPEN_DETECTOR_PATH`、`SEWER_OPEN_TEXT_ENCODER_PATH`，然后重启 API/CPU/GPU worker；本次未替你修改 `.env` 或启动产品进程。推理只用本地文件，缺失包/模型会明确失败，不在请求期间自动安装下载。模型文件替换应使用新文件名/版本，不能原位覆盖同名资产伪装成同一版本。

所有新增候选均为待审核，`OTHER` 必须人工确认；同源同帧同类重叠去重，原 finding 不被覆盖。视频与 VLM 共享总工具预算：缺省 32 次中预留主扫描 1 次、开放扫描最多 8 次，VLM 最多 23 帧；达到预算或缺少所需证据会报告审计不完整，不能输出正常。视频处理仍是稀疏证据补扫，不保证发现所有瞬时缺陷。

选择、跳过理由、调用版本和结果存入现有检查点/工具记录；Activity 响应丢失时复用成功检查点。实际计算崩溃窗口仍为 at-least-once，不宣称 exactly-once。GPU worker 仍串行，主模型卸载后才加载开放模型，开放扫描后释放模型。关联复检子任务保持 VLM-only；要显式使用 `open_scan`，应发起新检查。

```bash
SEWER_RUN_OPEN_SCAN_TEST=1 .venv/bin/pytest backend/tests/integration/test_open_scan_workflow.py -q --tb=short
.venv/bin/python .trellis/tasks/09-11-m3-open-candidates/smoke.py
```

第二条只在本服务器运行两张固定原图的 GPU 冒烟，生成原图坐标框和原始 JSON，不调用 API。零样本 YOLOE 的管网准确率未通过验收，默认仍关闭；T11 固定集增量对比见下文，不用候选数量替代准确率。

## 固定集增量评估（T11）

离线入口复用 48 张 Sewer2403 的已接受引导复核缓存，加 8 张真实视频证据帧的历史复核，仅条件式运行本地 YOLOE；不重新调用 VLM。图片使用 `guided-confidence-v2`，视频沿用历史 `image-review-v1` / `video-review-v1` 的事件输出，两组分开报告。它不修改生产复核策略或原标签。

```bash
.venv/bin/python -m pipe_inspector.evaluation.incremental \
  --fixed-manifest .trellis/tasks/09-11-m3-incremental-evaluation/fixed-set.json \
  --guided-state var/evaluations/2026-09-11-confidence/paired/state.json \
  --guided-state var/evaluations/2026-09-11-confidence/regression/state.json \
  --video-cache var/evaluations/2026-09-11-incremental/video-cache.json \
  --model var/models/yoloe/yoloe-26s-seg.pt \
  --text-encoder var/models/yoloe/mobileclip2_b.ts \
  --output var/evaluations/2026-09-11-incremental/verified
```

生成 `state.json`（固定输入/版本/逐样本检查点）、`summary.json`、`review-queue.csv`、`gallery/index.html`。CLI 按清单校验 48+8 的固定身份与策略，少传/换样本会拒绝。同参数重跑复用成功及失败记录，不重复计算；只补未完成项。要重新尝试已记录的模型错误，使用新输出目录，保留首次证据。单写进程运行；计算后落盘前的崩溃仍可能重复本地调用，不承诺 exactly-once。CSV 含 finding ID、来源/帧定位与图库链接，已有人工意见不覆盖；本轮仅导出，未自动导入为新真值。

版本约束采用 ID/路径/配置快照，不校验文件内容指纹：原图、权重和编码器不得原位替换同名文件；修改资产必须换身份/版本路径并使用新的评测目录。不会检测“相同路径、相同尺寸但像素已被改写”的情况，不应把元数据一致说成文件内容完整性证明。

`primary` 是原检测；`guided` 是已保存的复核融合；`combined` 是加上开放待审五类后的候选集合，**不是自动确认结果**。`OTHER` 单列人工待审。指标按原标注匹配，未匹配不能直接判定真实误报；视频无人工真值，指标为 null。图库逐项保留原图、原检测、VLM 新增建议、既有融合、开放模型全部框及去重后的待审联合层，跳过/失败/无标注明确显示。域内图片可能训练重叠，视频帧不代表全片覆盖；结论与证据见 [T11 交付记录](../.trellis/tasks/09-11-m3-incremental-evaluation/report.md)。

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

- 页面启动按钮或 POST /api/tasks/{id}/start 显式启动图片/视频检查，返回 202。相同任务重复点击沿用固定工作流 ID 与首次落盘配置；启动请求超时可重试，不另建任务；终态重处理需新建任务。
- GET /api/tasks/{id} 的 snapshot 包含实际阶段、重试次数、结论和证据；GET /api/tasks/{id}/calls 查看每个工具的各次尝试。成功 Activity 结果落检查点，可跨 worker 重启复用。
- 图片默认采用 `candidate_review`：保留已有 v2 候选复核并加入召回保护，所有图片审核一次，高置信检测也不跳过。代码保留全部原始检测类别/框/分数，VLM 分歧不能删除它们，新增和未决发现保持待审。视频仍沿用原条件式候选复核，不受本次策略修改影响。
- 正常必须满足画质、全图检测覆盖和完成的全图阴性审核。OTHER、冲突或不能判断的候选为待复核；T09 新任务进入真正的人工等待，旧 pinless 任务维持历史 partial 行为，详见下节。
- VLM 传输最多 3 次尝试；鉴权/配置/结构错误不重试，超时/网络失败标记 uncertain。检测端的可恢复传输失败最多重试 3 次；本地最终落库可耐久重试，但不会再执行检测或 VLM。供应商可能已处理请求，不能保证无重复计费。当前只记录次数与状态，未计量真实 token/金额。
- VLM 使用原图（默认最多 8 MiB）。可选实验模式 `SEWER_IMAGE_REVIEW_MODE=independent_discovery` 不提供检测器答案；大于 1024 像素且短边至少 256 时，同一次 API 请求加入最多四个重叠原生裁剪，不进行超分或增强。裁剪超出单图/总字节预算时省略该裁剪，保留整图。实验输出使用归一化坐标，由后端换算；原件不修改。API 失败保留检测证据，不报告正常。
- 两种图片模式均固定 `image-recall-v1`；默认候选复核为 `image-review-v2`，实验独检为 `image-discovery-v2` + `native-quadrants-v1`。模式及版本固定入任务；已有结果不改写，旧版本未完成任务/运行中切模式不会被新 worker 悄悄换规则执行，配置不符将明确失败。
- 独检模式因真实评测中误报/重复候选和自动确认退化，未获默认启用验收；只有显式设置才使用。相近 VLM 框作为定位备选保留待审，候选召回不等于最终准确率。当前 `.env` 未改动、服务未重启。
- 恢复时要求检测器/标签空间及规则、Prompt、VLM 配置版本一致；不要原地覆盖同版本权重。视频恢复/取消已接通；图片仅支持取消人工等待，活动中的图片 GPU 调用取消和开机自启尚未提供。

用户已选定 `qwen3.8-max`，LLM/VLM 两个角色均复用本地 `DASHSCOPE_API_KEY`；已验证 `https://dashscope.aliyuncs.com/compatible-mode/v1`。不自动切换地域或服务商。模型效果仍需固定集评估：[官方模型说明](https://help.aliyun.com/zh/model-studio/qwen3-8-max)。

配置示例见 `.env.example`。共享 Key 仅自动用于官方 HTTPS Qwen 地址；其他兼容服务需要明确设置对应角色的 Key，不能把共享密钥自动发往任意地址。`SEWER_API_ENABLE_THINKING=false` 与 `SEWER_API_MAX_OUTPUT_TOKENS=4096` 限制输出；参数在任务启动时固定。测试可临时覆盖上限为 256，正常工作流不自动使用测试值。

## 持久对话、关联复检与证据报告（T06）

- 升级后重新执行 `scripts/init_storage.py` 新增对话/复检关联表，并重启 API 与 CPU worker；GPU worker 命令不变。该操作不删除旧数据。网页“检查助手”支持文本、可选附件或已有任务；手动入队入口仍保留。
- `POST /api/sessions/{session_id}/turns` 接收 `request_id`、`text`、可选 `input_asset` / `task_id`，返回 202；同一路径 GET 恢复最近 50 条消息，加 `/{request_id}` 查询单条阶段。未指定任务时，沿用最近一次已完成对话的关联任务；超出保留窗口时应显式选择。
- PostgreSQL 保存原始消息、意图/计划/分派检查点；Temporal 依次规划、执行、结束。每会话最多一条活跃消息；启动确认失败可原样重发相同 request_id，刷新后也能用“确认启动”重试。终态不可被重试改写。
- `IntentParser` 使用最多 5 轮已完成历史（10 条消息）；服务端构建受约束计划，拒绝未部署工具。对话新检查始终执行五类，图片均按已配置模式进行 VLM 检查；视频仍按预算和条件复核。文本识别仍可能误解意图、增加已允许工具的调用，不能承诺语义判断完全准确；必要复检失败保留 partial，不冒充完成。
- 回答“已启动”只表示关联检查已提交，不表示检查完成；检测阶段由关联任务显示。查询/解释引用保存的结论、发现、覆盖和复检说明，不重新跑模型；意图解析本身仍调用一次 LLM。无证据、视频执行或未部署工具均明确说明限制。
- `GET /api/tasks/{id}/report?format=json` 导出版本化 TaskReport（任务快照＋工具尝试）；`format=html` 查看转义后的中文证据报告。仅终态可导出；进行中返回 409。partial/failed/cancelled 不被包装为正常，报告保留待审、覆盖及工具错误。
- 报告仅查询存储，不调用模型、不改变旧结论；HTML 证据链接指向服务器媒体接口，不包含离线打包的原图。
- 选择已完成或部分完成的原图片任务，发送“请对这个任务追加一次 VLM 复检”：新建带 `parent_task_id` 的关联任务，复用原图及冻结的检测证据，不运行 GPU 检测、不改写原结果。页面和 HTML 报告可回到原任务报告，原检测版本/调用记录仍属于原任务。
- 每个原任务最多一个复检子任务；重复 request_id 沿用首次身份与配置，子任务不能再追加子任务，失败也占用次数。启动确认失败可对同一子任务重试 `/start`；已结束的复检不因 `/start` 重做。复检只需 CPU/API worker，允许使用当前配置的 VLM，但重试不得切换模型或参数。
- 追加操作仅允许 `verify`（可附带摘要/报告）；若同时要求尚未部署的分割/增强等工具，本次全部不执行。前后 VLM 实质判断冲突保留待审；原有 VLM 新增发现/OTHER 不因新回答遗漏而消失。缺失或弃权的原复检可由新的有效审核补足；复检失败不能得出正常。后置工具仍未交付，不能称完整首版已完成。
- 任务和对话关联必须属于同一会话；上传资产沿用本机单用户全局模型，没有上传者所有权或用户鉴权。这不是多用户数据隔离方案，仍只能绑定本机使用。

本地对话恢复测试：`SEWER_RUN_CONVERSATION_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_conversation_workflow.py -m 'not live_api'`。只使用隔离测试 schema/队列，不重启共享服务；另加 `SEWER_RUN_LIVE_API=1` 并选择 `-k real_qwen` 才会付费调用文本 API。真实测试使用合成已存证据验证查询分派，不代表正常样本验收。远端成功但尚未保存本地检查点的崩溃窗口仍可能重复付费，不能承诺 exactly-once。

关联复检的本地 PG/Temporal 验证：`SEWER_RUN_CONVERSATION_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_image_supplement_integration.py`；无需 GPU 或付费 API，覆盖限流重试、重复分派、保留 OTHER、Worker 替换及最终写库失败恢复。

## 人工审核与事件证据（T09）

- 升级时先运行 `scripts/init_storage.py` 新增审核表，再重启 API 和 CPU worker；旧数据不重写。新入场任务固定 `human_review_version=human-review-v1`，已有旧任务不追加入场或重新打开。
- 新图片、关联复检及视频任务存在未决结果时停在 `waiting_review / human_review`（95%），无活动模型调用。页面“进入人工复核”展示原始输入、原坐标框与证据时间；必须逐项确认、改类或驳回并填写理由。OTHER 只能由人工明确名称。
- GET `/api/tasks/{id}/review` 查询快照及历史；POST 同路径提交 `request_id`、`reviewer`、`snapshot_updated_at`、完整 `decisions`，可附阴性审计声明和备注。同一内容重试必须沿用 UUID；内容变化或过期快照返回 409。审核人是自报字段，不是登录鉴权。
- 审核决定与前后快照在一个事务中保存；Temporal 收到持久化回执后结束等待。取消使用原 `/cancel` 接口；审核正在提交时拒绝竞争取消。已完成的人工结果不被自动流程重放覆盖。
- 不能通过人工点击补出缺失覆盖或画质；全拒绝也不必然等于正常。首版只支持一次完整批次，不含多人审核、补画新框或修订已提交审核。终态可查看审核记录、导出原报告。
- 视频时间轴复用已保存候选事件和证据帧，显示源时间戳/帧区间与解码完整性，不再调用模型；候选事件不等于已确认缺陷。

无付费调用的真实等待恢复测试：

```bash
SEWER_RUN_STORAGE_TEST=1 SEWER_RUN_HUMAN_REVIEW_WORKFLOW_TEST=1 .venv/bin/pytest \
  backend/tests/integration/test_human_review_storage.py \
  backend/tests/integration/test_human_review_recovery.py -q --tb=short --log-level=CRITICAL
```

## 视频初查核心（T07）

已提供独立的本地初查入口：逐帧解码并保留原始 PTS，调用可替换检测器，跨块聚合同类候选事件，选择少量原帧证据。网页/对话已通过 T08 接入 Temporal 长视频进度、检查点、取消与恢复；下面的独立 CLI 本身不支持断点续跑。

```bash
.venv/bin/python scripts/video_scan.py \
  --source /absolute/path/to/cctv.mp4 \
  --checkpoint yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt \
  --output var/video-scans/example-001
```

输出目录必须不存在。`observations.jsonl` 记录已处理原帧，`manifest.json` 保存事件、模型/规则版本、实际覆盖与错误，`frames/` 保存选中的原帧 PNG。每批默认 8 帧，临时图片随批次清理；证据通过第二次仅解码扫描提取，不再次推理。超时是“没有完整新帧”的等待上限，不是整段视频的时长上限。

这是初查候选，不是最终复核结论：包括零候选在内均为 `pending`；解码或检测中断保留已处理结果并标记不完整。事件只表示连续可见片段，不保证重复路过同一缺陷仍是同一事件。真实 FFmpeg 时间戳/短暂单帧/跨块测试及现有权重 GPU 冒烟通过；合成视频不代替真实 CCTV 效果验收。

## 复核增益先导评估

`scripts/review_eval.py` 对固定标注图片使用与图片 worker 相同的模式配置/保留融合规则，分开比较原始检测、保留候选、自动确认的 P/R/F1。`recall_attribution` 冻结原始命中后，另计原命中保留/丢失、VLM 补回目标及新增未匹配框；零检测图片的补回单列，重复观察不算补漏。默认只汇总已存结果，显式 `--live` 才调用本地模型和 API，默认最多 48 张；已落盘图片（包括失败）不重试。API 发出后、落盘前若进程崩溃，计费/结果可能未知，不能承诺跨故障零重复调用，恢复前须核对记录。

已完成真实 Sewer2403 配对试验，但原训练划分清单缺失，不能称为独立泛化测试；五类空标签也不等于管道正常。历史数据要求见[评估说明](../.trellis/tasks/09-10-m2-video-core/evaluation-report.md)。

YOLO 引导复核仍仅用于实验，不替换 worker 默认策略：`scripts/guided_review_eval.py --baseline <已有 state.json> --output <新实验目录> --live` 复用完全相同的检测/画质缓存，每张一次 VLM 请求，保存输入视图、逐框回答和融合预测。当前 `yolo-guided-review-v2` / `guided-confidence-v2` 将 score≥0.85 的原类别、框和分数保留，VLM 否决/改类仅记录待审；低分候选仍允许明确证据下的删除/原框改类，不确定保留。新增须可定位且无重复/类别冲突。去掉 `--live` 只复算同版本已存结果；新版本必须用新目录，不能把旧响应冒充新 Prompt 实测。

`evaluation.gallery.export_gallery(manifest, records, output_dir)` 导出全部图片的原图、YOLO、VLM 新增建议（融合前）、实际融合预测、标注及逐框理由。SVG 嵌入未经改写的原始图片；GT 只参与评分和展示，不进入模型。P/R 按同一原标注计算，未匹配框不等于人工确认误报；高分保留也不等于 VLM 确认正确。最新实验及逐图入口见[置信度分层复核记录](../.trellis/tasks/09-11-confidence-review/report.md)，[上轮结果](../.trellis/tasks/09-11-guided-review/report.md)保持不变。
