# VLM 增强 YOLO / 目标检测专题调查

> 更新日期：2026-08-15  
> 调查目标：寻找能够通过视觉语言模型、开放词表模型或大多模态模型改善 YOLO/目标检测系统准确率，并支持后续结构化报告生成的研究路线。  
> 上位文档：[YOLO + VLM 管网缺陷检测总调查](./summary_yolo_vlm_pipe_inspection.md)

## 1. 结论先行

对当前项目最有价值的不是把 YOLO 改成一个“会聊天的检测器”，而是区分四条可以独立验证的增益链：

1. **在线复核**：YOLO 先给候选框，VLM/LMM 根据 ROI、上下文和相邻帧纠正类别或拒绝误检。它改善的是最终系统的 precision、macro-F1 和校准，未必改变 YOLO 权重。
2. **补召回**：YOLOE、WeDetect-Uni、YOLO-World 或其他开放词表检测器扫描 YOLO 没有提出框的区域，改善候选召回率。普通“ROI 复核 VLM”无法挽回完全漏检。
3. **离线监督与蒸馏**：LLM/VLM 生成区域描述、伪标签、属性文本或合成缺陷数据，再训练/微调检测器。只有这条路线能在部署时拿掉大模型后，仍然提高 YOLO 本身的权重性能。
4. **域适应**：利用视觉—语言先验在测试域或新域中更新提示、适配器或教师模型，解决污渍、光照、设备和管径变化导致的性能下降。

报告生成属于第五个下游能力：它能提升可解释性和交付效率，但**不能被写成检测 mAP 的提升**。

当前建议优先复现：

- **短期工程原型**：固定类 YOLO26 + tracker + 事件触发 VLM 复核，再加入 YOLOE/WeDetect-Uni 补召回。
- **论文方法主线**：借鉴 WeDetect-Ref、ViTPrompt 和 SRA-Det，把专家文本、正例视觉原型、难负例和轨迹多帧统一为一个可校准的复核器。
- **真正改进 YOLO 权重**：借鉴 LLMDet、VL-PLM/VLM-PL 和 MIRAGE，建立离线标注、伪标签、合成数据与蒸馏闭环。
- **跨设备与跨工况**：把 TTAOD-F、DGS 作为第二阶段研究，而不是首个可交付版本。

## 2. 先统一“VLM 提升准确率”的含义

| 问题 | 是否改变 YOLO 权重 | 主要评测指标 | 代表工作 |
|---|---:|---|---|
| VLM 复核已有候选 | 否 | 最终事件 precision/recall、macro-F1、ECE、拒识率 | WeDetect-Ref、ROD-MLLM |
| 视觉原型二次推理 | 否，或只改推理流程 | box AP、class AP、额外延迟 | ViTPrompt |
| 开放词表模型补充候选 | 否 | candidate AR、rare-class recall、未知缺陷召回 | YOLOE、WeDetect-Uni、YOLO-World、OW-OVD |
| VLM/LLM 参与训练监督 | 是，训练得到新的检测器 | YOLO 的 mAP/AP、少样本 AP | LLMDet、VL-PLM、VLM-PL、MIRAGE |
| 测试时/持续域适应 | 可能更新 prompt、adapter 或 teacher | 跨域 AP、遗忘率、适应开销 | TTAOD-F、DGS |
| 根据检测事实生成报告 | 否 | 字段完整率、事实一致率、人工修改率 | PipeInspect Agent、Edge-Optimized VLM |

还要区分两类常被统称为 VLM 的模型：

- **视觉—语言编码器/开放词表检测器**：CLIP、YOLOE、YOLO-World、WeDetect。擅长区域—文本匹配，速度较快，但通常不会生成长文本推理。
- **生成式多模态大模型 MLLM**：Qwen3-VL、Phi-3.5 Vision、LLaVA 类模型。能按指令比较、解释和输出 JSON，但定位精度、时延和幻觉需要单独约束。

## 3. 与本项目对应的系统架构

