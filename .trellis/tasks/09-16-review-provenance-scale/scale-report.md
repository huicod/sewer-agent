# Sewer2403 分组修复与固定尺度诊断

日期：2026-09-16。只使用本地已有数据、`best.pt`、Pillow 与现有 YOLO/evaluator；未训练、未调用
LLM/VLM、未改线上阈值/检测策略、未覆盖源数据或历史评估。

## 结论

`*_frameNN` 的确是旧分组规则漏项。按旧代码、seed 3407、80/10/10 和当前 19,049 份源图/XML
重建后，72 个显式 frame 家族中有 43 个跨 reconstructed split；修复后同一组全部同 split，实际跨组
数为 0。旧 manifest 已丢失，因此这只是算法重建证据，不是找回的历史真相；新 split 也不能倒推成
现有权重从未见过的独立 test。

尺度是 SEDD 退化的一部分，但固定的 512 px、128 px overlap 切片不是可直接上线的解法。在 outcome-blind
固定抽取的 80 张 SEDD 上，full+tiles 把 recall 从 8.42% 提至 17.89%，但 precision 从 57.14%
降至 9.39%，F1 从 14.68% 降至 12.32%。在可能与训练重叠的 40 张 Sewer2403 对照上 recall 完全没变，
precision 从 82.98% 降至 41.05%。因此本轮没有证明切片带来可接受的整体改进，不应改生产策略。

## 分组与 split 证据

修复仅在 `group_mode=video` 增加大小写不敏感、必须位于 normalized stem 结尾的
`[_-]frame\d+` 家族识别；`source` 模式、Roboflow 后缀清理和原 MP4 规则保持不变。

| manifest | provenance | train / val / test | 五类 background（train / val / test） | frame 家族跨 split |
|---|---|---:|---:|---:|
| legacy reconstructed | 旧代码规则在当前 source 上重放；不是 recovered manifest | 15,239 / 1,904 / 1,906 | 4,938 / 695 / 696 | 43 / 72 |
| corrected future | 修复后、只供未来训练决策的候选 split | 15,239 / 1,904 / 1,906 | 4,938 / 695 / 696 | 0 / 72 |

两个 manifest 都显式写入 `independence_claim=none`。尤其不能把 corrected future 的 source test 用于宣称
当前 `best.pt` 的独立泛化能力。

## 全缓存共同面积分箱

统计复用保存的 SEDD 1,638 张与 Sewer2403 source 240 张预测，conf=0.25、同类 IoU≥0.5、置信度
降序一对一匹配。为了公平跨域比较，下表都只计 PL/BLOCK。相对面积均以各自原图面积为分母；已匹配
TP 归到 GT 面积分箱，未匹配 FP 归到预测框面积分箱。

| 相对面积 | SEDD TP/FP/FN | SEDD P / R | source240 TP/FP/FN | source240 P / R |
|---|---:|---:|---:|---:|
| <1% | 1 / 1 / 542 | 50.00% / 0.18% | 3 / 1 / 3 | 75.00% / 50.00% |
| 1–5% | 20 / 14 / 670 | 58.82% / 2.90% | 13 / 8 / 3 | 61.90% / 81.25% |
| 5–20% | 88 / 35 / 344 | 71.54% / 20.37% | 35 / 8 / 4 | 81.40% / 89.74% |
| ≥20% | 22 / 15 / 65 | 59.46% / 25.29% | 32 / 11 / 3 | 74.42% / 91.43% |
| overall | 131 / 65 / 1,621 | 66.84% / 7.48% | 83 / 28 / 13 | 74.77% / 86.46% |

完整 source 的 clipped preparation labels 中，PL/BLOCK 目标面积中位数分别为 7.4487%/26.9243%；
SEDD 为 3.4475%/0.6530%。固定 source240 子样本自身中位数为 8.1227%/17.8414%。这支持明显尺度差异，
特别是 BLOCK，但相关性不能单独证明尺度是域差的唯一或主要因果。source240 可能与训练集重叠，只是
健康对照，不是 held-out accuracy。

## 固定 paired ablation

在读取预测结果前，由旧 manifest 用 seed 20260916 固定 80 张 SEDD；source 每类固定 8 张、共 40 张，
并按修复后的 group key 去重。原图 arm 复用已保存的 full-image conf≥0.25 预测；实验 arm 合并同一 full
预测与 512 px/128 px overlap crop 的新预测，映射回原图、裁边，再作同类 IoU≥0.5 NMS。模型输入仍为
`imgsz=960`，评分仍为同类 IoU≥0.5。

