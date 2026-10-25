---
title: Seed-VC Cloud Cover
emoji: 🎵
colorFrom: pink
colorTo: blue
sdk: docker
app_port: 7860
---

# Seed-VC 云端翻唱服务

这是 AstrBot 语音插件的**独立云端部署包**，不是插件自身的依赖。它在云端完成 Demucs 人声分离、Seed-VC V1/F0 歌声转换和伴奏合成，返回可直接发送到 QQ 的 WAV。无需 Replicate、RVC 模型 ZIP，也不调用 MiniMax 音乐 API。

## 在 Hugging Face 部署

1. 登录 [Hugging Face](https://huggingface.co/new-space)，创建 Docker Space（Blank），上传本目录的 `Dockerfile`、`app.py`、`requirements.txt` 和本 `README.md` 到空间根目录。
2. 选择 **Public**，由本服务的密钥保护任务和音频。空间公开的是部署源码；音频只保存在运行容器内，下载需密钥。若改为 Private，额外的 HF 访问鉴权需要自行配置网关；当前插件只支持服务自己的 Bearer 密钥。
3. 在 Space Settings → Variables and secrets 添加 **Secret**：`COVER_API_KEY`，值为你自己生成的长随机密钥，不要填 MiniMax Key，不要把它写进源码或 README。可在自己的电脑执行 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成，勿公开输出。
4. 选择有 CUDA 的付费 GPU，建议从 24 GB 显存配置开始验证。CPU Basic 不适用于本部署包。构建和首次任务需要下载依赖及模型，第一次会慢。**本包未进行真实 GPU 构建或听感验收，显存与速度以你的云端测试为准。**
5. 等空间 Running，在插件配置中填写 `singing.base_url = https://你的空间.hf.space`，`singing.api_key` 填相同的 `COVER_API_KEY`。这不是作者原始 Gradio 演示的接口，不可直接填 `plachta-seed-vc.hf.space`。
6. 保存并重载插件，用一首短歌的音频直链测试 `/唱歌 https://你的存储/片段.mp3`。空间 Swagger 页面 `/docs` 可用 Authorize 输入相同密钥测试 `/v1/health`；`ready: true` 表示检测到 GPU 与源码，不代表权重已下载或听感已通过验收。

GPU 空间通常按运行时长计费，空闲时也可能收费；请在平台设置休眠或暂停，不要理解为按每首歌固定收费。配置方式与当前费用见 [GPU Spaces 官方说明](https://huggingface.co/docs/hub/spaces-gpus)。插件不会替你租用机器、创建账号或开通计费。

也可在支持 CUDA 容器的其他云端 GPU 主机部署：构建此目录的镜像，运行时注入 `COVER_API_KEY`，通过 HTTPS 反向代理暴露端口 7860。仅启动一个 Uvicorn worker，内存任务队列不能跨进程共享。

## 行为与限制

- 每个输入文件最多 40 MB，歌曲与参考音频时长 1–600 秒；参考声音只用前 25 秒。建议干净的单人语音，不含音乐或噪声。声线、歌词、音准和高音效果需要试听，无法保证与说话百分之百一致。
- 固定使用 Seed-VC 的 F0 歌声模型，保留原曲速度与调性；自动调 F0 和移调关闭，避免与原伴奏失配。每个任务串行运行分离和转换以释放模型显存，但进程启动/加载模型会增加等待时间。
- 最多两个排队/处理中任务，单 GPU 逐个处理；任务总超时 1 小时。插件超时/卸载会尝试取消任务并结束子进程，网络中断时仍需查看云端状态。
- 成功后清除输入和中间文件，结果保留至下一次请求触发一小时过期清理。任务状态与文件在空间重启时丢失。生产服务如需更多并发或持久队列，应另作部署。
- 请求体由 Web 框架解析为临时文件后，应用再执行 40 MB 检查；公开部署建议在网关额外设置上传大小限制。密钥不能发给 QQ 群成员，插件 `limits.allowed_users` 可限制付费功能使用者。
- 使用你拥有处理权限的歌曲和可用于音色转换的参考声音。MiniMax 预置音色的第三方转换使用范围以提供方条款为准；有自有授权角色录音时可直接配置参考直链。

## API 协议 `seedvc-cover-v1`

所有 `/v1/` 请求使用 `Authorization: Bearer COVER_API_KEY`，包括音频下载。

- `GET /v1/health` → `{"protocol":"seedvc-cover-v1","ready":true}`
- `POST /v1/jobs`：multipart `song` 与 `reference` 文件 → `{"id":"32位十六进制ID","status":"queued"}`
- `GET /v1/jobs/{id}`：状态 `queued/processing/succeeded/failed/canceled`
- `GET /v1/jobs/{id}/audio`：成功后下载单声道 24 kHz PCM WAV，歌曲含伴奏
- `DELETE /v1/jobs/{id}`：尝试取消排队/运行任务

## 来源与许可

[Seed-VC](https://github.com/Plachtaa/seed-vc) 是 Plachtaa 的开源项目，**不是字节跳动官方产品**。部署固定上游提交 `51383efd921027683c89e5348211d93ff12ac2a8`，保留 GPL-3.0 源码和许可证，调用其命令行程序，不将权重或代码塞入 AstrBot 插件。人声分离使用 [Demucs](https://github.com/facebookresearch/demucs)。服务封装使用本仓库的 MIT 许可；上游代码、模型、训练数据与生成内容各自遵循对应许可和使用条件。
