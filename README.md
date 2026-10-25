# DSH 角色语音与翻唱 · AstrBot 插件

给 QQ 机器人添加 DeepSeek Harness 对话、云端说话、角色翻唱和歌曲生成。不需要本地 GPU；AstrBot 和 DSH 仍需运行在电脑或服务器上，模型推理通过 API 完成。

这是 **AstrBot 的 Python 插件**，可以通过 AstrBot WebUI 上传 ZIP 或填写 GitHub 仓库地址安装。主要适配 QQ 的 OneBot v11（`aiocqhttp`）链路。

当前版本：`0.1.1`。默认 MiniMax 音色：**萌萌女童（`lovely_girl`）**。公开仓库：[Xtdzs/astrbot_plugin_dsh_voice](https://github.com/Xtdzs/astrbot_plugin_dsh_voice)。

**针对固定角色音色，推荐让说话和唱歌都经过同一个 RVC 模型。** MiniMax/千问先合成语音，RVC 再统一音色。两类输入的听感仍可能有差别，需要试听调参，不能保证完全一致。

```text
QQ → AstrBot → 插件 → DSH → DeepSeek API → 文字回复
                         ↓
              MiniMax / 千问 TTS → 可选云端 RVC → QQ 语音

/dsh 唱 音频直链 → 云端 RVC（角色模型）→ 角色歌曲
/dsh 写歌 描述  → MiniMax Music → 可选同一个 RVC → 角色歌曲
```

## 安装

1. 先让 AstrBot 成功接入 QQ。插件负责对话与音频，不负责登录 QQ。QQ 的连接方式和媒体发送能力取决于使用的适配器。
2. 选择以下任意一种方式安装：

   - **ZIP 导入**：下载 [Releases](https://github.com/Xtdzs/astrbot_plugin_dsh_voice/releases) 中的 `astrbot_plugin_dsh_voice-v0.1.1.zip`，打开 AstrBot WebUI → **插件 → 安装插件（＋）→ 文件上传**，选择 ZIP。
   - **GitHub URL 安装**：在同一安装窗口选择 URL，填写 `https://github.com/Xtdzs/astrbot_plugin_dsh_voice`。
   - **手动放置**：将插件文件放进 `AstrBot/data/plugins/astrbot_plugin_dsh_voice/`。该目录下应直接有 `main.py` 和 `metadata.yaml`，不要多套一层目录。
3. 在 **AstrBot 实际使用的 Python 环境**中安装依赖，然后重载插件：

   ```sh
   python -m pip install -r requirements.txt
   ```

   Docker 部署需在容器对应环境操作。DSH SDK 会安装配套原生运行时，首次安装需联网，有一定下载量。已固定 SDK 为 `0.2.1rc2`，避免预览版更新直接改变接口。
4. 在 AstrBot 管理面板的插件配置中填写 Key。修改后保存并重载插件。

## 先跑通说话

最少配置：

| 配置 | 内容 |
| --- | --- |
| `dsh.api_key` | DeepSeek API Key |
| `dsh.model` | 账户可用的 DeepSeek 模型，默认 `deepseek-v4-flash` |
| `dsh.persona` | 角色性格和回复习惯 |
| `speech.provider` | `minimax` 或 `qwen` |
| `minimax.api_key` / `qwen.api_key` | 对应语音服务的 Key |

默认关闭 RVC 音色转换，方便先检查 TTS 是否可用。分别测试：

```text
/dsh 状态
/dsh 说 你好，今天过得怎么样？
/dsh 你好，介绍一下你自己
```

`/dsh 状态`仅检查是否填写配置，不验证 Key 或余额。`dsh.base_url`默认使用 DeepSeek 官方地址；当前 DSH 原生适配器使用 DeepSeek Messages 协议，普通仅支持 Chat Completions 的中转不能直接替代。这里的千问配置用于 TTS，不会把 DSH 对话模型切换到千问。

MiniMax 和千问的地域、模型权限必须与 Key 对应。可以修改地址、模型和音色 ID。MiniMax 默认模型为 `speech-2.8-turbo`，千问为 `qwen3-tts-flash`。

MiniMax 默认 `voice_id` 为 `lovely_girl`（萌萌女童）。先关闭 `speech.character_conversion` 听原始音色；开启后，最终音色由 RVC 角色模型决定。已有安装的配置不会自动覆盖，请手动修改该字段并保存。

## 固定角色音色与翻唱

本版实现了 **Replicate 托管的 `meta-innovation/ultimate_rvc`**，不需要自己租 GPU 或本地部署 RVC。使用指定版本的公开 API；它是社区模型，服务可用性与效果取决于托管平台和模型作者。

需要准备：

1. 已开通付费调用的 Replicate 账户及 API Token。
2. 一份对应角色的 **RVC 模型 ZIP 直链**，包含 `.pth`，可附带 `.index`。MiniMax 的 `voice_id`、一小段录音、SVC 权重以及网页下载页面都不能直接替代这个 ZIP。
3. 一段可公网下载的演唱音频直链。可以是自己的录音或已准备好的歌曲文件。

如果没有角色模型，需要先取得可使用的现成模型，或另做云端训练；本插件不包含训练和自动搜歌。Replicate 的[歌声音色模型与训练集合](https://replicate.com/collections/sing-with-voices)可用来了解准备方式。角色名字本身不会自动生成对应模型。

填写配置：

| 配置 | 内容 |
| --- | --- |
| `rvc.api_key` | Replicate API Token |
| `rvc.voice_model_url` | 上述角色模型 ZIP 的公网直链 |
| `rvc.pitch` | 升降调，先用 `0`，再按原唱与目标角色试听调整 |
| `rvc.index_rate` | 索引强度，先用默认 `0.3` |
| `speech.character_conversion` | 先成功翻唱后，再设为 `true`，说话也转成该角色 |
| `music.character_conversion` | 设为 `true`，生成新歌后也转为该角色 |

```text
/dsh 唱 https://你的存储服务/song.mp3
```

不要把配置示例中的占位网址直接发给机器人。模型和音频直链必须允许云端服务访问，且在任务期间有效。

说话开启角色转换后，每次语音会经过 **TTS + RVC 两项付费调用**，延迟也会增加。建议先测试一小段音频，确认角色模型效果再长时间启用。费用以各平台实际计费为准，插件不会自动购买、充值或训练模型。

## 生成新歌

需要 MiniMax 的音乐 API 权限和余额，千问 TTS Key 不能替代音乐服务。

```text
/dsh 写歌 关于秋天和重逢的中文民谣，温柔女声
/dsh 写歌 轻快中文流行 | [Verse]
这里填你自己的第一行歌词
这里填第二行歌词
[Chorus]
这里填副歌
```

未提供歌词时使用音乐接口自动写词。开启 `music.character_conversion` 后，新歌会继续经过角色 RVC；否则使用音乐模型本身生成的歌声。

## QQ 使用与发送

| 指令 | 功能 |
| --- | --- |
| `/dsh 内容` | DSH 对话，默认先文字后语音 |
| `/dsh 说 内容` | 直接朗读 |
| `/dsh 唱 音频直链` | 固定角色 RVC 翻唱 |
| `/dsh 写歌 描述` | 自动写词并生成歌曲 |
| `/dsh 写歌 描述 \| 歌词` | 按歌词生成歌曲 |
| `/dsh 重置` | 本平台会话中当前用户开始新对话 |
| `/dsh 帮助`、`/dsh 状态` | 帮助、配置状态 |

开启 `auto_chat` 后，AstrBot 的私聊唤醒、@ 或唤醒词消息会由 DSH 回复。先用命令测试再开启。其他插件也可能处理相同消息，应实际检查是否有重复回复。

说话通过 `Record` 发 WAV，默认歌曲通过 `File` 发文件。若适配器不支持歌曲文件，可将 `music.delivery` 改为 `record`，但长语音可能受到 QQ 的格式、时长和消息窗口限制。没有在真实 QQ 环境验证所有适配器。

当返回音频不是 WAV 时，发送语音需要系统有 FFmpeg；没有本地模型需求，FFmpeg 仅转换文件格式。NapCat/OneBot 与 AstrBot 分开部署时，需要按适配器要求配置媒体传输或共享路径。

## 缓存、限制与测试

- 在 `limits.allowed_users` 填 QQ 号可限制付费功能的使用者；空列表表示不限。并发与用户调用间隔也可配置。
- DSH 默认全局同时处理一条对话；忙时提示稍后重试，其他音频任务在并发限制内独立运行。
- 会话按“平台会话 + 用户”隔离。群里的不同成员不共享 DSH 历史。插件数据在 AstrBot 的插件数据目录，DSH 日志保留，`重置`仅切换新会话。
- 音频默认保留 24 小时，下一次功能调用时清理过期文件；停止使用插件不会定时清理。不要在群聊中公开模型的私有下载链接。
- RVC 超时、取消或轮询失败时尝试取消任务，并设置云端最大运行时间。已执行的计算仍可能计费。插件不自动重试生成任务，避免重复扣费。
- 语音失败时已经生成的文字仍保留；超过语音字数限制会提示，不会静默截断。
- 当前输入支持文字和公网音频直链；没有接收 QQ 语音转文字、按歌名搜索、上传附件或角色训练功能。

开发者离线检查：

```sh
python -m unittest discover -s tests -v
python scripts/check_runtime.py
```

第一项使用模拟接口测试请求、轮询、取消、会话隔离和消息发送。第二项启动真实 DSH 与本机模拟模型，检查多轮、重启恢复、角色设定及模型无宿主工具；不调用云端付费 API。Windows 需允许 Python 的本机回环连接及原生运行时加载。

已验证：离线接口与路由测试、DSH `0.2.1rc2` Windows 原生运行时与本机模拟模型。待验证：真实账户的付费 API、所选角色模型听感、实际 QQ 的发送和播放。

## 开源发布

本项目采用 MIT 许可证，见 [LICENSE](LICENSE)。仓库包含插件源码、测试、配置结构和打包脚本；不包含 API Key、角色音色模型、用户音频、聊天日志或 DSH 原生依赖安装包。音色模型与云端服务各自适用其提供方的许可与条款。

重新打包运行 `python scripts/package_plugin.py`，安装包输出到 `dist/`。版本号从 `metadata.yaml` 读取。

GitHub 上公开代码不代表已进入 AstrBot 插件市场。根据[官方发布文档](https://docs.astrbot.app/dev/star/plugin-publish.html)，市场收录需通过 AstrBot Cloud 提交，插件 ZIP 不得超过 16 MB。文件上传入口见[官方 WebUI 文档](https://docs.astrbot.app/use/webui.html#插件)。

## 官方接口参考

- [DeepSeek Harness Python SDK](https://deepseek-harness.github.io/deepseek-harness/en/guide/python-sdk)
- [AstrBot 插件开发与安装结构](https://docs.astrbot.app/dev/star/plugin-new.html)
- [MiniMax 同步语音合成](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)
- [MiniMax 音乐生成](https://platform.minimax.cn/docs/api-reference/music-generation)
- [千问非实时 TTS API](https://help.aliyun.com/zh/model-studio/qwen-tts-api)
- [使用的 RVC 固定版本参数](https://replicate.com/meta-innovation/ultimate_rvc/versions/c7a02f18043c32141e29fd5d10f91bb06f44ed0a7bc491817609ab9de41017e4/api)
- [Replicate HTTP API 与任务轮询](https://replicate.com/docs/reference/http)
