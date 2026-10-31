# 用 Docker 运行管网检测工作台

适用于在一台新的 **Linux x86-64 NVIDIA GPU 主机**上部署当前在线应用。网页、图片/视频检查、对话、增强、分割、相对深度、人工审核及报告共用同一套代码。训练数据、历史会话和离线实验模型不参与新部署。

宿主机需要 Docker Engine、Docker Compose v2（建议 2.30 或更新）、Python 3.10+（仅配置/模型准备），以及支持 CUDA 12.8 的 NVIDIA 驱动和已配置的 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。主机不必安装项目的 Node、PyTorch、PostgreSQL 或 Temporal。构建时需联网下载公开依赖；运行对话、VLM 复检时需访问配置的 API。准备足够的镜像构建空间（建议至少 25 GiB 可用）和媒体存储空间；完整模型包另需约 1.07 GiB。

## 1. 取得代码和模型

从有权访问仓库的账号克隆 `dev/agent-v1`，以下命令都在仓库根目录执行。模型目录默认 `./models/`，不进入 Git 或 Docker 镜像。三种准备方式任选一种：

**已有训练好的检测器**：将本项目五类 YOLO26s 的 `best.pt` 路径交给准备脚本；脚本从各模型的原始来源下载另外六个固定版本，并生成几何模型要求的来源记录。

```bash
python3 scripts/model_bundle.py download --detector /path/to/best.pt --directory ./models
```

**已有完整 Hugging Face 包的读权限**：在 Python 虚拟环境中安装 `huggingface_hub==0.36.2`；私有仓库先用 `hf auth login` 登入有读取权限的账号。发布者提供模型仓库名和完整 40 位提交号，将其填入下面两个变量。固定提交可避免下载的模型与当前代码不匹配。

```bash
python -m pip install 'huggingface_hub==0.36.2'
hf auth login
python scripts/model_bundle.py fetch --repo-id "$MODEL_REPO" \
  --revision "$MODEL_REVISION" --directory ./models
```

**在已有本机资产的原服务器上**：从当前项目的既有位置复制清单中的文件，不下载、不改源文件。

```bash
python3 scripts/model_bundle.py prepare --source-root . --output ./models
```

每种方式完成后执行：

```bash
python3 scripts/model_bundle.py check --directory ./models
```

清单 [deploy/models.json](deploy/models.json) 包含 7 个权重和 8 个必要的配置/来源文件：`detector/best.pt`、`enhancement/` 下四个增强模型、`geometry/` 下 SAM2.1 和 Depth Anything V2 Small 的完整目录。文件大小与元数据检查用于发现缺失或版本不匹配，不代替可信来源或实际推理验证。使用其他训练版本时需明确更新清单和对应验证。容器用户 UID 10001 需要读取模型目录和文件。Qwen 走远程 API，无需 Qwen 权重。

## 2. 配置 API 和本地端口

```bash
python3 scripts/docker_setup.py
```

这会从 `.env.docker.example` 创建权限为 `0600` 的 **`.env.docker`**，并生成三份独立数据库密码。不会读取或覆盖原生部署的 `.env`，也不会覆盖已有配置。模型在其他位置时，首次生成可加 `--models-dir /absolute/path/to/models`，或直接修改 `.env.docker` 中的 `SEWER_MODELS_DIR`。

在 `.env.docker` 中填写：

- 使用官方 Qwen：设置 `DASHSCOPE_API_KEY`，并确认 LLM/VLM 地址、模型名和账号所属区域匹配。模板中的模型名是示例。
- 使用其他兼容服务：分别设置 `SEWER_LLM_BASE_URL`、`SEWER_LLM_MODEL`、`SEWER_LLM_API_KEY` 及对应 `SEWER_VLM_*`。视觉端点须支持图像输入。
- 默认网页仅绑定主机 `127.0.0.1:8000`。若与现有服务并行使用，把 `SEWER_HTTP_PORT` 改为 `18000`。项目尚无公网用户鉴权，远程访问用 SSH 端口转发。

不要重新生成正在使用的数据库卷所对应的密码；改变配置文件并不会自动轮换 PostgreSQL 角色密码。不要提交 `.env.docker`、HF Token 或真实媒体。

## 3. 首次启动、检查和停止

```bash
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker build initialize
docker compose --env-file .env.docker up -d --no-build --wait --wait-timeout 360
docker compose --env-file .env.docker ps -a
```

