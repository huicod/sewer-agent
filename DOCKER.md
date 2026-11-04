# 用 Docker 运行管网检测工作台

Windows 用户可直接使用 [Windows 从零部署教程](WINDOWS.md)及 `scripts/windows_setup.ps1`，从安装 WSL/Ubuntu 开始准备 Docker 环境。

适用于在一台新的 **Linux x86-64 NVIDIA GPU 主机**上部署当前在线应用。网页、图片/视频检查、对话、增强、分割、相对深度、人工审核及报告共用同一套代码。训练数据、历史会话和离线实验模型不参与新部署。

宿主机需要 Docker Engine、Docker Compose v2（建议 2.30 或更新）、Python 3.10+（仅配置/模型准备），以及支持 CUDA 12.8 的 NVIDIA 驱动和已配置的 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。主机不必安装项目的 Node、PyTorch、PostgreSQL 或 Temporal。构建时需联网下载公开依赖；运行对话、VLM 复检时需访问配置的 API。准备足够的镜像构建空间（建议至少 25 GiB 可用）和媒体存储空间；完整模型包另需约 1.07 GiB。

已在[干净的 Docker 主机](https://github.com/huicod/sewer-agent/actions/runs/41172667988)验证一键命令可以匿名下载完整模型包，使用一把测试 Key 启动网页和 CPU 服务、初始化数据库，并在容器重建后读回原图片和会话。七个真实模型另在 RTX 4080 SUPER 上逐一完成了推理。Docker 中的 GPU 和外部 API 仍需在部署主机上，用第 3 节的示例检查。

## 1. 新机一键部署

先从有权访问私有 GitHub 仓库的账号取得代码，也可以使用项目维护者交付的代码副本。

```bash
git clone --branch dev/agent-v1 --single-branch https://github.com/huicod/sewer-agent.git
cd sewer-agent
python3 scripts/docker_setup.py up
```

已有 `dev/agent-v1` 副本更新时，在完成下文的备份和停机步骤后拉取代码并重新构建：

```bash
git pull --ff-only origin dev/agent-v1
python3 scripts/docker_setup.py up
```

网页随应用镜像构建。更新后，会话中可直接查看已保存的图片检查、缺陷标注和视频播放器，并打开原始文件及证据帧；使用已有镜像的 `--no-build` 启动会继续显示该镜像内的界面。

首次无需在终端输入 API Key；网页启动后，在**设置**中填写对话与视觉复检的 API 地址、模型名和 Key。默认预设使用阿里云百炼北京地域的 `qwen3.8-max`，账号须已开通该模型。也可以继续通过部署环境提供 `DASHSCOPE_API_KEY` 或独立的角色配置。[百炼模型与视觉能力](https://help.aliyun.com/zh/model-studio/vision-model/)

脚本创建权限为 `0600` 的 `.env.docker`，生成三份数据库密码，匿名下载约 1.07 GiB 的模型到 `./models/`，校验七个权重的 SHA-256，然后构建镜像并等待服务就绪。模型下载用 Python 标准库，版本随代码固定。再次运行同一命令可复用已下载的完整文件、配置及三个持久卷。启动成功后在宿主机打开 `http://127.0.0.1:8000`。[公开模型包](https://huggingface.co/suwewa/sewer-agent-models) · [本版固定提交](deploy/model-revision.txt)

## 2. 使用其他 API 或自备模型

使用其他地域、模型服务或端口时，可先生成配置，再编辑 `.env.docker` 并启动。配置文件已经存在时直接编辑即可。

```bash
python3 scripts/docker_setup.py
# 编辑 .env.docker 后启动
python3 scripts/docker_setup.py up
```

- 默认北京百炼配置只需 `DASHSCOPE_API_KEY`。使用其他兼容服务时，分别设置 `SEWER_LLM_BASE_URL`、`SEWER_LLM_MODEL`、`SEWER_LLM_API_KEY` 及对应的 `SEWER_VLM_*`。视觉端点和账号必须支持图片输入。服务商允许时，两处可以填同一把 Key。
- 网页默认仅绑定 `127.0.0.1:8000`。端口被占用时，在配置文件把 `SEWER_HTTP_PORT` 改为其他空闲端口。远程访问可按第 3 节使用 SSH 转发。
- 若已有这版模型，把完整目录放在 `./models/`，或在配置文件设置 `SEWER_MODELS_DIR`。目录内直接包含 `detector/`、`enhancement/`、`geometry/`，需要允许容器用户 UID 10001 读取。运行 `up` 时会核验本地文件，缺失的文件将自动下载。
- 只想先下载模型时运行 `python3 scripts/model_bundle.py fetch-public --directory ./models`。从原服务器已有资产整理模型可用 `python3 scripts/model_bundle.py prepare --source-root . --output ./models`。权重的原始来源和各自条款见[模型说明](deploy/MODEL-NOTICES.md)。

请安全保存 `.env.docker`。有数据的数据库卷需要使用原密码，重新生成密码不会轮换数据库中的角色凭据。

## 3. 检查服务、试用和停止

```bash
docker compose --env-file .env.docker ps -a
```

需要手动分步排查时，先确认模型已下载，再依次执行以下命令。

```bash
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker build initialize
docker compose --env-file .env.docker up -d --no-build --wait --wait-timeout 360
```

`build initialize` 构建一次应用镜像，API、初始化任务及两个 worker 复用它。镜像内编译前端，安装 FFmpeg/ffprobe 和固定的 PyTorch 2.8.0、torchvision 0.23.0 CUDA 12.8 轮子。使用 Compose [服务依赖](https://docs.docker.com/compose/how-tos/startup-order/)等待首启完成，[GPU 设备](https://docs.docker.com/compose/how-tos/gpu-support/)只分配给串行 GPU worker。

启动顺序：PostgreSQL 创建角色与 `sewer_agent`、`temporal`、`temporal_visibility` 三个库 → 同版本 Temporal 1.31.2 管理工具准备两个工作流库的表 → Temporal 启动并注册 `default` namespace → 应用初始化业务表 → 启动网页/API 和 CPU、GPU worker。`temporal-schema`、`temporal-namespace`、`initialize` 正常状态为 **Exited (0)**，其余五个服务应运行且健康。数据库及 Temporal 不发布主机端口。

在主机浏览器打开 `http://127.0.0.1:8000`（端口随配置调整）。远程主机可在自己电脑运行 `ssh -N -L 18000:127.0.0.1:8000 <SSH别名>`，打开 `http://127.0.0.1:18000`。先确认页面能列出会话；然后点击“新建检查”，从浏览器所在电脑选择示例图片，填写检查指令，选择“纯 YOLO（不复检）”并提交。在“任务与证据”点击“启动检查”后，查看任务结果和媒体证据。手动纯 YOLO 不调用检查 VLM；对话入口或 VLM 复检使用部署者的 API 账户。几何与增强按界面明确选择验证，不凭首页健康检查宣称模型推理通过。

同一“新建检查”窗口的“素材来源 → 项目示例”可以直接选择现场视频（18–22）。镜像包含这五段私有演示短片，点击提交时由 API 从本地导入，浏览器不传输视频文件。已有部署更新代码后重新运行 `python3 scripts/docker_setup.py up` 即可使用。

```bash
docker compose --env-file .env.docker logs --tail=80 initialize gpu-worker
docker compose --env-file .env.docker down
```

普通 `down` 保留卷，之后可重新执行 `up`。**不要加 `-v`**，否则会删除持久业务数据。升级时先备份、`down`，再构建新镜像并 `up`；不支持在线替换运行中的前端或迁移任务。固定 `COMPOSE_PROJECT_NAME` 与模型挂载路径，改项目名会得到另一组命名卷。

## 4. 数据在哪里，怎样备份

| 内容 | 容器内位置 | 持久化方式 |
|---|---|---|
| 会话、任务、审核、检测记录、Temporal 状态 | PostgreSQL 的三个数据库 | `postgres-data` 命名卷 |
| 原始图片/视频、帧、增强、掩膜、深度等资产文件 | `/app/var/assets/<资产ID>/<版本>/source` | `assets` 命名卷，API 和两个 worker 共享 |
| 网页保存的 API 配置、密钥及历史修订 | `/app/var/settings` | `settings` 命名卷，API 和两个 worker 共享；目录 `0700`、文件 `0600` |
| 权重及模型配置 | `/models` | 宿主目录只读挂载，不随容器删除 |
| API 与数据库凭据 | 宿主机 `.env.docker` | 单独安全保存，不进镜像、Git 或模型包 |

**全新部署**使用空卷，不需要任何历史数据。**迁移已有部署**需在一致的停写点同时备份数据库、媒体和设置。设置备份含当前及历史 API Key，需和 `.env.docker` 一样限制访问；历史任务继续使用其固定的设置修订，不能只迁移最新配置。下面示例用于上述 Compose 部署；先等待处理中的模型步骤结束，然后在仓库根目录用 Bash 执行。使用新的备份目录，备份要保存在受控位置：

```bash
set -e
umask 077
backup_dir="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
dc() { docker compose --env-file .env.docker "$@"; }
dc stop api cpu-worker gpu-worker temporal
for db in sewer_agent temporal temporal_visibility; do
  dc exec -T postgres pg_dump -U postgres -Fc "$db" > "$backup_dir/$db.dump"
done
dc run --rm --no-deps -T --entrypoint tar api -C /app/var/assets -czf - . > "$backup_dir/assets.tar.gz"
dc run --rm --no-deps -T --entrypoint tar api -C /app/var/settings -czf - . > "$backup_dir/settings.tar.gz"
chmod 600 "$backup_dir"/*
dc up -d --no-build --wait --wait-timeout 360
```

另外单独备存对应 `.env.docker`、代码版本与模型版本。恢复到**新项目/新空卷**，先仅启动 `postgres` 等它健康；在其他服务启动前，用 `pg_restore -U postgres --no-owner --role=sewer_agent --exit-on-error -d sewer_agent` 恢复业务 dump，另两个 dump 分别以 `--role=sewer_temporal` 恢复到各自同名库。将 `assets.tar.gz` 和 `settings.tar.gz` 分别解入新卷的对应目录，保持应用用户 UID `10001` 可读写、设置目录 `0700` 及文件 `0600`，再启动整组服务。下面的设置归档由应用用户导出、恢复时也使用相同用户；不要打印归档内容或将其放入公开附件。例如备份目录已经通过 `backup_dir` 指定，且当前 `dc` **指向新空部署**时：

```bash
dc up -d --wait postgres
for db in sewer_agent temporal temporal_visibility; do
  role=sewer_temporal; if [ "$db" = sewer_agent ]; then role=sewer_agent; fi
  dc exec -T postgres pg_restore -U postgres --role="$role" --no-owner --exit-on-error \
    -d "$db" < "$backup_dir/$db.dump"
done
dc run --rm --no-deps -T --entrypoint tar api -C /app/var/assets -xzf - < "$backup_dir/assets.tar.gz"
dc run --rm --no-deps -T --entrypoint tar api -C /app/var/settings -xzf - < "$backup_dir/settings.tar.gz"
dc up -d --no-build --wait --wait-timeout 360
```

恢复后核对会话、任务、媒体证据和设置页的已配置状态。旧版本备份没有 `settings.tar.gz` 时只可跳过该设置归档步骤，继续使用对应 `.env.docker`；已经使用网页设置的部署不可省略此卷。原生主机历史任务可能固定了旧的**绝对模型路径**，要迁移未完成任务，应先在副本验证路径与版本兼容；上述命令不会迁移或操作原生数据库。

## 5. 模型维护与排错

本节仅供需要发布新权重的维护者使用。向自己的 Hugging Face 仓库存储此包时，在**持有模型文件的机器**登录有写权限的账号，然后显式指定目标。上传工具只发送清单列出的模型/配置以及生成的模型卡、来源和许可说明，不复制训练集、`.env`、用户媒体或数据库。新仓库默认私有。如果目标是组织私有仓库，向有读权限的成员提供命令返回的仓库 ID 和提交号。第三方模型的来源和许可分别列出，不把架构代码许可等同于权重的统一再分发许可。

```bash
hf auth login
python scripts/model_bundle.py upload --directory ./models --repo-id YOUR_ACCOUNT/sewer-agent-models
```

| 现象 | 检查 |
|---|---|
| 初始化任务提示模型不完整 | 在宿主机运行 `model_bundle.py check`，确认只读挂载根目录直接含 `detector/`、`enhancement/`、`geometry/`，且子文件可读 |
| API/worker 配置不完整 | 核对 `.env.docker` 的两套模型名、端点和密钥；不要把整份配置或 `docker compose config` 的展开输出贴到日志 |
| GPU worker 未就绪 | 检查宿主机 `nvidia-smi`、容器 GPU 支持、驱动与 CUDA 兼容性；普通 CPU Docker 主机不能验收 GPU 推理 |
| 首启数据库或 Temporal 异常 | 用 `dc ps -a` 和对应服务日志检查。保留数据库卷；不要通过删除卷来重置密码 |
| 重启后看不到旧会话 | 检查 Compose 项目名和三个卷是否与原部署一致；恢复时缺媒体卷会导致证据无法打开 |
