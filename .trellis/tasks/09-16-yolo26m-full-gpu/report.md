# 整卡训练配置交接

2026-09-16，用户撤销前端资源预留；仅调整配置与短测，正式训练未启动。

- 唯一参数/启动说明：`yolov26/docs/merged_five_yolo26m_5090.md`。
- YOLO26m/960/batch16/AMP，allocator30GiB、启动空闲门30.5GiB，workers8/主线程8；当前CPU配额仍25核，变动后启动前重查。
- `save=true/save_period=10`沿用框架：零基epoch0/10/20快照，last每轮、best按最佳指标；周期快照有optimizer/scaler状态，正常结束的last/best会被精简。
- 短测证据：`var/training_preflight/2026-09-16-yolo26m-full/result.json`。128张train图片、8个batch；资源验证复用其中64张，不是准确率评估。实际batch16/val32，无自动降批；8个loss有限，验证及保存/回读通过。峰值allocated18.490GiB/reserved20.311GiB，约21秒；结束GPU占用归零。
- 39相关测试通过，Ruff、profile dry-run、diff检查通过；一次独立只读复核未发现阻塞问题。未修改训练代码、数据划分、线上模型或API配置，未恢复服务、未推送GitHub。

整卡用途不等于必须用满显存，也不能承诺任意输入无OOM。沿用100epoch上限/早停30；SEDD只评PL/BLOCK，数据集限制见原数据报告。下一步等待用户确认正式启动。
