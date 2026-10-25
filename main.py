from __future__ import annotations

import asyncio
import copy
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
from .providers import CloudAudio

VERSION = "0.6.0"
PREFIX = "voice_addon_"
MEDIA_POLICY = """[语音外挂]
每条回复的表达形式由你根据当前对话、用户意愿和原有角色性格自主选择，不需要用户切换模式。
选择文字时直接正常回复，不调用音频工具。聊天适合开口、表达情绪、问候或口头解释时，你可以自主调用 voice_speak，把真正要说的正文放进 text；不要只是宣称自己正在说话。
用户对本次发送形式有明确要求时优先遵从。仅在本轮实际提供对应工具时调用；没有可用工具就自然回复文字，不输出伪造的调用代码。
语音工具发送成功后，本轮不要再发送文字正文、相同内容的转录或成功确认，直接结束即可。工具返回只是给你的执行结果，不要照读给用户。
若语音发送失败且没有声音送出，就用文字自然回答；不要假装已经成功，也不要向用户展示 API 字段或技术过程。
这段规则只补充发送方式，不改变你原有的人格、记忆和聊天能力。"""
HELP = """语音说话 · 精简版
每条回复由现有模型自行决定发文字还是语音，无需切换模式。
/说话 今天也要开心 —— 手动测试语音
/voice 状态 —— 查看语音配置
/voice 帮助 —— 查看说明
配置面板只需填写 MiniMax 或千问语音 API 与音色。现有模型需启用工具调用，才能自主选择语音。"""


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

    def key(self, event):
        return conversation_key(event.unified_msg_origin, str(event.get_sender_id()))

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
        """Keep model-written text unless this turn has actually sent audio."""
        if not self.event_enabled(event):
            return
        result = event.get_result()
        if result is None or not result.is_llm_result():
            return
        self.restore_stream(event)
        if event.get_extra(PREFIX + "tool_audio", False) or any(
            isinstance(part, Comp.Record) for part in (result.chain or [])
        ):
            result.chain = [
                part
                for part in (result.chain or [])
                if not isinstance(part, Comp.Plain)
            ]

    @filter.on_llm_request()
    async def prepare_stream(self, event: AstrMessageEvent, req):
        """Offer audio tools and wait for the model to choose this turn's medium."""
        if not self.event_enabled(event):
            return
        # A stable addition preserves the existing persona and avoids a second model call.
        if req is not None and hasattr(req, "system_prompt"):
            current = req.system_prompt or ""
            if MEDIA_POLICY not in current:
                req.system_prompt = current + "\n\n" + MEDIA_POLICY
        if event.get_extra(PREFIX + "stream"):
            return
        original = event.send_streaming

        async def wrapped(generator, *args, **kwargs):
            async def audio_stream():
                texts = []
                has_audio = False
                async for chain in generator:
                    kind = getattr(chain, "type", None)
                    if kind == "break":
                        # Only the final text is eligible; audio already sent stays sent.
                        texts.clear()
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
                    text
                    and not has_audio
                    and not event.get_extra(PREFIX + "tool_audio", False)
                ):
                    # No audio tool was selected (or it failed): deliver the model's text.
                    # Buffering prevents a late voice tool from duplicating earlier text.
                    yield MessageChain([Comp.Plain(text)])

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
        logger.warning(f"[voice_addon] 音频调用未完成：{detail}")

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
        """查看模型自主选择语音的配置，或手动测试朗读。"""
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
                await event.send(
                    event.plain_result(
                        "现在每条回复由模型自行选择文字或语音，无需固定模式。你可以正常聊天，或用 /说话 测试声音。"
                    )
                )
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
                # Mark each delivered segment, including partial success before failure.
                event.set_extra(PREFIX + "tool_audio", True)
            return "语音已发送，不要重复发送相同文字。"
        raise PluginError("发送 /voice 帮助 查看说话和配置说明。")

    @filter.llm_tool(name="voice_speak")
    async def speak_tool(self, event: AstrMessageEvent, text: str) -> str:
        """由你根据当前对话自主决定是否用语音表达；闲聊、问候、表达情绪等适合时可以调用。选择文字就正常回复，不调用本工具。成功后不要再发送文字确认或重复正文。

        Args:
            text(string): 需要说出的自然语言内容，不要包含思考过程或技术说明。
        """
        return await self.tool_action(event, "说", text)

    async def tool_action(self, event, verb, argument):
        # Provider/tool loops can repeat identical calls. Never send/pay twice per turn.
        calls = event.get_extra(PREFIX + "tool_calls")
        if calls is None:
            calls = {}
            event.set_extra(PREFIX + "tool_calls", calls)
        signature = conversation_key(verb, argument)
        if signature in calls:
            return (
                calls[signature]
                or "同一段音频正在发送，请等待，不要重复调用或声称已经成功。"
            )
        calls[signature] = None
        try:
            result = await self.perform(event, verb, argument)
        except PluginError as error:
            self.log_voice_failure(error)
            result = str(error)
            if event.get_extra(PREFIX + "tool_audio", False):
                result += " 已有部分音频送出，本轮不要再发文字或从头重复播放。"
            else:
                result += " 本次音频未发送，请自然地用文字回复，不要声称已经成功。"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(f"[voice_addon] 工具失败：{type(error).__name__}")
            result = (
                "后续音频发送失败；已有音频送出，不要再发文字或从头重复。"
                if event.get_extra(PREFIX + "tool_audio", False)
                else "音频未发送，请自然地用文字回答，不要声称已经成功。"
            )
        calls[signature] = result
        return result

    async def ensure_wav(self, source: Path) -> Path:
        with source.open("rb") as handle:
            header = handle.read(12)
        if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            return source
        executable = shutil.which("ffmpeg")
        if executable is None:
            raise PluginError(
                "云端返回的音频不是 WAV。请安装 FFmpeg 并加入 PATH；MiniMax 默认请求 WAV，无需转换。"
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
            **({"creationflags": 0x08000000} if os.name == "nt" else {}),
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
        provider = self.config.get("speech", {}).get("provider", "minimax")
        section, env = (
            ("qwen", "DASHSCOPE_API_KEY")
            if provider == "qwen"
            else ("minimax", "MINIMAX_API_KEY")
        )
        cfg = self.config.get(section, {})
        voice = (
            (cfg.get("voice") or "Cherry")
            if provider == "qwen"
            else (cfg.get("voice_id") or "lovely_girl")
        )
        filled = (
            "已填写（未验证）" if cfg.get("api_key") or os.getenv(env) else "未填写"
        )
        return "\n".join(
            [
                f"语音说话 · 精简版 v{VERSION}",
                "发送方式：由现有聊天模型每条自主选择文字或语音",
                "语音服务：" + str(provider),
                "说话音色：" + str(voice),
                "API Key：" + filled,
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
