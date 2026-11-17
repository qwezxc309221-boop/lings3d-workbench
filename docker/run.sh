#!/bin/bash
# 启动/进入 lings3d 容器（主机网络 + X11 图形界面 + NVIDIA 显卡加速）
cd "$(dirname "$0")"
xhost +local:docker >/dev/null 2>&1
if docker ps -a --format '{{.Names}}' | grep -qx lings3d; then
  docker start lings3d >/dev/null
  exec docker exec -it ${ROS_IP:+-e ROS_IP=$ROS_IP} lings3d /entrypoint.sh bash
fi

# 主机 X 由 NVIDIA 驱动渲染，容器内的 Mesa 无法与之配合（RViz 报 GLXContext 错误），
# 因此把主机当前版本的 NVIDIA GLX 库挂进容器
GPU_ARGS=()
NV_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
L=/usr/lib/x86_64-linux-gnu
if [ -n "$NV_VER" ] && [ -f "$L/libGLX_nvidia.so.$NV_VER" ]; then
  for d in /dev/nvidia0 /dev/nvidiactl /dev/nvidia-modeset; do [ -e $d ] && GPU_ARGS+=(--device $d); done
  GPU_ARGS+=(-v "$L/libGLX_nvidia.so.$NV_VER:/usr/lib/nvidia-host/libGLX_nvidia.so.0:ro")
  for f in libnvidia-glcore libnvidia-tls libnvidia-glsi libnvidia-gpucomp; do
    [ -f "$L/$f.so.$NV_VER" ] && GPU_ARGS+=(-v "$L/$f.so.$NV_VER:/usr/lib/nvidia-host/$f.so.$NV_VER:ro")
  done
  GPU_ARGS+=(-e __GLX_VENDOR_LIBRARY_NAME=nvidia -e LD_LIBRARY_PATH=/usr/lib/nvidia-host)
  echo "使用 NVIDIA 驱动 $NV_VER 进行图形渲染"
else
  echo "警告: 未检测到 NVIDIA 驱动，RViz 可能无法显示"
fi

exec docker run -it --name lings3d --net=host --ipc=host \
  -e DISPLAY="$DISPLAY" -e QT_X11_NO_MITSHM=1 ${ROS_IP:+-e ROS_IP=$ROS_IP} \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw --device /dev/dri \
  "${GPU_ARGS[@]}" \
  -v "$PWD/catkin_ws/src:/root/catkin_ws/src" \
  -v "$PWD/entrypoint.sh:/entrypoint.sh:ro" \
  -v "$PWD/rviz:/root/.rviz" \
  lings3d:melodic bash
