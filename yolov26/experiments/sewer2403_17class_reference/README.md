# Sewer2403 17 类实验：仅供参考

该目录不是完整可部署实验包。

已保存：

- `args.yaml`：训练参数；
- `train.log`：100 轮训练、最终验证和逐类指标日志。

尚未从原服务器同步：

- `weights/best.pt`；
- `weights/last.pt`；
- `results.csv`；
- 混淆矩阵、PR/F1 曲线和验证预测图。

原日志记录的服务器输出目录是：

```text
/root/autodl-tmp/yolov26/runs_detect/sewer2403_yolo26s_img960_e100-2/
```

如果原服务器上仍有该目录，应优先把整个目录补传回来，再计算哈希并更新顶层 `MANIFEST.md`。不要把旧 SEDD 三类模型误认为该 17 类模型。
