# YOLO + VLM 管网缺陷检测：论文、工程路线与项目方案总结

> 调研状态：2026-08-15  
> 项目目标：在现有 YOLO26 管网缺陷检测器已接近数据上限的前提下，构建兼具工程可落地性与论文、专利、软件著作权价值的系统。  
> 相关本地调研：[VLM 增强 YOLO / 目标检测专题调查](./summary_vlm_enhanced_yolo_detection_survey.md)、[目标检测调研](./summary_detection.md)、[多模态管网异常检测与量化](./summary_multimodal_pipe_anomaly_detection.md)、[VideoPipe/多标签分类调研](./summary.md)。

## 1. 结论先行

最适合本项目的主线不是“用一个通用 VLM 替换 YOLO26”，而是：

1. 保留已训练的固定类别 YOLO26，负责高频、低延迟的候选框和初始类别；
2. 用 ByteTrack 或 BoT-SORT 把逐帧结果合并为稳定的缺陷轨迹或事件；
3. 只对低置信度、易混类别、少样本类别、模型分歧和疑似漏检帧触发 VLM；
4. VLM 同时接收缺陷 ROI、上下文、相邻帧、专家文本定义、正例原型和难负例，负责语义复核与拒识；
5. 另设 YOLOE 作为第二候选源，周期性或按需补充 YOLO26 完全没有提出框的疑似区域；
6. 对最终确认的缺陷使用 YOLO26-Seg、SAM 系列或 Grounded SAM2 得到精细掩膜，再结合双目、RGB-D、LiDAR 或标定后的深度进行尺寸量化；
7. LLM 只读取结构化检测与量测结果生成报告，不允许凭图像自由编造尺寸、位置和严重等级。

这条主线把系统分成两个计算环：

- **快速环**：YOLO26 + tracker，每帧或高频运行；
- **慢速语义环**：YOLOE/VLM/分割/量测/报告，事件触发、异步运行。

