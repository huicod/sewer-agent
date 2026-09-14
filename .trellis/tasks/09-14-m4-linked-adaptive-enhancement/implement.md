# 实施范围

主Agent负责计划、前端提示、集成验证、一次审查、文档/jj。实现Agent只负责以下独立后端任务，无共享文件写入；先RED后最小GREEN。

## Task 1：后端自动关联闭环

允许文件：`agent/conversation.py`、`workflow/start.py`、`workflow/supplement.py`、`workflow/enhancement_selection.py`、必要时`workflow/supplement_activities.py`。测试：现有`test_adaptive_enhancement.py`、`test_adaptive_enhancement_workflow.py`、`test_supplement_workflow.py`及必要的新`test_linked_adaptive_enhancement.py`；如发现其它必要文件，先向主Agent说明。

1. RED：当前自动关联拒绝；替换为当前轮许可进入auto，而不是悄悄选一个显式模型。参数化验证缺输入、互斥和旧显式路径。
2. Starter接收auto flag，与显式/geometry互斥；现有request优先返回冻结child；无existing才构建auto候选pins，调用原原子supplement准入，不改schema或一次额度。
3. Workflow仅auto pin新增select，可选执行增强；skip不进入verify_image。选择失败正常传给finish。冻结原run不覆盖，selected运行参数只派生。
4. Selector缺child verify不崩溃；baseline取冻结source。SupplementActivities对于auto skipped/error保留父baseline，错误partial，不能把子复检没运行当新VLM判断。先检查现有finish是否已覆盖，最小增加必要分支。
5. GREEN：实际命令顺序、selected/skipped/error、cache/ack-loss、父快照不变和0 detector/原VLM。运行相关CPU/Ruff，简短保存backend-report.md。

## Task 2：主Agent验证与交接

前端说明补齐“关联原图也可本轮许可按需增强”，不另造页面。复用既有PG/Temporal增强集成夹具新增或参数化linked auto变体，模型替身、无付费/GPU；原真实模型/API已在T12-C验收不重复。后端完整回归/前端相关测试与build；一次集中spec/quality review，仅修阻塞项。当前长片实际结果可随后补记，不宣称全部首版交付。
