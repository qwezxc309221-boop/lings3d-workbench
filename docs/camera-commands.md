# 相机与 ROS 命令参考

以下 ROS 命令在 `lings3d` 容器中运行；网卡命令在宿主机运行。相机网页为 <http://192.168.0.12/webview.html>。

## 查看与建图

```bash
roslaunch lings_3d multimaster_start.launch
# 另开一个容器终端，选择下面一个入口：
roslaunch lings_3d lings3d_test.launch
roslaunch lings_3d lings3d_mapping.launch
```

查看与建图启动文件都会使用双目处理链路，不要同时运行。继续地图使用默认参数；只定位用 `localization:=true`。默认数据库为 ROS 包下 `maps/rtabmap.db`；使用 `new_map:=true` 会清空指定数据库，先备份再操作。

## 标定参考

项目已从相机读取标定，不需例行重复。确定需要重新标定时，按实际棋盘规格修改以下参数：

```bash
rosrun camera_calibration cameracalibrator.py --size 6x8 --square 0.04 \
  left:=/sensor_stereo_2/left/image_raw \
  left_camera:=/sensor_stereo_2/left \
  right:=/sensor_stereo_2/right/image_raw \
  right_camera:=/sensor_stereo_2/right --no-service-check
```

## 立体匹配调节

单独实验时启动，避免与已运行的处理节点重名：

```bash
ROS_NAMESPACE=sensor_stereo_2 rosrun stereo_image_proc stereo_image_proc
rosrun image_view stereo_view stereo:=/sensor_stereo_2 image:=image_rect_color
rosrun rqt_reconfigure rqt_reconfigure
```

原操作备忘录中的重启 NetworkManager、删除状态文件不作为常规恢复步骤。优先检查 IP，并按部署说明恢复既有有线连接。
