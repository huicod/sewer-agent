# 五类数据清洗阶段交接

2026-09-16。用户要求合并 SEDD + sewer2403，保持 PL/BX/FS/BLOCK/AJ，最终比例90/5/5。
已完成机械清洗、去重、分组候选；**最终划分未执行**，等待SEDD评估口径选择。不训练、不调用模型/API、不改在线系统、不推送。

## 已保存

`var/datasets/merged_five_v1_preparation/`：

- `cleaned_manifest.jsonl`：19,431条，含源图片/XML绝对路径、原文件名、标准化五类框、来源类别、修正记录、分组ID。
- `excluded.jsonl`：7,181条排除原因。只是不纳入候选，原文件均保留。
- `similar_pairs.csv`：1,214对近似画面，归入同组但不删除。
- `summary.json`：统计；`preview/index.html`：7张转换标注预览，不是模型预测。
- 本阶段没有`data.yaml`、最终train/val/test目录或复制的训练图片。不要误当最终训练数据集。

| 来源 | 原图片 | 保留候选 | 排除 |
|---|---:|---:|---:|
| sewer2403 | 19,049 | 18,320 | 729 |
| SEDD | 7,563 | 1,111 | 6,452 |
| 合计 | 26,612 | 19,431 | 7,181 |

保留目标框：PL 8,851，BX 348，FS 881，BLOCK 4,647，AJ 2,022。
五类ID保持`0 PL / 1 BX / 2 FS / 3 BLOCK / 4 AJ`。SEDD仅crack→PL、deposit→BLOCK；root不改成BLOCK。

## 清洗规则与实际发现

- 图像完整解码、尺寸/框/类别校验。sewer排除1张含无效框图片，685张命名可识别的预增强图
  （翻转、mosaicmix、噪声等）；保留低照度/小缺陷，不按模型结果挑数据。
- sewer同名图片/XML保留；6,148张原文件改为Roboflow后缀ID，XML中可证明对应的原视频文件名用于分组，
  最终保留6,104条恢复记录。保留记录中5,177个框越界不超过1像素，裁至图内；不是扩大或猜测框。
- SEDD排除5,001张无XML、924张XML内部filename冲突、527张无可映射目标的图片。
  924项很可能是原包改名留下的旧元数据，但尚无逐图证据，因此不猜配对、不把它们当正常。
  5001缺XML不能据“官方近2/3无缺陷”就认定全部是五类正常。
- 全文件内容相同才去重，不用SHA。11张一致重复副本不纳入，32张同图但五类框不一致的样本全部隔离。
- 原视频/来源变体别名加8×8 RGB近邻候选、32×32 RGB平均绝对差≤4/255的相似图分组，
  共13,487组、最大35张。近似图只分组，不凭相似度删除或合并标签。
- 已知组可以做到不跨split，但SEDD无可信视频/管段元数据，sewer也有来源信息不足的图片，
  **不宣称完全管段独立**。SEDD数字连续文件名不是可靠视频标识。

## 等待的一个决定

已异步询问用户：

1. 推荐：SEDD只补训练；验证/测试从sewer2403分组抽取，保持全局90/5/5。
2. 两者均划分；SEDD评估只统计PL/BLOCK，不能把其未标注的FS/BX/AJ计成误报。

第一种避免SEDD污染五类评估，但并不消除训练中潜在漏标；本轮没有逐图补齐语义标注。
90/5/5可执行，BX稀少（总348框）意味着其评估统计较不稳定，应报告逐类数量，不只总指标。
新split只用于未来从通用预训练权重开始的训练；旧best.pt可能已见过新val/test，不能拿它报独立泛化。

## 验证与继续方式

实际运行：

```bash
.venv/bin/python -u yolov26/code/prepare_merged_five.py \
  --prepare-only --out var/datasets/merged_five_v1_preparation
.venv/bin/python -m pytest yolov26/code/test_prepare_merged_five.py \
  backend/tests/test_sewer_frame_grouping.py -q
.venv/bin/ruff check yolov26/code/prepare_merged_five.py yolov26/code/test_prepare_merged_five.py
```

18个相关测试通过；已有分组回归包含在内。清单独立复算19,431行、五类框边界/数量、源文件存在、
唯一来源路径、同别名同组均通过；主Agent查看FS/BX/SEDD裂缝共3张转换图，未改变原语义标签。
独立只读审查无阻塞缺陷。没有运行与本次纯数据脚本无关的前后端全套测试。

收到用户选择后，从保存清单恢复`Record`（`labels`转`BoxLabel`、`image/xml`转`Path`），
复用group_id；无需再解码所有原图或重新近邻搜索。为选定划分策略补对应测试，导出独立
`var/datasets/merged_five_v1/`，核对逐来源/逐类划分与组交叉为0，写简短数据README和最终统计。
当前`split_rows()`实现的是按来源/类别平衡的混合划分，**不能不经选择就将其当最终口径**。
原始来源不改不删；无新训练、无服务恢复、无GitHub推送授权。
