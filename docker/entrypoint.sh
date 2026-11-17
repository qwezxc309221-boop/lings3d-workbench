#!/bin/bash
source /opt/ros/melodic/setup.bash
# ROS_IP 选择：手动指定且存在于本机网卡 > 192.168.0.x(相机网段) > 有线网口 > 其他(如WiFi)
ips() { ip -4 -o addr | awk '{print $2, $4}' | cut -d/ -f1 | grep -v -e '^lo ' -e '^docker' -e '^br-' -e '^veth'; }
if [ -n "$ROS_IP" ] && ! ips | awk '{print $2}' | grep -qx "$ROS_IP"; then
  echo "警告: ROS_IP=$ROS_IP 不在本机网卡上，自动重新选择"; ROS_IP=
fi
if [ -z "$ROS_IP" ]; then
  ROS_IP=$(ips | awk '$2 ~ /^192\.168\.0\./ {print $2; exit}')
  [ -z "$ROS_IP" ] && ROS_IP=$(ips | awk '$1 ~ /^(en|eth)/ {print $2; exit}')
  [ -z "$ROS_IP" ] && ROS_IP=$(ips | awk '{print $2; exit}')
fi
export ROS_IP
case "$ROS_IP" in
  192.168.0.*) ;;
  *) echo -e "\033[1;31m警告: 本机没有 192.168.0.x 地址（当前 ROS_IP=$ROS_IP），连接不上相机！\033[0m"
     echo -e "\033[1;31m  请在主机终端执行: nmcli con up dobot-quad-wired ，然后 exit 并重新运行 run.sh\033[0m" ;;
esac
export ROS_MASTER_URI=${LINGS_MASTER_URI:-http://${ROS_IP}:11311}
export SVGA_VGPU10=0
if [ ! -f /root/catkin_ws/devel/setup.bash ]; then
  (cd /root/catkin_ws && catkin build) || echo "catkin build 失败"
fi
source /root/catkin_ws/devel/setup.bash 2>/dev/null
grep -q melodic /root/.bashrc || cat >> /root/.bashrc <<'RC'
source /opt/ros/melodic/setup.bash
source /root/catkin_ws/devel/setup.bash 2>/dev/null
RC
echo "ROS_IP=$ROS_IP  ROS_MASTER_URI=$ROS_MASTER_URI"
exec "$@"