```text
视频帧 / 环形缓存
    ├─ 快速环：YOLO26 ──> 候选框 ──> Tracker ──> 缺陷轨迹/事件
    │                                      │
    └─ 慢速补召回：YOLOE / WeDetect-Uni ──┘
                                           │
                         触发器：低置信度、易混类、模型分歧、稀有类
                                           │
                            VLM 轨迹级复核与拒识
                         ROI + 上下文 + 代表帧 + 专家定义
                                           │
                              Schema / 规则 / 状态机
                                  ├─ 人工审核
                                  ├─ 分割与几何量测
                                  └─ LLM 结构化报告

审核后的高价值样本 ──> 伪标签/人工修正/合成数据 ──> 重训 YOLO26
```

关键责任边界：

- YOLO26 负责固定类别的高频检测；VLM 不应逐帧全量调用。
- YOLOE/WeDetect-Uni 是独立候选源，不是接在 tracker 后面的分类器。
- 新候选需要重新关联轨迹，或先作为 tentative track 接受多帧、VLM、几何或人工确认。
- 报告模型只能读取已验证的结构化事实，不能从图像猜测毫米尺寸、管道里程或严重等级。
- 当前 `videopipe_task1_boost` 是整段视频多标签分类分支，没有 bbox、mask、`track_id` 接口，因此需要适配层，不能直接视为这套检测架构的一部分。

## 4. 顶会与高相关论文总表

代码状态按 2026-08-15 可公开访问的材料记录；“有仓库”不等于已验证能够复现实验表格。

