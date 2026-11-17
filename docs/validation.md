# 验证记录

日期：2026-11-17 09:13（北京时间 UTC+8）。

在原开发机使用既有 pytorch 环境执行 `./scripts/check.sh`：

- 141 个 Python 文件通过语法解析，13 份文档的本地链接通过检查。
- 工作台 JavaScript、10 个 Shell 脚本及 ROS XML 配置通过检查。
- 现有 23 项自动测试通过，测试部分耗时 2.007 秒。
- 环境脚本在重新定位且含空格的目录中读取 `.env` 正常。
- 控制服务正确读取 Python、权重和 ROS 根目录覆盖设置。
- 第三方 LICENSE.txt、CITATION.cff 与原副本一致。

测试使用临时数据，不连接真实相机，不执行 GPU 推理。未在其他电脑完成安装验证，也未重新构建容器镜像或检查桌面图形显示。

原开发机版本：torch 2.8.0+cu128、torchvision 0.23.0+cu128、FastAPI 0.162.7、Uvicorn 0.54.0。测试输出中的 `flashinfer not available` 不影响通过；推理配置使用 SDPA。

压缩包包含源码与说明，不含权重、扫描、日志、缓存和环境。包内文件可用 `sha256sum -c SHA256SUMS` 核对。
