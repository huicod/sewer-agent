# Windows 从零部署

这条路线在 Windows 内安装 WSL 2 和 Ubuntu，再由 Ubuntu 运行 Docker Engine 与项目。Windows 不需要预先安装 Git、Python、Node 或 Docker Desktop。适用于 Windows 11 x86-64 和 NVIDIA 显卡；显卡需安装支持 CUDA 12.8 的 Windows 驱动。Ubuntu 默认选用 24.04 LTS。

安装入口会检查已经完成的步骤，可以重复执行。首次安装 WSL 可能需要重启 Windows；Ubuntu 首次创建用户名、密码，以及后续 `sudo` 输入密码，需要你在终端完成。脚本不会自动重启电脑。

## 1. 下载代码

打开公开的 [dev/agent-v1 分支](https://github.com/huicod/sewer-agent/tree/dev/agent-v1)，选择 **Code → Download ZIP**，解压到一个普通本地文件夹，例如 `C:\sewer-agent`。确认其中能看到 `WINDOWS.md`、`compose.yaml` 和 `scripts` 文件夹。也可使用维护者提供的完整代码 ZIP。

代码和七个视觉模型包均可匿名下载，无需 GitHub 登录、GitHub 令牌或 Hugging Face Token。部署者自己的模型服务 API Key 在网页设置中填写，不随公开代码提供。

预留至少 **25 GiB** 镜像构建空间，模型另约 **1.07 GiB**；Ubuntu 的虚拟磁盘、视频、证据帧和数据库还会占用空间。完整模型推理建议配置与已测边界见 [Docker 部署手册](DOCKER.md)。这些空间数不代表显卡最低配置。

## 2. 运行安装入口

在解压目录右键打开 PowerShell。首次安装 WSL 时，请使用**自己平时的 Windows 账号，以管理员身份**打开 PowerShell，进入解压目录后运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_setup.ps1 -Action setup
```

`ExecutionPolicy Bypass` 仅用于这次脚本进程，不更改系统长期执行策略。脚本安装以下内容：WSL/Ubuntu、Docker Engine、Docker Compose、NVIDIA Container Toolkit，以及应用镜像和模型。构建和下载需要能够访问微软、Docker、NVIDIA、Python/npm 和 Hugging Face 的服务。

按当前机器状态，安装可能在以下两处暂停：

1. **WSL 安装完成后。** 按 Windows 提示重启；之后运行 `wsl -d Ubuntu-24.04`，完成 Ubuntu 用户名和密码的创建，输入 `exit` 返回 Windows，再重复上面的安装命令。Ubuntu 密码输入时不显示字符。
2. **需要启用 systemd 时。** 脚本只修改 `/etc/wsl.conf` 的 `boot.systemd`，保留其余设置并备份原文件。关闭这个 Ubuntu 中的其他工作，按提示运行 `wsl --terminate Ubuntu-24.04`，再重复安装命令。无需关闭其他 WSL 发行版。

如果 WSL 下载失败，先按照终端信息处理 Windows 更新或网络问题。微软提供的手动入口为 `wsl --install -d Ubuntu-24.04 --web-download`；WSL 本身较旧时，可先执行 `wsl --update`。安装器发现 WSL 1 会提示 `wsl --set-version Ubuntu-24.04 2`，不会自动转换正在使用的发行版。[微软安装说明](https://learn.microsoft.com/en-us/windows/wsl/install) · [WSL 命令说明](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)

安装默认把源码放在 **Ubuntu 用户主目录下的 `sewer-agent`**，如 `/home/你的Ubuntu用户名/sewer-agent`，避免直接在 Windows 挂载盘运行数据库及大量模型文件。默认网址是 `http://127.0.0.1:8000`。如需选择其他端口或独立项目，可在第一次安装时指定：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_setup.ps1 -Action setup -Port 18082 -InstallDir sewer-agent-demo -ProjectName sewer-agent-demo
```

`InstallDir` 必须是 Ubuntu 当前用户主目录里的专用目录；不能指向主目录本身、Windows 挂载目录或符号链接。安装器不会接管已有非本安装器管理的目录。多套部署须使用不同的安装目录、项目名和端口；默认项目名和镜像标签是 `sewer-agent-wsl`。

## 3. 在网页配置 API

安装完成后打开终端给出的网址，进入侧栏的**设置**，填写对话模型和视觉复检模型的 API 地址、模型名与 Key。可以使用预设百炼地址，也可填写兼容服务。没有 Key 时可以先打开工作台并使用支持的纯 YOLO 图片检查；需要对话或视觉复检时再完成对应配置。

以后更换 Key 也在“设置 → API 连接”操作：在对应角色输入新密钥；两者需要一起更换时，勾选“视觉复检使用相同的 API 配置”，然后点击“保存设置”。新检查立即生效，无需修改 `.env.docker`、重建镜像或重启服务；已开始的检查继续使用原配置。密码框留空表示保持已保存的密钥。完整步骤见 [网页配置和更换密钥](DOCKER.md#通过网页配置和更换-api-密钥)。

设置页只显示密钥是否已配置，不回显保存的完整 Key。API 配置保存在本机服务端持久存储中，不能只备份网页或源码；备份时同时保留数据库、媒体和设置，参见 [Docker 备份说明](DOCKER.md#4-数据在哪里怎样备份)。网页默认仅绑定本机，当前部署用于本机或可信私网，未提供公网多人登录鉴权。

首次使用可上传项目 `example` 中的一张图片，选择纯 YOLO，确认任务能完成、原图与报告能打开；配置 API 后，再尝试带视觉复检的检查。安装成功、容器健康与真实模型/API 推理是分别验证的步骤。

## 4. 日常启动、停止与查看状态

继续在 Windows 的代码解压目录运行。若首次使用了自定义 `-Distro` 或 `-InstallDir`，以后每次也要带上相同参数。端口和项目名首次生成后以 Ubuntu 中保存的 `.env.docker` 为准，重复 setup 不覆盖它们或数据库密码。

```powershell
# 启动现有镜像，不重新下载模型或构建
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_setup.ps1 -Action start

# 查看本项目的容器状态
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_setup.ps1 -Action status

# 停止本项目，保留容器、卷、模型、历史检查和设置
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_setup.ps1 -Action stop
```

启动时会创建一个隐藏的 WSL 保活进程，重复启动不会创建多个有效保活循环；停止本项目时它会自行退出。电脑重启后需要再次运行 `start`，本入口不注册 Windows 开机任务。

## 5. 更新版本

先让正在执行的检查结束，按 [Docker 手册](DOCKER.md#4-数据在哪里怎样备份)备份当前部署。使用同一 Windows 账号下载最新 `dev/agent-v1` ZIP 并解压到**新的 Windows 文件夹**，保留旧文件夹作为源码回退参考。

1. 从旧文件夹执行 `-Action stop`。
2. 从新文件夹执行 `-Action setup`，使用原来的 `-Distro`、`-InstallDir`。
3. 安装器发现源码变化后，只更新自己管理的代码文件，再重新构建；`.env.docker`、模型、业务数据卷和网页设置保留。若项目仍在运行，会先拒绝覆盖并提示停止。
4. 确认启动完成、旧会话及媒体可以打开。之后从新文件夹使用 `start/stop/status`。

重复运行相同版本的 `setup` 可以续装失败步骤。它会比较当前源码文件，明确输出源码未变或已更新，不会用旧源码冒充新版本。安装器不负责把任意手工安装目录迁移为托管目录；已有 `sewer-agent-standby` 等部署应继续遵循原来的更新流程，先做好完整备份。

## 6. 常见问题

| 情况 | 处理 |
|---|---|
| Ubuntu 默认用户是 root | 先完成 Ubuntu 首次用户创建，或按发行版说明设置正常默认用户；安装器不创建或猜测你的密码 |
| 提示没有 `/dev/dxg` 或 NVIDIA GPU | 更新 Windows NVIDIA 驱动和 WSL，再检查 `wsl -d Ubuntu-24.04 -- /usr/lib/wsl/lib/nvidia-smi -L` |
| 已安装 Docker Desktop | 此路线使用 Ubuntu 内的 Docker Engine；不要让 Desktop 的 WSL 集成接管同一个发行版。选择另一个 Ubuntu 发行版，或按现有 Desktop 路线手动部署 |
| 检测到其他容器正在运行 | 安装器不会为了添加 NVIDIA runtime 重启它们的 Docker。先自行安排停止其他容器，再继续，或使用独立发行版 |
| 出现 Docker/模型下载网络错误 | 保留当前目录和数据，再次 setup；不要删除数据库卷来重试。错误输出不要包含 API Key |
| 端口被占用 | 新安装使用 `-Port` 选择空闲端口；已有部署在停止后编辑 Ubuntu 项目的 `.env.docker` 中 `SEWER_HTTP_PORT`，然后 start |
| 想看容器日志 | 进入 `wsl -d Ubuntu-24.04`，`cd ~/sewer-agent`，执行 `sudo docker compose --env-file .env.docker logs --tail 100 api cpu-worker gpu-worker` |

WSL 使用 Windows NVIDIA 驱动，**不要在 Ubuntu 安装 Linux 显卡驱动或 `cuda-drivers`**。本项目的 CUDA/PyTorch 用户态库已经在镜像中。[NVIDIA WSL 说明](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)

脚本依据 [Docker Ubuntu 官方安装流程](https://docs.docker.com/engine/install/ubuntu/) 和 [NVIDIA Container Toolkit 官方流程](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)编写。已有 Docker 配置会先探测，发现其他发行版包或正在使用的冲突环境时停止并给出处理提示。

本入口提供语法、参数、重复安装和数据保留的离线测试；已有机器上的 Docker 运行证据见项目部署记录。未在一台全新 Windows 上完整重放 WSL 安装、系统重启和首次 Ubuntu 用户创建，因此这些系统安装阶段仍需在目标新机按提示验证。
