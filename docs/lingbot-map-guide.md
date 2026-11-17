# 视频与图片重建

无需相机也能使用 LingBot-Map demo。先按[使用说明](getting-started.md)安装依赖、下载权重并设置 `.env`。

从项目根目录执行：

```bash
source scripts/environment.sh
cd third_party/lingbot-map
"$LINGS_PYTHON" ../../scripts/run_demo_lowmem.py demo.py \
  --model_path "$LINGS_MODEL" \
  --video_path /absolute/path/to/video.mp4 --fps 3 --first_k 60 \
  --image_size 350 --use_sdpa --num_scale_frames 2 \
  --kv_cache_sliding_window 16 --keyframe_interval 8 \
  --camera_num_iterations 1 --offload_to_cpu --port 8083
```

把视频路径改为自己的文件。输入图片序列时，将 `--video_path ... --fps 3 --first_k 60` 换成 `--image_folder /absolute/path/to/images`。首次建议仍限制输入数量以检查资源占用。

推理成功输出地址后访问对应端口。8083 用于避开工作台端口，Ctrl+C 结束前台演示。原 demo 可能先完成视频抽帧再选择帧数，长视频需要额外磁盘空间。

该入口使用单目多帧，不直接消费双目标定；物理尺度需要额外参考。ROS 采集和外部双目融合使用[工作台](workbench-guide.md)。

完整参数见[上游说明](../third_party/lingbot-map/README.md)。
