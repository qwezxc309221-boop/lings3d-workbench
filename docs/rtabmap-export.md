# RTAB-Map 导出

RTAB-Map 在容器中运行，导出窗口里的路径属于容器。只填写文件名时，文件通常留在当前目录；它不会自动出现在宿主桌面。

建议导出时填写：

```text
/root/catkin_ws/src/lings_3d/exports/scan.ply
```

首次部署时先确保该目录存在且可写。在默认挂载下，宿主机对应 `<项目>/docker/catkin_ws/src/lings_3d/exports/scan.ply`；已有容器按实际挂载查找。每次使用新文件名，避免覆盖上次结果。

PLY 可保存点云，也可保存三角网格。头部有 `element face` 才表示声明了面；只有顶点时仍是点云。需要表面时，在 RTAB-Map 导出界面启用对应网格化选项，具体选项随版本变化。

查看用 MeshLab 或 CloudCompare。MeshLab 清理、平滑和另存见[操作教程](meshlab-guide.md)。
