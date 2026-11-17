# 采集与重建工作台

安装与启动见[使用说明](../docs/getting-started.md)，日常操作见[工作台指南](../docs/workbench-guide.md)。

```bash
./workbench/start.sh
./workbench/stop.sh
```

`server.py` 管理任务，`ros_bridge.py` 采集图像，`infer_worker.py` 执行推理，`stereo_fusion.py` 与 `stream_fusion.py` 做双目融合，`viewer.py` 加载结果。`runtime/` 保存运行时日志和 PID，不随代码分发。

真实相机数据目录由 `LINGS_ROS_ROOT` 决定，必须与容器挂载一致。测试使用临时目录，不要将测试的 `LINGS_SCAN_ROOT` 用于生产采集。
