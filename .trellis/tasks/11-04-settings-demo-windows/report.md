# 设置、演示示例与 Windows 部署交付记录

日期：2026-11-04。代码基线：`df94421f`，发布分支：`dev/agent-v1`。

## 已实现

- 设置 Drawer 沿用蓝白工作台样式，保留已挂载会话、草稿和附件。可配置 LLM/VLM 的地址、模型、密钥，明确选择共享配置，读取模型目录测试连接，并设置请求超时、输出上限、思考模式及浏览器内的新图片默认模式。
- 凭据只保存在服务端共享 `settings` 卷；读取接口不回显 Key。跨进程锁、原子写入和修订检查保护并发保存；首次任务接纳即持久化修订 0。全部云活动与对话派生任务读取固定版本，历史无版本任务继续遵循原环境契约。新增卷已纳入备份恢复文档。
- Windows 提供 `setup/start/stop/status` PowerShell 入口及 `WINDOWS.md`。从源码 ZIP 安装 WSL/Ubuntu、Docker Engine/Compose、NVIDIA Container Toolkit；提示系统重启和首次用户创建，再次执行可续装。源码、配置和数据边界独立，所有安装阶段 Docker 调用固定本机 socket。
- 机制图替换使用 2026-11-02 的真实 PL 检查：任务 `2ff87e71-4546-46db-83a1-8037e7121974`，YOLO26s 分数 0.709、IoU 0.626，VLM 接受后保留原框。原始图、GT、检测、复检、融合和渲染证据见 `docs/experiment-cases/accepted-crack/`。与 606 图 YOLO26m 统计明确分开，统计指标和原历史证据保留。

## 验证

- 前端全量：205 passed；最后标签/存储容错调整后，5 项设置专项再次通过，TypeScript 与生产构建通过。
- 后端非 integration 全量：1338 passed、2 skipped。先在既有运行镜像验证，再在此次完整构建的新镜像上断网复核，同样通过。
- 新增设置及实际 activity 配置固定测试共 35 项。独立审查发现的修订 0 未落盘问题已经复现、修复并复核；首次接纳/保存竞争和文件丢失拒绝回退也有覆盖。
- Windows/WSL 安装器：24 项源码保留、路径、重复执行和 shell 模拟测试通过；Windows PowerShell 5.1 离线语法、参数、初装及失败分支通过；Bash 语法与 Python lint 通过。
- Edge 实际浏览器：1440px、390px；设置测试使用明确的假接口完成保存/共享/测试连接与草稿/焦点保留，未发送真实推理。最终静态包 `index-DgNfEB6G.js`。字段名称、滚动、选中态和媒体预览已核对。
- 完整 Dockerfile 构建通过。新镜像只读/断网隔离测试通过真实设置 API 的读写、凭据跨实例读取及权限验证；一次独立 GPU YOLO 推理识别出 PL，不写业务记录、不调用云模型。
- 网站 Astro 全量构建 2363 页通过；首例在桌面/手机中显示正确，两个保留框坐标完全一致。网站提交准备于隔离副本，提交 `e82963cc`。

## 本机更新

工作台仍为 `http://127.0.0.1:18082`，Compose 项目仍为 `sewer-agent-standby`。更新前在一致停写点保存三个数据库、媒体、配置及源码：

`/home/lnsdf/sewer-agent-backups/settings-20261104-203433`

旧镜像保留为 `sewer-agent:before-settings-20261104`。新镜像为 `sewer-agent:settings-20261104`，已作为原 `local` 标签部署；API、CPU/GPU worker、PostgreSQL、Temporal 均健康。新增设置目录对应用用户可写，目录权限为 0700。

更新前后 8 个会话、25 项任务（15 待审核、10 已完成）逐字段相等；24 个原始输入元数据与 HTTP 字节范围样本相等。实际设置 GET 成功识别原环境中的两个角色配置，密钥未改动；真实主页已加载最新前端包。

部署后的 Edge 真实验收不再 mock 设置接口：1440px/390px 的 GET 均为 200，密钥字段为空，关闭设置保留草稿和焦点。案例 17 的图片可解码及打开原图；案例 19 的视频在两个视口均实际播放并能打开原片。无 JavaScript/HTTP 错误，无业务写入，历史记录再次核对相等。

本机详细日志和截图保存在工作区 `var/standby/evidence/settings-windows-20261104/`（不提交含业务记录的本机证据）。旧根工作副本及其已有未提交修改未纳入发布。

## 边界与待确认

- 未在全新 Windows 机器上完整重放 WSL 安装、重启和首次 Ubuntu 用户创建，教程明确此边界。
- 没有运行付费云推理；连接测试只查询模型目录，不证明模型视觉能力。
- 个人网站仓库 `huicod/huicod.github.io` 默认分支发布被自动审批拒绝，理由为缺少此次明确网站发布授权。已向用户询问，等待答复；线上展示页面在授权前不变。项目代码与案例证据已推送 `dev/agent-v1`。
- 不将本地验证等同于 GitHub Actions 成功；本次未确认远端 Docker smoke 完成。

