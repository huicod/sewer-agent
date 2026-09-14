# 前端实现报告

## 结果

- `VideoTimeline` 在展开后用默认折叠的 `选择已存证据帧（N）` 区域容纳已持久化帧，避免长片数百帧挤占事件时间线；展开后通过明确按钮把精确的 `frame_index`、`timestamp_seconds` 和 FRAME `AssetRef` 交给 `App`。
- 对话提交保持既有附件/普通任务二选一规则；选帧是窄例外，提交绑定父 VIDEO `task_id` 与所选 FRAME `input_asset`，不会上传或自动执行工具。
- 选中区显示帧号、时间、图片链接和显式操作提示；切换会话、任务、附件或取消选帧会清理选择及待重试身份。
- 正在提交对话或已有 queued/running 对话时，所有帧选择按钮保持原 accessible name 但被禁用；选中区的“取消选帧”也同步禁用，不能在 inflight 期间清空 pending/retry 身份。
- 网络失败重试保留相同 `request_id`、父任务和 FRAME 引用，测试逐字比较两次 POST body；离开选帧后则用独立 frame identity 阻止旧 FRAME 被普通任务、无任务或切回父视频的新请求复用。
- 只有非空且完全相同的真实 `File` 才能复用已上传资产；既有同 File/同文本模糊失败重试仍保持相同请求且只上传一次。
- FRAME child 即使没有 findings 也展示冻结来源、父报告链接和单帧边界，继续使用现有增强/几何面板，并明确禁止触发 `/video` 详情请求。
- `TaskRecord.frame_source` 保持旧响应缺字段及 `null` 兼容；非空时严格要求父任务、精确字段、严格 `AssetRef`、非负整数帧号与有限非负时间。

## TDD 证据

RED：新增 `frontend/src/video-frame-tools.test.tsx` 后先运行聚焦测试，12 项中 11 项按预期失败、1 项旧字段兼容基线通过。失败覆盖缺少 validator、选择入口、FRAME 提交/重试、清理和 FRAME child 展示。

GREEN：实现后聚焦测试 12/12 通过。全量测试首次发现新增帧选择器的 `listitem` 语义干扰既有事件列表顺序断言；将选择器改为非事件列表结构后，相关测试 23/23 通过，最终全量 106/106 通过。

长片收口 RED：先补默认折叠、活跃对话禁用和提交中禁用断言，聚焦测试 14 项中 5 项按预期失败；最小实现后聚焦测试 14/14、最终全量 108/108 通过。

集中 review 修复 RED：新增参数化回归覆盖失败 FRAME 请求后改选普通图片、不关联、离开后切回原视频，并补 inflight 取消按钮断言；17 项中 4 项按预期失败。修复后相关 `video-frame-tools` 与既有附件重试测试 23/23 通过，最终全量 111/111 通过。

## 验证命令

- `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend test -- src/video-frame-tools.test.tsx`：17/17 通过。
- `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend test -- src/video-frame-tools.test.tsx src/conversation.test.tsx`：23/23 通过。
- `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend test -- src/review-and-video.test.tsx src/video-frame-tools.test.tsx`：23/23 通过。
- `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm --prefix frontend test`：13 个文件、111/111 通过。
- 在 `frontend` 目录运行 `PATH=/root/autodl-tmp/server/.tools/node/bin:$PATH npm exec -- tsc --noEmit`：通过。
- `git diff --check -- frontend/src`：通过。

未运行生产 build、e2e、服务重载或 VCS 写操作。

## 修改文件

- `frontend/src/App.tsx`
- `frontend/src/VideoTimeline.tsx`
- `frontend/src/types.ts`
- `frontend/src/validators.ts`
- `frontend/src/video-frame-tools.test.tsx`
- `.trellis/tasks/09-14-m4-video-frame-tools/frontend-report.md`
