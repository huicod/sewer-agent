# 管网检测 Agent（sewer-agent）

初始资料交付：2026-08-25；应用运行入口更新：2026-09-14

本目录包含对话＋文件上传的管网检查原型，以及保留的训练材料和架构研究。
本机启动、SSH访问与停止/恢复见 **[运行手册](RUNNING.md)**。
项目介绍、分缺陷演示图片与视频、模型说明和工作台功能 见 [项目文档](https://huicod.github.io/projects/sewer-agent/)。
现有功能已收口并可人工试用，验收清单也在运行手册；规范检索待指定版本/原文，不宣称首版全项完成。

## 新机部署（从克隆到打开网页）

**准备主机和账号。** 完整项目需要 Linux x86-64 主机、NVIDIA GPU、支持 CUDA 12.8 的驱动、已配置的 NVIDIA Container Toolkit、Git、Python 3.10+，以及可用的 Docker Engine 和 Compose v2（建议 2.30+）。镜像构建建议预留至少 25 GiB，模型另需约 1.07 GiB；上传的图片和视频还会占用存储空间。主机需能联网下载镜像、依赖和模型，并访问配置的 API。安装步骤见 [Docker Engine](https://docs.docker.com/engine/install/) 和 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

1. **在新服务器克隆部署分支。** 使用有该私有 GitHub 仓库读取权限的账号。当前 Docker 部署代码在 `dev/agent-v1`，克隆时请指定分支。

   ```bash
   git clone --branch dev/agent-v1 --single-branch https://github.com/huicod/sewer-agent.git
   cd sewer-agent
   ```

2. **启动整个项目。** 在刚克隆的目录中运行：

   ```bash
   python3 scripts/docker_setup.py up
   ```

   首次启动会提示输入**阿里云百炼北京地域的 API Key**（输入时不回显）。默认模型为 `qwen3.8-max`，账号需已开通该模型；对话和图片复检共用这把 Key。脚本会自动从 [Hugging Face](https://huggingface.co/suwewa/sewer-agent-models) 匿名下载并校验 7 个模型，生成数据库密码，初始化 PostgreSQL 和 Temporal，再启动网页、API、CPU 和 GPU 工作进程。使用其他 API 服务时，先按[部署指南](https://github.com/huicod/sewer-agent/blob/dev/agent-v1/DOCKER.md)配置文字和视觉端点，再运行启动命令。

3. **打开网页并验证一次检测。** 等启动命令提示服务就绪后，可在服务器查看状态：

   ```bash
   docker compose --env-file .env.docker ps -a
   curl -fsS http://127.0.0.1:8000/health
   ```

   正常时健康接口返回 `"status":"alive"`，三个初始化任务显示 `Exited (0)`。如果浏览器就在部署主机上，打开 `http://127.0.0.1:8000`。如果部署在远程服务器，请在**自己电脑**另开终端，替换 SSH 用户名和主机名后运行并保持连接：

   ```bash
   ssh -N -L 18000:127.0.0.1:8000 your-user@your-server
   ```

   然后在自己电脑打开 `http://127.0.0.1:18000`。点击“新建检查”，从浏览器所在电脑选择一张管道图片（可下载[仓库示例图](https://github.com/huicod/sewer-agent/blob/dev/agent-v1/example/01_wall_trace/image.jpg)），填写检查指令并选择“纯 YOLO（不复检）”。点击“提交检查”，在“任务与证据”中点击“启动检查”并查看结果。要验证 API Key，可再发送一条对话或启用 VLM 复检。

**演示真实视频。** 在“新建检查 → 素材来源 → 项目示例”选择内置的现场短片，提交后启动任务。五段短片及其检查要求随 [示例目录](example/README.md) 提供，无需重新上传。

**停止与恢复。** 在项目目录执行 `docker compose --env-file .env.docker down` 可停止服务并保留数据卷；下次再运行 `python3 scripts/docker_setup.py up`。请保留 `.env.docker`，其中保存 API Key 和数据库密码。`down -v` 会删除数据库及媒体卷。端口调整、自备模型和备份恢复见[完整 Docker 部署指南](https://github.com/huicod/sewer-agent/blob/dev/agent-v1/DOCKER.md)。

## 初版原型快照：v0.1.0-prototype

2026-09-16 按用户要求暂停在线验收，保存当前代码供后续迭代；这不是已通过人工验收的正式生产版本。

- 已有对话/上传、YOLO＋VLM、可恢复工作流、人工审核、可选增强/几何、视频及报告。
- 已知待改进：前端尚未清晰并排呈现YOLO初检与VLM复检的独立结果；VLM新增候选仍按现有规则待人工确认。本次只记录，不修改策略。
- 规范检索等待规范编号、版本和原文；完整人工验收未完成。
- 仓库保留代码、测试、文档和无真实密钥的`.env.example`。实际`.env`、凭据、模型权重、上传媒体、数据库及`var/`运行/实验结果不随本次推送上传，仍保留在服务器。克隆仓库不能替代运行数据备份。
- 本轮本地回归：后端1127通过/159默认跳过，前端111通过；没有重跑付费模型实验。

```text
server/
├── backend/    # API、持久工作流、检测/复检与工具
├── frontend/   # 对话、上传、任务进度、证据和人工审核页面
├── scripts/    # 本机运行、存储及工具入口
├── yolov26/     # 较新的 Sewer2403 训练链路、数据压缩包、权重和实验记录
└── vlm_review/  # VLM 事件级复检、补召回、跟踪和报告调研
```

## 1. 本次 YOLO26 取舍

初版后的图片链路修正见[在线引导复核记录](.trellis/tasks/09-16-online-guided-review/report.md)：新图片任务默认复用已认可的离线引导复核（原图＋候选局部图、置信度分层融合），旧任务仍执行固定的旧版本。此修正不改变上述初版标签，也未恢复暂停的服务。

主版本采用 `Sewer2403 五类 YOLO26s + 960`，因为它是当前本地最新且产物完整的实验，包含 `best.pt`、`last.pt`、逐轮指标、曲线、混淆矩阵和验证集预测图。

同时保留 Sewer2403 17 类实验的训练配置与完整日志，用于读取逐类结果和后续复现。17 类的 `best.pt`、`results.csv` 和曲线当时没有同步回本机，因此没有伪装成完整 checkpoint 放入交付包。

旧的 SEDD 三类实验、RT-DETR 失败实验和早期 YOLO26 参数扫描没有复制进来。它们仍保留在原工作区，但不是这次服务器包的主线。

## 2. 上传方式

在 Windows PowerShell 中，从 `E:\cctv-lab` 执行：

```powershell
scp -P <端口> -r .\server root@<服务器IP>:/root/autodl-tmp/
```

完整包包含约 2.23 GB 的三卷 Sewer2403 压缩数据。若服务器已经有原始 `sewer2403/images` 和 `sewer2403/labels`，可以跳过 `server/yolov26/data_archives`，只上传其余目录。

`SHA256SUMS.txt` 仅保留原始交付记录，日常开发不执行全量哈希校验，也不随文档或代码变更重新生成它。

## 3. 使用入口

- 总体开发计划与阶段验收：[DEVELOPMENT_PLAN.md](project_management/DEVELOPMENT_PLAN.md)；本机使用见 [运行手册](RUNNING.md)，开发与实验命令见 [后端入口](backend/README.md)。T14-B等待规范文档，整体首版验收尚未完成；开放补检已停用。
- 当前视频运行与恢复证据：[T08 runtime report](.trellis/tasks/09-10-m2-video-workflow/runtime-report.md)；复检实测结果：[paired pilot](.trellis/tasks/09-10-m2-video-workflow/data-report.md)。工程闭环可用不代表准确率已提升。
- 当前图片策略：[置信度分层离线实测](.trellis/tasks/09-11-confidence-review/report.md)及[在线接入验证](.trellis/tasks/09-16-online-guided-review/report.md)。高置信度保留、低置信度明确纠错、可定位非重复缺陷补充；不确定/其他类别交人工。独立扫描保留为旧实验模式。
- 开发规则：[AGENTS.md](AGENTS.md)

- YOLO26 数据准备、训练和评估：`yolov26/README.md`
- VLM + YOLO 架构、方法、论文与实验路线总览：`vlm_review/VLM_YOLO_ARCHITECTURE_AND_RESEARCH_SUMMARY.md`
- VLM 复检方案与文档阅读顺序：`vlm_review/README.md`
- 文件清单和用途：`MANIFEST.md`

## 4. 边界说明

- `Sewer2403 五类`当前只有验证集结果，尚未完成独立测试集评估。
- 训练完成后的 Ultralytics checkpoint 已移除优化器状态，适合推理、评估和作为初始权重继续微调，不保证精确恢复原优化器进度。
- VLM 目录目前是架构与调研交付，不包含已经部署完成的 VLM 服务或模型权重。
- Ultralytics、VLM、YOLOE、跟踪器及论文代码的许可证需要在部署前分别复核。
