# 语音与角色翻唱 · AstrBot 插件

**给已经能聊天的 QQ 机器人加上说话和唱歌功能。** 复用你在 AstrBot 中配置好的语言模型、人格、记忆和消息触发规则。插件只负责声音，不再启动另一套聊天模型，不需要 DeepSeek API Key。

当前版本：**0.3.0**。主要面向 QQ 的 OneBot v11（`aiocqhttp`）链路。默认音色：**MiniMax 萌萌女童 `lovely_girl`**。

## 模型自己决定怎么回复

**每条消息由你现有的语言模型自主选择发文字、语音，还是唱歌。** 配置页由你填写 API、音色、地址和调用限制，里面没有固定“只发文字/只发语音”的开关。

| 模型本条的选择 | 插件执行的动作 |
| --- | --- |
| 文字 | 直接发送模型原本的文字回复，不调用 TTS |
| 语音 | 模型调用 `voice_speak`，插件合成并发送声音；不再重复发文字正文或成功确认 |
| 翻唱或创作歌曲 | 模型调用 `voice_sing_cover` 或 `voice_compose_song`，发送歌曲；不再重复发文字确认 |

模型可根据聊天语境、当前用户意愿及已有角色性格决定。用户可以自然地说“这句打字回我”“想听你说”“唱首开心的歌”，无需切换固定模式；没有指定时，模型仍可自己判断。下一条回复重新选择，上一条用语音不会把后续对话锁定为语音。

### 让模型能使用声音工具

在 AstrBot 中，确保当前聊天模型/执行器支持工具调用，并允许当前人格使用本插件的工具。插件会为原模型补充稳定的工具使用说明，保留已有角色设定，不另配判断模型或 DeepSeek API。

- `voice_speak`：模型觉得适合开口、问候、表达情绪或口头解释时可自主调用。
- `voice_sing_cover`：已有真实演唱音频直链时可选择翻唱，不会根据歌名编造链接。
- `voice_compose_song`：对话适合一首新歌时可选择生成歌曲。

如果模型或执行器不支持工具调用，原来的文字聊天仍然可用；手动 `/说话`、`/唱歌`、`/写歌` 可用于测试音频配置。实际选择频率和表达风格由你现有模型及人格决定，插件不按概率强制转换。

音频实际送出之后才抑制文字。失败时把结果交回原模型自然用文字回答，不假装成功。重复的相同工具调用在同一条消息内不会重复发送或重复请求付费服务。流式正文会暂存到本轮选择确定后再投递，避免先发出文字后模型又选择语音。原有图片等非文字媒体保留。

## 安装或升级

