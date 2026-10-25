# 语音与角色翻唱 · AstrBot 插件

给已经能聊天的 QQ 机器人增加说话与指定歌曲翻唱。复用现有语言模型、人格和记忆，**每条由模型自主决定发文字还是声音；声音实际发送成功后不再重复发文字。** 不需要 DeepSeek API 或另一套聊天模型。

当前版本 **0.4.0**，面向 QQ OneBot v11（`aiocqhttp`）。默认说话音色为 MiniMax **萌萌女童 `lovely_girl`**。插件本机只需 Python 依赖 `httpx`，不安装 PyTorch、Seed-VC 或任何角色权重。

## 下载安装

从 [v0.4.0 Release](https://github.com/Xtdzs/astrbot_plugin_dsh_voice/releases/tag/v0.4.0) 下载：

- **`astrbot_plugin_dsh_voice-v0.4.0.zip`**：在 AstrBot WebUI → 插件 → 安装插件 → 文件上传，直接选择，无需解压。
- **`seedvc-cloud-v0.4.0.zip`**：独立云端 GPU 服务部署包，翻唱时需要；不要导入 AstrBot，不会随插件安装其依赖。

也可通过插件安装页的 URL 入口填写本仓库地址 `https://github.com/Xtdzs/astrbot_plugin_dsh_voice`。保存配置并重载，在当前模型/人格中允许使用本插件的工具。原模型/执行器需支持工具调用，才能自行选择声音；手动指令可用于检查音频配置。

升级直接上传新版插件 ZIP。插件标识保留旧名以支持覆盖升级；旧 DSH、固定回复模式、Replicate/RVC 设置不再读取。0.4.0 删除了 RVC 和 Replicate 调用；旧版 RVC 模型 ZIP 与 Token 无需迁移，翻唱改配下文的独立服务。

## 先跑通说话

| 配置 | 内容 |
| --- | --- |
| `speech.provider` | `minimax` |
| `minimax.api_key` | 你的 MiniMax API Key，也可设置 `MINIMAX_API_KEY` |
| `minimax.base_url` | 默认 `https://api.minimax.cn`，按账户地域调整 |
| `minimax.speech_model` | 默认 `speech-2.8-turbo` |
| `minimax.voice_id` | 默认 `lovely_girl`（萌萌女童） |
| `speech.speed` | 默认 `1.0`；`0.7` 会明显更慢 |

测试 `/说话 嘿嘿，你终于来啦！`。说话本身不需要翻唱服务。

千问可将 `speech.provider` 改为 `qwen`，填写 `qwen.api_key`（或环境变量 `DASHSCOPE_API_KEY`），默认 `qwen3-tts-flash` 与 `Cherry`；接口地址和 Key 需匹配北京/新加坡地域。这些是语音模型，聊天模型仍使用 AstrBot 原配置。

## 让说话音色唱指定歌曲

流程：**歌曲音频 → 云端分离人声 → Seed-VC 参考说话音色转换歌声 → 混回原伴奏 → QQ 发送**。云端固定保留原曲调性和速度，避免与伴奏失配。效果是音色转换，不保证高音、咬字或身份相似度百分之百一致，需要先试听。

Seed-VC 是 [Plachtaa 的开源项目](https://github.com/Plachtaa/seed-vc)，**不是字节跳动官方产品**。不需要训练角色模型或寻找 RVC ZIP。

### 部署一次云端服务

解压独立云端包，在 Hugging Face 创建 **Docker Space**，上传包内文件，在设置中选择付费 CUDA GPU 并添加 Secret `COVER_API_KEY`。推荐从 24 GB 显存配置开始验证。也可部署到其他提供 HTTPS 的云端 GPU 主机。

完整步骤见 [云端部署说明](cloud_seedvc/README.md)。**这是需要自行部署的服务模板，不是已替你开通的公共 API；填 MiniMax Key 不能单独完成翻唱。** 云端镜像构建、真实 GPU 推理、听感及 QQ 实际发送尚未验收，当前以预发布方式提供。租用 GPU 通常按运行时长计费，空闲也可能收费，应设置休眠或暂停。

| 插件配置 | 内容 |
| --- | --- |
| `singing.base_url` | 部署后的 HTTPS 地址，如 `https://你的空间.hf.space` |
| `singing.api_key` | 云端设置的 `COVER_API_KEY`；也可设置 `SINGING_API_KEY` |
| `singing.reference_url` | 通常留空，自动用当前说话音色生成并缓存样本 |
| `singing.timeout_seconds` | 默认 `1800`，含排队、分离、转换和合成 |

翻唱服务密钥和 MiniMax Key 是两个不同的密钥。这里使用本仓库的 HTTP 服务协议，**不能直接填写作者的 Gradio 演示地址**。

留空参考直链时，第一次翻唱会调用当前 MiniMax/千问说话 API 生成样本，后续复用；换音色会重新生成，样本缓存在插件数据目录 `voice-reference/`。也可配置干净的单人参考录音，云端仅使用前 25 秒。使用的声音需获准用于转换；MiniMax 预置音色的第三方转换使用范围以其条款为准。

### 指定歌曲和歌名点唱

先用一段短歌的公网音频直链测试：

```text
/唱歌 https://你的存储/歌曲片段.mp3
```

需要真实音频文件，不是下载页面、分享页或音乐网页。当前不包含搜歌或自动下载歌曲服务。

在 `cover.songs` 列表中每项填 `歌名 | 音频直链`，例如：

```text
我的第一首歌 | https://你的存储/song.mp3
```

之后可以直接说“唱一下我的第一首歌”，模型能先查歌单再调用翻唱；手动也支持 `/唱歌 我的第一首歌`。歌名按歌单精确匹配，版本不同请分别命名。歌曲需有权处理，每个输入最多 40 MB，时长 1–600 秒。

## 模型自主选择发送形式

配置页只管理接口、声音、歌单和调用限制，不提供固定文字/语音模式。普通文字回复不会调用 TTS，下一条回复重新选择，不继承上一条的发送形式。

| 工具 | 作用 |
| --- | --- |
| `voice_speak` | 模型根据语境选择开口说话，参数是真正要说的内容 |
| `voice_list_songs` | 查询可点唱的准确歌名，不发送音频 |
| `voice_sing_cover` | 用歌单名或真实音频直链翻唱 |
| `voice_compose_song` | 可选新歌生成，需启用且有 MiniMax 历史音乐 API 权限 |

插件为现有模型补充发送规则，不改变原人格。声音实际送出后抑制重复正文/成功确认，保留原有图片等非文字媒体。重复相同工具调用会去重，部分语音已送出时不会从头重复。流式正文暂存至本轮选择确定后再投递，避免先发文字后又发相同声音。失败时交回现有模型自然回答，不假装成功。

### 可选：生成新歌

**MiniMax 自 2026-08-20 起不再向新用户开放音乐付费 API，历史有权限用户可继续使用。** 因此 `music.enabled` 默认关闭。仅有历史权限时开启，使用 `/写歌 关于秋天的轻快民谣` 或 `/写歌 曲风 | 歌词`。这一路不保证采用说话音色，不能替代指定歌曲翻唱。

## 常用指令与设置

| 指令/配置 | 用途 |
| --- | --- |
| `/说话 内容` | 手动测试说话 |
| `/唱歌 歌名或音频直链` | 指定歌曲翻唱 |
| `/voice 歌单` | 查看可点唱歌曲 |
| `/voice 状态`、`/voice 帮助` | 配置填写状态及帮助 |
| `music.delivery = file/record` | 歌曲发文件或 QQ 语音，默认文件；长语音受适配器限制 |
| `limits.allowed_users` | 限制可使用付费功能的 QQ 号，空列表表示不限 |

`/voice 说/唱/写歌` 与旧 `/dsh` 前缀仍兼容。旧 `/voice 文字/语音/默认` 只解释自主选择，不再保存模式。

每段朗读默认最多 500 字，千问最多 600 字；总上限默认 2000 字，超出明确失败并交回模型，不悄悄截断。MiniMax 和本云端翻唱服务返回 WAV，无需本机 FFmpeg；其他语音 API 返回非 WAV 时，需系统 FFmpeg 转码或改用文件发送。

生成音频缓存默认保留 24 小时，由后续调用清理；固定参考样本长期复用，可手动删除 `voice-reference/` 强制重建。同一消息的重复工具调用不会再次计费；网络中断造成云端提交结果未知时，不会自动重试。卸载/超时会尝试取消正在运行的翻唱任务，取消失败时需在云端核查。

不要同时开启多个自动配音插件，以免重复发送。真实选择频率由现有语言模型/人格决定；外部执行器是否触发标准 AstrBot 回复钩子，需要在实际环境检查。

## 开发与验证

```sh
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python scripts/package_plugin.py
```

插件依赖只有根目录的 `requirements.txt`。如要运行独立云端 HTTP 协议测试，可额外安装 `fastapi` 与 `python-multipart`；模型推理不参与离线测试。测试覆盖消息发送、歌单、参考缓存、鉴权、任务取消及失败路径，**不等于真实 GPU/QQ/付费 API 验收**。

打包采用明确白名单，插件 ZIP 不包含云端依赖、GPU 模型、用户配置、密钥、音频、测试或运行时。独立云端 ZIP 仅包含部署源码与说明。插件采用 MIT 许可，上游代码/模型/服务各自遵循对应许可。

## 参考

- [AstrBot ZIP 安装](https://docs.astrbot.app/use/webui.html#插件) / [工具注册](https://docs.astrbot.app/dev/star/guides/ai.html)
- [MiniMax 语音](https://platform.minimax.cn/docs/api-reference/speech-t2a-http) / [音乐及权限公告](https://platform.minimax.cn/docs/api-reference/music-generation)
- [千问 TTS](https://help.aliyun.com/zh/model-studio/qwen-tts-api)
- [Seed-VC](https://github.com/Plachtaa/seed-vc) / [Demucs](https://github.com/facebookresearch/demucs)
- [Hugging Face Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker) / [GPU Spaces](https://huggingface.co/docs/hub/spaces-gpus)
