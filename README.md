# 语音说话 · AstrBot 精简版

给已经能聊天的 QQ 机器人增加说话能力。复用 AstrBot 原有模型、人格和记忆，**每条由模型自主选择文字或语音；语音实际发送成功后，不再重复发送文字或转录。**

当前版本 **0.6.0**，支持 QQ OneBot v11（`aiocqhttp`）。只有 MiniMax / 千问 TTS，不包含唱歌、歌单、音乐生成、本地模型或后台服务。插件只依赖 `httpx`，无需另配聊天模型。

## 安装与升级

从 [v0.6.0 Release](https://github.com/Xtdzs/astrbot_plugin_dsh_voice/releases/tag/v0.6.0) 下载 **astrbot_plugin_dsh_voice-v0.6.0.zip**，在 AstrBot WebUI → 插件 → 安装插件 → 文件上传，直接选择 ZIP，无需解压。

也可在插件安装页填写本仓库地址。现有插件直接覆盖升级并重载；插件标识仍为 `astrbot_plugin_dsh_voice`，原 MiniMax / 千问 Key 和音色配置继续读取。旧翻唱、歌曲和 RVC 配置不再使用，界面也不再展示这些项目。旧版翻唱源码可在历史标签 v0.5.1 查看。

**在当前聊天模型/人格中允许使用 `voice_speak` 工具。** 配置页由管理员填写接口和音色；实际对话中的发送形式仍由模型逐条决定，没有固定文字/语音模式。

## 配置 MiniMax

| 设置 | 填写内容 |
| --- | --- |
| `speech.provider` | `minimax` |
| `minimax.api_key` | 你的 MiniMax API Key，或环境变量 `MINIMAX_API_KEY` |
| `minimax.base_url` | 默认 `https://api.minimax.cn`，按账户地域填写 API 根地址 |
| `minimax.speech_model` | 默认 `speech-2.8-turbo` |
| `minimax.voice_id` | 默认 `lovely_girl`（萌萌女童），也可改为你的可用音色 ID |
| `speech.speed` | 默认 `1.0`，范围 `0.5`–`2.0`；仅用于 MiniMax |

测试发送：`/说话 嘿嘿，你终于来啦！`。需要对应语音接口权限和余额，网页地址不能填作 API 地址。

## 可选千问

将 `speech.provider` 改为 `qwen`，填写 `qwen.api_key` 或环境变量 `DASHSCOPE_API_KEY`；默认模型 `qwen3-tts-flash`、音色 `Cherry`。

北京账户默认根地址为 `https://dashscope.aliyuncs.com/api/v1`，新加坡账户使用 `https://dashscope-intl.aliyuncs.com/api/v1`，Key 和地址需匹配地域。只需配置所选语音服务，不必同时购买两个平台。

## 日常使用

- 正常聊天：原模型可以直接回复文字，也可以调用 `voice_speak(text)` 开口说话。
- `/说话 内容` 或 `/voice 说 内容`：手动测试语音。
- `/voice 状态`：查看当前服务、音色和 Key 是否已填写，不显示密钥。
- `/voice 帮助`：查看使用说明。`/语音` 与旧 `/dsh` 前缀保留兼容。

较长语音按自然停顿分段，默认每段最多 500 字（千问最多 600 字），单次总上限 2000 字。超出上限时交回模型用文字回答，不截断回复。代码块和 Markdown 标记不会照读。

工具失败且没有音频送出时，保留文字回答；已经送出部分语音时，不再重复正文或从头播放。同一轮重复工具调用会去重，普通文字不会触发 TTS 计费。流式正文会等本轮发送形式确定后再投递，避免先发文字再重复发语音；原有图片等媒体保留。

QQ 发语音优先使用 WAV。MiniMax 默认请求 WAV，无需本地推理或 FFmpeg；若语音服务返回其他格式，需系统 FFmpeg 转为 WAV。模型是否选择语音、选择频率与表达风格由原模型和人格决定；插件不改变聊天人格，也不保证每条都用声音。

## 调用限制与缓存

`limits.allowed_users` 可填写允许调用的 QQ 号，空列表表示不限。默认最多同时处理 2 个任务，每位用户调用间隔 1 秒；可在配置页调整。

已生成语音默认保留 24 小时，由后续调用清理。升级不删除 AstrBot 中的用户 Key 或已有数据。不要同时开启多个自动配音插件，以免重复发送。

本版不再需要翻唱服务。如果你在其他机器手动安装过旧版本机服务，应停掉那台机器上的服务并移除 `AstrBot-SeedVC-Local` 启动任务；仅升级插件不会代替其他主机卸载后台服务。

## 开发与验证

```sh
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python scripts/package_plugin.py
```

测试验证 MiniMax / 千问请求结构、消息投递、模型自主选择、重复调用、流式回复及失败回退，不调用付费接口，不等于真实 QQ 发送或音色听感验收。

ZIP 使用明确白名单，仅包含插件源码、配置描述和说明，不包含模型、环境、测试、用户配置、密钥或音频。MIT 许可。

参考：[AstrBot ZIP 安装](https://docs.astrbot.app/use/webui.html#插件)、[工具注册](https://docs.astrbot.app/dev/star/guides/ai.html)、[MiniMax 语音 API](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)、[千问 TTS](https://help.aliyun.com/zh/model-studio/qwen-tts-api)。