| cohort / arm | TP / FP / FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| SEDD80 full | 8 / 6 / 87 | 57.14% | 8.42% | 14.68% |
| SEDD80 full+tiles | 17 / 164 / 78 | 9.39% | 17.89% | 12.32% |
| source40 full | 39 / 8 / 7 | 82.98% | 84.78% | 83.87% |
| source40 full+tiles | 39 / 56 / 7 | 41.05% | 84.78% | 55.32% |

逐面积结果：

| cohort / arm | <1% P/R | 1–5% P/R | 5–20% P/R | ≥20% P/R |
|---|---:|---:|---:|---:|
| SEDD80 full | 0/0% | 0/0% | 53.85/23.33% | 100/16.67% |
| SEDD80 full+tiles | 0/0% | 5.56/14.81% | 12.64/36.67% | 15.38/33.33% |
| source40 full | 0/0% | 81.82/81.82% | 78.95/83.33% | 88.24/93.75% |
| source40 full+tiles | 0/0% | 31.03/81.82% | 34.09/83.33% | 68.18/93.75% |

120 张 tiled run 墙钟 20.26 秒，新 tile inference 累计 18.84 秒；原图 arm 的 5.22 秒来自先前缓存，
不是本轮重新计时，因此不把二者包装成严格延迟 benchmark。新 tile 平均每图为 SEDD 0.183 秒、source
0.105 秒，差异来自输入尺寸/窗口数构成不同。

这里的 FP 只表示“没有按当前类别和 IoU 匹配原标注”：它可能包含 tile 边缘的重复/片段框、原标注
漏标或真正误检，未经人工逐框确认，不能把 precision 下降全部称作幻检。SEDD 本轮仅对可映射的 PL/BLOCK 评分，其他三类
不评分；未知视频/管段分组使 80 张图也不是独立事件样本。切片只试了一个预先固定设置，没有搜索
tile size、overlap、边缘抑制、WBF/NMS 或重新校准阈值。小图库是按结果选出的诊断例，不是准确率样本。

## 产物与复现

- `var/evaluations/2026-09-16-scale-followup/legacy-reconstructed-manifest.json`
- `var/evaluations/2026-09-16-scale-followup/corrected-future-manifest.json`
- `split-summary.json`、`cached-area-summary.json`
- `ablation-manifest.json`、`ablation-runtime.json`、`ablation-predictions.jsonl`、
  `ablation-summary.json`、`ablation-timing.json`
- `gallery/index.html`：6 例 Pillow 生成的 GT/full/full+tiles 并排图；选择理由另存
  `gallery/selection.json`

从仓库根目录运行：

```bash
.venv/bin/python .trellis/tasks/09-16-review-provenance-scale/scale_diagnostic.py prepare-splits
.venv/bin/python .trellis/tasks/09-16-review-provenance-scale/scale_diagnostic.py summarize-splits
.venv/bin/python .trellis/tasks/09-16-review-provenance-scale/scale_diagnostic.py summarize-cached
.venv/bin/python .trellis/tasks/09-16-review-provenance-scale/scale_diagnostic.py prepare-ablation
.venv/bin/python .trellis/tasks/09-16-review-provenance-scale/scale_diagnostic.py run-ablation
.venv/bin/python .trellis/tasks/09-16-review-provenance-scale/scale_diagnostic.py summarize-ablation
```

raw manifest/prediction 创建使用 exclusive write，避免无意覆盖；summary/gallery 可从 raw 结果重建。
测试覆盖 explicit-frame 回归、tile 覆盖/映射/裁边、同类 NMS、一对一匹配、面积分箱、split crossing
及图库去重。实际验证命令：

```bash
.venv/bin/python -m pytest backend/tests/test_sewer_frame_grouping.py \
  .trellis/tasks/09-16-review-provenance-scale/test_scale_diagnostic.py -q
.venv/bin/ruff check backend/tests/test_sewer_frame_grouping.py \
  .trellis/tasks/09-16-review-provenance-scale/scale_diagnostic.py \
  .trellis/tasks/09-16-review-provenance-scale/test_scale_diagnostic.py
```

## 建议与人类决策门

不推广当前 naive full+tiles，也不改线上阈值。若要继续，先由人决定是否投入训练/域适配；在任何训练
决定前先冻结真正未参与训练、具备管段/视频分组且五类标注更完整的独立评估集。若只继续切片路线，
应作为单独的受控实验解决边缘片段/重复与阈值校准，并仍需独立集验证。本报告停在该人类决策门，
不据此启动重训。
