from __future__ import annotations

import asyncio
import re
import shutil
import time
import uuid
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
import astrbot.api.message_components as Comp

from .core import DshEngine, RequestGate, conversation_key
from .errors import PluginError
from .providers import Audio, CloudAudio, public_url

HELP = """DSH 角色语音与翻唱
/dsh 你好 —— DSH 对话，可附带语音
/dsh 说 今天也要开心 —— 直接朗读
/dsh 唱 https://…/song.mp3 —— 角色音色翻唱
/dsh 写歌 轻快中文流行 | 你的歌词 —— 生成歌曲
/dsh 写歌 关于秋天的民谣 —— 自动生成歌词和歌曲
/dsh 重置 —— 开始新对话
/dsh 状态 —— 查看配置是否填写
翻唱需要音频直链和角色 RVC 模型；不能只输入歌名。
API Key 请在 AstrBot 管理面板填写，不要发到 QQ。"""


class DshVoicePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.root = Path(StarTools.get_data_dir("astrbot_plugin_dsh_voice")).resolve()
        self.media_dir = self.root / "audio"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.dsh = DshEngine(
            config.get("dsh", {}),
            self.root / "dsh",
            Path(__file__).parent / "dsh_chat.patch.yml",
        )
        self.cloud = CloudAudio(config)
        limits = config.get("limits", {})
        self.gate = RequestGate(
            int(limits.get("max_concurrent", 2)),
            float(limits.get("cooldown_seconds", 5)),
        )
        self.tasks: set[asyncio.Task] = set()
        self.closing = False

    @filter.command("dsh", priority=100)
    async def dsh_command(self, event: AstrMessageEvent):
        """DSH 对话、语音与角色翻唱，发送 /dsh 帮助 查看说明。"""
        event.set_extra("dsh_voice_handled", True)
        event.stop_event()
        raw = event.message_str.strip()
        parts = re.split(r"\s+", raw, maxsplit=1)
        await self.dispatch(event, parts[1] if len(parts) > 1 else "帮助")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-100)
    async def auto_chat(self, event: AstrMessageEvent):
        """Optional replacement for replies to AstrBot wake/mention messages."""
        if not self.config.get("auto_chat", False) or event.get_extra(
            "dsh_voice_handled", False
        ):
            return
        if not getattr(event, "is_at_or_wake_command", False):
            return
        text = event.message_str.strip()
        if not text or text.startswith("/"):
            return
        event.stop_event()
        await self.dispatch(event, text, chat_only=True)

    def allowed(self, sender: str) -> bool:
        users = self.config.get("limits", {}).get("allowed_users", [])
        return not users or sender in {str(x) for x in users}

    async def dispatch(self, event: AstrMessageEvent, text: str, chat_only=False):
        if self.closing:
            return
        sender = str(event.get_sender_id())
        if not self.allowed(sender):
            await event.send(event.plain_result("你不在此插件的使用名单中。"))
            return
        key = conversation_key(event.unified_msg_origin, sender)
        parts = re.split(r"\s+", text.strip(), maxsplit=1)
        verb = parts[0]
        argument = parts[1].strip() if len(parts) > 1 else ""
        if not chat_only and verb in ("帮助", "help"):
            await event.send(event.plain_result(HELP))
            return
        if not chat_only and verb in ("状态", "status"):
            await event.send(event.plain_result(self.status()))
            return
        entered = False
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            self.gate.enter(key)
            entered = True
            self.cleanup()
            if not chat_only and verb in ("重置", "reset"):
                await self.dsh.reset(key)
                await event.send(
                    event.plain_result("已开始新对话。历史日志仍保留在插件数据目录。")
                )
            elif not chat_only and verb in ("说", "say"):
                if not argument:
                    raise PluginError("用法：/dsh 说 要朗读的内容")
                audio = await self.cloud.speech(argument)
                await self.send_audio(event, audio, song=False)
            elif not chat_only and verb in ("唱", "sing"):
                if not argument:
                    raise PluginError("用法：/dsh 唱 公网音频直链")
                self.cloud.rvc_settings()
                source = Audio(url=public_url(argument), extension="mp3")
                await event.send(
                    event.plain_result("开始角色翻唱，云端启动和转换可能需要几分钟。")
                )
                audio = await self.cloud.convert(source)
                await self.send_audio(event, audio, song=True)
            elif not chat_only and verb in ("写歌", "music"):
                if not argument:
                    raise PluginError("用法：/dsh 写歌 风格描述 | 歌词（歌词可省略）")
                self.cloud.minimax_key()
                if self.config.get("music", {}).get("character_conversion", False):
                    self.cloud.rvc_settings()
                prompt, separator, lyrics = argument.partition("|")
                await event.send(event.plain_result("开始生成歌曲，可能需要几分钟。"))
                audio = await self.cloud.music(
                    prompt.strip(), lyrics.strip() if separator else ""
                )
                await self.send_audio(event, audio, song=True)
            else:
                limit = int(
                    self.config.get("limits", {}).get("max_chat_characters", 2000)
                )
                if len(text) > limit:
                    raise PluginError(f"对话输入最多 {limit} 字。")
                reply = await self.dsh.chat(key, text)
                # Text is delivered even when a later speech request fails.
                await event.send(event.plain_result(reply))
                if self.config.get("speech", {}).get("auto_speak", True):
                    await self.send_audio(
                        event, await self.cloud.speech(reply), song=False
                    )
        except PluginError as error:
            await event.send(event.plain_result(str(error)))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(f"[dsh_voice] 操作失败：{type(error).__name__}")
            await event.send(
                event.plain_result("操作失败，请检查插件配置、音频格式和平台发送权限。")
            )
        finally:
            if entered:
                self.gate.leave(key)
            self.tasks.discard(task)

    async def send_audio(self, event, audio: Audio, song: bool):
        name = f"dsh-{uuid.uuid4().hex}"
        path = self.media_dir / f"{name}.{audio.extension}"
        try:
            await self.cloud.download(audio, path)
            delivery = (
                self.config.get("music", {}).get("delivery", "file")
                if song
                else "record"
            )
            if delivery == "file":
                await event.send(
                    event.chain_result(
                        [Comp.File(file=str(path), name="角色歌曲" + path.suffix)]
                    )
                )
            else:
                path = await self.ensure_wav(path)
                await event.send(
                    event.chain_result([Comp.Record(file=str(path), url=str(path))])
                )
        except BaseException:
            # Keep completed files until the retention window; adapters may read later.
            # Failed/partial files are never sent and can be removed immediately.
            if path.exists():
                path.unlink(missing_ok=True)
            raise

    async def ensure_wav(self, source: Path) -> Path:
        with source.open("rb") as handle:
            header = handle.read(12)
        if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            return source
        executable = shutil.which("ffmpeg")
        if executable is None:
            raise PluginError(
                "云端返回的音频不是 WAV。请安装 FFmpeg 并加入 PATH，或将歌曲发送方式设为 file。"
            )
        target = source.with_name(source.stem + "-voice.wav")
        process = await asyncio.create_subprocess_exec(
            executable,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), 60)
            if process.returncode or not target.exists():
                raise PluginError("音频格式转换失败，请检查 FFmpeg。")
            return target
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            target.unlink(missing_ok=True)
            raise

    def cleanup(self):
        retention = (
            max(1, int(self.config.get("limits", {}).get("retention_hours", 24))) * 3600
        )
        cutoff = time.time() - retention
        for path in self.media_dir.glob("dsh-*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def status(self):
        import os

        def filled(section, env):
            return (
                "已填写（未验证）"
                if self.config.get(section, {}).get("api_key") or os.getenv(env)
                else "未填写"
            )

        speech = self.config.get("speech", {})
        return "\n".join(
            [
                "DSH 角色语音与翻唱 v0.1.1",
                "DSH Key：" + filled("dsh", "DEEPSEEK_API_KEY"),
                "MiniMax Key：" + filled("minimax", "MINIMAX_API_KEY"),
                "千问 Key：" + filled("qwen", "DASHSCOPE_API_KEY"),
                "Replicate Key：" + filled("rvc", "REPLICATE_API_TOKEN"),
                "角色 RVC 模型："
                + (
                    "已填写"
                    if self.config.get("rvc", {}).get("voice_model_url")
                    else "未填写"
                ),
                "语音服务：" + str(speech.get("provider", "minimax")),
                "说话转角色音色："
                + ("开" if speech.get("character_conversion", False) else "关"),
                "生成歌曲转角色音色："
                + (
                    "开"
                    if self.config.get("music", {}).get("character_conversion", False)
                    else "关"
                ),
            ]
        )

    async def terminate(self):
        self.closing = True
        active = [task for task in self.tasks if task is not asyncio.current_task()]
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        await self.cloud.close()
        await self.dsh.close()