## 2026-11-05：公开仓库与真实页面密钥轮换验收

用户明确确认将 `huicod/sewer-agent` 改为 Public，同时要求确保可通过设置页面部署新 API Key。已使用 GitHub 官方 API 完成可见性修改，GitHub 连接器及不带认证的请求均确认 `visibility=public`、`private=false`。默认分支仍为 `main`，当前可部署版本仍在 `dev/agent-v1`。

公开前已检查四个远端分支及 prototype 标签的可达 Git 内容：87 个提交、1963 个 blob。规则扫描未发现明确的真实令牌、私钥或被跟踪数据库备份，环境文件只有示例模板；不将这一检查扩大为对 Actions 日志、Issues 附件或任意格式秘密的保证。执行前再次确认远端提交与已检查版本一致。

README、DOCKER、WINDOWS 已去除需要私有仓库登录的旧说明，并补充网页更换密钥、共享配置、空值保留、保存后新请求生效及持久化备份步骤。文档发布为 `08310939`，三个原始 Markdown 文件匿名读取均返回 HTTP 200。

设置验收使用正式镜像 `sewer-agent:settings-20261104` 中的前端、生产 `settings_router` 与生产文件存储，在独立 loopback 端口和独立设置卷进行。仅空会话与本地模型服务为 fixture，未 mock 设置 GET/PUT/test：

- 从真实页面保存明确假 Key A，得到修订 1；刷新后两角色已配置，密码字段为空。
- 用生产 `run_config` / `turn_config` 固定旧任务修订，再在页面共用配置中保存 Key B，得到修订 2。
- 服务端发出的真实 HTTP 模型目录请求依次使用 A、B；容器重启后再次使用 B。六次请求均为本地 `GET /provider/models`，无云推理调用。
- 新进程的 Settings、新 run/turn 均读取修订 2 / B；旧 run/turn 继续读取修订 1 / A。设置文件权限为 0600。
- 响应和浏览器存储不包含测试密钥；跨源写入和缺少写请求标记均返回 403。
- 每阶段均只读比较实际 18082 工作台的设置、8 会话、任务及消息快照，前后保持相等。

详细证据保存在 `var/standby/evidence/public-settings-20261105/`。本轮不需要修改运行时代码或重新部署日常工作台。原个人网站示例发布仍是独立的待授权事项，本次仓库公开授权未用于推送该网站默认分支。

## 2026-11-05：按截图移除两图对比模块

用户将网站展示要求明确调整为删除“两张测试图里的变化”整个模块。最终仅修改 `src/components/sewer-docs/Results.astro`：删除两组对比图、说明、专属数据与样式，并将后文依赖案例的一段话改成独立的分类/定位说明。其余正文恢复并保持当前线上版本，所有实验表格与原始证据不变。此前 PL 替换稿不再是待发布版本。

网站原工作区与隔离副本文件一致，待发布提交为 `e670d3965f3cf5321b406ceb4142e78641e5352e`，父提交 `06af1dcb`。完整 Astro 构建成功，共 2363 页。独立 Edge 验收覆盖 1440px 与 390px：5 张表格逐单元格相同，模块外 232 个数字按序保留，无悬空案例引用、脚本错误或全页横向溢出；衔接间距为 24px。证据保存在 `var/standby/evidence/remove-test-cases-20261105/`。

尝试发布该精确删除版本时，自动审批在命令执行前再次拒绝网站 `main` 推送，理由为“用户明确要求删除页面模块，但未明确授权此次默认分支发布”。未改用其他途径发布，线上仍未变化；已准备好具体版本，等待用户明确授权推送网站主分支并部署。

### 发布完成

用户随后明确授权同步到个人网站最新分支并核对线上更新。2026-11-05 已通过 jj 将 `e670d3965f3cf5321b406ceb4142e78641e5352e` 正常向前推送至 `huicod/huicod.github.io` 的 `main`。GitHub Pages 运行 `41940167792` 的 build/deploy 均成功：<https://github.com/huicod/huicod.github.io/actions/runs/41940167792>。

正式页面 <https://huicod.github.io/projects/sewer-agent/results/> 返回 HTTP 200，目标模块已消失。独立 Edge 直接访问真实 HTTPS 页面（无请求替换），1440px/390px 均通过：5 张表格逐单元格、232 个数字和其余标题与本地验收版一致；段落衔接为 24px，无全页横向溢出、JS/console/HTTP 或请求失败。主 Agent 另行核对正式 HTML 与手机截图。线上证据在 `var/standby/evidence/remove-test-cases-20261105/live/`，部署记录为同级 `deployment-run.json`。此前待发布事项已关闭，设置、部署与网站展示任务完成。
