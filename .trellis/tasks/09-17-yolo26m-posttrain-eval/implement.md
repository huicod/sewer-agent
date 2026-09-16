# 执行顺序

1. 主Agent确认tmux退出码、训练曲线、数据评估入口；独立只读审计训练产物/checkpoint。
2. 复用Ultralytics validator和项目score_boxes运行冻结测试：mixed606、sewer562、SEDD44（classes=[0,3]）。保存标准曲线/指标；固定阈值另行逐图比较新旧模型。
3. 输出可下载的GT/old/new图片及HTML/JSON证据，按失败/成功展示且不改变测试集；做真实适配器单图/批量/卸载和相关单测。
4. 主Agent核对样本数/来源/类别/评价口径及图示，报告结论，Trellis与jj本地交接。无部署步骤。

验证集只用于复现已选best及观察过拟合；测试集不用于选择阈值/epoch。SEDD未标注其他三类不能计作这些类别FP；旧模型sewer结果不当作独立泛化证据。正常背景仅2张测试图，正常误报率不可充分估计。