`build initialize` 构建一次应用镜像，API、初始化任务及两个 worker 复用它。镜像内编译前端，安装 FFmpeg/ffprobe 和固定的 PyTorch 2.8.0、torchvision 0.23.0 CUDA 12.8 轮子。使用 Compose [服务依赖](https://docs.docker.com/compose/how-tos/startup-order/)等待首启完成，[GPU 设备](https://docs.docker.com/compose/how-tos/gpu-support/)只分配给串行 GPU worker。

启动顺序：PostgreSQL 创建角色与 `sewer_agent`、`temporal`、`temporal_visibility` 三个库 → 同版本 Temporal 1.31.2 管理工具准备两个工作流库的表 → Temporal 启动并注册 `default` namespace → 应用初始化业务表 → 启动网页/API 和 CPU、GPU worker。`temporal-schema`、`temporal-namespace`、`initialize` 正常状态为 **Exited (0)**，其余五个服务应运行且健康。数据库及 Temporal 不发布主机端口。

在主机浏览器打开 `http://127.0.0.1:8000`（端口随配置调整）。远程主机可在自己电脑运行 `ssh -N -L 18000:127.0.0.1:8000 <SSH别名>`，打开 `http://127.0.0.1:18000`。先确认页面能列出会话；然后用“手动提交”上传仓库 `example/` 中的示例图片并选择“纯 YOLO”，检查成功生成任务结果和媒体证据。手动纯 YOLO 不调用检查 VLM；对话入口或 VLM 复检使用部署者的 API 账户。几何与增强按界面明确选择验证，不凭首页健康检查宣称模型推理通过。

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
| 权重及模型配置 | `/models` | 宿主目录只读挂载，不随容器删除 |
| API 与数据库凭据 | 宿主机 `.env.docker` | 单独安全保存，不进镜像、Git 或模型包 |

**全新部署**使用空卷，不需要任何历史数据。**迁移已有部署**需在一致的停写点同时备份数据库和媒体。下面示例用于上述 Compose 部署；先等待处理中的模型步骤结束，然后在仓库根目录用 Bash 执行。使用新的备份目录，备份要保存在受控位置：

```bash
set -e
umask 077
backup_dir="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
dc() { docker compose --env-file .env.docker "$@"; }
dc stop api cpu-worker gpu-worker temporal
for db in sewer_agent temporal temporal_visibility; do
  dc exec -T postgres pg_dump -U postgres -Fc "$db" > "$backup_dir/$db.dump"
done
dc run --rm --no-deps -T --entrypoint tar api -C /app/var/assets -czf - . > "$backup_dir/assets.tar.gz"
dc up -d --no-build --wait --wait-timeout 360
```

另外单独备存对应 `.env.docker`、代码版本与模型版本。恢复到**新项目/新空卷**，先仅启动 `postgres` 等它健康；在其他服务启动前，用 `pg_restore -U postgres --no-owner --role=sewer_agent --exit-on-error -d sewer_agent` 恢复业务 dump，另两个 dump 分别以 `--role=sewer_temporal` 恢复到各自同名库。将 `assets.tar.gz` 解入新 `assets` 卷的 `/app/var/assets`，保持应用用户可读写，再启动整组服务。例如备份目录已经通过 `backup_dir` 指定，且当前 `dc` **指向新空部署**时：

```bash
dc up -d --wait postgres
for db in sewer_agent temporal temporal_visibility; do
  role=sewer_temporal; if [ "$db" = sewer_agent ]; then role=sewer_agent; fi
  dc exec -T postgres pg_restore -U postgres --role="$role" --no-owner --exit-on-error \
    -d "$db" < "$backup_dir/$db.dump"
done
dc run --rm --no-deps -T --entrypoint tar api -C /app/var/assets -xzf - < "$backup_dir/assets.tar.gz"
dc up -d --no-build --wait --wait-timeout 360
```

恢复后核对会话、任务与媒体证据。原生主机历史任务可能固定了旧的**绝对模型路径**，要迁移未完成任务，应先在副本验证路径与版本兼容；上述命令不会迁移或操作原生数据库。

## 5. 模型维护与排错

向自己的 Hugging Face 仓库存储此包时，在**持有模型文件的机器**登录有写权限的账号，然后显式指定目标。上传工具只发送清单列出的模型/配置以及生成的模型卡、来源和许可说明，不复制训练集、`.env`、用户媒体或数据库。新仓库默认私有。向使用者提供命令返回的仓库 ID 和提交号，私有仓库还需授予读取权限。第三方模型的来源和许可分别列出，不把架构代码许可等同于权重的统一再分发许可。

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
| 重启后看不到旧会话 | 检查 Compose 项目名和两个卷是否与原部署一致；恢复时缺媒体卷会导致证据无法打开 |
