# T10 条件式开放候选交付

日期：2026-09-11；jj change：`prnozqso`；基线：`ba3da925`。

## 工程结果

已完成 T10，不训练模型、不追加 VLM 付费实验、不改用户接受的实验结果，也未将实验融合切换为 worker 默认。

- 新增可替换 `OpenDetectorAdapter`，首个实现为本地 YOLOE-26s text-prompt；受控英文缺陷词表映射五类与具名 OTHER，输出原图坐标，不传播模型内部对象。
- 图片/视频顺序为 detect → verify → 条件选择 → open_scan → 融合 → 原有人工审核。显式文本要求、VLM 疑似漏报和画质足够的负结果触发；视频复用保存的证据帧，不逐帧调用 YOLOE。
- 默认最多 8 帧、每事件一次、固定总预算与传输重试。视频默认 32 次模型调用中预留主扫描 1、开放扫描最多 8，VLM 上限 23；未覆盖、失败不能伪装完整负审计。
- 保存 open_plan、跳过理由、ToolCall 和原始输出；已成功检查点复用，工具执行阶段显示 `tools / open_scan`。CPU/API 与单 GPU 队列沿用，不增加数据库表或调度平台。
- 开放候选只增不删，保留原 finding/class/score/确认状态；同源同帧同类 IoU≥0.5 去重，OTHER 还须同名。所有新候选待人工确认；新未知类别不会自动确认。
- 旧运行无 open_scan pin 时不新增 Activity；关联复检子任务仍是 VLM-only。模型、编码器和词表版本固定到新运行，请勿原位覆盖同名模型文件。

## 验证证据

| 验证 | 最终结果 |
|---|---|
| 默认后端全套 | 636 passed / 84 skipped（显式集成开关） |
| 新开放扫描 + 原图片/视频 + 人工等待真实 PG/Temporal | 12 passed |
| 前端 Vitest / TypeScript + Vite | 52 passed / build passed |
| Ruff backend + scripts / diff check | 通过 |
| 两张固定 sewer2403 原图真实 GPU | 两模型推理、重复推理及释放验证通过；无 API |

新集成用真实数据库、Temporal 和 FFmpeg，模型/VLM 使用确定性替身。已证明主检测无框时开放 PL 候选进入人工等待、人工确认后完成；成功落库但响应丢失时 Activity 重试，主检测/VLM/开放模型仍各一次。短视频仅补扫保存的周期审计帧；预算为零时输出待审核/不可判而不是正常。旧图片/视频和等待恢复回归保留。没有模拟“真实模型漏检被成功救回”或据此宣称精度提高。

独立集成审查发现 3 个 Important、无 Critical，均已修复并回归：不同名称 OTHER 被错误合并；画质分数拦截 VLM 明确疑点；视频未遵守固定传输重试次数。四个回归用例先失败，修复后通过。另由真实 PG 验证修正跳过记录的 step_id 与检查点一致性。保留原审查报告，不将其初始结论改写为已通过。

## 真实模型与图片

模型保存在 `var/models/yoloe/yoloe-26s-seg.pt`（约 30 MiB）与 `mobileclip2_b.ts`（约 242 MiB）。来源及配置见 `backend/README.md`。已在 `.venv` 加装固定 revision 的 CLIP tokenizer、ftfy 6.3.1、regex 2026.9.3；原 torch / torchvision / Ultralytics 未升级。推理预检本地文件及依赖，不自动下载/安装，不改变进程工作目录。

输入为此前置信度实验保留的 `paired/gallery/sample-0007/original.jpg` 与 `sample-0018/original.jpg`；不解压新数据、不改原图/标签。结果目录：

`/root/autodl-tmp/server/var/open-scans/2026-09-11-t10-b8h4j412/`

其中 `results.json` 保存原始框、类别、分数、模型版本和耗时；四张 `sample-*-primary.png` / `sample-*-open.png` 分别展示主检测和开放候选。两图主检测框数分别 1、2；开放模型各 2 个 AJ 候选（分数约 0.422/0.301、0.528/0.279），它会把管道开口提出为 AJ，不能直接认定为正确缺陷。此结果仅证明接口运行，不能声称准确率提高，也不是新的配对评测。

GPU 实测开放模型首次调用约 1.01 / 0.58 秒，同实例重复约 0.028 / 0.023 秒；不是吞吐基准。卸载后分配量稳定为 32 MiB，与首次主模型卸载后的运行库基线一致；一次诊断清理 cuBLAS 工作区后为 0，确认不是开放模型残留。不在生产代码使用该内部清理接口。最初零字节基线断言过严的冒烟失败已记录，最终使用初始化后的基线与第二轮无增长检查。早期两次未完成目录为空，保留；有效证据以上述目录为准。

## 运行状态与下一步

YOLOE **仍默认关闭**，`.env` 未修改，产品 API/CPU/GPU worker 未启动，未推送。现有 loopback PG/Temporal 仍运行；隔离测试 schema 已由测试清理，不删除用户资产。T10 无新增表，但尚未升级过 T09 的产品库仍需原有 `init_storage.py` 步骤。启用方法、复验命令与依赖组见 `backend/README.md` / `.env.example`。

下一任务 T11：固定小型 sewer2403 图片与少量已标记视频片段，分别比较主检测、VLM 和开放候选的增量作用，人工判断标注遗漏与真误报，并报告精确率/召回率、审核量、覆盖与成本；不要用候选数量宣称效果。用户已有 API 配置可沿用，当前不需要新 Key。T12–T16 后置工具与完整交付尚未开始。
