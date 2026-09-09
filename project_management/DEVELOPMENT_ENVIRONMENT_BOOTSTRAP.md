# 开发前置环境与复刻手册

初始记录：2026-08-25；身份与执行边界更新：2026-09-09。

本文件保留环境安装历史，不是每次开发需要重跑的检查清单。当前硬件、依赖缺项和任务顺序以[总体开发计划](DEVELOPMENT_PLAN.md)第 3～4 节为准；日常不做全量文件/Skill 哈希校验。

本手册记录正式开发前已经完成的环境、治理、版本控制、插件和 Skill 准备工作。目标是在新服务器、容器或开发机上快速复刻同一套可审计工作方式，而不是只恢复若干二进制。

## 1. 当前完成状态

| 项目 | 固定版本或来源 | 当前状态 |
|---|---|---|
| 工作根目录 | `/root/autodl-tmp/server` | 已固定 |
| 项目治理 | `AGENTS.md` + P0 治理文档 | 已写入 |
| Git | `2.55.0` | 已安装 |
| jj | `0.44.0-af45d57` | 已安装 |
| 仓库模式 | colocated Git + jj | 已初始化，尚无正式基线 |
| Node.js | `20.18.2` | 已安装 |
| npm/npx | `10.8.2` | 已从同版本 Node 官方归档补齐 |
| Python | `3.12.3` | 已存在 |
| Codex CLI | `0.153.4` | 2026-09-09 本机检查版本 |
| `stop-that-shit` | `0.1.0`, commit `8a424ab5...` | 已安装并固定，Hook 尚待人工信任 |
| `grill-me` + `grilling` | Matt Pocock commit `697d4ce9...` | 已安装；一次一问；仅显式进入完整访谈 |
| Trellis | npm `@mindfoldhq/trellis@0.6.15` | CLI 已安装，huicod 已确认；初始化待计划批准 |
| Superpowers | `6.3.0`, obra commit `b36e0829...` | Codex 插件已安装并启用 |

当前操作系统基线是 Ubuntu 22.04.5 LTS x86_64。换环境时不要求逐字复制操作系统，但必须重新验证命令、包名和二进制架构。

## 2. 不可省略的项目级决策

1. 所有开发工作位于 `/root/autodl-tmp/server`。
2. 先读 `AGENTS.md` 和 `project_management/AGENT_PROJECT_DEVELOPMENT_GOVERNANCE.md`。
3. Git 用于生态和远端兼容；jj 是本地 change、workspace、历史和恢复的主要接口。
4. 数据压缩包、生成数据、模型权重和实验 `train.log` 不进入普通版本历史；被忽略不代表可以删除。
5. 主 Agent 负责编排、整合、测试和汇报；子 Agent 只接受原子、可验证、写入隔离的任务。
6. `grill-me` 既用于风险歧义，也用于对齐项目术语、数据、指标、失败代价和交付知识。
7. Trellis、Superpowers 和项目治理不能同时争夺流程控制权；每项任务必须声明一个主工作流。
8. 插件、Skill 和工具不得扩大用户授权，也不得替代测试、jj 锚点与人类决策门。

## 3. 基础工具

### 3.1 Git

jj 0.44.0 要求较新的 Git。Ubuntu 22.04 自带版本过旧，本机使用 Git 官方推荐的 `git-core/ppa`：

```bash
apt-get update
apt-get install -y software-properties-common
add-apt-repository -y ppa:git-core/ppa
apt-get update
apt-get install -y git
git --version
```

本次 PPA 签名密钥指纹：

```text
F911AB184317630C59970973E363C90F8F1B6217
```

期望结果：

```text
git version 2.55.0
```