1. 从 [Release](https://github.com/Xtdzs/astrbot_plugin_dsh_voice/releases/tag/v0.3.0) 下载 **`astrbot_plugin_dsh_voice-v0.3.0.zip`**。
2. 无需解压，打开 **AstrBot WebUI → 插件 → 安装插件（＋）→ 文件上传**，选择 ZIP。
3. 在插件配置中填写语音服务的 API Key、音色等参数，保存并重载；在当前模型/人格中允许使用本插件的声音工具。

也可以在安装窗口的 URL 方式填写：

```text
https://github.com/Xtdzs/astrbot_plugin_dsh_voice
```

首次安装只需要联网下载 Python 依赖 `httpx`，不需要 GPU、DSH 或本地语音模型。若环境没有自动安装依赖，在 **AstrBot 实际使用的 Python 环境**执行 `python -m pip install -r requirements.txt`。

从 0.1.x 或 0.2.x 升级时直接上传新版 ZIP。仓库名和插件标识沿用 `astrbot_plugin_dsh_voice` 以支持覆盖升级。新版不再使用 DSH/DeepSeek 对话配置，也不读取 0.2.x 的固定回复模式或模式记忆文件；无需手动清除旧模式。

## 最少填什么

### 用 MiniMax 说话（推荐先跑通）

| 配置 | 填写内容 |
| --- | --- |
| `speech.provider` | `minimax` |
| `minimax.api_key` | MiniMax 的 API Key |
| `minimax.voice_id` | 默认 `lovely_girl`，即萌萌女童 |
| `speech.character_conversion` | 先保持 `false`，直接使用原生音色 |

也可通过环境变量 `MINIMAX_API_KEY` 提供密钥。默认模型 `speech-2.8-turbo`，接口根地址 `https://api.minimax.cn`。请按账户实际地域与权限调整。

配置并启用工具后，像平时一样和原来的机器人聊天，由模型自行决定什么时候开口。也可先发送 `/说话 嘿嘿，你终于来啦！` 验证 Key 和音色。

### 用千问说话

将 `speech.provider` 改为 `qwen`，填写 `qwen.api_key`，选择该模型支持的 `qwen.voice`（默认 `Cherry`）。也可使用 `DASHSCOPE_API_KEY` 环境变量。

默认非实时语音模型为 `qwen3-tts-flash`，北京接口根地址为 `https://dashscope.aliyuncs.com/api/v1`。新加坡账户要使用匹配地域的地址和 Key。这里的模型是**语音合成模型**，你的聊天模型仍由原 AstrBot 配置决定。

## 唱歌

### 翻唱已有演唱音频

```text
/唱歌 https://你的存储服务/song.mp3
```

配置 `rvc.api_key`（Replicate Token）与 `rvc.voice_model_url`（角色 RVC 模型 ZIP 公网直链）。ZIP 内含 `.pth`，可附带 `.index`。本版调用 Replicate 托管的 `meta-innovation/ultimate_rvc`，无需本地运行。

当前输入是**演唱音频的直链**，不能只报歌名；插件不包含搜歌或角色模型训练。MiniMax 的 `lovely_girl` 是说话音色 ID，不能直接作为 RVC 模型使用，也没有随插件附送同音色的歌声模型。

### 创作新歌

```text
/写歌 关于秋天的轻快中文民谣
/写歌 中文流行 | [Verse]
这里是你的歌词
[Chorus]
这里是副歌
```

需要 MiniMax 音乐 API 权限与余额。提供歌词时按歌词生成；省略歌词时交给音乐接口生成。生成音乐不需要另配语言模型 Key。开启 `music.character_conversion` 后，可再用同一份 RVC 模型转换歌声音色。

## 常用指令

| 指令 | 用途 |
| --- | --- |
| `/说话 内容` | 用语音说出指定内容 |
| `/唱歌 音频直链` | 云端 RVC 翻唱 |
| `/写歌 描述`、`/写歌 描述 \| 歌词` | 生成歌曲 |
| `/voice 帮助`、`/voice 状态` | 帮助和配置填写状态 |

上述指令是手动触发和配置测试入口，平时模型可以自主调用工具。兼容 `/voice 说`、`/voice 唱`、`/voice 写歌` 与旧的 `/dsh` 音频前缀。`/voice 语音`、`/voice 文字` 等旧模式命令只提示新版工作方式，不再保存固定模式。

## 音色与发送设置

- 开启 `speech.character_conversion`，说话也会走 RVC。与歌声使用同一个模型有助于接近同一角色，但增加延迟和费用，建议先试听。只想要默认萌萌女童说话时保持关闭即可。
- `speech.max_characters` 为每段朗读上限（默认 500，千问每段最多 600），`speech.max_reply_characters` 为总上限（默认 2000）。超过总上限时让工具明确失败，交回原模型用文字回答，不截断后声称全部说完。
- 语音使用 WAV。云端返回其他格式时需要系统安装 FFmpeg；它仅做文件格式转换，不运行模型。
- 歌曲默认以文件发送；希望以 QQ 语音发送时，将 `music.delivery` 设为 `record`，长语音仍受 QQ 适配器限制。
- 如果同时开启 AstrBot 自带 TTS 或另一款语音转换插件，建议只保留一个自动配音来源，避免重复声音。外部执行器若不触发 AstrBot 的标准回复钩子，需要在实际环境检查兼容性。
- `limits.allowed_users` 可限制付费功能的使用者。并发、调用间隔与音频大小上限均可配置。
- 音频缓存保留 24 小时，后续调用时清理。不再存储回复模式，API Key 不会回显到 QQ。

## 开发与验证

```sh
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python scripts/package_plugin.py
```

离线测试覆盖模型自主选择文字/声音、不继承上一条的形式、普通与流式去重、声音失败回退、重复工具调用不重复计费、原有图片保留、云端请求与角色翻唱。**真实付费 API、音色听感和 QQ 实际发送仍需部署后验证。**

打包脚本只收录白名单文件，版本从 `metadata.yaml` 读取，安装包输出到 `dist/`。不包含 API Key、用户音频、聊天日志、RVC 权重或原生运行时。采用 [MIT 许可证](LICENSE)，音色模型与云端服务各自遵循提供方条款。

## 官方参考

- [AstrBot 消息事件与回复装饰](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)
- [AstrBot 工具注册](https://docs.astrbot.app/dev/star/guides/ai.html)
- [AstrBot ZIP/URL 安装入口](https://docs.astrbot.app/use/webui.html#插件)
- [MiniMax 同步语音合成](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)
- [MiniMax 音乐生成](https://platform.minimax.cn/docs/api-reference/music-generation)
- [千问 TTS API](https://help.aliyun.com/zh/model-studio/qwen-tts-api)
- [RVC 固定版本参数](https://replicate.com/meta-innovation/ultimate_rvc/versions/c7a02f18043c32141e29fd5d10f91bb06f44ed0a7bc491817609ab9de41017e4/api)
