# 语音说话 · AstrBot 精简版

给已经能聊天的 QQ 机器人增加说话能力。复用 AstrBot 原有模型、人格和记忆，**每条由模型自主选择文字或语音；语音实际发送成功后，不再重复发送文字或转录。**

当前版本 **0.7.0**，支持 QQ OneBot v11（`aiocqhttp`）。只有 MiniMax / 千问 TTS，不包含唱歌、歌单、音乐生成、本地模型或后台服务。插件只依赖 `httpx`，无需另配聊天模型。

## 安装与升级

从 [v0.7.0 Release](https://github.com/Xtdzs/astrbot_plugin_dsh_voice/releases/tag/v0.7.0) 下载 **astrbot_plugin_dsh_voice-v0.7.0.zip**，在 AstrBot WebUI → 插件 → 安装插件 → 文件上传，直接选择 ZIP，无需解压。

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
| `speech.speed` | 手动 `/说话` 的默认语速，默认 `1.0`，范围 `0.5`–`2.0`；模型工具会单独选择本次语速 |

测试发送：`/说话 嘿嘿，你终于来啦！`。需要对应语音接口权限和余额，网页地址不能填作 API 地址。

## 可选千问

将 `speech.provider` 改为 `qwen`，填写 `qwen.api_key` 或环境变量 `DASHSCOPE_API_KEY`；默认模型 `qwen3-tts-flash`、音色 `Cherry`。

北京账户默认根地址为 `https://dashscope.aliyuncs.com/api/v1`，新加坡账户使用 `https://dashscope-intl.aliyuncs.com/api/v1`，Key 和地址需匹配地域。只需配置所选语音服务，不必同时购买两个平台。

## 模型自主控制表达

使用 MiniMax 时，现有模型通过同一个 `voice_speak` 工具逐条决定表达方式，无需管理员为每条回复配参数，也不会另加聊天模型。

| 工具参数 | 作用 | 默认值与范围 |
| --- | --- | --- |
| `text` | 真正要说的内容 | 自然语言正文，可带下述控制标签 |
| `emotion` | 本次主要情绪 | `auto`；可选 `happy`、`sad`、`angry`、`fearful`、`disgusted`、`surprised`、`calm` |
| `speed` | 本次语速 | `1.0`，限制在 `0.85–1.2` |
| `volume` | 本次音量 | `1.0`，限制在 `0.8–1.2` |
| `pitch` | 本次音高 | `0`，限制在 `-2–2`，整数 |

数值超出日常范围时自动限幅；非有限数字、非整数音高或未知情绪会明确失败，不提交付费请求。`emotion=auto` 时不向接口硬编码情绪，由 MiniMax 根据文本判断。每次调用独立选择，下一条默认恢复，不继承上一条的情绪和倍速。

模型调用默认 1.0 倍速，不再因旧配置的 0.5 倍速而机械地半速朗读；`/说话` 手动指令仍读取 `speech.speed`。音色 ID、模型、编码、采样率和接口密钥由管理员固定，模型没有修改这些设置的入口。

模型可以在工具的 `text` 中使用：

- 句间停顿：`你回来啦<#0.3#>今天过得怎么样？`。每次最多 8 处，限制为 `0.1–0.6` 秒，开头和结尾的停顿会移除。
- Speech 2.8 的语气动作：`(laughs)`、`(chuckle)`、`(sighs)`、`(breath)`，每次最多两处。其他语气动作不开放，避免频繁表演或不合适的声音。

这些标签只放在语音工具正文里，正常文字回复不应携带。分段时保留完整控制标签，千问及不支持的模型会移除对应标签，避免照着英文标签朗读。

一次调用只选一种主要情绪，不支持百分比混合；需要前句惊讶、后句开心时，可依次调用不同正文。多段调用会串行发送，同一轮相同正文即使换了参数也不会重复发送或计费。同一轮后续句子不受用户调用间隔阻挡，新的消息仍受限流保护。

示例工具参数（由模型生成，不用你手写）：

```json
{
  "text": "(chuckle)你回来啦<#0.3#>今天也要开心呀！",
  "emotion": "happy",
  "speed": 1.1,
  "volume": 1.05,
  "pitch": 1
}
```

千问当前保留基础朗读：不向它发送 MiniMax 专用表达字段，也会去除控制标签。情绪听感仍取决于所选音色、文本和服务模型，不保证每次完全按人类预期表达。

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

自动测试验证请求参数、表达范围、控制标签、消息投递、模型自主选择、重复调用、流式回复及失败回退，不调用付费接口。另用已配置的 MiniMax 账号实测两句表达：开心 1.1 倍速和安慰 0.9 倍速，从工具调用到本地 WAV 就绪约 1.52 秒和 0.76 秒；不含聊天模型生成、QQ 上传和转码，也不等于听感或 QQ 实发验收。

ZIP 使用明确白名单，仅包含插件源码、配置描述和说明，不包含模型、环境、测试、用户配置、密钥或音频。MIT 许可。

参考：[AstrBot ZIP 安装](https://docs.astrbot.app/use/webui.html#插件)、[工具注册](https://docs.astrbot.app/dev/star/guides/ai.html)、[MiniMax 语音 API](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)、[千问 TTS](https://help.aliyun.com/zh/model-studio/qwen-tts-api)。