其他发行版应遵循 [Git 官方 Linux 安装说明](https://git-scm.com/install/linux)，不要照搬 Ubuntu PPA。

### 3.2 jj

来源：[jj v0.44.0 release](https://github.com/jj-vcs/jj/releases/tag/v0.44.0)

```text
asset: jj-v0.44.0-x86_64-unknown-linux-musl.tar.gz
sha256: 0a07bab4641a55fd2bc2fd1563ba3a3f9a577584086ad74086a1c5b69b3ffce9
```

最小安装步骤：

```bash
curl -fL -o /tmp/jj-v0.44.0-x86_64-unknown-linux-musl.tar.gz \
  https://github.com/jj-vcs/jj/releases/download/v0.44.0/jj-v0.44.0-x86_64-unknown-linux-musl.tar.gz
sha256sum /tmp/jj-v0.44.0-x86_64-unknown-linux-musl.tar.gz
tar -xzf /tmp/jj-v0.44.0-x86_64-unknown-linux-musl.tar.gz -C /tmp
install -m 0755 /tmp/jj /usr/local/bin/jj
jj --version
```

必须人工比对 SHA-256 后才能执行 `install`。具体兼容要求参考 [jj 安装文档](https://docs.jj-vcs.dev/latest/install-and-setup/)。

### 3.3 Node、npm 与 Python

Trellis 要求 Node >= 18.17 和 Python >= 3.9。本机结果：

```text
node v20.18.2
npm 10.8.2
python 3.12.3
```

优先使用 Node.js 官方完整发行版或受支持的版本管理器。当前服务器最初只有 Node 二进制，因此从同版本官方归档中只补齐 `npm`、`npx` 和 `lib/node_modules/npm`：

```text
archive: node-v20.18.2-linux-x64.tar.xz
sha256: 4e50f727ae09bdafecf2322c72faf7cd82bf3b8851a16b8bb63974e0d8d6eceb
npm: 10.8.2
```

不要在这台 Ubuntu 22.04 上安装仓库候选 `nodejs 12.22.9` 来满足 Trellis；它低于 Trellis 的最低要求。

## 4. 建立共置 Git + jj 仓库

先写入安全忽略规则，再初始化。当前规则位于根 `.gitignore`，至少包含：

```gitignore
/yolov26/data_archives/
/yolov26/data_raw/
/yolov26/runs_data/
/yolov26/runs_detect/
/yolov26/runs_eval/
/yolov26/experiments/**/train.log
*.pt
```

初始化命令：

```bash
cd /root/autodl-tmp/server
git init -b main
git add --dry-run .
jj git init --colocate .
jj status
git status --short --branch --ignored
```

在本机，约 2.23 GiB 数据压缩包和所有模型权重均被排除；已进入 Git 对象库的内容约 6.8 MiB。两份训练日志保留在本地，但暂不版本化。

2026-09-09 已按用户授权配置仓库级 Git/jj 作者身份；没有配置远端或执行推送。配置如下（无需重复执行）：

```bash
git config --local user.name "huicod"
git config --local user.email "109233438+huicod@users.noreply.github.com"
jj config set --repo user.name "huicod"
jj config set --repo user.email "109233438+huicod@users.noreply.github.com"
```

## 5. Codex Guardrail：stop-that-shit

官方来源：[lennney/stop-that-shit](https://github.com/lennney/stop-that-shit)

```text
plugin version: 0.1.0
audited commit: 8a424ab587f20d3d2ea6787f233740c833ba8476
license: MIT
```

安装：

```bash
codex plugin marketplace add lennney/stop-that-shit \
  --ref 8a424ab587f20d3d2ea6787f233740c833ba8476
codex plugin add stop-that-shit@stop-that-shit
codex plugin list
```

安装或升级后启动全新的 Codex CLI TUI，运行：

```text
/hooks
```

由人类逐条审阅并信任 `UserPromptSubmit` 与 `PreToolUse`。未完成这个步骤时只能说“已安装”，不能说“Guard 已强制生效”。

常用模式：

```text
$stop-that-shit answer
$stop-that-shit review
$stop-that-shit change
$stop-that-shit lock change files=path/a|path/b
$stop-that-shit status
```

本次审计运行了插件完整测试：182 项中 181 通过，1 项为预期的 OpenCode 跳过，0 失败；安装后 smoke test 为 21/21。

## 6. grill-me

官方来源：[mattpocock/skills](https://github.com/mattpocock/skills)，MIT。

当前 `main` 已改成“一轮询问多个 frontier 问题”，不符合本项目“一次一问”。因此固定到最后一个保留一次一问且含 Codex 元数据的官方提交：

```text
commit: 697d4ce9742da558fd1ba6697c8e9775e2e302dd
grill-me SKILL.md sha256:
6189dfceb7304a6e5558f75d87e68fa3bc7fcf7ba120e44f21f8a61fe01eba54
grilling SKILL.md sha256:
44331dda57f461db4fec3f2efb6ddabe7aaaa0a57ae0f88a883bc61aed8a0587
```

`grill-me` 是手动入口，实际访谈逻辑位于必要依赖 `grilling`，两者必须一起安装：

```bash
python3 /root/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo mattpocock/skills \
  --ref 697d4ce9742da558fd1ba6697c8e9775e2e302dd \
  --path skills/productivity/grill-me skills/productivity/grilling
```

为了满足“完整 grill 仅人工触发”，本机在 `/root/.codex/skills/grilling/agents/openai.yaml` 增加：

```yaml
policy:
  allow_implicit_invocation: false
```

使用方式：

```text
/grill-me
```

也可以在调用时带上主题，例如“请 grill 我们的五类缺陷业务定义”。本项目覆盖规则是：

1. 先查仓库和权威资料，不向人询问可检索事实。
2. 一次只问一个信息增益最高的问题。
3. 优先对齐目标用户、领域术语、数据、类别、标注、切分、指标、失败处理、部署和验收。
4. 访谈本身不写文件、不开始实现。
5. 人类确认共同理解后，主 Agent 再把答案写入项目文档、ADR、数据说明或测试。

安装后的 Skill 会在下一次 Codex 会话中可用。

## 7. Trellis

官方来源：[mindfold-ai/Trellis](https://github.com/mindfold-ai/Trellis)，AGPL-3.0-only。

```text
npm package: @mindfoldhq/trellis@0.6.15
tag commit: d8fff53ce4964ed1a3e52fea6b418b27eba093e4
SHA-1: 2119748cccde49006e9897fc7a3481febdf98076
SRI: sha512-grbF8PToesHojsaWkoG4+Aupih7eZHkXH5y33uzPrWQXwIRewwlM1AoeJEttcXAia9nLZzF/ezuR338PWCKv+A==
```

Trellis 不是一个可独立复制的 Skill，而是 npm CLI + 项目级 harness。只安装 `.agents/skills/trellis-*` 会缺少 `.trellis/scripts`、task、spec 和 workspace 状态。

全局安装：

```bash
npm install -g @mindfoldhq/trellis@0.6.15 \
  --ignore-scripts --no-audit --no-fund
trellis --version
```

本机 CLI 已安装；开发者名称已由用户确认为 `huicod`。项目尚未初始化，以下操作在总体计划批准后执行，并先核对本机 CLI 选项：

```bash
cd /root/autodl-tmp/server
trellis init --cursor --codex --skip-existing --yes --no-monorepo \
  -u "huicod"
```

官方没有 dry-run。该命令可能写入：

- `.trellis/` 的 workflow、spec、task、workspace、scripts 和版本信息；
- `.agents/skills/trellis-*`；
- `.codex/` 的 agents、skills、hooks 和配置；
- `.cursor/` 的 commands、skills、agents 和 hooks；
- 根 `AGENTS.md` 与 `.gitattributes`。

`--skip-existing` 会保护内容不同的现有文件，但 `.gitattributes` 仍可能增量加入 journal 的 `merge=union` 规则。初始化后必须：

1. 运行 `jj status`、`jj diff` 和 `git status --short`。
2. 人工合并被跳过的 `AGENTS.md` 入口，不允许覆盖本项目 P0。
3. 把 `.trellis/workflow.md` 中硬编码的 `git add/commit` 阶段改为 jj change/workspace 流程。
4. 审阅 Trellis 的 `UserPromptSubmit` 与 `SubagentStart` Hook，并通过 `/hooks` 单独信任。
5. 在完成上述检查前，不运行 Trellis 自动实现或自动收尾。

日常使用是在自然语言任务基础上，由 Trellis 管理 `.trellis/tasks/`、`.trellis/spec/` 和 `.trellis/workspace/`。显式收尾入口通常是：

```text
/trellis:finish-work
```

## 8. Superpowers

官方来源：[obra/superpowers](https://github.com/obra/superpowers)，MIT。

```text
version: 6.3.0
tag/source commit: b36e0829c6d0140e93cfef2ca599b1b07d4a7797
marketplace name: superpowers-dev
Codex manifest hooks: {}
```

应安装完整插件，不要逐个复制 Skill；其中 14 个 Skill 存在跨 Skill 工作流引用：

```bash
codex plugin marketplace add obra/superpowers \
  --ref b36e0829c6d0140e93cfef2ca599b1b07d4a7797
codex plugin add superpowers@superpowers-dev
codex plugin list
```

常见用法：

- 新功能：`brainstorming -> writing-plans -> subagent-driven-development -> test-driven-development -> review -> verification`。
- Bug：`systematic-debugging -> TDD 回归复现 -> verification-before-completion`。
- 两个以上真正独立且写入隔离的问题：`dispatching-parallel-agents`。
- 可以显式请求 `$superpowers:brainstorming`、`$superpowers:systematic-debugging` 或 `$superpowers:test-driven-development`。

本项目对上游方法论做以下覆盖：

1. P0 和当前用户指令高于 Superpowers 的“只要可能适用就必须调用”；仍坚持 Skill 少而精。
2. Trellis 管持久任务状态；Superpowers 只提供实现、调试、TDD、复核等工程技巧。
3. 禁止执行上游 `git worktree`、`git add/commit/checkout/pull/merge` 工作流；改用 jj workspace/change，并受人类门禁。
4. “先写代码就删除”只允许作用于本 Agent、本任务、刚写且已有 jj 锚点的内容；不得删除用户或其他 Agent 资产。
5. 可视化 companion 只有在用户明确同意时启用；建议设置 `SUPERPOWERS_DISABLE_TELEMETRY=1`，且不得绑定 `0.0.0.0`。

Superpowers 的 Codex manifest 没有 Hook，因此不新增 Hook 信任步骤。插件和 Skill 会在下一次 Codex 会话中完整可用。

## 9. 三个工具的职责边界

```text
grill-me
  -> 无状态、只读地对齐项目知识和决策
  -> 人类确认后退出

Trellis
  -> 把确认后的任务、Spec、工作日志和团队规范持久化
  -> 负责跨会话项目流程

Superpowers
  -> 在单个已确认任务内提供设计、TDD、调试、子代理、审查和验证技巧
  -> 不另建与 Trellis 竞争的任务状态

jj
  -> 承担所有本地 change、workspace、审计与恢复
```

主 Agent 每个任务开始时应明确当前控制流程。不要同时启动 Trellis brainstorm 和 Superpowers brainstorming；先用 `grill-me` 对齐事实，再选择其中一个规划入口。

## 10. IDE 与 CLI 支持边界

| 入口 | stop-that-shit | Superpowers | Trellis 项目文件 |
|---|---|---|---|
| Codex CLI TUI | 支持，需信任 Hook | 支持 | 初始化后支持 |
| Codex App | 支持插件 | 支持插件 | 取决于打开的仓库 |
| Cursor/VS Code 的 Codex 侧栏 | 不支持 Plugins | 不支持 Plugins | 可读取仓库规则，但不等价于插件 |
| IDE 集成终端内运行 `codex` | 与 Codex CLI 相同 | 与 Codex CLI 相同 | 初始化后支持 |
| Cursor 自带 Agent | stop-that-shit 不适用 | 需另行 `/add-plugin superpowers` | Trellis 用 `--cursor` 初始化 |

依据：[Codex IDE](https://learn.chatgpt.com/docs/codex/ide)、[Codex Plugins](https://learn.chatgpt.com/docs/plugins)、[Codex Hooks](https://learn.chatgpt.com/docs/hooks)。

## 11. 验证清单

```bash
cd /root/autodl-tmp/server

git --version
jj --version
node --version
npm --version
python3 --version
trellis --version

codex plugin marketplace list
codex plugin list

jj status
jj diff
jj op log
git status --short --branch --ignored
```

还应确认：

- `jj status` 不再因训练日志产生大文件告警；
- `git check-ignore -v` 能命中数据包、权重和训练日志；
- `stop-that-shit` 与 Superpowers marketplace 都固定到完整 commit SHA；
- 当前任务需要的 Skill 可以读取并符合项目边界；不重复核对文件哈希；
- 新 Codex 会话可以发现新安装的 Skill；
- 任何完成声明都附带测试、未测试项、风险和回滚锚点。

## 12. 仍需人类完成的门禁

1. 批准总体开发计划后，以已确认的 `huicod` 初始化 Trellis，并做 jj 适配审查。
2. Git/jj 作者已配置；未来需要远端同步时，再确认仓库地址、认证方式与推送范围。
3. 在全新 Codex CLI 中运行 `/hooks`，审阅并信任 stop-that-shit；Trellis 初始化后再审阅其 Hook。
4. 通过持续的项目知识 grill 确认项目章程、目标用户、数据语义、成功指标、非目标和发布边界。

升级任一插件、Skill、Trellis、Git 或 jj 时，不得只修改版本号。必须重新核验来源、许可证、manifest、Hook、权限、依赖、测试和与本项目 P0 的冲突，并同步更新本手册。
