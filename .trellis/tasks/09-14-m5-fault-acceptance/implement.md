# T15-C Implementation Plan

> Superpowers subagent-driven-development/TDD；仓库覆盖：Trellis唯一账本，jj原工作区、分文件并行、一次全切片审查。

Goal: 有界、持久、不重复的失败处理。Architecture: 既有PG/Temporal/Playwright，外部边界注入。Stack:pytest/httpx/Temporal/React。Spec:design.md。
Global constraints:0真实API/GPU推理，独立schema/队列，不改.env/依赖/模型/策略，不停共享服务/用户任务，不推送。

| 接口/分工 | 自检 |
|---|---|
| 后端agent→主agent | 独占新文件，复用case；SEWER_RUN_IMAGE_WORKFLOW_TEST=1，产物目录由主agent指定 |
| 主agent浏览器→现有测试服务 | 只加测试/testMatch，真实202后abort；独立于后端schema/队列 |
| 各任务自身 | 断言真实snapshot/calls/checkpoints，不以替身调用数单独证明产品行为 |

- [x] 后端agent写七场景，7项20.05秒通过；主agent产物运行7项20.51秒通过。真实PG/Temporal验证上述次数/状态/检查点，无产品代码修改。
- [x] 主agent新增浏览器回执丢失：`await route.fetch()`检查202后`route.abort('failed')`；重发`expect(payloads[1]).toEqual(payloads[0])`，真实GET的tasks/turns各1且刷新待审。默认testMatch加faults.spec.ts；core+faults两项28.3秒通过，证据在var/evaluations/2026-09-14-fault-browser，截图已查看。前端84/build通过。
- [x] 主agent新矩阵7项、既有检查点恢复1项通过；完整pytest1048/144跳过、Ruff、前端84/build和E2E类型检查通过，JSON及截图已检查。
- [x] 一次独立审查通过，无剩余问题；report.md保存覆盖矩阵，总体计划、Session31和jj stvsvnru保存。

起点c0514263，工作区clean，相关单元基线28 passed。9月14日检查应用及PG/Temporal已停，现有runtime start恢复持久服务；恢复应用前检查历史任务。
