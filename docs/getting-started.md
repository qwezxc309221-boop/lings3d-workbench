# 使用说明

这份代码包含两条流程：中文工作台负责双目采集与 LingBot-Map 重建；RTAB-Map 用于独立的双目建图。首次运行建议先完成代码检查，再连接相机。

## 1. 准备环境

原项目运行环境为 Ubuntu 22.04、RTX 5060 Laptop 8GB、约 16GB 内存，ROS Melodic 在 Docker 内运行。这里只列原设备配置，不代表所有相同显存设备都已验证。

主机需要 Python 3.10 及以上和支持 CUDA 的 PyTorch。原项目使用已有 conda 环境 `pytorch`；环境本身未打包。PyTorch、torchvision 应按显卡和驱动安装兼容版本。安装好后，在项目根目录运行：

```bash
conda activate pytorch
python -m pip install -e './third_party/lingbot-map[vis]'
python -m pip install -r workbench/requirements.txt
```

模型安装项不包含 PyTorch；请先安装合适的 CUDA 版本。原项目的版本参考为 torch 2.8.0+cu128、torchvision 0.23.0+cu128，接收方仍需核对驱动兼容性。

## 2. 下载权重并设置路径

从 [LingBot-Map 模型页](https://huggingface.co/robbyant/lingbot-map)下载 `lingbot-map.pt`。权重约 4.63GB，不在压缩包里。

```bash
cp .env.example .env
```

用文本编辑器打开 `.env`，把 `LINGS_PYTHON` 和 `LINGS_MODEL` 改为自己电脑的绝对路径。

| 变量 | 用途 |
|---|---|
| `LINGS_PYTHON` | 主机 Python 可执行文件 |
| `LINGS_MODEL` | `lingbot-map.pt` 路径 |
| `LINGS_ROS_ROOT` | 宿主机 ROS 包目录，必须与容器挂载对应 |

启动、停止和检查脚本都会读取 `.env`。不设置时，Python 优先使用已激活 conda 环境，否则尝试 `~/miniconda3/envs/pytorch/bin/python`；ROS 包默认使用项目内 `docker/catkin_ws/src/lings_3d`。

## 3. 先检查代码

需要 Node.js、Bash 和上述 Python 依赖。在项目根目录执行：

```bash
./scripts/check.sh
```

检查包含现有自动测试，不连接相机，也不执行模型推理。出现 `flashinfer not available` 时可使用 SDPA，不能仅据此判断测试失败；以退出码和测试结果为准。

## 4. 准备 ROS 容器

在主机执行：

```bash
docker build -t lings3d:melodic docker
./docker/run.sh
```

容器名固定为 `lings3d`。若已有同名容器，脚本会进入该容器，挂载仍是原设置；此时必须把 `.env` 的 `LINGS_ROS_ROOT` 指向该容器实际使用的宿主 ROS 包目录。查看挂载可用：

```bash
docker inspect lings3d --format '{{json .Mounts}}'
```

不要用空目录覆盖已有扫描。首次部署、相机配置和图形环境要求见[部署说明](deployment.md)。没有相机时可以先检查代码或运行[视频演示](lingbot-map-guide.md)。

## 5. 打开工作台

```bash
./workbench/start.sh
```

浏览器打开 <http://localhost:8080>，按“连接相机 → 新建扫描 → 开始拍摄 → 结束并保存 → 开始重建”操作。默认每秒保存 3 对校正图，重建前至少需要 2 帧。预览接收帧率不等于保存或推理速度。

推理成功后可以回放和导出点云。再次打开已保存的结果不需要重新推理。详细操作见[工作台指南](workbench-guide.md)。

停止工作台：

```bash
./workbench/stop.sh
```

若 8080 上已有工作台，启动脚本会打开已有服务。需要切换代码目录时先保存任务并停止旧服务。

## 常见问题

- 找不到 Python：检查 `.env`，或先激活对应 conda 环境。
- 权重不存在：检查 `LINGS_MODEL`，包内没有附带权重。
- 相机连接失败：检查供电、有线地址、设备 IP 和 ROS 话题。
- 容器内有图但宿主读不到：核对 `LINGS_ROS_ROOT` 与容器挂载。
- CUDA 显存不足：关闭其他 GPU 任务，先用短序列及 350 输入验证。
- 模型有空洞或重影：先检查图像质量、画面重叠、双目匹配和位姿；平滑或补洞不能保证修正尺寸。

这份包已完成代码检查及现有自动测试，尚未在接收方电脑完成安装、相机和 GPU 验证。