它最接近 [PipeEye](https://authoring.jamesdysonaward.org/en-US/2026/project/pipe-eye) 的工程级串联，同时吸收 [PipeInspector](https://doi.org/10.1016/j.autcon.2026.106942) 的“增强—检测—分割—深度—报告”、[ViTPrompt](https://openaccess.thecvf.com/content/CVPR2026/html/Qin_ViTPrompt_Training-Free_Prompt_Refinement_with_Visual_Tokens_for_Open-Vocabulary_Detection_CVPR_2026_paper.html) 的视觉原型思想、[PipeCLIP](https://doi.org/10.1109/TASE.2026.3670430) 的专家文本描述，以及 [VISION](https://arxiv.org/abs/2509.21370) 的“发现疑点后重新靠近拍摄”。

## 2. 先区分三种容易混淆的任务

### 2.1 LLM/VLM 不参与检测，只负责解释和报告

检测类别、位置和掩膜已经由小模型给出，VLM/LLM负责把结构化结果转换为状态描述、严重度建议、维修建议和标准报告。

- 代表工作：[Edge-Optimized Vision-Language Models for Underground Infrastructure Assessment](https://arxiv.org/abs/2602.03742)。
- 优点：边界清楚、容易审计，不容易破坏 YOLO 的实时性。
- 局限：检测器漏掉的缺陷仍然漏掉，不能单独提高 detection mAP 或 recall。

### 2.2 VLM 参与识别或检测，目标是提高最终准确率

VLM可能承担候选框复核、类别重判、开放词表候选生成、区域检索、特征融合或训练数据增强。

- 候选框内复核主要提高 precision 和类别区分能力；
- 第二候选源才能补充 YOLO 没有框出的 false negative，从而提高 recall；
- 离线伪标签、合成数据和蒸馏最终仍要重新训练小模型。

本项目真正要解决的是这一类问题，但应采用“选择性触发”，而不是每帧都调用大 VLM。

### 2.3 VLM 参与主动感知

模型不仅判断缺陷，还决定“哪里看不清、是否应重新拍摄、应从什么视角靠近”。

- 代表工作：[Language-in-the-Loop Culvert Inspection on the Erie Canal](https://arxiv.org/abs/2509.21370)。
- 这不是普通后处理，而是把识别不确定性转化为相机或机器人动作。
- 若项目拥有可控云台、变焦或机器人，这是很强的论文和专利方向；若只有离线 CCTV，则可退化为“自动选择最清晰的相邻帧”。

## 3. 推荐的完整系统架构

> 新版完整设计见：[YOLO26 + YOLOE + VLM 管网缺陷检测系统架构](./architecture_yolo26_vlm_pipe_inspection.md)。其中已将 YOLOE 修正为受路由器触发、输出回流候选融合与跟踪的第二检测器。

```text
管道 CCTV / 机器人相机 / 可选深度与 LiDAR
                  │
                  ▼
       帧接入、质量评估、原图与环形缓存
       选择性去雾 / 去模糊 / 超分辨率
                  │
                  ▼
       YOLO26 固定类别主检测器（高频）
                  │
                  ▼
       候选标准化 + ByteTrack / BoT-SORT
   track_id、运动补偿、去重、选择最佳证据帧
                  │
                  ▼
           不确定性与事件路由器
       ┌───────────┼──────────────┐
       │           │              │
已有框不确定   疑似完全漏检     证据不足
       │           │              │
       ▼           ▼              ▼
 VLM ROI复核   YOLOE整帧/切片   选帧/变焦/重拍
 文本+视觉原型  补充框和掩膜     或人工复核
       │           │              │
       │           └──> 新候选回流候选标准化和跟踪
       │                          │
       └──────────────┬───────────┘
                      ▼
     候选融合、校准、拒识和事件状态机
                  │
                  ▼
   YOLO-Seg / SAM / Grounded SAM2 精细掩膜
                  │
                  ▼
      深度/双目/LiDAR + 标定 + 几何量化
                  │
                  ▼
     结构化事实记录 JSON + 审核证据包
                  │
                  ▼
       LLM 生成标准化检测与维修报告
```

### 3.1 各部件的责任边界

| 部件 | 应负责 | 不应负责 | 可借鉴工作 |
|---|---|---|---|
| YOLO26 | 高频框、初始类别、置信度 | 长文本推理、自由生成报告 | [YOLO26 文档](https://docs.ultralytics.com/models/yolo26/) |
| Tracker | `track_id`、跨帧去重、最佳帧、轨迹级投票 | 重新定义缺陷类别 | [ByteTrack](https://arxiv.org/abs/2110.06864)、[BoT-SORT](https://arxiv.org/abs/2206.14651) |
| VLM 复核器 | ROI 分类、易混类对比、证据描述、拒识 | 从零生成精确框和真实尺寸 | PipeCLIP、ViTPrompt、管网 VLM benchmark |
| YOLOE/开放词表检测器 | 第二候选源、零样本探索、离线辅助标注 | 直接取代领域微调 YOLO | [YOLOE](https://arxiv.org/abs/2503.07465)、[YOLO-World](https://arxiv.org/abs/2401.17270) |
| 分割器 | 像素级边界 | 仅凭掩膜确定真实厘米尺度 | YOLOE-Seg、SAM、Grounded SAM2 |
| 几何模块 | 长度、宽度、面积、体积、堵塞率、变形率 | 用语言模型猜数值 | PipeInspector、RGB-D/双目/LiDAR 路线 |
| LLM Agent | 工具编排、标准条文检索、报告生成 | 覆盖模型结果、捏造事实 | PipeInspect Agent、Edge-Optimized VLM |

### 3.2 为什么必须同时保留“复核”和“第二候选源”

只把 YOLO 阈值降下来再送 VLM 复核，可以找回一部分低置信度目标，但找不回“YOLO 完全没有产生候选框”的缺陷。因此应区分：

- **分类错误**：已有框但类别错，VLM ROI 复核最有效；
- **低置信度目标**：已有弱框，降低阈值 + VLM 可以处理；
- **完全漏检**：必须靠 YOLOE、Grounding DINO、WeDetect-Uni、滑窗/切片、多帧差异或主动重拍产生第二批候选；
- **边界不准**：需要第二次检测、ViTPrompt 式视觉提示或分割器；
- **尺度不准**：需要真实几何传感器和标定，VLM 无法补救。

## 4. 管网领域最重要的论文与工程

### 4.1 PipeInspector / PipeInspect Agent

- 论文：[LLM-based agent for urban sewer pipeline inspection integrating image enhancement and multimodal defect analysis](https://doi.org/10.1016/j.autcon.2026.106942)，期刊 *Automation in Construction*，2026。
- 两个名称不是两篇论文：**PipeInspector** 是视觉与量化流水线，**PipeInspect Agent** 是建立在它之上的 LLM 工具编排层。
- 数据：从长三角真实管道视频筛出 1,710 张图像，包含 intrusion、fracture、misalignment、obstacle 四类。

完整链路为：

```text
退化 CCTV 图像
 -> LT-DehazeGAN / FDT-DeblurNet / ST-RCAN
 -> Grounding DINO 定位
 -> Grounded SAM 精细分割
 -> Depth Pro 深度
 -> 几何参数计算
 -> PipeInspect Agent 调工具并生成报告
```

对本项目的直接价值：

- 给出了从图像质量、缺陷定位、掩膜、深度量测到报告的完整学术叙事；
- 可将其中 Grounding DINO 主检测替换为现有 YOLO26，只在疑难样本上调用 Grounding DINO；
- Agent 应读取各工具的结构化输出，而不是让 LLM 自己“看图后猜尺寸”。

边界与复现性：

- 论文中的 LLM 主要负责理解指令、规划和调用工具，没有充分证明它提高了 Grounding DINO 的检测 mAP；
- 截至调研日期未找到完整官方代码与数据集；Grounding DINO、Grounded SAM 和 Depth Pro 等上游模块分别开源，可做近似复现；
- 单目深度的绝对尺度仍必须通过标定、已知管径、双目/RGB-D 或 LiDAR 验证。

### 4.2 Edge-Optimized VLMs for Underground Infrastructure Assessment

- 论文：[Edge-Optimized Vision-Language Models for Underground Infrastructure Assessment](https://arxiv.org/abs/2602.03742)，2026 预印本。
- 定位：轻量分割器持续检测，微调后的 VLM 按事件生成管网缺陷摘要；它属于“检测与报告分离”，不是用 VLM 增强检测器。

方法：

1. RAPID-SCAN 语义分割模型产生缺陷掩膜和标签；
2. Phi-3.5-Vision 输入 RGB 图像、掩膜和标签，生成 condition、location、severity、implications 四部分描述；
3. 使用 QLoRA 做领域适配：3.8B 基座只训练约 67M 参数，4-bit NF4，训练 3 个 epoch；
4. INT8、混合精度和 TensorRT 部署到 Jetson AGX Orin；
5. 分割持续运行，VLM 只在发现显著缺陷或完成管段时触发。

数据与结果：

- Sewer and Culvert Defect 数据含 5,051 张 RGB 图像和八类缺陷；类别明显长尾，例如 holes 只有 87 张，而 cracks 和 fracture 各 1,661 张；
- RAPID-SCAN：0.64M 参数、0.19 GFLOPs、F1 0.834、mIoU 0.729；
- 论文报告约 15 FPS 的持续检测和秒级的 VLM 摘要，因此所谓“实时”主要指检测环，而不是逐帧 VLM；
- 文本质量使用 ROUGE、BLEU、METEOR 等生成指标，不能当作检测 mAP。

对本项目的直接价值：

- 最值得复制的是“事件触发 VLM”和“结构化报告微调数据”；
- 可用 Qwen3-VL 替代 Phi-3.5-Vision，并用 LoRA/QLoRA 学习中文管网术语和报告格式；
- 不应期待该论文的 VLM 自动提高 YOLO26 召回率。

复现性：截至调研日期未找到论文配套的完整官方代码或 SCD 自然语言标注集，需自行实现训练与部署链。

### 4.3 PipeEye

- 工程介绍：[James Dyson Award 项目页](https://authoring.jamesdysonaward.org/en-US/2026/project/pipe-eye)、[DCON 2026 团队页](https://dcon.ai/teams/toyota2026/)。
- 性质：机器人和 AI 工程项目，不是经过论文指标验证的学术方法。
- 硬件：面向约 150–500 mm 管径，使用 FAST-LIO2、LiDAR/IMU、Jetson Orin Nano 和高分辨率相机。

公开链路：

```text
FAST-LIO2 自主定位与行驶
 -> YOLO26 实时发现 crack/root
 -> SAM3 零样本分割
 -> Qwen3-VL 判断严重度和 JSWA 类别
 -> Qwen3.5 生成 JSWA 报告
 -> GIS/Web 平台人工确认
```

对本项目的直接价值：

- 它是“固定类别 YOLO26 + 分割 + VLM 复核 + LLM 报告”在管网中的最直接工程例子；
- 检测命中后机器人减速并采集更清晰证据，可借鉴为云台变焦或邻帧最佳帧选择；
- 项目页称 Toyota City 提供了约 20,000 张标注图像并完成实管道 PoC。

证据边界：公开页面没有给出可复核的 mAP、逐类召回、消融实验或完整代码，因此适合作为系统设计佐证，不适合作为算法效果基线。

### 4.4 Language-in-the-Loop Culvert Inspection on the Erie Canal

- 论文：[arXiv](https://arxiv.org/abs/2509.21370)；[项目主页](https://droneslab.github.io/vision/)。
- 系统名称：VISION。
- 性质：2025/2026 预印本和真实涵洞部署，重点是主动感知而非实时目标检测排行榜。

工作流程：

1. VLM 根据前视图像和简短提示输出开放词表 ROI、置信度和自然语言理由；
2. 双目深度把 ROI 恢复到三维空间；
3. 受涵洞几何约束的规划器控制 Spot 与云台相机靠近疑点；
4. 高分辨率特写再次送入 VLM，返回 Confirmed、Partially Confirmed 或 Not Confirmed；
5. 最终图像与理由用于报告和人工审计。

公开实验在真实涵洞中完成。论文报告初始 ROI 与专家判断的一致率为 61.4%，重新成像后的评估达到 80%。这些是专家一致性指标，不是目标检测 mAP。VLM 请求、视角规划和机器人移动均为秒到分钟级，因此不能视为每帧实时检测。

对本项目最重要的启发不是换模型，而是：**当模型不确定时，主动获取更好的证据可能比继续堆大模型更有效。** 如果无法控制机器人，可实现软件版本：从轨迹前后帧选择曝光最好、距离最近、遮挡最少的一帧重新复核。

复现性：有论文和项目页，但未发现完整端到端代码仓库；机器人、ROS、双目、云台和云端 VLM 依赖较重。

### 4.5 Multi-class sewer defect detection with VLMs

- 论文：[Multi-class sewer defect detection with vision-language models](https://www.cambridge.org/core/journals/cambridge-prisms-water/article/multiclass-sewer-defect-detection-with-visionlanguage-models/5A50F4D3610089FF1771B78D1FE69AB3)，*Cambridge Prisms: Water*，2026；[公开结果与图像浏览器](https://huggingface.co/spaces/rtaormina/vlms_for_sewerml)。
- 它是 18 个 VLM 在管道 CCTV 图像上的零样本分类 benchmark，不是综述，也不输出检测框。
- 数据为 100 张 SewerML 图像，任务是在五类缺陷加 no-defect 中选择一个标签，并输出解释和自报置信度。
- 最佳 GPT-4.1 mini 的 macro-F1 为 0.50；所有模型对 production error 失败，对 deformation 也较差，模型自报置信度普遍没有良好校准。

这篇论文说明通用 VLM 具备领域零样本能力，但目前不能直接替代专用检测器。它也不能与我们 YOLO26 的 mAP 直接比较，原因见第 10 节。

### 4.6 PipeCLIP

- 论文：[PipeCLIP: Defect-Conditioned and Cross-Focus-Driven Vision-Language Model for Video-Based Sewer Defect Inspection](https://doi.org/10.1109/TASE.2026.3670430)，IEEE T-ASE，2026。
- 任务：视频级、多标签管道缺陷分类，不是逐目标框检测，也不是生成式 LLM。
- 需要领域训练或微调；它不是只在推理时把文本写长一点。

核心方法：

- EPD：专家为每种缺陷编写更具形态区分力的先验描述；
- DAC：把缺陷和管材、位置、外观等属性耦合；
- DecTP：将类别名、EPD 和 DAC 组合为 defect-conditioned text prompt；
- CFT：融合视频中不同焦距/局部段的时序视觉信息。

对本项目的直接帮助是构建“专家文本原型库”：不要只写“crack”，而应给出裂缝的方向、边缘、连续性、所在管壁、与接缝/水渍/反光的区别。它适合轨迹或短视频级类别复核，不负责发现 YOLO 未产生的框。

论文给出的代码地址是[匿名仓库](https://anonymous.4open.science/r/PipeCLIP-0925)，长期可用性和最终版本对应关系需要谨慎核验。

### 4.7 SewerMan

- 论文页：[SewerMan: A vision-language collaborative framework with domain prior for robust multi-label sewer defect classification](https://www.sciencedirect.com/science/article/pii/S1568494626000311)；[作者预印本页](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5397392)。
- 任务：Sewer-ML 上的多标签图像分类，而非目标框检测。
- 方法：OTIP 清理标签噪声；文本引导特征提取把管材信息注入视觉网络；HVSA 做分层视觉—语义对齐；SGFS 通过语义选择关键视觉区域。

它对本项目的价值在于说明“管材、缺陷语义关系、噪声标签”可以作为可训练的领域先验。若现有瓶颈来自材质变化和混淆类，这比直接换一个更大的通用 VLM 更有针对性。

## 5. 路线 A：VLM 离线增强数据或监督小模型

这条路线在部署时仍然只运行 YOLO，VLM 在训练前或训练过程中充当伪标注器、噪声过滤器或数据生成器。

### 5.1 Robust Object Detection with Pseudo Labels from VLMs using Per-Object Co-teaching

- 论文：[arXiv](https://arxiv.org/abs/2511.09955)，预印本/评审阶段。
- OWLv2 离线给未标注图像产生伪框；两个随机初始化的 YOLOv5 互相根据“每个目标的损失”挑选更可靠的框训练对方，而不是把整张图一起丢掉。
- KITTI 实验中 YOLOv5m 的 mAP@0.5 从 31.12% 提升到 46.61%；加入 10% 真实标注后达到 57.97%。这些数值来自自动驾驶数据，不能外推到管道。

此前的理解基本正确，但“通过对方来减少损失”更准确地说是：每个模型用自己的损失判断框是否可能有噪声，再把筛出的低损失框交给另一个模型更新。

对本项目的优先级较低：我们已有固定类别标注和成熟 YOLO26，且不希望再训练两个 YOLO；如果只是补标签，先用 YOLOE/Grounding DINO 预标注后人工审核更简单。截至调研日期未发现专用官方代码仓库。

### 5.2 VLM-PL

- 论文：[VLM-PL](https://arxiv.org/abs/2403.05346)、[CVPR Workshop 版本](https://openaccess.thecvf.com/content/CVPR2024W/CLVISION/html/Kim_VLM-PL_Advanced_Pseudo_Labeling_Approach_for_Class_Incremental_Object_Detection_CVPRW_2024_paper.html)。
- 目标是 class-incremental object detection：加入新类别时，用 VLM 验证旧检测器产生的旧类伪标签，缓解灾难性遗忘。
- VLM 对每个候选区域回答“它是否确实属于某类别”，验证后的旧类伪标签与新类真值一起训练下一代检测器。

因为本项目不以持续新增类别为核心，这篇论文主要贡献一个可借鉴的“逐 ROI 是/否验证 prompt”，整体训练路线优先级低。未找到完整官方代码。

### 5.3 VL-PLM

- 论文：[Exploiting Unlabeled Data with Vision and Language Models for Object Detection](https://arxiv.org/abs/2207.08954)，ECCV 2022；[官方代码](https://github.com/xiaofeng94/VL-PLM)。
- 它先生成 class-agnostic region proposals，再用 CLIP 比较每个 crop 与类别文本的相似度，经阈值和 NMS 得到伪框，最后训练开放词表或半监督检测器。

换句话说，它不是在线让 CLIP 给 YOLO“加几分”，而是把大量无标注图像转换成可训练的伪检测集。只有在 `serverData` 中存在大量未标注管道视频、且愿意重新训练 YOLO26 时才有明显价值。

### 5.4 MIRAGE：少样本缺陷的合成数据路线

- 论文：[MIRAGE: Model-agnostic Industrial Realistic Anomaly Generation and Evaluation for Visual Anomaly Detection](https://arxiv.org/abs/2603.13507)，CVPR 2026 VAND Workshop；[官方代码](https://github.com/vadnomalous/mirage)；[公开合成数据](https://huggingface.co/datasets/visualanom/mirage_mvtec_visa)。
- 输入只需正常图像；VLM 为每个对象生成可能的异常描述，图像生成 API 合成异常图像，CLIP 过滤生成失败样本，Grounding DINO 与 YOLO26-Seg 的双分支特征差异自动生成掩膜。
- 论文公开 13,000 余对图像—掩膜，并报告合成图像训练下游异常分割的效果；它不是管道数据上的直接证据。

这是本项目少样本类最值得实验的 VLM 路线之一，但必须改造成管道版本：

1. 缺陷 prompt 由管网专家审核，限制形态、材料、位置和物理可行性；
2. 保留真实测试集，任何合成图都不能进入验证或测试；
3. 用 CLIP/VLM 过滤后仍需人工抽检；
4. 同时和 Copy-Paste、扩散生成、传统重采样、class-balanced loss 做对照；
5. 只在真实少样本类的 AP/recall 确实提升时保留。

### 5.5 不依赖 VLM 的长尾基线

为避免论文结论变成“任何新增数据都会提升”，VLM 方案必须和这些低成本方法比较：

- 类别重采样、class-aware sampler、Focal Loss、class-balanced loss；
- copy-paste 和小目标切片；
- 两阶段微调或 decoupled head；
- 半监督一致性训练；
- 少样本检测基线 [TFA](https://proceedings.mlr.press/v119/wang20j.html)；
- 长尾检测参考 [SimLTD](https://cvpr.thecvf.com/virtual/2025/poster/32569) 和 [CascadeMatch](https://machinelearning.apple.com/research/semi-supervised-long-tailed)。

## 6. 路线 B：VLM/开放词表模型在线参与识别

### 6.1 ViTPrompt

- 论文：[ViTPrompt: Training-Free Prompt Refinement with Visual Tokens for Open-Vocabulary Detection](https://openaccess.thecvf.com/content/CVPR2026/html/Qin_ViTPrompt_Training-Free_Prompt_Refinement_with_Visual_Tokens_for_Open-Vocabulary_Detection_CVPR_2026_paper.html)，CVPR 2026。
- 代码地址：[Test-time Adaptation for Object Detection](https://github.com/buerzlh/Test-time-Adaptation-for-Object-Detection)；截至调研日期仓库仍未提供足够的可复现实现，应视为“论文可读、代码尚不可依赖”。

它对同一张图做两次 Grounding DINO 前向：

1. 第一次用文本 prompt 得到高置信度框；
2. 从这些框 ROIAlign 出视觉 token；
3. 把视觉 token 与文本 embedding 拼接为增强 prompt；
4. 第二次检测同时修正类别分数和框坐标。

其核心不是“让大语言模型看一眼”，而是使用当前图像中已检测实例形成视觉原型。论文在分布偏移基准上有明显收益，但计算量约从一次 Grounding DINO 的 145.64 GFLOPs 增至 252.26 GFLOPs，约 1.73 倍。

对本项目的最合适迁移方式不是照搬两次 Grounding DINO，而是：

- 为每个管道缺陷类建立 5–20 个专家确认的视觉原型；
- 对 YOLO26 ROI 提取 VLM/CLIP 图像 embedding；
- 同时与类别文本原型、正例视觉原型和难负例原型比较；
- 将这些相似度与 YOLO 置信度融合。

这避免了 ViTPrompt 的自举风险：如果第一遍检测错了，错误实例不会被拿来当该类的视觉提示。

### 6.2 WeDetect / WeDetect-Uni / WeDetect-Ref

- 论文：[WeDetect: Fast Open-Vocabulary Object Detection as Retrieval](https://arxiv.org/abs/2512.12309)，CVPR 2026；[官方代码与权重](https://github.com/WeChatCV/WeDetect)。
- WeDetect：双塔开放词表检测器，把区域和文本映射到同一空间，以检索方式分类；
- WeDetect-Uni：冻结 WeDetect，只学习一个“objectness prompt”，产生不带最终类别的通用候选框；
- WeDetect-Ref：使用 Qwen3-VL 类模型从 WeDetect-Uni 候选中按复杂语言描述选择目标。

它不等于简单降低 YOLO 阈值。降低阈值仍受 YOLO26 自身的类别头和特征空间限制；WeDetect-Uni 是另一套视觉—语言特征空间的通用 objectness proposal source，因此理论上可能找出 YOLO26 未提议的区域。

但对管道纹理缺陷存在风险：通用“物体性”更偏向有清晰边界的物体，裂缝、腐蚀和渗漏不一定像独立物体。最公平的验证是固定候选数量，对比：

- 低阈值 YOLO26；
- YOLOE；
- WeDetect-Uni；
- Grounding DINO。

评价 AR@100/300、少样本类 recall、false proposals/image 和延迟。官方仓库已提供推理、微调、ONNX 和权重，复现条件比 ViTPrompt 好，但代码/模型为 GPL-3.0，应用前需核查许可证影响。

### 6.3 ROD-MLLM

- 论文：[ROD-MLLM: Towards More Reliable Object Detection in Multimodal Large Language Models](https://openaccess.thecvf.com/content/CVPR2025/papers/Yin_ROD-MLLM_Towards_More_Reliable_Object_Detection_in_Multimodal_Large_Language_CVPR_2025_paper.pdf)，CVPR 2025。
- 目标：让 MLLM 根据自由语言同时定位零个、一个或多个对象，并学会对不存在的目标输出 None，而不是强行幻觉一个框。
- 方法：查询式定位模块提取低层对象特征，将全局和对象级视觉信息对齐到语言空间，再由 LLM 完成高层语义理解与最终定位；同时构造包含零目标和多目标描述的 ROD 数据。
- 论文在 D3 language-based detection benchmark 上相对既有 MLLM 报告 +13.7 AP。

对项目的意义主要是“可靠拒识”和复杂描述定位，例如“位于管顶、沿轴向连续、边缘不规则且不是接缝的裂缝”。但它是需要专门训练的 MLLM 检测器，不是可直接挂在 YOLO26 后面的轻量插件；截至调研日期未找到完整官方代码，因此近期工程优先级低。

### 6.4 LED

- 论文：[LED: LLM Enhanced Open-Vocabulary Object Detection without Human Curated Data Generation](https://arxiv.org/abs/2503.13794)，CVPR 2026 Findings。
- 它不生成伪数据，而是把冻结 MLLM 的早期 decoder hidden states，通过零初始化 cross-attention adapter 注入 Grounding DINO/GLIP 等开放词表检测器。
- Qwen2-0.5B 的前两层即可提供主要增益，论文报告相对 Grounding DINO 增加约 8.7% GFLOPs，在 OmniLabel 复杂描述任务上提升明显；普通类别 AP 的增益很小。

LED 的学术启示很强：VLM 的语义知识可以进入检测器特征，而不是只在输出端投票。但它需要多阶段对齐、adapter 训练和较复杂的 Grounding DINO 代码链；论文也明确说明直接用 MLLM hidden states 替换检测特征会崩溃，必须学习对齐。因此本项目当前不优先复现 LED，可把它作为“未来架构创新”的论文背景。

### 6.5 YOLOE

- 论文：[YOLOE: Real-Time Seeing Anything](https://arxiv.org/abs/2503.07465)，ICCV 2025；[官方代码](https://github.com/THU-MIG/yoloe)；[Ultralytics 文档](https://docs.ultralytics.com/models/yoloe/)。
- 已开源代码和权重；官方仓库使用 AGPL-3.0，商业集成需单独评估许可。

YOLOE 接受三类提示：

1. **文本提示**：类别名或描述列表，例如 `crack`、`root intrusion`；RepRTA 改善区域—文本对齐；
2. **视觉提示**：参考图中的框或掩膜及其类别 id，SAVPE 把参考实例编码成视觉提示；
3. **prompt-free**：使用内置大词汇和 LRPC 进行开放集检测。

输入仍是图像/视频；提示是额外条件。输出是框、类别、置信度，分割权重还可输出实例掩膜。Ultralytics API 中可通过 `set_classes()` 设置文本类；视觉提示通过参考图上的 `bboxes` 或 `masks` 与 `cls` 传入。导出到 ONNX/TensorRT 后提示会固化，要更换类别通常需要回到 `.pt` 设置后重新导出。

是否需要用本项目数据训练：

- **验证零样本能力**：不需要训练，先用中英文类别名、专家描述和视觉参考测试；
- **作为第二候选源**：可以先不训练，只在疑似漏检帧或离线数据上运行；
- **要求达到领域生产精度**：很可能仍需用管道数据微调，因为裂缝、沉积、接头问题等与通用目标存在显著域差异；
- **固定类别且已有强 YOLO26**：不建议让 YOLOE 替换主检测器，应作为互补提议器、视觉原型工具和自动标注器。

YOLOE 对项目有用，但用途排序应是：

1. 零训练补充候选与漏检审计；
2. 使用视觉提示做少样本原型匹配；
3. 离线辅助标注；
4. 有明确收益后再领域微调；
5. 不把“开放词表”本身当作论文创新，因为项目类别已经固定。

### 6.6 YOLO-World、Grounding DINO 与 Grounded SAM2

- [YOLO-World 论文](https://arxiv.org/abs/2401.17270) / [代码](https://github.com/AILab-CVC/YOLO-World)：实时文本开放词表检测基线，适合和 YOLOE 比较；工程链较旧，但仍是重要学术基线。
- [Grounding DINO 论文](https://arxiv.org/abs/2303.05499) / [代码](https://github.com/IDEA-Research/GroundingDINO)：自由文本 grounding，召回强但推理更重，适合高召回检索、离线标注和疑难帧。
- [Grounded SAM2 代码](https://github.com/IDEA-Research/Grounded-SAM-2)：把文本定位和 SAM2 图像/视频分割、跟踪串起来，适合制作高质量掩膜和自动标注，不适合取代每帧 YOLO 主环。

## 7. 五套工程组合的准确定位

| 组合 | 工程成熟度 | 真正解决的问题 | 与论文/开源的关系 | 对本项目的判断 |
|---|---:|---|---|---|
| DeepStream/VSS + 检测跟踪 + Cosmos Reason 2/Qwen | 高，但平台绑定强 | 多路视频接入、GPU 推理、跟踪、检索、事件理解和报告 | [DeepStream 9.0](https://docs.nvidia.com/metropolis/deepstream/dev-guide/index.html)、[VSS 3.1 release notes](https://docs.nvidia.com/vss/3.1.0/release-notes.html)、[Cosmos Reason 2](https://docs.nvidia.com/vss/3.1.0/warehouse-docs/CR2.html)；VSS 3.1 仍标注 Early Access | 适合多路固定 CCTV/中心服务器；管道机器人单路视频可能过重。平台本身不保证检测精度提升 |
| YOLOE-26 + BoT-SORT + Qwen3-VL + vLLM | 高 | 开放词表候选、稳定轨迹、中文语义复核、自托管服务 | [YOLOE](https://docs.ultralytics.com/models/yoloe/)、[BoT-SORT](https://github.com/NirAharon/BoT-SORT)、[Qwen3-VL](https://github.com/QwenLM/Qwen3-VL)、[vLLM](https://docs.vllm.ai/) | 研究性和自托管最好，但要自行实现事件队列、缓存、状态机和校准 |
| 固定类别 YOLO26 + ByteTrack + VLM 复核 | 最高 | 保留现有高性能主模型，只处理疑难事件 | [ByteTrack 代码](https://github.com/ifzhang/ByteTrack)；整体是工程组合而非单篇论文 | **当前最推荐主线**。类别固定、实时性要求高时收益风险比最好 |
| YOLO-World v2.1 + Qwen3-VL | 中高 | 文本动态类别与语义复核 | YOLO-World 有 CVPR 论文和代码，Qwen3-VL 开源 | 可作为 YOLOE 的开放词表基线，但不必同时维护两套生产主检测器 |
| Grounding DINO/Grounded SAM2 + VLM | 中 | 高召回检索、自动标注、精细掩膜、离线分析 | 各组件均有论文或开源代码 | 适合标注工具和疑难帧慢速分支，不适合每帧实时主检测 |

DeepStream/VSS 的当前版本边界需要特别说明：NVIDIA 官方 [VSS 3.1 Quickstart](https://docs.nvidia.com/vss/3.1.0/quickstart.html) 将视频接入/存储、Cosmos Reason 2 NIM、LLM Agent 等组成完整蓝图，但 release notes 仍把 3.1 标为 Early Access。它解决“如何运行和管理多路视频 AI”，不是一个证明管道缺陷 mAP 提升的算法论文。

## 8. 少样本与低识别率类别：建议采用的组合方法

### 8.1 数据层

1. 按 pipe/site/video 划分训练、验证、测试，杜绝同一视频相邻帧泄漏；
2. 建立每类错误库：漏检、误检、易混类、模糊/反光/水膜、尺度过小；
3. 用 MIRAGE 式生成模型合成少样本缺陷，但必须经过领域约束和人工抽检；
4. 从无标注视频中用低阈值 YOLO26、YOLOE 和 Grounding DINO 取并集，再人工确认；
5. 对真实少样本类优先做 track-level 取样，避免连续相邻帧造成数量虚高。

### 8.2 模型层

- 先跑 class-balanced sampler、Focal/Class-Balanced Loss、Copy-Paste、小目标切片等强基线；
- 用 YOLOE 视觉提示提供少样本原型候选；
- 冻结 VLM，只训练很小的分数融合或校准层，降低训练资源；
- 最终若希望边缘端仍只有一个 YOLO，可把 VLM 的高置信复核结果蒸馏回 YOLO26。

建议的融合分数可以写成：

```text
S(c) = α · s_yolo(c)
     + β · sim(ROI, text_prototype_c)
     + γ · max_j sim(ROI, positive_visual_prototype_c,j)
     - δ · max_k sim(ROI, hard_negative_prototype_c,k)
```

其中 `hard_negative` 应包括接缝、反光、水渍、污物纹理、字幕/时间戳等经常被误判成缺陷的区域。`α、β、γ、δ` 只能在验证集拟合，不能用测试集调参。

### 8.3 最有论文价值的创新点

- 管网缺陷的“文本定义 + 正例视觉原型 + 难负例原型”三元提示库；
- 同时考虑 YOLO 不确定性、VLM 分歧、图像质量和时序一致性的事件路由器；
- 轨迹级而非逐帧级的 VLM 复核；
- VLM 不确定时触发最佳邻帧选择、变焦或主动重拍；
- 把最终语义判断蒸馏回边缘 YOLO26，形成 teacher-reviewer-student 闭环。

## 9. Prompt 如何优化，才能真正帮助 VLM 复核

“写得更详细”本身并不保证更准确。有效 prompt 应把任务边界、视觉证据、类别定义和负例对比组织成固定结构。

### 9.1 推荐输入包

- 原始整帧，提供管道位置和环境上下文；
- YOLO 框内 ROI；
- 向外扩 20%–50% 的 context crop；
- 同一 `track_id` 的前、中、后或最佳质量三帧；
- YOLO 初始类别、分数，但在 prompt 中明确“仅作参考”；
- 当前管材、管径、里程、相机方向等元数据；
- 每类专家定义、正例图和难负例图。

### 9.2 推荐的结构化复核模板

```text
角色：你是管道 CCTV 缺陷复核器，只负责复核候选区域。

允许类别：crack、root_intrusion、deposit、joint_displacement、deformation、...
允许输出：上述类别之一、normal_artifact、uncertain。

候选信息：
- detector_label: crack
- detector_score: 0.43
- pipe_material: concrete
- position: pipe crown

类别定义：
- crack：细长、非规则、可沿轴向或环向延伸，通常不是规则接缝；
- joint_displacement：沿完整或大部分圆周出现的规则边界，常伴随台阶；
- reflection：随光源或视角变化，边缘高亮，不形成稳定结构断裂；
...

任务：
1. 判断 ROI 是否有真实缺陷；
2. 在允许类别中选择最匹配者；
3. 列出最多 3 条可见证据；
4. 列出最容易混淆的类别及排除理由；
5. 若证据不足必须输出 uncertain，不能猜测；
6. 不估计图像中不可标定的厘米尺寸。

仅输出 JSON：
{
  "is_defect": true,
  "label": "crack",
  "confidence": 0.78,
  "evidence": ["..."],
  "confuser": "joint_displacement",
  "abstain": false,
  "need_reimage": false
}
```

### 9.3 可借鉴论文

- PipeCLIP：专家先验描述和缺陷—属性耦合；
- [DetPro](https://openaccess.thecvf.com/content/CVPR2022/papers/Du_Learning_To_Prompt_for_Open-Vocabulary_Object_Detection_With_Vision-Language_Model_CVPR_2022_paper.pdf)：学习连续 prompt，而不是只依赖人工模板；
- [CoOp](https://arxiv.org/abs/2109.01134)：少样本学习 CLIP prompt context；
- VLM-PL：对每个候选 ROI 使用明确的是/否验证问题；
- ViTPrompt：在文本提示外加入视觉实例 token；
- VISION：宽泛发现 prompt 与特写确认 prompt 分成两阶段。

### 9.4 Prompt 实验必须固定的变量

- 同一测试集、同一 ROI、同一 VLM 版本；
- 解码温度、图像分辨率、帧数和输出 schema 固定；
- 比较“类别名 only”“专家描述”“+正例图”“+难负例”“+时序帧”；
- 评价 macro-F1、逐类 recall、拒识率、ECE/置信度校准和单事件延迟；
- 不使用模型自报 confidence 直接替代校准分数，管网 VLM benchmark 已显示自报置信度不可靠。

## 10. 精细分割与真实尺寸量化

### 10.1 分割路线

- 已有分割标注：优先训练 YOLO26-Seg，速度和领域一致性最好；
- 只有框：用 SAM/SAM3/Grounded SAM2 产生初始掩膜，再人工修正形成训练集；
- 开放词表和离线标注：Grounding DINO + Grounded SAM2；
- 快速试验：YOLOE-Seg 可同时输出开放词表框和掩膜，但仍需在管道数据上验证。

### 10.2 尺寸量化必须有尺度来源

单个 RGB 图像中的像素长度不能自动等价为毫米。推荐的可靠性顺序通常是：

1. 线激光/结构光或高质量 RGB-D；
2. 标定双目；
3. LiDAR/ToF 与 RGB 外参标定；
4. 已知管径与圆柱模型约束；
5. 单目 metric depth，仅用于候选估计并做现场标定验证。

几何算法建议：

- 裂缝长度：掩膜骨架在三维表面上的测地长度；
- 裂缝宽度：距离变换或骨架法向截线，在深度坐标中转换；
- 缺损/腐蚀面积：对掩膜内每个像素投影后的表面面积求和；
- 变形率：对管道横截面拟合圆/椭圆，与设计管径比较；
- 沉积/堵塞率：沉积轮廓面积除以拟合管道截面面积；
- 位置：相机里程、里程计/SLAM 位姿、钟点方位共同表示。

这里可直接衔接 [PipeInspector](https://doi.org/10.1016/j.autcon.2026.106942) 的 mask + depth 思路，以及本地[多模态量化调研](./summary_multimodal_pipe_anomaly_detection.md)中的 RGB-D、双目、线激光和 LiDAR 方案。

## 11. LLM 报告生成：以结构化事实为唯一依据

### 11.1 事件数据结构

建议每个缺陷事件至少保存：

```json
{
  "event_id": "...",
  "pipe_id": "...",
  "chainage_m": 12.4,
  "clock_position": "11 o'clock",
  "track_id": 37,
  "frame_timestamp": "...",
  "proposal_source": ["yolo26", "yoloe"],
  "detector": {"label": "crack", "score": 0.43},
  "vlm_review": {
    "label": "crack",
    "confidence_calibrated": 0.78,
    "evidence": ["..."],
    "abstain": false
  },
  "geometry": {
    "length_mm": 124.0,
    "max_width_mm": 5.8,
    "method": "stereo_calibrated",
    "uncertainty_mm": 1.6
  },
  "severity": {"grade": "...", "standard": "..."},
  "evidence_frames": ["..."],
  "human_review": "pending"
}
```

### 11.2 报告层规则

- LLM 只能引用 JSON 中存在的类别、坐标、尺寸、标准条款和证据帧；
- 缺失字段写“未量测/待复核”，不能自动补数；
- 严重等级由显式规则或标准检索工具计算，LLM 负责解释；
- 输出同时保留机器可读 JSON 和人类可读 PDF/Word；
- 保存 prompt、模型版本、输入事实和输出，满足论文复现与工程审计。

Edge-Optimized VLM 提供了“condition/location/severity/implications”模板；PipeInspect Agent 提供工具编排范式；PipeEye 提供标准报告和 GIS 闭环的工程示例。

## 12. 指标：macro-F1 与 YOLO mAP 为什么不能直接比较

单类 F1 为：

```text
Precision_c = TP_c / (TP_c + FP_c)
Recall_c    = TP_c / (TP_c + FN_c)
F1_c        = 2 · Precision_c · Recall_c / (Precision_c + Recall_c)
Macro-F1    = (1/C) · Σ_c F1_c
```

Macro-F1 给每个类别相同权重，因此适合观察少样本类是否被多数类掩盖。

但是 Cambridge 管网 VLM 论文的 macro-F1=0.50 是“100 张图像、六选一分类、不要求框”的结果；YOLO 常见的 mAP@0.5 或 mAP@0.5:0.95 同时要求类别正确和框的 IoU 达标。二者任务、数据集和匹配规则都不同，不能说 0.50 比某个 YOLO mAP 更好或更差。

本项目应同时报告：

- 检测：mAP@0.5、mAP@0.5:0.95、per-class AP、per-class recall；
- 候选生成：AR@N、false proposals/image；
- 事件级：track/event macro-F1、重复报警率；
- VLM：复核 macro-F1、abstention rate、ECE、幻觉率；
- 量测：长度/宽度/面积 MAE 和不确定度覆盖率；
- 工程：YOLO FPS、VLM P50/P95 延迟、触发率、显存、端到端吞吐；
- 报告：字段正确率、无依据陈述率、人工修改量。

## 13. 推荐实验矩阵

在完全相同的按视频/管段划分测试集上逐步增加模块：

| 实验 | 系统 | 要回答的问题 |
|---|---|---|
| E0 | YOLO26 | 当前真实基线和逐类瓶颈是什么 |
| E1 | YOLO26 降低阈值 | 单纯增加候选能找回多少漏检，代价多少误报 |
| E2 | E1 + VLM 类别名 prompt | 最朴素复核是否有效 |
| E3 | E1 + 专家文本 prompt | PipeCLIP 式描述是否改善易混类 |
| E4 | E3 + 正例视觉原型 | ViTPrompt/YOLOE visual prompt 思想是否有效 |
| E5 | E4 + 难负例原型 | 是否能抑制反光、接缝、水渍误报 |
| E6 | E5 + 相邻帧/track 聚合 | 视频证据能否进一步稳定结果 |
| E7 | E6 + YOLOE 第二候选源 | 能否恢复完全漏检并提高 rare-class recall |
| E8 | E7 + 主动选帧/重拍 | 更好证据是否比更大模型更有效 |
| E9 | 最终确认 + segmentation + depth | 是否能可靠量化尺寸 |
| E10 | 结构化结果 + LLM report | 是否减少报告工时且不增加无依据陈述 |

关键消融必须包括：

- 相同候选预算下比较低阈值 YOLO26、YOLOE、WeDetect-Uni 和 Grounding DINO；
- 只用文本、只用视觉原型、文本+视觉、再加难负例；
- 单帧与多帧；
- 通用 VLM、领域 LoRA VLM、小型 VLM；
- VLM 自报置信度与经过验证集校准后的分数。

## 14. 论文、专利和软著可以如何形成一条链

### 14.1 论文主线

建议题目方向：**面向长尾管道缺陷的、不确定性感知的文本—视觉原型协同检测框架**。

可形成四个贡献：

1. 管网专用文本、正例视觉和难负例三元原型库；
2. YOLO26 快速检测与 YOLOE/VLM 慢速复核之间的可学习事件路由器；
3. 轨迹级多帧证据和拒识机制，重点提升少样本类别 macro-F1/recall；
4. 像素掩膜、真实几何量测和有证据约束的自动报告闭环。

论文的核心主张应是“最终系统在同一管网测试集上显著优于 YOLO26”，而不是引用通用 benchmark 的零样本成绩。

### 14.2 专利方向

较有独立性的技术点：

- 基于检测不确定性、图像质量和跨模型分歧的 VLM 触发方法；
- 文本—视觉—难负例原型共同复核管道缺陷的方法；
- 根据 VLM 不确定性自动选择相邻帧、变焦或控制机器人重拍的方法；
- 缺陷轨迹、三维量测和标准条款共同约束报告生成的方法。

### 14.3 软件著作权方向

可形成一套完整软件：视频接入、检测跟踪、事件队列、VLM 服务、提示词/原型管理、量测、报告模板、人工审核、GIS/管段档案和模型评估面板。

## 15. 分阶段实施建议

### 阶段 1：两周内可验证的最小闭环

1. 固化 YOLO26 测试集和逐类错误表；
2. 接 ByteTrack，生成缺陷事件和最佳帧；
3. 选择一个本地 Qwen3-VL 规格，先做 ROI 离线复核；
4. 实现类别名、专家描述、正例和难负例四组 prompt 对照；
5. 只比较同一批候选上的逐类 precision、recall、macro-F1 和延迟。

### 阶段 2：验证是否能真正补漏检

1. YOLOE 零样本测试，不训练；
2. 文本提示与视觉提示分别评估；
3. 与低阈值 YOLO26、YOLO-World、Grounding DINO 或 WeDetect-Uni 比较候选召回；
4. 只把新增候选送 VLM，不让其进入每帧主环。

### 阶段 3：解决少样本类别

1. MIRAGE 式管道缺陷生成；
2. 人工抽检和自动质量过滤；
3. 与传统增强、重采样和损失函数做严格消融；
4. 若提升稳定，再将 VLM 高置信结果蒸馏回 YOLO26。

### 阶段 4：分割、量测和报告

1. 建掩膜标注子集并训练 YOLO26-Seg，或用 SAM 预标后人工修正；
2. 完成相机/深度标定与尺寸误差测试；
3. 将检测、复核、量测写入统一 JSON Schema；
4. LLM 依据 schema 和标准模板生成报告；
5. 加入人工审核、版本留痕和失败回退。

## 16. 论文与代码索引

> VLM 如何参与在线复核、补召回、离线监督和域适应的详细对比，见 [VLM 增强 YOLO / 目标检测专题调查](./summary_vlm_enhanced_yolo_detection_survey.md)。

### 管网领域

- [PipeInspector / PipeInspect Agent](https://doi.org/10.1016/j.autcon.2026.106942)
- [Edge-Optimized Vision-Language Models for Underground Infrastructure Assessment](https://arxiv.org/abs/2602.03742)
- [PipeEye 工程项目](https://authoring.jamesdysonaward.org/en-US/2026/project/pipe-eye)
- [Language-in-the-Loop Culvert Inspection](https://arxiv.org/abs/2509.21370) / [项目页](https://droneslab.github.io/vision/)
- [Multi-class sewer defect detection with VLMs](https://doi.org/10.1017/wat.2026.10019) / [公开结果](https://huggingface.co/spaces/rtaormina/vlms_for_sewerml)
- [PipeCLIP](https://doi.org/10.1109/TASE.2026.3670430) / [匿名代码](https://anonymous.4open.science/r/PipeCLIP-0925)
- [SewerMan](https://www.sciencedirect.com/science/article/pii/S1568494626000311)

### 离线监督、伪标签与少样本

- [Per-Object Co-teaching with VLM Pseudo Labels](https://arxiv.org/abs/2511.09955)
- [VLM-PL](https://arxiv.org/abs/2403.05346)
- [VL-PLM](https://arxiv.org/abs/2207.08954) / [代码](https://github.com/xiaofeng94/VL-PLM)
- [MIRAGE](https://arxiv.org/abs/2603.13507) / [代码](https://github.com/vadnomalous/mirage) / [数据](https://huggingface.co/datasets/visualanom/mirage_mvtec_visa)
- [TFA few-shot object detection](https://proceedings.mlr.press/v119/wang20j.html)
- [SimLTD](https://cvpr.thecvf.com/virtual/2025/poster/32569)

### 在线开放词表检测、复核与特征融合

- [LLMDet（CVPR 2025 Highlight）](https://openaccess.thecvf.com/content/CVPR2025/html/Fu_LLMDet_Learning_Strong_Open-Vocabulary_Object_Detectors_under_the_Supervision_of_CVPR_2025_paper.html) / [代码、模型与数据](https://github.com/iSEE-Laboratory/LLMDet)
- [YOLOE 论文（ICCV 2025）](https://openaccess.thecvf.com/content/ICCV2025/html/Wang_YOLOE_Real-Time_Seeing_Anything_ICCV_2025_paper.html) / [代码](https://github.com/THU-MIG/yoloe) / [Ultralytics 文档](https://docs.ultralytics.com/models/yoloe/)
- [YOLO-World（CVPR 2024）](https://openaccess.thecvf.com/content/CVPR2024/html/Cheng_YOLO-World_Real-Time_Open-Vocabulary_Object_Detection_CVPR_2024_paper.html) / [代码](https://github.com/AILab-CVC/YOLO-World)
- [Grounding DINO](https://arxiv.org/abs/2303.05499) / [代码](https://github.com/IDEA-Research/GroundingDINO)
- [Grounded SAM2](https://github.com/IDEA-Research/Grounded-SAM-2)
- [ViTPrompt](https://openaccess.thecvf.com/content/CVPR2026/html/Qin_ViTPrompt_Training-Free_Prompt_Refinement_with_Visual_Tokens_for_Open-Vocabulary_Detection_CVPR_2026_paper.html) / [待发布代码仓库](https://github.com/buerzlh/Test-time-Adaptation-for-Object-Detection)
- [WeDetect（CVPR 2026）](https://openaccess.thecvf.com/content/CVPR2026/html/Fu_WeDetect_Fast_Open-Vocabulary_Object_Detection_as_Retrieval_CVPR_2026_paper.html) / [代码与权重](https://github.com/WeChatCV/WeDetect)
- [SRA-Det（CVPR 2026）](https://openaccess.thecvf.com/content/CVPR2026/html/Yang_SRA-Det_Learning_Omni-Grained_Open-Vocabulary_Detection_Beyond_Category_Names_CVPR_2026_paper.html)
- [TTAOD-F（NeurIPS 2025 Main）](https://proceedings.neurips.cc/paper_files/paper/2025/hash/88c3c482430a62d35e03926a22e4b67e-Abstract-Conference.html) / [代码](https://github.com/gaoyingjay/TTAOD-F)
- [OW-OVD（CVPR 2025）](https://openaccess.thecvf.com/content/CVPR2025/html/Xi_OW-OVD_Unified_Open_World_and_Open_Vocabulary_Object_Detection_CVPR_2025_paper.html) / [代码](https://github.com/xxyzll/OW_OVD)
- [DGS / Cross-Domain Incremental Detection（CVPR 2026）](https://openaccess.thecvf.com/content/CVPR2026/html/Wang_Boosting_Vision-Language_Models_Towards_Cross-Domain_Incremental_Object_Detection_CVPR_2026_paper.html) / [代码](https://github.com/Never-wx/dgs)
- [Generalist YOLO（WACV 2025，对照工作）](https://openaccess.thecvf.com/content/WACV2025/html/Chang_Generalist_YOLO_Towards_Real-Time_End-to-End_Multi-Task_Visual_Language_Models_WACV_2025_paper.html) / [代码](https://github.com/WongKinYiu/GeneralistYOLO)
- [ROD-MLLM](https://openaccess.thecvf.com/content/CVPR2025/papers/Yin_ROD-MLLM_Towards_More_Reliable_Object_Detection_in_Multimodal_Large_Language_CVPR_2025_paper.pdf)
- [LED](https://arxiv.org/abs/2503.13794)
- [DetPro](https://openaccess.thecvf.com/content/CVPR2022/papers/Du_Learning_To_Prompt_for_Open-Vocabulary_Object_Detection_With_Vision-Language_Model_CVPR_2022_paper.pdf)
- [CoOp](https://arxiv.org/abs/2109.01134)

### 工程部署组件

- [NVIDIA DeepStream](https://docs.nvidia.com/metropolis/deepstream/dev-guide/index.html)
- [NVIDIA VSS 3.1](https://docs.nvidia.com/vss/3.1.0/quickstart.html)
- [Cosmos Reason 2 in VSS](https://docs.nvidia.com/vss/3.1.0/warehouse-docs/CR2.html)
- [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL)
- [vLLM](https://docs.vllm.ai/)
- [ByteTrack](https://github.com/ifzhang/ByteTrack)
- [BoT-SORT](https://github.com/NirAharon/BoT-SORT)

## 17. 最终选型

当前推荐优先级如下：

1. **生产主线**：固定类别 YOLO26 + ByteTrack/BoT-SORT + 事件触发 Qwen3-VL 复核；
2. **补召回支线**：YOLOE 零样本/视觉提示作为第二候选源；
3. **论文创新点**：专家文本 + 正例视觉 + 难负例原型、轨迹级复核、校准与拒识；
4. **少样本增强**：MIRAGE 式合成数据，严格真实测试与消融；
5. **量化闭环**：分割掩膜 + 标定深度/三维几何，而不是 VLM 猜尺寸；
6. **报告闭环**：结构化事实 + 规则/标准 + LLM，保留证据和人工审核；
7. **暂不优先**：ROD-MLLM、LED 的大规模专门训练，以及完整 VSS 平台迁移。

这条路线既能复用现有 YOLO26，又能对低识别率类别提供真正独立的视觉—语言证据，还能自然扩展到尺寸量化、报告生成和主动重拍，适合作为一篇系统论文、一组方法专利和一套可登记的软件平台共同推进。