| 工作 | 正式状态 | VLM/语言如何影响检测 | 在线/离线 | 是否直接适合当前项目 | 公开状态 |
|---|---|---|---|---|---|
| [LLMDet](https://openaccess.thecvf.com/content/CVPR2025/html/Fu_LLMDet_Learning_Strong_Open-Vocabulary_Object_Detectors_under_the_Supervision_of_CVPR_2025_paper.html) | CVPR 2025，Highlight | 用详细图像描述和区域短语共同监督检测器，加入 grounding 与 caption loss | 离线训练 | **高**：最接近“用 LLM/VLM 提升检测器本身” | [代码、模型与数据](https://github.com/iSEE-Laboratory/LLMDet)，Apache-2.0 |
| [WeDetect](https://openaccess.thecvf.com/content/CVPR2026/html/Fu_WeDetect_Fast_Open-Vocabulary_Object_Detection_as_Retrieval_CVPR_2026_paper.html) | CVPR 2026 | WeDetect-Uni 产生通用候选；WeDetect-Ref 用 LMM 一次性分类候选 | 在线 | **很高**：分别对应补召回和复核 | [官方代码](https://github.com/WeChatCV/WeDetect)，已有推理、微调和部署材料 |
| [ViTPrompt](https://openaccess.thecvf.com/content/CVPR2026/html/Qin_ViTPrompt_Training-Free_Prompt_Refinement_with_Visual_Tokens_for_Open-Vocabulary_Detection_CVPR_2026_paper.html) | CVPR 2026 | 第一遍高置信检测生成实例视觉 token，第二遍细化框和分类分数 | 在线、免训练 | **很高**：可转化为正例/难负例原型复核 | [作者所列仓库](https://github.com/buerzlh/Test-time-Adaptation-for-Object-Detection) 当前为空，代码尚不可用 |
| [SRA-Det](https://openaccess.thecvf.com/content/CVPR2026/html/Yang_SRA-Det_Learning_Omni-Grained_Open-Vocabulary_Detection_Beyond_Category_Names_CVPR_2026_paper.html) | CVPR 2026 | LLM 生成类别视觉属性，经 CLIP 筛选后做细粒度语义检索 | 离线构造属性，在线匹配 | **高**：适合裂缝/接口/沉积等细粒度混淆 | 官方论文页未给出代码链接 |
| [YOLOE](https://openaccess.thecvf.com/content/ICCV2025/html/Wang_YOLOE_Real-Time_Seeing_Anything_ICCV_2025_paper.html) | ICCV 2025 | 文本提示、视觉提示和无提示词表进入实时检测/分割器 | 在线 + 可微调 | **很高**：最自然的第二候选源 | [官方代码](https://github.com/THU-MIG/yoloe)，含训练、推理、验证和权重 |
| [YOLO-World](https://openaccess.thecvf.com/content/CVPR2024/html/Cheng_YOLO-World_Real-Time_Open-Vocabulary_Object_Detection_CVPR_2024_paper.html) | CVPR 2024 | CLIP 文本编码器与 YOLO 区域特征对齐，实现实时开放词表检测 | 在线 + 预训练 | **中高**：成熟基线，能力被 YOLOE/WeDetect 扩展 | [官方代码](https://github.com/AILab-CVC/YOLO-World)，GPL-3.0 |
| [TTAOD-F](https://proceedings.neurips.cc/paper_files/paper/2025/hash/88c3c482430a62d35e03926a22e4b67e-Abstract-Conference.html) | NeurIPS 2025 Main | 视觉/文本 prompt 的 Mean-Teacher、动态实例记忆与测试时适应 | 在线适应 | **高但后置**：针对跨设备、污染和光照域偏移 | [官方代码](https://github.com/gaoyingjay/TTAOD-F) |
| [OW-OVD](https://openaccess.thecvf.com/content/CVPR2025/html/Xi_OW-OVD_Unified_Open_World_and_Open_Vocabulary_Object_Detection_CVPR_2025_paper.html) | CVPR 2025 | 属性相似度与不确定性融合，同时发现未知目标并零样本识别 | 在线 + 增量学习 | **中高**：适合发现标签体系外异常 | [官方代码](https://github.com/xxyzll/OW_OVD) |
| [DGS / Cross-Domain Incremental Detection](https://openaccess.thecvf.com/content/CVPR2026/html/Wang_Boosting_Vision-Language_Models_Towards_Cross-Domain_Incremental_Object_Detection_CVPR_2026_paper.html) | CVPR 2026 | 动态分组任务和逐步合并 adapter，平衡跨域适应与遗忘 | 离线/持续学习 | **中**：适合多设备、多管线长期迭代 | [官方代码](https://github.com/Never-wx/dgs) |
| [ROD-MLLM](https://openaccess.thecvf.com/content/CVPR2025/papers/Yin_ROD-MLLM_Towards_More_Reliable_Object_Detection_in_Multimodal_Large_Language_CVPR_2025_paper.pdf) | CVPR 2025 | 让 MLLM 在零目标、多目标和 `None` 情形中可靠 grounding | 在线 MLLM | **中**：可借鉴拒识和多目标输出设计 | 代码状态需另行核查 |
| [Generalist YOLO](https://openaccess.thecvf.com/content/WACV2025/html/Chang_Generalist_YOLO_Towards_Real-Time_End-to-End_Multi-Task_Visual_Language_Models_WACV_2025_paper.html) | WACV 2025 | 共享编码器统一检测、分割和图像描述，多任务共同训练 | 离线训练 | **低**：不是开放词表，也不是外部 VLM 复核 YOLO | [官方代码](https://github.com/WongKinYiu/GeneralistYOLO)，GPL-3.0 |

## 5. 第一条路线：在线 VLM/LMM 复核

### 5.1 WeDetect-Ref：最接近可实现的“候选框 + LMM 分类”

WeDetect 将任务拆得很清楚：

- WeDetect-Uni 先产生类别无关候选及区域 embedding；
- WeDetect-Ref 接收候选列表和复杂指代表达；
- LMM 不再自回归生成坐标，而是在一次前向中对候选对象分类。

这比让通用 VLM 直接输出自由文本框坐标更适合工程系统。迁移到管网时，可以把问题改成：在 `track_id` 的若干候选 ROI 中，判断哪一个满足“沿管壁纵向延伸、边缘不规则、与接缝阴影不同”的缺陷定义。

可直接借鉴：

- 候选生成与语义判断解耦；
- LMM 只在有限候选集合内决策；
- 输出类别概率和拒识，而非自由生成几何坐标；
- 区域 embedding 可用于历史视频回查与相似缺陷检索。

仍需自行解决：WeDetect 的公开 benchmark 不是管网缺陷，不能把论文 AP 直接迁移为本项目收益；还需要真实管网数据上的延迟、校准和长尾类别消融。

### 5.2 ViTPrompt：用当前图像的高置信实例改进第二遍检测

ViTPrompt 是免训练的两遍式方案：第一遍选择高置信检测，将对应视觉 token 作为实例提示；第二遍用这些 token 细化框和类别分数。其价值不是“换一个更大的 VLM”，而是让当前图像中的可靠实例成为即时视觉原型。

对项目的改造建议：

1. 不局限于单帧高置信框，改为从稳定轨迹中选 1–3 个代表帧；
2. 为每类同时维护正例原型和难负例原型，例如裂缝与接缝阴影；
3. 只对低置信或模型分歧候选运行第二遍；
4. 将二次结果与第一次结果做校准融合，允许 `abstain`，不强制改类。

成本必须计入：论文表中，Grounding DINO Swin-T 的两遍方案计算量从约 145.64 GFLOPs 增至 252.26 GFLOPs；因此更适合作为事件触发慢环，而不是每帧全量运行。该数字只代表论文设定，不代表本项目硬件实测。

### 5.3 SRA-Det：把类别名扩展为可观察属性

普通开放词表检测只比较“crack”“deposit”等短类别名，难以区分外观相近缺陷。SRA-Det 的关键改进是：让 LLM 生成类别特有的视觉属性，再用双 CLIP 进行验证；推理时从 token 级文本表示检索，并用可微分 soft-min 表达多个属性需要共同满足的逻辑。

本项目可以把它转化为专家可审核的属性库：

```json
{
  class: longitudinal_crack,
  required_attributes: [
    沿管道轴向延伸,
    局部边缘不规则,
    跨相邻帧保持连续
  ],
  hard_negatives: [
    规则环形接缝,
    光照反射细线,
    附着物边界
  ]
}
```

LLM 适合生成候选属性，最终属性必须经过专家审核和真实图像验证；否则语言模型会把不可见或不稳定的属性引入判别规则。

### 5.4 ROD-MLLM：重点学习“没有目标”和“有多个目标”

ROD-MLLM 的意义在于可靠性边界：输入可能没有目标，也可能有多个目标，模型需要显式输出 `None` 或多个实例。它更适合作为复核协议参考，而不是直接替代 YOLO：

- 允许零候选确认，减少 VLM 被迫选类；
- 多目标输出必须绑定候选 ID；
- 评测 hallucinated box、漏框和重复框，而不只评测回答是否流畅。

## 6. 第二条路线：开放词表模型补召回

### 6.1 YOLOE：当前最自然的 YOLO 系第二候选源

YOLOE 提供三种提示方式：

- 文本提示：用缺陷名称或描述检索目标；
- 视觉提示：用已确认的缺陷区域作为范例；
- prompt-free：通过内置大词表产生候选。

它同时支持检测和实例分割。对本项目最合理的用法不是长期替代固定类 YOLO26，而是在以下条件触发：

- 视频质量/深度分支提示异常，但 YOLO26 没有框；
- 稀有类周期扫描；
- 轨迹出现中断；
- VLM 返回 `need_more_context`；
- 新设备或新管材使固定类模型置信度整体下降。

视觉提示尤其适合小样本管网缺陷，但原型必须来自目标设备和工况，并包含难负例；仅给一个漂亮正例通常会过拟合外观。

### 6.2 WeDetect-Uni：候选召回与历史视频检索

WeDetect-Uni 冻结整个检测器，只微调 objectness prompt 来检索通用对象候选。它生成的区域 embedding 还能对历史视频做对象级检索。项目价值有两点：

- 与固定类 YOLO26 并行，查找“像目标但无法归入已知类”的区域；
- 专家确认一个新缺陷后，用其 embedding 回查历史视频，形成主动学习数据池。

### 6.3 YOLO-World 与 OW-OVD：成熟基线和未知类发现

YOLO-World 是验证实时开放词表检测的成熟基线；YOLOE 在提示方式、分割和转为标准 YOLO 等方面更贴近当前实现。OW-OVD 则进一步同时处理：

- 已知类零样本识别；
- 主动发现无法归入词表的未知对象；
- 新类别到来后的增量学习。

如果研究问题是“现有 17 类之外是否存在未建模异常”，OW-OVD 比单纯增加类别 prompt 更有意义。但未知对象召回不等于缺陷召回：泥水、反光和设备结构也会被视作未知对象，仍需语义与时序过滤。

## 7. 第三条路线：让 VLM/LLM 真正改进检测器权重

### 7.1 LLMDet：当前最直接的顶会依据

LLMDet 不是在推理后让 LLM 修改框，而是在训练阶段共同优化 detector 和 LLM：

- 图像级详细 caption 提供全局语义；
- 区域级 phrase 提供对象定位监督；
- grounding loss 使文本和区域对齐；
- caption generation loss 强化视觉语义表示；
- GroundingCap-1M 提供区域—文本训练数据。

官方 CVPR 论文报告，在其 Swin-T/B/L 与 LVIS 设定下，相比对应基线分别提升 3.3、3.8、14.3 AP，稀有类别 AP 分别提升 3.1、3.3、17.0。这里只能作为该训练方案有效性的证据，不能外推为管网 YOLO26 的预期增益。

迁移到本项目的轻量化版本不必共同训练完整 LLM：

1. 用强 VLM 离线为已标框生成可审核的缺陷属性短语；
2. 将专家修正后的短语与 ROI 建立对齐数据；
3. 在 YOLO neck/head 旁增加轻量文本对齐或属性蒸馏分支；
4. 训练结束后丢弃大 VLM，仅保留 YOLO 和小型属性头；
5. 用固定测试集验证 YOLO-alone 是否真的提升，而不是只报告 teacher-assisted 结果。

### 7.2 VL-PLM 与 VLM-PL：VLM 作为伪标签教师

[VL-PLM](https://arxiv.org/abs/2207.08954)（ECCV 2022）用类别无关 proposal 与 CLIP 相似度构造伪框，再训练检测器；[VLM-PL](https://openaccess.thecvf.com/content/CVPR2024W/CLVISION/html/Kim_VLM-PL_Advanced_Pseudo_Labeling_Approach_for_Class_Incremental_Object_Detection_CVPRW_2024_paper.html)（CVPR 2024 Workshop）用 VLM 验证类增量检测中的旧类伪标签。

两者给出的共同启示是：VLM 最有价值的训练角色不是自动替代人工标注，而是筛选未标注数据中的高价值候选。推荐闭环：

```text
YOLO/开放词表候选
  -> VLM 置信与语义一致性过滤
  -> 时序一致性过滤
  -> 人工只审核高不确定样本
  -> 写回训练集
  -> 重训 YOLO
```

必须报告伪标签 precision、人工审核量和最终 YOLO-alone 指标，否则无法判断收益来自更多数据、VLM 过滤还是测试时仍调用了大模型。

### 7.3 MIRAGE：VLM 参与工业异常数据生成

[MIRAGE](https://arxiv.org/abs/2603.13507) 是 CVPR 2026 VAND Workshop 工作，不是 CVPR 主会论文。它让 VLM 编写异常描述，图像生成器合成缺陷，CLIP 过滤样本，并结合 Grounding DINO 与 YOLO 分割特征产生掩膜。

对少样本管网缺陷有较高启发，但风险也最明显：

- 生成模型可能制造真实视频中不存在的纹理；
- 合成背景、光照和视角过于规则会带来捷径学习；
- 只在合成测试或混合测试集上提升没有说服力。

所有结论必须在完全独立的真实管网测试集上验证，并单独报告每个稀有类的召回和误报。

## 8. 第四条路线：域适应与长期迭代

### 8.1 TTAOD-F：部署时利用视觉—语言基础模型适应新域

TTAOD-F 是 NeurIPS 2025 Main Conference 工作，使用多模态提示 Mean-Teacher，在无源数据条件下对目标测试域进行参数高效适应，并维护高质量伪标签的动态实例记忆。

它对应管网中的真实难题：摄像头、照明、浑浊度、管材和压缩参数变化。适用前提是项目已经建立稳定 baseline 和域划分；否则在线更新可能把连续误检写入记忆，形成确认偏差。

验证时至少需要：

- 按设备、管径、材质或工况划分 domain，而不是随机切帧；
- 冻结测试标注，只用于离线评测；
- 报告适应前后 AP、灾难性遗忘、每帧更新成本和错误累积；
- 保留关闭在线更新的安全回退。

### 8.2 DGS：多域、多任务的持续适配器

DGS 提出 Cross-Domain Incremental Object Detection，使用动态任务分组和逐步合并 adapter，控制参数增长并平衡新域适应与旧域稳定性。它适合未来“每个城市、机器人或管材都是一个新域”的持续学习平台，但不适合作为首个 YOLO + VLM 原型的核心，因为其工程与评测复杂度明显更高。

### 8.3 2026 年邻近研究

- [Language Does Matter for Cross-Domain Few-Shot Visual Feature Enhancement](https://openaccess.thecvf.com/content/CVPR2026/html/Zhou_Language_Does_Matter_for_Cross-Domain_Few-Shot_Visual_Feature_Enhancement_CVPR_2026_paper.html)（CVPR 2026）：用图像 caption 模型和 LLM 构造图像级、域级语言描述，以轻量残差交叉注意力增强视觉特征；覆盖目标检测实验。它与“专家语言增强 YOLO 特征”的想法最接近，但需要确认公开代码和具体检测 backbone 后再评估移植成本。
- [Once for All: VLM-Based Domain-Generalized Object Detection](https://openaccess.thecvf.com/content/CVPR2026F/html/Zhang_Once_for_All_An_End-to-End_Paradigm_for_VLM-Based_Domain-Generalized_Object_CVPRF_2026_paper.html)（CVPR 2026 Findings）：通过域相关文本 embedding 和域正交风格学习获得域不变特征。它不是主会论文，也不是运行时大 VLM 复核方案，可作为域泛化损失设计参考。
- [FALCON-SFOD](https://openaccess.thecvf.com/content/CVPR2026/html/VCR_Foundation_Model_Priors_Enhance_Object_Focus_in_Feature_Space_for_CVPR_2026_paper.html)（CVPR 2026）：用基础模型先验增强源数据不可用时的目标聚焦与伪标签质量。它更偏 source-free adaptation，不应与通用 VLM 分类器混为一类。

## 9. 报告生成：有价值，但不是检测增益

### 9.1 Edge-Optimized VLM for Underground Infrastructure

[Edge-Optimized Vision-Language Models for Underground Infrastructure Assessment](https://arxiv.org/abs/2602.03742) 将 RAPID-SCAN 产生的掩膜连同图像输入微调后的 Phi-3.5 Vision，让模型描述缺陷状况、位置、严重程度和影响。VLM 确实接收图像与掩膜，而不只是读取文字结果；但论文链路是“已有掩膜 → VLM 解释”，没有证据表明 VLM 回流改善 RAPID-SCAN 的检测框或掩膜准确率。

因此它应被引用为：

- 边缘 VLM 微调与量化案例；
- 掩膜引导的缺陷解释；
- 结构化评估/报告生成。

不应被引用为“VLM 提高 YOLO 识别率”的证据。

### 9.2 PipeInspect Agent

[PipeInspector / PipeInspect Agent](https://doi.org/10.1016/j.autcon.2026.106942) 的主要价值是把增强、检测、分割、深度、查询和报告工具组织为智能体工作流。它说明 agent 可以承担工具编排和报告闭环，但不能据此推导“agent 或 LLM 自动提高检测 mAP”。检测增益必须来自某个明确模块和对应消融。

建议报告输入固定为：

```json
{
  event_id: E-00042,
  track_id: 137,
  canonical_label: crack,
  detector_score: 0.61,
  review_score: 0.84,
  review_decision: accept,
  evidence_frames: [1820, 1827, 1834],
  mask_uri: ...,
  measured_geometry: {
    source: calibrated_stereo,
    length_mm: 46.2,
    uncertainty_mm: 3.8
  },
  needs_human_review: false
}
```

LLM 可以改写措辞和组织章节，但不能修改这些事实字段。

## 10. 为什么 Generalist YOLO 不是当前优化主线

Generalist YOLO 的贡献是用共享编码器和主次协同注意力统一检测、实例/语义/全景分割与图像描述，并减少多任务 decoder 的计算量。它回答的是“怎样高效地让一个模型完成多种视觉和语言任务”。

它没有解决当前最关键的三个问题：

- 不用外部预训练 VLM/LLM 复核 YOLO 候选；
- 不通过文本或视觉 prompt 补充新缺陷类别；
- 不建立 VLM 教师到固定类 YOLO 的伪标签/蒸馏闭环。

因此可把它放在“多任务共享骨干与端到端报告”相关工作中，但论文方法主线更应对齐 WeDetect-Ref、ViTPrompt、LLMDet 和 SRA-Det。

## 11. 项目优先级与落地建议

### P0：先证明最终系统真的更准

1. 固定现有 YOLO26 权重和测试集；
2. 加入 tracker，并按轨迹选择代表帧；
3. 用一个可替换的 VLM reviewer 接口，输入 ROI、上下文、3 帧、专家定义和难负例；
4. 只审核低置信、易混类别和模型分歧候选；
5. 输出固定 JSON Schema，并允许 `abstain`；
6. 对比 YOLO-only 与 YOLO+reviewer 的事件级 precision、recall、macro-F1、ECE 和延迟。

VLM 应通过同一接口替换，避免把“换模型”和“换 prompt/阈值/样本选择”混为一个变量。首轮可比较 2–3 个规模不同的 VLM，但不应在没有项目数据时预设哪一个必然最好。

### P1：补完全漏检

加入 YOLOE 或 WeDetect-Uni 作为第二候选源，评测：

- YOLO26 candidate AR；
- 开放词表分支单独 candidate AR；
- 候选并集 AR；
- 并集经过 VLM、时序和规则过滤后的最终 precision；
- 新增候选的平均计算开销。

如果只报告最终 mAP，无法判断收益来自补召回还是复核误检。

### P2：把审核结果蒸馏回 YOLO

建立离线数据闭环：

1. 收集 reviewer 高置信改判、拒识和分歧样本；
2. 人工审核高信息量样本；
3. 生成区域属性短语和难负例对；
4. 用伪标签/属性对齐/合成数据训练新的 YOLO；
5. 推理时完全关闭 VLM，评测 YOLO-alone；
6. 再叠加在线 reviewer，分离两部分增益。

### P3：跨域适应与长期学习

只有在多设备、多地点数据足够且域划分明确后，再引入 TTAOD-F/DGS。否则复杂的在线更新会掩盖数据泄漏、阈值漂移和 tracker 错误。

## 12. 必做实验矩阵

| 编号 | YOLO | Tracker | 第二候选源 | VLM 复核 | 离线蒸馏 | 目的 |
|---|---:|---:|---:|---:|---:|---|
| E0 | ✓ |  |  |  |  | 帧级基线 |
| E1 | ✓ | ✓ |  |  |  | 时序聚合增益 |
| E2 | ✓ | ✓ |  | ✓ |  | 只测复核误检/改类 |
| E3 | ✓ | ✓ | ✓ |  |  | 只测候选补召回 |
| E4 | ✓ | ✓ | ✓ | ✓ |  | 完整在线系统 |
| E5 | 重训后 | ✓ |  |  | ✓ | 只测 YOLO 权重是否改进 |
| E6 | 重训后 | ✓ | ✓ | ✓ | ✓ | 最终系统上限 |

每组至少报告：

- detection：mAP50-95、每类 AP、candidate AR、误检数；
- event/track：事件级 precision、recall、macro-F1、重复事件率、轨迹稳定性；
- reviewer：改正率、错误改判率、覆盖率、abstain 率、ECE/Brier score；
- efficiency：YOLO FPS、VLM 触发率、P50/P95 延迟、显存和每小时视频成本；
- reporting：结构化字段完整率、证据可追溯率、事实冲突率、人工修改率。

数据划分必须按管段、视频、设备或项目切分，不能把相邻帧随机分到训练和测试集，否则会产生严重时序泄漏。

## 13. 推荐阅读顺序

1. [WeDetect](https://openaccess.thecvf.com/content/CVPR2026/html/Fu_WeDetect_Fast_Open-Vocabulary_Object_Detection_as_Retrieval_CVPR_2026_paper.html)：先理解候选、检索和 LMM 分类如何解耦。
2. [ViTPrompt](https://openaccess.thecvf.com/content/CVPR2026/html/Qin_ViTPrompt_Training-Free_Prompt_Refinement_with_Visual_Tokens_for_Open-Vocabulary_Detection_CVPR_2026_paper.html)：理解视觉原型如何在测试时改善第二遍检测。
3. [LLMDet](https://openaccess.thecvf.com/content/CVPR2025/html/Fu_LLMDet_Learning_Strong_Open-Vocabulary_Object_Detectors_under_the_Supervision_of_CVPR_2025_paper.html)：理解语言监督怎样进入检测器训练。
4. [YOLOE](https://openaccess.thecvf.com/content/ICCV2025/html/Wang_YOLOE_Real-Time_Seeing_Anything_ICCV_2025_paper.html)：评估第二候选源和视觉提示的工程成本。
5. [SRA-Det](https://openaccess.thecvf.com/content/CVPR2026/html/Yang_SRA-Det_Learning_Omni-Grained_Open-Vocabulary_Detection_Beyond_Category_Names_CVPR_2026_paper.html)：设计细粒度专家属性和逻辑组合。
6. [TTAOD-F](https://proceedings.neurips.cc/paper_files/paper/2025/hash/88c3c482430a62d35e03926a22e4b67e-Abstract-Conference.html)：在基线稳定后研究跨域测试时适应。
7. [Generalist YOLO](https://openaccess.thecvf.com/content/WACV2025/html/Chang_Generalist_YOLO_Towards_Real-Time_End-to-End_Multi-Task_Visual_Language_Models_WACV_2025_paper.html)：作为多任务统一架构的对照，而非主要精度优化依据。

## 14. 最终判断

就当前项目而言，最有论文价值且风险可控的题目不是泛化地写“YOLO + VLM Agent”，而是更具体地定义为：

> 面向长尾管网缺陷的双候选源、轨迹级视觉—语言复核与可校准拒识框架，并通过审核数据闭环蒸馏回轻量检测器。

这一定义同时覆盖：

- YOLO26 的实时性；
- YOLOE/WeDetect-Uni 对完全漏检的补召回；
- WeDetect-Ref/ViTPrompt/SRA-Det 式语义和视觉原型复核；
- LLMDet/VL-PLM 式离线监督；
- tracker 的时序证据；
- 结构化量测和报告；
- 可清楚拆分的论文消融。

目前所有论文结果都只能说明方法在各自公开数据和实验条件下成立。是否能提高本项目的 YOLO26 或最终管网事件识别率，仍需通过上述固定测试集和实验矩阵验证。
