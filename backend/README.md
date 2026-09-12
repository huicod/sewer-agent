# 后端开发入口

当前已接通对话＋附件、图片/视频检测与复检、报告、关联复检、人工审核等待/恢复和视频证据时间轴。2026-09-12 起开放补检退出在线调用，保留五类主检测＋VLM 主链路；用户接受的图片实验融合未切为 worker 默认，本次也不改动。总范围见 [总体开发计划](../project_management/DEVELOPMENT_PLAN.md)，当前交接见 [停用记录](../.trellis/tasks/09-12-retire-open-detectors/report.md)。

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

## 开放候选历史实现（T10，已退出在线）

YOLOE / OWLv2 / MM-Grounding-DINO 在本次管网对照中未表现出足够的有效补检收益，用户决定弃用额外补检分支。当前在线顺序为主检测 → 条件 VLM → 规则融合/人工审核 → 报告。VLM 的复检、全图审计与其他异常待审能力保留；不改主模型、VLM 或融合策略。

本服务器已准备以下官方资产（均不入 Git）：

- `var/models/yoloe/yoloe-26s-seg.pt`：[官方权重](https://github.com/ultralytics/assets/releases/download/v8.4.0/yoloe-26s-seg.pt)。
- `var/models/yoloe/mobileclip2_b.ts`：[官方文本编码器](https://github.com/ultralytics/assets/releases/download/v8.4.0/mobileclip2_b.ts)。
- `.venv` 已安装固定 revision 的 Ultralytics CLIP、ftfy 6.3.1、regex 2026.9.3；可选依赖组为 `.[gpu,open-scan]`，现有 torch/torchvision/Ultralytics 未升级。

新任务不携带 `open_scan` 配置，对话与直接启动均拒绝显式开放补检。遗留 `SEWER_OPEN_*` 配置不能启用在线调用；这些字段仅保留给历史实现/实验兼容。部署本次变更需重启 API、CPU/GPU worker，不能混用旧执行器。本次不改真实 `.env`，不自动启动产品服务。

旧结果、`open_detector` 来源和报告仍可读。正式 worker 注册的是只读兼容 activities：已有检查点直接返回，未完成补检以非重试 `tool_retired` 失败，并保留不完整结果，不能伪装正常。保留旧 workflow 命令顺序用于历史重放；不改写旧 run pins。新视频不再为开放扫描预留预算，恢复原 VLM 选帧上限 32。

模型权重、适配器与实验脚本均保留，不再是正式系统可选工具。以下仅为显式运行的历史实现回归和离线冒烟，不代表生产调用：

```bash
SEWER_RUN_OPEN_SCAN_TEST=1 .venv/bin/pytest backend/tests/integration/test_open_scan_workflow.py -q --tb=short
.venv/bin/python .trellis/tasks/09-11-m3-open-candidates/smoke.py
```

第二条只在本服务器运行两张固定原图的 GPU 冒烟，生成原图坐标框和原始 JSON，不调用 API。T11 固定集增量对比见下文；保留证据，不以候选数量替代准确率，也不继续自动寻找替代补检模型。

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
- 追加操作支持 `verify` 或一种显式图片增强（见 T12），共用上述一次关联补证额度；可附带摘要/报告。若同时要求尚未部署的分割等工具，本次全部不执行。前后 VLM 实质判断冲突保留待审；原有 VLM 新增发现/OTHER 不因新回答遗漏而消失。复检失败不能得出正常。其余后置工具尚在开发，不能称完整首版已完成。
- 任务和对话关联必须属于同一会话；上传资产沿用本机单用户全局模型，没有上传者所有权或用户鉴权。这不是多用户数据隔离方案，仍只能绑定本机使用。

本地对话恢复测试：`SEWER_RUN_CONVERSATION_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_conversation_workflow.py -m 'not live_api'`。只使用隔离测试 schema/队列，不重启共享服务；另加 `SEWER_RUN_LIVE_API=1` 并选择 `-k real_qwen` 才会付费调用文本 API。真实测试使用合成已存证据验证查询分派，不代表正常样本验收。远端成功但尚未保存本地检查点的崩溃窗口仍可能重复付费，不能承诺 exactly-once。

关联复检的本地 PG/Temporal 验证：`SEWER_RUN_CONVERSATION_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_image_supplement_integration.py`；无需 GPU 或付费 API，覆盖限流重试、重复分派、保留 OTHER、Worker 替换及最终写库失败恢复。

## 历史证据比较（T14-A）

在同一会话选择“已有任务”和“比较参考任务”，发送“比较这两份已保存检查，给出比较报告”。
也可在本条文本写明参考任务 UUID；当前任务未选时沿用最近已完成对话的关联任务。
两项必须不同、已结束（completed/partial），且同为原始图片或同为视频的检查；新上传媒体需先检查。
`POST /api/sessions/{session_id}/turns` 新增可选 `reference_task_id`，与 request_id 一起绑定重试身份。

比较按五类＋OTHER＋未定类分别展示已确认、待定、待人工复核、已驳回数量，同时保留源快照、
覆盖限制和证据链接。两图没有自动配准，视频没有逐事件物理匹配；计数差异不是缺陷真实新增、
消失或维修效果，也不能靠“零发现”判正常。比较不重跑检测/VLM，不修改源任务或占用补证额度；
意图解析仍使用一次 LLM。比较快照保存在现有对话检查点，恢复复用首次结果。

- `GET /api/sessions/{session_id}/turns/{request_id}/comparison`：保存的比较或 null。
- 同路径加 `/report?format=json|html`：导出比较报告；未生成返回 409，与原任务报告分开。
- 页面在已完成对话下展开读取。HTML 仍引用服务器资产接口，不是离线媒体打包。

本次无需新表/包；使用前重启 API、CPU worker并更新前端，不混用旧版本。
规范检索 T14-B 尚待指定规范编号/版本及真实文档，当前不提供无来源评级。

```bash
SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_comparison_storage.py
SEWER_RUN_CONVERSATION_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_comparison_live.py
```

第二条使用真实 PostgreSQL/Temporal、合成已存检查，不启动 GPU/VLM；覆盖完成响应丢失和 worker
替换。额外设置 `SEWER_RUN_LIVE_COMPARISON=1` 才调用真实付费意图 API；可设置一个尚不存在的
`SEWER_COMPARISON_OUTPUT` 目录保存请求、快照和报告。这是功能测试，不是准确率实验。

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

## 开放补检模型替代实验（图片，非生产切换）

安装可选 `open-scan-hf` 依赖，并按[模型记录](../.trellis/tasks/09-11-open-detector-alternatives/research/models.md)
用 `huggingface_hub.snapshot_download` 将固定版本的官方模型、处理器及 tokenizer 下载到
`var/models/open-detectors/mm-gdino-base` 和 `owlv2-large`，保留其自动生成的下载版本元数据。
模型使用 safetensors，本地推理不联网，不训练，也不读取 API Key。

```bash
.venv/bin/python -m pipe_inspector.evaluation.open_alternatives \
  --reference var/evaluations/2026-09-11-incremental/verified/state.json \
  --fixed-manifest .trellis/tasks/09-11-m3-incremental-evaluation/fixed-set.json \
  --guided-state var/evaluations/2026-09-11-confidence/paired/state.json \
  --guided-state var/evaluations/2026-09-11-confidence/regression/state.json \
  --models-root var/models/open-detectors \
  --output var/evaluations/2026-09-11-open-alternatives
```

复用原 48 图的检测/VLM 缓存及条件触发，不包含视频、不产生 VLM 请求。
新模型输入为原图和相同七项文字概念，不是 VLM 绘制图或 YOLO 裁剪。
`index.html` 并排展示 VLM 基线与各补检结果；模型子目录包含 `state.json`、
`summary.json`、全流程图库和保留已有人工编辑的 `review-queue.csv`。
原 YOLOE 缓存仅只读引用/导出，不覆盖历史目录。重复运行复用结果；不同配置须新目录。
新模型在一次离线运行内复用显存，结束后释放；旧评测/生产逐次释放默认不变。
当前原生实现采用 MM FP32、OWLv2 eager FP16；版本元数据检查不是文件内容哈希，
不得原位替换同版本权重/处理器/图片，换资产应使用新版本和新实验目录。
新增框均为待审，OTHER 不计入五类指标；各模型阈值、分辨率、计时口径不同，不能按框数
判胜或宣称严格速度排名。开放补检现已退出在线入口和 worker；上述入口仅保留为历史离线实验。

## 图像增强工具与在线对话（T12）

本地适配器支持 DarkIR-m 低照度恢复、DehazeFormer-MCT 去雾、Restormer motion 去模糊和
Real-ESRGAN `realesr-general-x4v3` ROI 四倍超分。使用已有 GPU PyTorch 环境，额外依赖仅
`einops==0.8.1`（`enhancement` 可选依赖组）。固定权重来源、版本及许可见
[模型记录](../.trellis/tasks/09-12-m4-enhancement-tools/model-report.md)。推理只读取本地可信权重，不联网下载。

```bash
.venv/bin/python -m pipe_inspector.evaluation.enhancement \
  --manifest .trellis/tasks/09-12-m4-enhancement-tools/samples.json \
  --models-dir var/models/enhancement \
  --output var/evaluations/enhancement-example-001
```

输出目录必须不存在；该离线比较入口不提供断点续跑。每张样本显式比较三种工具，输出
`index.html`、`results.json` 和各 `sample-*/` 下的原图/增强 PNG；失败单独记录，不用插值或锐化冒充模型结果。
原图保持不变；增强资产记录父资产、模型版本、原始 ROI 坐标和前后质量摘要。
单次输入最多 1,048,576 像素，输出最多 4,194,304 像素；超分必须提供原图坐标 ROI。

普通检查默认不增强。T12-C 支持本次对话明确许可后的按需选择：原图检测/VLM → 独立 VLM
选择一种工具或跳过 → 后端校验退化/区域/固定模型 → 既有增强及双图复核。低对比不等于有雾，
全图亮度/模糊指标只是线索，不直接执行模型。旧三工具离线评价的质量建议仍不自动执行。
锐度、对比度提高不证明缺陷准确率提高，超分前后不同分辨率的锐度值也不能直接比较。
增强图始终是需复核的派生证据，不可替代原图或用于无标定的真实尺寸测量。
在线示例：上传图片后说“检查图片，并做去雾辅助复核”；或选择已完成/部分完成的原图片任务，
说“对原图像素区域 [150,50,406,306] 追加局部超分辅助复核”。坐标必须适合实际图片；
超分缺 ROI 会先澄清，每次仅一种增强，不支持视频增强或增强图继续增强。
低照度示例：“新建图片检查，并做暗光恢复辅助复核”。按需示例：“新建图片检查，允许你
根据实际画质按需选择一次增强，不需要就跳过”。未明确许可、历史许可或本次禁止增强，
均不应生成 `auto_enhancement=true`。自动选择仅限新原始图片检查；关联补证仍需明确种类。

新检查：原图检测 → 原 VLM → 保存基线 → 本地增强 → 原图/增强图辅助 VLM → 结果/人工审核。
关联增强只复用冻结的原任务证据，不重新检测或重做原图 VLM；不修改父任务。
辅助结论不删除/改类/移动/改分原发现，新异常和冲突待人工确认；不凭增强提高覆盖/阴性审计。
必需增强或辅助复核失败保留原证据并输出 partial，不进入可误点完成的人工等待。

沿用 CPU/API worker 和串行 GPU worker（`scripts/inspection_worker.py cpu|gpu`），模型路径配置见
`.env.example`。阶段显示增强类型和辅助复核；`GET /api/tasks/{id}/enhancement` 可读取中途/终态的
原图、派生资产、ROI、质量、辅助建议与 `selection`（选中/跳过及依据），无结果字段为 null。
选择失败保留原复核结论但任务 partial，不把下游失败当作原 VLM 失败。页面“图片增强对照”按需加载，
终态 JSON/HTML 报告包含相同证据。原 VLM Prompt、主检测/融合及开放补检停用策略不变。

无付费恢复验收（只使用独立 schema/队列，不重启共享服务）：

```bash
SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_enhancement_storage.py
SEWER_RUN_ENHANCEMENT_WORKFLOW_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_enhancement_workflow_live.py
SEWER_RUN_ENHANCEMENT_WORKFLOW_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_adaptive_enhancement_live.py
```

真实对话/上传/GPU/API 示例见 [T12-B 记录](../.trellis/tasks/09-12-m4-enhancement-online/report.md)。
复现需显式设置 `SEWER_RUN_LIVE_ENHANCEMENT=1`、`SEWER_LIVE_IMAGE` 和不存在的
`SEWER_ENHANCEMENT_OUTPUT` 后运行 `backend/tests/integration/test_live_enhancement_online.py`；
该受控测试固定示例 ROI，至多 3 次 API 和一次增强推理，结束后保留文件、删除自己的测试 schema，
不代替人工审核。原图/模型同名文件不得原位替换；提交前崩溃仍可能重算/重复计费，不承诺 exactly-once。

T12-C 同一 smoke 增加 `SEWER_LIVE_AUTO_ENHANCEMENT=1` 可验证主动分支，至多四次 API、
一次 GPU 增强；跳过时三次 API、零次增强。新工作流固定可用模型和选择版本，已持久化选择
不重新询问 API；增强失败/无收益不递归尝试其他模型。
DarkIR 权重 `var/models/enhancement/darkir-384-hf-8a73ebf2.pt`；HVI-CIDNet 仅离线对照，
来源/许可/下载版本见 [低照度记录](../.trellis/tasks/09-13-m4-adaptive-enhancement/lowlight-report.md)。
运行 `.venv/bin/python scripts/evaluate_lowlight_models.py --output var/evaluations/lowlight-new-run`
可复现六图比较（新输出目录；需本地两份权重；不调用 API）。既有三种增强模型保持基线，
后续替换需逐图效果依据。当前 [T12-C 实图和在线证据](../var/evaluations/2026-09-13-adaptive-enhancement/index.html)
证明工程可用，不代表缺陷级准确率提升。

### 离线增强候选对照（T12-D）

`evaluation.restoration` 比较三组固定模型：Restormer/Concertormer、DehazeFormer-MCT/ConvIR-L、
RealESR-general-x4v3/HAT-L。候选仅由离线 factory 创建，不进入 Agent 在线白名单，也不修改
已验收 VLM 或融合。需本地权重，最多 12 张图、每类恰好两模型，必须用不存在的新输出目录：

```bash
.venv/bin/python -m pipe_inspector.evaluation.restoration \
  --manifest .trellis/tasks/09-13-m4-enhancement-candidates/samples.json \
  --models .trellis/tasks/09-13-m4-enhancement-candidates/models.json \
  --output var/evaluations/restoration-new-run \
  --detector-checkpoint yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt \
  --device cuda:0
```

图册含完整原图、共同分析图、固定 ROI、bicubic 控制、增强输出、对齐局部、检测框/JSON、
时间/显存/失败。框保留不是真值，ROI 不混入全图 P/R，无 API 调用。
[已完成的八图对照](../var/evaluations/2026-09-13-restoration-candidates/index.html)
没有提供替换现有模型的充分依据；ConvIR 的暗光失真保留为失败证据。
来源、许可证及结果限制见 [T12-D 报告](../.trellis/tasks/09-13-m4-enhancement-candidates/report.md)。

## 分割与相对深度工具（T13-A）

本地工具入口为 `vision.geometry.run_segmentation`、`run_relative_depth` 和 `measure_geometry`。
分割接收显式原图坐标框，不自行检测类别；深度使用同一原图。只接受原始 image/frame，
最多 4,194,304 像素，不用增强图量测。当前模型为 SAM 2.1 Hiera Large 和 Depth Anything V2 Small，
通过既有 Transformers 4.57.6 加载本地 safetensors；依赖声明在 `geometry` extra。
权重来源/固定版本见 [选型记录](../.trellis/tasks/09-13-m4-geometry-tools/research/models.md)。
六图实测与效果限制见 [T13-A 交接](../.trellis/tasks/09-13-m4-geometry-tools/report.md)；
[原图/掩膜/深度图册](../var/evaluations/2026-09-13-geometry-tools/verified/index.html) 保存实际输出。

```bash
.venv/bin/python -m pipe_inspector.evaluation.geometry \
  --manifest .trellis/tasks/09-13-m4-geometry-tools/samples.json \
  --segmentation-checkpoint var/models/geometry/sam2.1-hiera-large \
  --depth-checkpoint var/models/geometry/depth-anything-v2-small \
  --output var/evaluations/geometry-new-run \
  --device cuda:0
```

必须使用新的输出目录。结果包括原图、提示框、二值 mask/叠加 PNG、float32 相对深度 NPY、
深度预览和 JSON/HTML。深度预览按单图归一化，白近黑远；原始 NPY 用
`np.load(path, allow_pickle=False)` 读取，不是米或毫米。面积占比分母为整幅图像，
不是管道真实堵塞率；同一源图才可合并掩膜与深度，分割质量分数也不是缺陷正确率。
空掩膜、常数/非有限深度会报告失败，不生成替代结果。

### 在线几何工具（T13-B）

图片几何是显式可选工具，不是默认检查步骤。上传原图后可说：
“检查这张图片，在原图像素框 [280,66,549,350] 做分割和相对量化。”
坐标只是示例，必须适合实际图片。新检查先完成原图检测/条件 VLM，再串行执行几何工具。
只要深度图可说“检查图片，并生成整图相对深度图”，不需要分割框。

选择已完成或部分完成的原始图片任务后，可说“按 finding ID <实际 UUID> 追加分割”，
或提供原图框追加 `relative_measure`。页面发现列表/几何证据面板及对话结果显示 finding ID。
关联操作冻结父任务证据，不重跑检测器或 VLM，不改父任务；与已有复检/增强共享一次补证额度。
若只选择已有任务而本条没有上传/指定原图，却被解析成新建几何检查，服务端先澄清，不绕过关联额度。
缺框、无效 finding、视频/派生图、未部署权重会拒绝或澄清，不猜框。
首轮每次仅一个几何操作包，不和增强、自动增强或额外 VLM 补证混用。

`segment` 保存掩膜、叠加及像素统计；`relative_depth` 保存整图相对深度；
`relative_measure` 依次分割、深度并统计掩膜内分位数。几何结果始终是待复核附加证据，
不提高或降低五类结论/置信度/覆盖；必需工具失败保留原结论但任务为 partial，记录工具错误。
无标定不能测毫米尺寸、实际裂缝深度、体积或堵塞率，不能将这类要求默默降级成已完成的相对量测。

沿用 `scripts/inspection_worker.py cpu|gpu`、Temporal 和串行 GPU 队列；配置见 `.env.example`。
模型每次加载/卸载，pin 固定版本和路径；结果资产、工具调用、checkpoint 同事务提交。
已提交结果恢复时直接复用；提交前故障仍可能重算，不承诺物理推理 exactly-once。
`GET /api/tasks/{id}/geometry` 支持处理中或终态查询；旧任务返回 null 字段。
网页“几何证据”显示原图框/真实掩膜/叠加/白近黑远深度与 NPY 下载；终态 JSON/HTML 报告包含相同证据。

```bash
SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_geometry_storage.py
SEWER_RUN_GEOMETRY_WORKFLOW_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_geometry_workflow_live.py
```

付费真实联调入口 `backend/tests/integration/test_live_geometry_online.py` 需显式设置
`SEWER_RUN_LIVE_GEOMETRY=1`、`SEWER_LIVE_IMAGE`、不存在的 `SEWER_GEOMETRY_OUTPUT`。
固定示例 ROI [280,66,549,350]；上限两次 API、一次原检测、一次分割与一次深度。
使用隔离 schema/队列，结束后只删除自己的测试 schema，保留文件；不伪造人工决策。
它是流程测试，不是精度实验，不改变已验收原检测/VLM/融合或增强策略。
