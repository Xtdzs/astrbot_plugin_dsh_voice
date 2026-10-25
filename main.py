from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools
import astrbot.api.message_components as Comp

from .core import RequestGate, conversation_key, spoken_chunks
from .errors import PluginError
from .providers import Audio, CloudAudio, public_url

VERSION = "0.2.0"
PREFIX = "voice_addon_"
HELP = """语音与角色翻唱
/voice 语音 —— 本会话中你的回复只发语音
/voice 文字 —— 本会话中你的回复只发文字
/voice 默认 —— 恢复管理面板的回复模式
/voice 说 今天也要开心 —— 直接说出指定内容
/voice 唱 https://…/song.mp3 —— 角色音色翻唱
/voice 写歌 轻快中文流行 | 歌词 —— 生成歌曲
/voice 状态 —— 查看模式和语音配置
也支持 /说话、/唱歌、/写歌。翻唱需要音频直链和 RVC 模型。"""


class VoiceAddonPlugin(Star):
    """Add a voice to existing AstrBot replies; never request a chat completion."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # Keep the installed identifier/data path so previous releases upgrade in place.
        self.root = Path(StarTools.get_data_dir("astrbot_plugin_dsh_voice")).resolve()
        self.media_dir = self.root / "audio"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.cloud = CloudAudio(config)
        limits = config.get("limits", {})
        self.gate = RequestGate(
            int(limits.get("max_concurrent", 2)),
            float(limits.get("cooldown_seconds", 1)),
        )
        self.tasks: set[asyncio.Task] = set()
        self.closing = False
        self.mode_path = self.root / "reply-modes.json"
        try:
            loaded = json.loads(self.mode_path.read_text("utf-8"))
            self.modes = (
                {str(k): v for k, v in loaded.items() if v in ("text", "voice")}
                if isinstance(loaded, dict)
                else {}
            )
        except FileNotFoundError:
            self.modes = {}
        except (ValueError, OSError):
            self.modes = {}
            logger.warning("[voice_addon] 回复模式文件无法读取，使用管理面板的模式。")

    def key(self, event):
        return conversation_key(event.unified_msg_origin, str(event.get_sender_id()))

    def mode(self, event):
        value = self.modes.get(self.key(event), self.config.get("reply_mode", "voice"))
        return "voice" if value == "voice" else "text"

    def allowed(self, sender: str) -> bool:
        users = self.config.get("limits", {}).get("allowed_users", [])
        return not users or sender in {str(x) for x in users}

    def event_enabled(self, event):
        return (
            not self.closing
            and self.allowed(str(event.get_sender_id()))
            and not event.get_extra(PREFIX + "command", False)
        )

    @filter.on_decorating_result(priority=-100)
    async def decorate_reply(self, event: AstrMessageEvent):
        """Replace only a completed model reply's text after all audio is ready."""
        if not self.event_enabled(event):
            return
        result = event.get_result()
        if result is None or not result.is_llm_result() or not result.chain:
            return
        if event.get_extra(PREFIX + "tool_audio", False):
            result.chain = [
                part for part in result.chain if not isinstance(part, Comp.Plain)
            ]
            return
        if self.mode(event) == "text":
            self.restore_stream(event)
            return
        if any(isinstance(part, Comp.Record) for part in result.chain):
            result.chain = [
                part for part in result.chain if not isinstance(part, Comp.Plain)
            ]
            return
        text = "\n".join(
            part.text for part in result.chain if isinstance(part, Comp.Plain)
        ).strip()
        if not text:
            return
        self.restore_stream(event)
        try:
            records = await self.make_records(event, text)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Leave the original result intact when TTS fails. No extra technical message.
            self.log_voice_failure(error)
            return
        if records:
            result.chain = [
                part for part in result.chain if not isinstance(part, Comp.Plain)
            ] + records

    @filter.on_llm_request()
    async def prepare_stream(self, event: AstrMessageEvent, req):
        """Translate a streaming reply to audio before any answer text is delivered."""
        if not self.event_enabled(event) or event.get_extra(PREFIX + "stream"):
            return
        original = event.send_streaming

        async def wrapped(generator, *args, **kwargs):
            async def audio_stream():
                texts = []
                has_audio = False
                async for chain in generator:
                    if self.mode(event) == "text" and not event.get_extra(
                        PREFIX + "tool_audio", False
                    ):
                        yield chain
                        continue
                    kind = getattr(chain, "type", None)
                    if kind == "break":
                        # A tool round ended: speak the final answer, not its preamble.
                        texts.clear()
                        has_audio = False
                        continue
                    if kind in ("reasoning", "tool_call", "agent_stats"):
                        continue
                    if kind == "audio_chunk":
                        has_audio = True
                        yield chain
                        continue
                    parts = getattr(chain, "chain", None) or []
                    texts.extend(
                        part.text for part in parts if isinstance(part, Comp.Plain)
                    )
                    others = [
                        part for part in parts if not isinstance(part, Comp.Plain)
                    ]
                    if others:
                        has_audio |= any(
                            isinstance(part, Comp.Record) for part in others
                        )
                        forwarded = copy.copy(chain)
                        forwarded.chain = others
                        yield forwarded
                text = "".join(texts).strip()
                if (
                    not text
                    or self.mode(event) == "text"
                    or has_audio
                    or event.get_extra(PREFIX + "tool_audio", False)
                ):
                    return
                if self.closing:
                    yield MessageChain([Comp.Plain(text)])
                    return
                try:
                    records = await self.make_records(event, text)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.log_voice_failure(error)
                    records = []
                # Do not emit text until conversion is known to have failed or been skipped.
                yield MessageChain(records if records else [Comp.Plain(text)])

            try:
                return await original(audio_stream(), *args, **kwargs)
            finally:
                self.restore_stream(event)

        event.set_extra(PREFIX + "stream", (original, wrapped))
        event.send_streaming = wrapped

    def restore_stream(self, event):
        saved = event.get_extra(PREFIX + "stream")
        if saved:
            original, wrapped = saved
            if event.send_streaming is wrapped:
                event.send_streaming = original
            event.set_extra(PREFIX + "stream", None)

    def log_voice_failure(self, error):
        detail = str(error) if isinstance(error, PluginError) else type(error).__name__
        logger.warning(f"[voice_addon] 语音未生成，保留文字：{detail}")

    async def make_records(self, event, text):
        speech = self.config.get("speech", {})
        limit = min(
            int(speech.get("max_characters", 500)),
            600 if speech.get("provider", "minimax") == "qwen" else 9999,
        )
        chunks = spoken_chunks(
            text, limit, int(speech.get("max_reply_characters", 2000))
        )
        if not chunks:
            return []
        key = self.key(event)
        self.gate.enter(key)
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            self.cleanup()
            records = []
            for chunk in chunks:
                audio = await self.cloud.speech(chunk)
                path = self.media_dir / f"dsh-{uuid.uuid4().hex}.{audio.extension}"
                try:
                    await self.cloud.download(audio, path)
                    path = await self.ensure_wav(path)
                    records.append(Comp.Record(file=str(path), url=str(path)))
                except BaseException:
                    path.unlink(missing_ok=True)
                    raise
            return records
        finally:
            self.gate.leave(key)
            self.tasks.discard(task)

    @filter.command("voice", alias={"语音", "dsh"}, priority=100)
    async def voice_command(self, event: AstrMessageEvent):
        """选择文字或语音回复，或使用朗读、翻唱、写歌功能。"""
        event.set_extra(PREFIX + "command", True)
        event.stop_event()
        parts = re.split(r"\s+", event.message_str.strip(), maxsplit=1)
        await self.dispatch(event, parts[1] if len(parts) > 1 else "帮助")

    @filter.command("说话", priority=100)
    async def say_command(self, event: AstrMessageEvent):
        event.set_extra(PREFIX + "command", True)
        event.stop_event()
        parts = re.split(r"\s+", event.message_str.strip(), maxsplit=1)
        await self.dispatch(event, "说 " + (parts[1] if len(parts) > 1 else ""))

    @filter.command("唱歌", priority=100)
    async def sing_command(self, event: AstrMessageEvent):
        event.set_extra(PREFIX + "command", True)
        event.stop_event()
        parts = re.split(r"\s+", event.message_str.strip(), maxsplit=1)
        await self.dispatch(event, "唱 " + (parts[1] if len(parts) > 1 else ""))

    @filter.command("写歌", priority=100)
    async def music_command(self, event: AstrMessageEvent):
        event.set_extra(PREFIX + "command", True)
        event.stop_event()
        parts = re.split(r"\s+", event.message_str.strip(), maxsplit=1)
        await self.dispatch(event, "写歌 " + (parts[1] if len(parts) > 1 else ""))

    async def dispatch(self, event, text):
        if self.closing:
            return
        if not self.allowed(str(event.get_sender_id())):
            await event.send(event.plain_result("你不在此插件的使用名单中。"))
            return
        parts = re.split(r"\s+", text.strip(), maxsplit=1)
        verb, argument = parts[0], parts[1].strip() if len(parts) > 1 else ""
        try:
            if verb in ("帮助", "help"):
                await event.send(event.plain_result(HELP))
            elif verb in ("状态", "status"):
                await event.send(event.plain_result(self.status(event)))
            elif verb in ("语音", "文字", "默认", "voice", "text", "default"):
                key = self.key(event)
                if verb in ("默认", "default"):
                    self.modes.pop(key, None)
                else:
                    self.modes[key] = "voice" if verb in ("语音", "voice") else "text"
                temp = self.mode_path.with_suffix(".tmp")
                temp.write_text(json.dumps(self.modes), encoding="utf-8")
                temp.replace(self.mode_path)
                confirmation = (
                    "好呀，接下来我用语音和你聊。"
                    if self.mode(event) == "voice"
                    else "好，接下来用文字回复你。"
                )
                if self.mode(event) == "voice":
                    try:
                        records = await self.make_records(event, confirmation)
                        for record in records:
                            await event.send(event.chain_result([record]))
                    except PluginError as error:
                        self.log_voice_failure(error)
                        await event.send(event.plain_result(confirmation))
                else:
                    await event.send(event.plain_result(confirmation))
            else:
                await self.perform(event, verb, argument)
        except PluginError as error:
            await event.send(event.plain_result(str(error)))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(f"[voice_addon] 操作失败：{type(error).__name__}")
            await event.send(event.plain_result("这次没能发出来，请稍后再试。"))

    async def perform(self, event, verb, argument):
        if self.closing:
            raise PluginError("插件正在关闭。")
        if not self.allowed(str(event.get_sender_id())):
            raise PluginError("你不在此插件的使用名单中。")
        if verb in ("说", "say"):
            if not argument:
                raise PluginError("用法：/说话 要说的内容")
            records = await self.make_records(event, argument)
            if not records:
                raise PluginError("这段内容没有可以朗读的文字。")
            for record in records:
                await event.send(event.chain_result([record]))
            event.set_extra(PREFIX + "tool_audio", True)
            return "语音已发送，不要重复发送相同文字。"
        if verb not in ("唱", "sing", "写歌", "music"):
            raise PluginError("发送 /voice 帮助 查看说话、唱歌和模式切换。")
        if not argument:
            raise PluginError("用法：/唱歌 音频直链，或 /写歌 风格描述 | 歌词")
        key = self.key(event)
        self.gate.enter(key)
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            self.cleanup()
            if verb in ("唱", "sing"):
                self.cloud.rvc_settings()
                audio = await self.cloud.convert(
                    Audio(url=public_url(argument), extension="mp3")
                )
            else:
                prompt, separator, lyrics = argument.partition("|")
                audio = await self.cloud.music(
                    prompt.strip(), lyrics.strip() if separator else ""
                )
            await self.send_audio(event, audio, song=True)
            event.set_extra(PREFIX + "tool_audio", True)
            return "歌曲已发送，不要重复发送歌曲或技术过程说明。"
        finally:
            self.gate.leave(key)
            self.tasks.discard(task)

    @filter.llm_tool(name="voice_speak")
    async def speak_tool(self, event: AstrMessageEvent, text: str) -> str:
        """仅当用户明确要求用语音说出某段内容时调用，普通回复由插件自动处理。

        Args:
            text(string): 需要说出的自然语言内容，不要包含思考过程或技术说明。
        """
        return await self.tool_action(event, "说", text)

    @filter.llm_tool(name="voice_sing_cover")
    async def cover_tool(self, event: AstrMessageEvent, audio_url: str) -> str:
        """用户明确要求翻唱且提供了演唱音频直链时调用，不能把歌名或网页地址当成音频。

        Args:
            audio_url(string): 用户提供的可公网下载的演唱音频直链。
        """
        return await self.tool_action(event, "唱", audio_url)

    @filter.llm_tool(name="voice_compose_song")
    async def compose_tool(
        self, event: AstrMessageEvent, prompt: str, lyrics: str = ""
    ) -> str:
        """仅当用户明确要求创作一首新歌时调用，生成并发送歌曲。

        Args:
            prompt(string): 用户想要的歌曲主题、曲风和演唱风格。
            lyrics(string): 用户提供或当前模型已经创作的歌词，可为空字符串。
        """
        return await self.tool_action(
            event, "写歌", prompt + (" | " + lyrics if lyrics else "")
        )

    async def tool_action(self, event, verb, argument):
        try:
            return await self.perform(event, verb, argument)
        except PluginError as error:
            return str(error)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(f"[voice_addon] 工具失败：{type(error).__name__}")
            return "音频发送失败，不要向用户声称已经成功。"

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

    def status(self, event):
        def filled(section, env):
            return (
                "已填写（未验证）"
                if self.config.get(section, {}).get("api_key") or os.getenv(env)
                else "未填写"
            )

        provider = self.config.get("speech", {}).get("provider", "minimax")
        voice = (
            self.config.get("qwen", {}).get("voice") or "Cherry"
            if provider == "qwen"
            else self.config.get("minimax", {}).get("voice_id") or "lovely_girl"
        )
        return "\n".join(
            [
                f"语音与角色翻唱 v{VERSION}",
                "当前回复模式："
                + ("只发语音" if self.mode(event) == "voice" else "只发文字"),
                "语音服务：" + str(provider),
                "说话音色：" + str(voice),
                "MiniMax Key：" + filled("minimax", "MINIMAX_API_KEY"),
                "千问 Key：" + filled("qwen", "DASHSCOPE_API_KEY"),
                "Replicate Key：" + filled("rvc", "REPLICATE_API_TOKEN"),
            ]
        )

    async def terminate(self):
        self.closing = True
        active = [
            task for task in tuple(self.tasks) if task is not asyncio.current_task()
        ]
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        await self.cloud.close()
