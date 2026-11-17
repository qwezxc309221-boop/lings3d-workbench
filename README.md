# 双目采集与三维重建工作台

用于有线双目相机采集、RTAB-Map 建图和 LingBot-Map 离线重建。页面支持左右预览、扫描保存、双目融合、点云回放及导出。

## 开始使用

先看 [使用说明](docs/getting-started.md)。需要准备 Linux、NVIDIA GPU、Docker、Python 环境和 LingBot-Map 权重。安装包不包含权重、扫描数据和已配置的运行环境。

已有环境时，在项目根目录执行：

```bash
cp .env.example .env
# 编辑 .env，填写 Python 和权重路径。
./scripts/check.sh
./workbench/start.sh
```

打开 <http://localhost:8080>。停止工作台用 `./workbench/stop.sh`。

## 文件校验

解压后可用 `sha256sum -c SHA256SUMS` 校验包内文件。

## 目录

| 目录 | 内容 |
|---|---|
| `workbench/` | 网页、采集、融合、结果查看和测试 |
| `docker/` | ROS Melodic 镜像、启动脚本和相机配置 |
| `third_party/lingbot-map/` | 模型源码、演示及基准评估 |
| `scripts/` | 环境设置、检查和低显存启动器 |
| `docs/` | 安装、操作及验证说明 |

## 使用范围

当前流程是采集完成后重建。工作台双目融合尚无全局回环、多段自动拼接或连续表面输出。RTAB-Map 是独立建图入口。点云 PLY/GLB 不一定包含三角面，测量精度需要用实物核验。

相机地址、ROS 话题及容器名称按原设备配置设置；使用其他设备前需修改对应配置。详情见[部署说明](docs/deployment.md)。

来源、许可及本地修改见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。检查结果见 [docs/validation.md](docs/validation.md)。
