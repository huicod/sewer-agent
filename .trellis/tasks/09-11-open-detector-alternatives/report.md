# 开放补检替代模型实测 — 2026-09-11

结论：两个替代模型均完成本机 GPU 试验，但**本轮固定提示词/阈值下，没有证明比 YOLOE
更有价值的补漏收益**，不切换生产、不默认启用。已接受 VLM、主检测器及融合均未改动；
无需回退 `ba3da925` / `5f574be4`。本轮工程/实验交付完成，模型效果未获验收。

## 候选与协议

- MM-Grounding-DINO Base O365/GoldG/V3Det：官方对应权重 COCO 零样本 box mAP 52.5。
- OWLv2 Large Patch-14 ST/FT ensemble：官方对应检查点 LVIS-val AP 44.6 / APr 42.6。
- 两者榜单和训练协议不同，不可直接横比，更不是管网准确率；公开权重/版本/来源见
  [模型记录](research/models.md)。没有训练或使用付费推理服务。
- 固定原 Sewer2403 的 48 图（原 paired 36 + regression 12），27 图按原规则触发，
  21 图未触发。复用原 YOLO、VLM 和 YOLOE 缓存，排除 8 个视频探针，不使用 SEDD。
- 两个新模型均输入原图 RGB + 同一 `sewer-open-v1` 七项文字概念，不是画框图、
  YOLO ROI 或新增 VLM 指令。GT 不进入推理；没有对 GT 调阈值或挑图追分。
- MM：800 短边/1333 长边限制，FP32，概念 token sigmoid 均值分类，阈值 0.30。
  OWL：原生 1008 方形处理，eager FP16，原生分数阈值 0.10。新模型均同概念 NMS 0.5、
  最多 100 框、原图坐标；全部新增仅待审，OTHER 不纳入五类评分。

## 实际增量

以下“新增命中”仅指相对已接受 VLM 融合、同类别框 IoU≥0.5 的原标注匹配：

| 补检模型 | 运行成功 / 未触发 / 失败 | 模型候选 | 新增待审 | 其中 OTHER | 新增原标注命中 | 其中原 YOLO 漏检 |
|---|---|---:|---:|---:|---:|---:|
| YOLOE（原缓存） | 27 / 21 / 0 | 11 | 11 | 1 | 0 | 0 |
| MM-GDINO Base | 27 / 21 / 0 | 97 | 96 | 23 | 0 | 0 |
| OWLv2 Large | 27 / 21 / 0 | 722 | 715 | 408 | 1 | 0 |

原标注不完整，未匹配框不能一律称为误报；同样，新增数也不能称为准确率提升。
各模型分数未校准，阈值/分辨率不同，这不是等审核预算下的最终模型排名。
联合五类对原标注的 TP/未匹配预测/FN：YOLOE 34/57/12；MM 34/120/12；OWL 35/353/11。
完整 P/R/F1 和逐类指标在模型 `summary.json`，不把待审联合层当正式结论。
所有新分支完整保留已有 VLM 融合框，均未造成已有标注命中丢失。

## 应优先查看的证据

统一目录：`/root/autodl-tmp/server/var/evaluations/2026-09-11-open-alternatives/`。

- [全部 48 图并排对比](../../../var/evaluations/2026-09-11-open-alternatives/index.html)：
  VLM 基线、YOLOE、MM、OWL；每列可进入原图、YOLO、VLM 建议、原融合、补检和待审联合层。
- [sample-0042](../../../var/evaluations/2026-09-11-open-alternatives/owlv2-large/gallery/sample-0042/index.html)：
  OWL 唯一新增标注匹配为 FS（对应新增框分数约 0.134）；原 YOLO 已有 FS 0.503，
  VLM 认为不符合腐蚀特征而拒绝，同时指出悬挂异物 BLOCK。是否是真 FS 需人工审查，
  不能把这个匹配直接当作补到新 YOLO 漏检或确认 VLM 错误。
- [sample-0004](../../../var/evaluations/2026-09-11-open-alternatives/mm-gdino-base/gallery/sample-0004/index.html)：
  MM 新增 2 BLOCK + 1 FS，OWL 新增 15 项；原 VLM 的 PL、FS 完整保留。
- [sample-0022](../../../var/evaluations/2026-09-11-open-alternatives/owlv2-large/gallery/sample-0022/index.html)：
  MM 新增 2 项、OWL 19 项，包含大区域/不同类别候选；原高分 AJ 未被替换。

模型子目录各含 `state.json`（完整输入/输出与版本）、`summary.json`、`review-queue.csv`
及 48 个详情页。YOLOE 仅重导出旧缓存，原 T11 目录未覆盖；人工队列已有编辑不覆盖。

## 运行、验证与恢复

- RTX 3080 Ti 12GB；Torch 2.8.0+cu128；新增可选 Transformers 4.57.6 及其依赖。
  权重在 `var/models/open-detectors/{mm-gdino-base,owlv2-large}`，两份约 2.7 GB；
  普通 HTTP 首次下载停顿/重新开始，原生 Xet 最终成功，首次权重准备约 36 分钟。
- MM 27 次检测合计 14.19 秒；OWL 23.70 秒（首次含加载、后续复用模型，不含图库和最终释放）。
  原 YOLOE 25.43 秒含逐图装卸，不能直接据此作速度排名。
- 同一 sample-0004 冒烟峰值分配/保留显存：MM 1738/1962 MiB；OWL 2559/3410 MiB，
  仅代表该单图，不是全数据集峰值。两组完整 27 次推理均无 OOM，结束后 GPU 已释放。
- 新增 VLM 请求 0。MM FP16 首检和定位复现均失败于原生 Float/Half 类型冲突；
  改 FP32 后成功。失败 `smoke.json`、成功 `mm-smoke-fp32.json` / `owl-smoke-fp16.json` 均保留。
- 版本只检查 snapshot_download 的已有 commit 元数据，不校验文件哈希；它不防止
  同路径同版本字节被人为替换。模型/处理器/原图不得原位覆盖，换资产须新身份/目录。
- TDD：比较入口先 5 项预期失败；适配器及修复的 RED/GREEN 见 [实现记录](adapter-report.md)。
  最终后端 **695 passed / 84 skipped**，Ruff 通过；一次集成审查及定向修复复审通过，
  [审查记录](review.md)保留被撤回的评分意见及其模型特定依据。
- 复现/续跑命令见 [后端说明](../../../backend/README.md)，相同参数再次运行读取检查点，
  不重跑已完成检测、不调用 VLM。原 VLM Prompt、融合和在线默认配置无修改；没有推送。

当前 jj change：`nzqxsnsw`。T12 仍暂停；下一步先人工看本轮疑点图，决定是否继续验证
定点/受限补检，还是暂不采用通用开放检测分支。不因换模型就自动启用全图补扫。
