# 本机 Seed-VC · Intel NPU 实验版

在 Windows 电脑上独立运行翻唱服务，AstrBot 通过 `127.0.0.1` 调用。**DiT 歌声转换核心用 OpenVINO 在 NPU 上执行；人声分离、Whisper、音高提取和声码器仍使用 CPU。** 不是整条链路都在 NPU 上运行。

已在 Core Ultra X7 358H、32 GB 内存、Arc B390 / Intel AI Boost 上实测。OpenVINO 2026.4.1，NPU 驱动 32.0.100.4841。

| 实测项目 | 结果 |
| --- | --- |
| 真实 DiT 核心与稳定 PyTorch FP32 对照 | 128 帧相对误差约 0.075%；实际音频特征约 0.16% |
| 3 秒歌声，首次 10 步转换 | 约 198 秒，其中约 127 秒为首次转换/编译/校验 |
| 同一 3 秒歌声，缓存后 30 步转换 | 约 55 秒，NPU 核心计算合计约 4.9 秒 |
| 本机 HTTP 全流程：分离、NPU 转换、伴奏合成、下载 | 约 73 秒，返回 3 秒、24 kHz 单声道 WAV |

以上是公开示例片段，不是用户 MiniMax 音色的听感验收，不能直接外推整首歌耗时。CPU 环节和每次加载模型仍明显影响速度，长歌曲可能需要数分钟甚至更长；当前属于可用性实验版。没有进行真实 QQ 发送测试。

## 首次准备

解压本地服务 ZIP，保留 `local_seedvc/` 与 `cloud_seedvc/` 的相邻目录结构。也可直接使用完整 GitHub 仓库。

在终端切换到解压目录，安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 后执行：

```powershell
python local_seedvc/setup.py --runtime .runtime/local-seedvc
```

脚本创建独立 Python 3.10 环境，安装本地推理依赖，下载固定版本 Seed-VC 源码及公开权重。不改 AstrBot 的 Python 环境，不更新驱动。**首次要下载数 GB 文件，并占用数 GB 至十余 GB 磁盘；这与只有约几十 KB 的插件安装包是两回事。** 初次下载需要网络，推理权重准备后在本机执行，不需要 HF 账号或租用云端 GPU。

若 GitHub 需要你的本地代理，可指定 `--git-proxy http://127.0.0.1:你的代理端口`。不要把代理账号密码放进公开脚本。

检查设备是否实际能执行推理：

```powershell
.\.runtime\local-seedvc\.venv\Scripts\python.exe local_seedvc/diagnose.py
```

看到 `NPU` 且测试 `success: true` 才表示基础计算可用；这项小测试仍不等于完整翻唱验收。脚本不会擅自安装或升级 NPU 驱动。驱动与支持情况见 [Intel NPU 驱动](https://www.intel.com/content/www/us/en/download/794734/intel-npu-driver-windows.html) 和 [OpenVINO NPU 说明](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html)。

## 启动本机服务

```powershell
.\.runtime\local-seedvc\.venv\Scripts\python.exe local_seedvc/server.py --runtime .runtime/local-seedvc --device NPU --port 17860
```

服务只监听本机 `127.0.0.1`，首次启动会生成 `.runtime/local-seedvc/api-key.txt`，密钥不在屏幕或日志中打印。

在 AstrBot 插件配置中填写：

| 设置 | 内容 |
| --- | --- |
| `singing.base_url` | `http://127.0.0.1:17860` |
| `singing.api_key` | 从该运行目录的 `api-key.txt` 复制 |
| `singing.reference_url` | 通常留空，首次用 MiniMax/千问当前说话音色生成参考样本 |
| `singing.timeout_seconds` | 默认 1800，按歌曲长度调整，最高 3600 |

说话继续使用原来的 MiniMax/千问 API。固定参考样本在 AstrBot 插件数据目录缓存，本机版使用前 5 秒来控制上下文和首次编译大小。每条发文字还是声音仍由原模型决定，不设固定模式。

AstrBot 需直接运行在同一台电脑；Docker 内的 `127.0.0.1` 指向容器自身，其他电脑的 `127.0.0.1` 也不会连接到这里。跨机器部署需要另行配置网络连接，当前启动脚本不会向局域网或公网开放监听。

## 日常使用

- 启动后可在插件中 `/唱歌 音频直链`，或按已配置歌单名点唱。
- 歌曲会先由 Demucs 分离人声，再由 NPU/CPU 混合处理，最后混回原伴奏。首次分离会额外下载 Demucs 权重到运行目录 `torch-cache/`。
- 模型有首次导出/编译等待，之后复用 `models/dit-local-*.xml/bin` 与 `ov-cache/`，无需每次重新编译。不同输入长度可能生成新的固定形状缓存。
- 默认 30 步。启动参数 `--steps 10` 用于快速试验，质量需试听。输入已是清唱人声且不需要伴奏时可加 `--vocals-only`。
- 可将 `--device NPU` 改为 `GPU`，使用 Intel Arc 的 OpenVINO 后端；基础核心测试中 GPU 更快，但完整 GPU 翻唱未验收，不自动把 NPU 失败伪装成 GPU 成功。
- 日志在运行目录的 `last-worker.log`，上次转换计时在 `last-benchmark.json`。输入和中间文件在任务结束后清理，输出按共享服务的一小时保留规则处理。
- 停止终端中的服务即可停止接单；没有设置开机启动，也没有安装 Windows 服务。任务并发与取消沿用共享 API。

## 适配与来源

使用 [Seed-VC 官方仓库](https://github.com/Plachtaa/seed-vc)固定提交 `51383efd921027683c89e5348211d93ff12ac2a8`，上游 GPL-3.0 源码和许可保留在运行目录。准备脚本对独立副本作三项兼容改动：CPU Whisper 用 FP32、上下文缩短至 10 秒、缓存长度改为 1024。使用该机验证稳定的单线程 PyTorch 基准，并关闭有数值异常的旧版快速注意力路径。

OpenVINO 适配在导出时固定帧数并保留实际长度掩码，补齐部分不会参与注意力；首次使用真实特征校验，出现非有限值或相对误差超过 3% 就失败，不发送错误结果。NPU 扩散步数和长歌质量仍需要在实际素材上检查。

本地封装代码使用根目录 MIT 许可，Seed-VC、Demucs、各模型与依赖分别遵循上游许可。这里复用 `cloud_seedvc/app.py` 的任务 API，不会连接 HF、Replicate 或其他云端推理服务。
