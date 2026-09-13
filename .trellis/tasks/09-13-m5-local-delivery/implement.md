# T16 Implementation Plan

> 使用Superpowers subagent-driven-development/TDD，Trellis拥有唯一任务账本，jj原工作区；遵循项目覆盖规则，不另建worktree/重复审查账本。

Goal: 同机应用运行与重启交付。Architecture:脚本控制已有三个进程；API可选托管dist。Stack:现有Python标准库/FastAPI/Temporal/Node。Spec:design.md。
Global constraints:127.0.0.1:8000；无新增依赖、无付费API/新模型推理、无.env覆盖/业务删除、GPU1并发、停止不强杀/不终止workflow。

| 任务接口 | 提供/使用 | 自检 |
|---|---|---|
| 管理脚本 → worker | --ready-file JSON pid/kind；启动/停止信号 | 参数与主agent实现一致 |
| 管理脚本 → API | SEWER_WEB_DIST；8000 health及静态网页 | 仅API环境设置，不改变默认工厂 |
| 主agent → 验收 | 脚本build/start/status/stop/restart | 先查当前任务，禁止意外消费付费队列 |

- [x] 管理agent先写实际子进程身份/锁/生命周期失败用例，再实现scripts/app.py。最终控制脚本12项及Ruff通过；子agent无真实服务操作。拒绝假PID/重复启动/占用端口，失败不误清理既有进程；不造通用服务框架。
- [x] 主agent先写web_dist挂载回归（GET /返回固定构建HTML、静态JS200、/health仍JSON、默认无网页、路径不越界），再实现Settings/API；先写worker失败/退出marker测试再实现信号桥，保留注册测试。相关14项通过，Ruff通过。
- [x] 主agent组装后完整pytest1048通过/137跳过、Ruff、前端84及build通过；实际启停/重启与Chromium通过，JSON和截图在2026-09-13-delivery-final。会话持久化、同源资源、组件ready及停止后无运行组件均验证。
- [x] 一次独立审查两项Important修复并通过；运行手册/阶段报告、总体计划/Session30及jj urlozrsx保存。T15未验能力不标完成。

单位测试例如：独立睡眠子进程填入错误command时stop必须抛安全错误且child.poll()仍None；正确pid/启动时刻stop应使child退出。启动失败后created进程退出但existing进程仍存活。
静态验收示例：`assert (await client.get('/')).text == '<h1>fixture</h1>'`；worker在run抛错时ready_file不存在，正常SIGTERM后shutdown被执行且marker删除。

实测前置事实：原工作区clean，基线相关10项通过；PG/Temporal已运行。public sewer_tasks=0，Temporal Running=0，8000可绑定。
已显式执行既有init_storage.py补齐缺失业务表（成功），未执行runtime init、改.env或调用模型/API。
