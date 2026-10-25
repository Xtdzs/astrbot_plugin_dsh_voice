# 语音与角色翻唱 · AstrBot 插件

**给已经能聊天的 QQ 机器人加上说话和唱歌功能。** 复用你在 AstrBot 中配置好的语言模型、人格、记忆和消息触发规则。插件只负责声音，不再启动另一套聊天模型，不需要 DeepSeek API Key。

当前版本：**0.2.0**。主要面向 QQ 的 OneBot v11（`aiocqhttp`）链路。默认音色：**MiniMax 萌萌女童 `lovely_girl`**。

## 先选择：文字还是语音

在插件配置的第一项 **回复方式** 中选择：

| 模式 | 实际效果 |
| --- | --- |
| **只发语音（默认）** | 把你原来模型生成的回复转换成语音，不同时发送同内容文字 |
| **只发文字** | 正常发送原有文字回复，不调用 TTS |

也可以直接在 QQ 切换：

```text
/voice 语音
/voice 文字
/voice 默认
```

选择按“当前平台会话 + 当前用户”记住，重启后仍有效；`默认` 恢复管理面板的设置。模式切换的确认也尽量使用选定的方式。帮助、配置状态和调用错误属于操作提示。

语音模式下，普通回复在音频准备成功后替换为语音；流式回复会等待最终内容，再发送语音，不先把文字逐段发出来。较长回复按自然停顿分段，代码、Markdown 符号和网址不会逐字念出来。原本的图片等媒体会保留。

语音服务失败、并发已满、内容只有代码或超过朗读总长度限制时，保留原有文字，避免机器人没有回复。不会先发送部分语音再把整段文字重复发出。实际听感由选定音色和原有模型的表达方式共同决定。

## 安装或升级

1. 从 [Release](https://github.com/Xtdzs/astrbot_plugin_dsh_voice/releases/tag/v0.2.0) 下载 **`astrbot_plugin_dsh_voice-v0.2.0.zip`**。
2. 无需解压，打开 **AstrBot WebUI → 插件 → 安装插件（＋）→ 文件上传**，选择 ZIP。
3. 在插件配置中选择回复方式、语音服务并填写对应 Key，保存并重载。

也可以在安装窗口的 URL 方式填写：

```text
https://github.com/Xtdzs/astrbot_plugin_dsh_voice
```

首次安装只需要联网下载 Python 依赖 `httpx`，不需要 GPU、DSH 或本地语音模型。若环境没有自动安装依赖，在 **AstrBot 实际使用的 Python 环境**执行 `python -m pip install -r requirements.txt`。

从 0.1.x 升级时直接上传新版 ZIP。为了覆盖原插件，仓库名和插件标识沿用 `astrbot_plugin_dsh_voice`，但 0.2.0 已没有 DSH/DeepSeek 对话功能。旧的独立对话配置不再使用，现有 AstrBot 的模型配置保持原样。

## 最少填什么

### 用 MiniMax 说话（推荐先跑通）

| 配置 | 填写内容 |
| --- | --- |
| 回复方式 `reply_mode` | `voice`（只发语音）或 `text`（只发文字） |
| `speech.provider` | `minimax` |
| `minimax.api_key` | MiniMax 的 API Key |
| `minimax.voice_id` | 默认 `lovely_girl`，即萌萌女童 |
| `speech.character_conversion` | 先保持 `false`，直接使用原生音色 |

也可通过环境变量 `MINIMAX_API_KEY` 提供密钥。默认模型 `speech-2.8-turbo`，接口根地址 `https://api.minimax.cn`。请按账户实际地域与权限调整。

配置后，像平时一样和原来的机器人聊天即可；或者发送 `/说话 嘿嘿，你终于来啦！` 测试声音。

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

### 让现有机器人自然调用

插件为你现有的语言模型注册了三个工具：`voice_speak`、`voice_sing_cover`、`voice_compose_song`。如果你的模型/执行器支持工具调用，用户明确要求语音、翻唱或新歌时，它可以使用这些工具。

普通聊天回复直接由回复处理流程配音，不需要模型额外调用工具。工具成功发送音频后，不再重复发送同内容的文字确认；也不会在 QQ 中插入“正在调用接口”这样的技术过程说明。模型不支持工具调用时，上述指令仍然可用。

## 常用指令

| 指令 | 用途 |
| --- | --- |
| `/voice 语音`、`/voice 文字` | 切换自己的回复方式 |
| `/voice 默认` | 恢复管理面板的方式 |
| `/说话 内容` | 用语音说出指定内容 |
| `/唱歌 音频直链` | 云端 RVC 翻唱 |
| `/写歌 描述`、`/写歌 描述 \| 歌词` | 生成歌曲 |
| `/voice 帮助`、`/voice 状态` | 帮助和配置填写状态 |

兼容 `/voice 说`、`/voice 唱`、`/voice 写歌`，旧的 `/dsh` 前缀仅保留为这些音频命令的别名，不再用于聊天。

## 音色与发送设置

- 开启 `speech.character_conversion`，说话也会走 RVC。与歌声使用同一个模型有助于接近同一角色，但增加延迟和费用，建议先试听。只想要默认萌萌女童说话时保持关闭即可。
- `speech.max_characters` 为每段朗读上限（默认 500，千问每段最多 600），`speech.max_reply_characters` 为总上限（默认 2000）。超过总上限保留完整文字，不静默截断。
- 语音使用 WAV。云端返回其他格式时需要系统安装 FFmpeg；它仅做文件格式转换，不运行模型。
- 歌曲默认以文件发送；希望以 QQ 语音发送时，将 `music.delivery` 设为 `record`，长语音仍受 QQ 适配器限制。
- 如果同时开启 AstrBot 自带 TTS 或另一款语音转换插件，建议只保留一个自动配音来源，避免重复声音。外部执行器若不触发 AstrBot 的标准回复钩子，需要在实际环境检查兼容性。
- `limits.allowed_users` 可限制付费功能的使用者。并发、调用间隔与音频大小上限均可配置。
- 音频缓存保留 24 小时，后续调用时清理；回复模式存放在插件数据目录。API Key 不会回显到 QQ。

## 开发与验证

```sh
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python scripts/package_plugin.py
```

离线测试覆盖普通/流式语音替换、文字模式无 TTS 调用、模式记忆、失败回退、原有图片保留、云端请求与角色翻唱。**真实付费 API、音色听感和 QQ 实际发送仍需部署后验证。**

打包脚本只收录白名单文件，版本从 `metadata.yaml` 读取，安装包输出到 `dist/`。不包含 API Key、用户音频、聊天日志、RVC 权重或原生运行时。采用 [MIT 许可证](LICENSE)，音色模型与云端服务各自遵循提供方条款。

## 官方参考

- [AstrBot 消息事件与回复装饰](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)
- [AstrBot 工具注册](https://docs.astrbot.app/dev/star/guides/ai.html)
- [AstrBot ZIP/URL 安装入口](https://docs.astrbot.app/use/webui.html#插件)
- [MiniMax 同步语音合成](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)
- [MiniMax 音乐生成](https://platform.minimax.cn/docs/api-reference/music-generation)
- [千问 TTS API](https://help.aliyun.com/zh/model-studio/qwen-tts-api)
- [RVC 固定版本参数](https://replicate.com/meta-innovation/ultimate_rvc/versions/c7a02f18043c32141e29fd5d10f91bb06f44ed0a7bc491817609ab9de41017e4/api)
