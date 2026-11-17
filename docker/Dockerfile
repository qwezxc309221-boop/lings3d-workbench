ARG BASE=docker.m.daocloud.io/osrf/ros:melodic-desktop-full
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
# 旧 ROS apt 密钥已于 2025 年过期：先移除旧源，换新密钥后再加回
RUN rm -f /etc/apt/sources.list.d/ros*.list \
 && sed -i "s@http://\(archive\|security\).ubuntu.com@http://mirrors.tuna.tsinghua.edu.cn@g" /etc/apt/sources.list \
 && apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] https://mirrors.tuna.tsinghua.edu.cn/ros/ubuntu bionic main" > /etc/apt/sources.list.d/ros1.list \
 && apt-get update && apt-get install -y --no-install-recommends \
      ros-melodic-multimaster-fkie \
      ros-melodic-stereo-image-proc ros-melodic-camera-calibration \
      ros-melodic-image-view ros-melodic-rqt-reconfigure ros-melodic-rviz \
      ros-melodic-tf2-ros ros-melodic-rtabmap-ros \
      python-catkin-tools net-tools iproute2 mesa-utils \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
WORKDIR /root/catkin_ws
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
