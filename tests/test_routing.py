"""Verify addon delivery without invoking any chat model or cloud service."""

import asyncio
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from test_plugin import PACKAGE, PluginError, config, core, providers, wav_bytes


def load_main():
    def decorator(*args, **kwargs):
        return lambda function: function

    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = Mock()
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.filter = types.SimpleNamespace(
        command=decorator,
        llm_tool=decorator,
        on_llm_request=decorator,
        on_decorating_result=decorator,
    )
    star = types.ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = type("Star", (), {"__init__": lambda self, context: None})
    star.StarTools = Mock()
    components = types.ModuleType("astrbot.api.message_components")

    class Component:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Plain(Component):
        def __init__(self, text):
            self.text = text

    components.Plain = Plain
    components.Record = type("Record", (Component,), {})
    components.File = type("File", (Component,), {})
    components.Image = type("Image", (Component,), {})

    class MessageChain:
        def __init__(self, chain=None, type=None):
            self.chain = chain or []
            self.type = type

    event.MessageChain = MessageChain
    sys.modules.update(
        {
            "astrbot": types.ModuleType("astrbot"),
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.star": star,
            "astrbot.api.message_components": components,
        }
    )
    return importlib.import_module(PACKAGE + ".main")


main = load_main()


class Result(main.MessageChain):
    def __init__(self, chain=None, llm=True):
        super().__init__(chain)
        self.llm = llm

    def is_llm_result(self):
        return self.llm


class Event:
    def __init__(self, text="你好", sender="10001", result=None):
        self.message_str = text
        self.sender = sender
        self.unified_msg_origin = "qq:group:20001"
        self.extras = {}
        self.stopped = False
        self.messages = []
        self.result = (
            result
            if result is not None
            else Result([main.Comp.Plain("今天过得怎么样？")])
        )
        self.streamed = []

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def get_sender_id(self):
        return self.sender

    def get_result(self):
        return self.result

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain

    async def send(self, message):
        self.messages.append(message)

    async def send_streaming(self, generator, *args, **kwargs):
        async for chain in generator:
            self.streamed.append(chain)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.plugin = object.__new__(main.VoiceAddonPlugin)
        self.plugin.config = config()
        self.plugin.root = Path(self.folder.name)
        self.plugin.media_dir = self.plugin.root
        self.plugin.mode_path = self.plugin.root / "reply-modes.json"
        self.plugin.modes = {}
        self.plugin.gate = core.RequestGate(cooldown=0)
        self.plugin.tasks = set()
        self.plugin.closing = False
        self.plugin.cloud = types.SimpleNamespace(
            speech=AsyncMock(return_value=providers.Audio(content=wav_bytes())),
            convert=AsyncMock(return_value=providers.Audio(content=wav_bytes())),
            music=AsyncMock(return_value=providers.Audio(content=wav_bytes())),
            rvc_settings=Mock(),
            minimax_key=Mock(),
            close=AsyncMock(),
            download=AsyncMock(
                side_effect=lambda audio, path: (path.write_bytes(wav_bytes()), path)[1]
            ),
        )
        self.record = main.Comp.Record(file="voice.wav", url="voice.wav")
        self.plugin.make_records = AsyncMock(return_value=[self.record])
        self.plugin.send_audio = AsyncMock()

    async def stream(self, event, chunks):
        async def source():
            for chunk in chunks:
                yield chunk

        await self.plugin.prepare_stream(event, None)
        await event.send_streaming(source(), True)

    async def test_voice_mode_replaces_answer_text_without_calling_chat_model(self):
        event = Event()
        await self.plugin.decorate_reply(event)
        self.assertEqual(event.result.chain, [self.record])
        self.assertFalse(event.stopped)
        self.assertEqual(event.messages, [])
        self.plugin.make_records.assert_awaited_once_with(event, "今天过得怎么样？")
        self.assertFalse(hasattr(self.plugin, "dsh"))

    async def test_text_mode_preserves_reply_without_tts(self):
        self.plugin.config["reply_mode"] = "text"
        event = Event()
        original = event.result.chain
        await self.plugin.decorate_reply(event)
        self.assertIs(event.result.chain, original)
        self.plugin.make_records.assert_not_awaited()

    async def test_voice_failure_keeps_only_original_text(self):
        self.plugin.make_records.side_effect = PluginError("余额不足")
        event = Event()
        original = event.result.chain
        await self.plugin.decorate_reply(event)
        self.assertIs(event.result.chain, original)
        self.assertEqual(event.messages, [])

    async def test_text_not_removed_before_audio_is_ready(self):
        pending = asyncio.get_running_loop().create_future()

        async def delayed(*args):
            return await pending

        self.plugin.make_records.side_effect = delayed
        event = Event()
        task = asyncio.create_task(self.plugin.decorate_reply(event))
        await asyncio.sleep(0)
        self.assertIsInstance(event.result.chain[0], main.Comp.Plain)
        pending.set_result([self.record])
        await task
        self.assertEqual(event.result.chain, [self.record])

    async def test_already_spoken_reply_is_not_synthesized_twice(self):
        event = Event(result=Result([main.Comp.Plain("你好"), self.record]))
        await self.plugin.decorate_reply(event)
        self.assertEqual(event.result.chain, [self.record])
        self.plugin.make_records.assert_not_awaited()

    async def test_plugin_messages_and_unallowed_users_are_not_modified(self):
        event = Event(result=Result([main.Comp.Plain("插件帮助")], llm=False))
        await self.plugin.decorate_reply(event)
        self.plugin.config["limits"]["allowed_users"] = ["99999"]
        await self.plugin.decorate_reply(Event())
        self.plugin.make_records.assert_not_awaited()

    async def test_empty_tool_round_does_not_consume_final_reply(self):
        event = Event(result=Result([]))
        await self.plugin.decorate_reply(event)
        event.result = Result([main.Comp.Plain("最终答案")])
        await self.plugin.decorate_reply(event)
        self.plugin.make_records.assert_awaited_once_with(event, "最终答案")

    async def test_nontext_media_is_preserved(self):
        image = main.Comp.Image(file="picture.png")
        event = Event(result=Result([main.Comp.Plain("看这张图"), image]))
        await self.plugin.decorate_reply(event)
        self.assertEqual(event.result.chain, [image, self.record])

    async def test_stream_sends_voice_without_answer_text_or_reasoning(self):
        event = Event()
        original = event.send_streaming
        await self.stream(
            event,
            [
                main.MessageChain([main.Comp.Plain("秘密思考")], type="reasoning"),
                main.MessageChain([main.Comp.Plain("你")]),
                main.MessageChain([main.Comp.Plain("好呀！")]),
            ],
        )
        self.assertEqual(
            [part for chain in event.streamed for part in chain.chain], [self.record]
        )
        self.plugin.make_records.assert_awaited_once_with(event, "你好呀！")
        self.assertEqual(event.send_streaming, original)

    async def test_text_stream_is_unchanged_and_does_not_synthesize(self):
        self.plugin.config["reply_mode"] = "text"
        event = Event()
        chunks = [main.MessageChain([main.Comp.Plain("原来的回答")])]
        await self.stream(event, chunks)
        self.assertEqual(event.streamed, chunks)
        self.plugin.make_records.assert_not_awaited()

    async def test_stream_tts_failure_emits_original_text_once(self):
        self.plugin.make_records.side_effect = PluginError("网络错误")
        event = Event()
        await self.stream(
            event,
            [
                main.MessageChain([main.Comp.Plain("你")]),
                main.MessageChain([main.Comp.Plain("好")]),
            ],
        )
        parts = [part for chain in event.streamed for part in chain.chain]
        self.assertEqual([part.text for part in parts], ["你好"])

    async def test_stream_does_not_emit_partial_text_while_tts_is_pending(self):
        pending = asyncio.get_running_loop().create_future()

        async def delayed(*args):
            return await pending

        self.plugin.make_records.side_effect = delayed
        event = Event()
        task = asyncio.create_task(
            self.stream(event, [main.MessageChain([main.Comp.Plain("你好")])])
        )
        await asyncio.sleep(0)
        self.assertEqual(event.streamed, [])
        pending.set_result([self.record])
        await task
        self.assertEqual(event.streamed[0].chain, [self.record])

    async def test_stream_tool_preamble_is_not_spoken(self):
        event = Event()
        await self.stream(
            event,
            [
                main.MessageChain([main.Comp.Plain("我查一下")]),
                main.MessageChain(type="break"),
                main.MessageChain([main.Comp.Plain("查到了")]),
            ],
        )
        self.plugin.make_records.assert_awaited_once_with(event, "查到了")

    async def test_native_stream_voice_is_not_duplicated(self):
        event = Event()
        await self.stream(
            event,
            [
                main.MessageChain([self.record]),
                main.MessageChain([main.Comp.Plain("转录文字")]),
            ],
        )
        self.plugin.make_records.assert_not_awaited()
        self.assertEqual(
            [part for chain in event.streamed for part in chain.chain], [self.record]
        )

    async def test_broken_stream_restores_original_sender(self):
        event = Event()
        original = event.send_streaming

        async def broken():
            yield main.MessageChain([main.Comp.Plain("未完成")])
            raise RuntimeError("model failed")

        await self.plugin.prepare_stream(event, None)
        with self.assertRaises(RuntimeError):
            await event.send_streaming(broken())
        self.assertEqual(event.send_streaming, original)
        self.plugin.make_records.assert_not_awaited()

    async def test_prepare_stream_does_not_wrap_twice(self):
        event = Event()
        await self.plugin.prepare_stream(event, None)
        wrapped = event.send_streaming
        await self.plugin.prepare_stream(event, None)
        self.assertIs(event.send_streaming, wrapped)
        self.plugin.restore_stream(event)

    async def test_mode_command_persists_and_is_per_user(self):
        event = Event("voice 文字")
        await self.plugin.voice_command(event)
        self.assertEqual(self.plugin.mode(event), "text")
        self.assertEqual(self.plugin.mode(Event(sender="10002")), "voice")
        saved = json.loads(self.plugin.mode_path.read_text("utf-8"))
        self.assertEqual(saved[self.plugin.key(event)], "text")
        event.message_str = "voice 默认"
        await self.plugin.voice_command(event)
        self.assertEqual(self.plugin.mode(event), "voice")

    async def test_manual_speech_sends_no_text_copy(self):
        event = Event("说话 今天天气真好")
        await self.plugin.say_command(event)
        self.assertEqual(event.messages, [[self.record]])
        self.assertTrue(event.stopped)

    async def test_switching_to_voice_confirms_in_voice_only(self):
        event = Event("voice 语音")
        await self.plugin.voice_command(event)
        self.assertEqual(event.messages, [[self.record]])

    async def test_explicit_speech_tool_does_not_add_text_confirmation_in_text_mode(
        self,
    ):
        self.plugin.config["reply_mode"] = "text"
        event = Event()
        await self.plugin.speak_tool(event, "你好")
        await self.plugin.decorate_reply(event)
        self.assertEqual(event.messages, [[self.record]])
        self.assertEqual(event.result.chain, [])

    async def test_streamed_tool_confirmation_is_suppressed_in_text_mode(self):
        self.plugin.config["reply_mode"] = "text"
        event = Event()

        async def source():
            await self.plugin.speak_tool(event, "你好")
            yield main.MessageChain([main.Comp.Plain("已说出你好")])

        await self.plugin.prepare_stream(event, None)
        await event.send_streaming(source())
        self.assertEqual(event.messages, [[self.record]])
        self.assertEqual(event.streamed, [])

    async def test_lyrics_newlines_preserved(self):
        event = Event("voice 写歌 民谣 | [Verse]\n第一句\n第二句")
        await self.plugin.voice_command(event)
        self.plugin.cloud.music.assert_awaited_once_with(
            "民谣", "[Verse]\n第一句\n第二句"
        )
        self.assertEqual(event.messages, [])

    async def test_cover_uses_rvc_without_a_chat_or_tts_request(self):
        event = Event("唱歌 https://audio.example.org/song.mp3")
        await self.plugin.sing_command(event)
        self.plugin.cloud.convert.assert_awaited_once()
        self.plugin.cloud.speech.assert_not_awaited()
        self.plugin.send_audio.assert_awaited_once()
        self.assertEqual(event.messages, [])

    async def test_existing_model_can_call_song_tool(self):
        event = Event()
        reply = await self.plugin.cover_tool(
            event, "https://audio.example.org/song.mp3"
        )
        self.assertIn("歌曲已发送", reply)
        self.plugin.cloud.convert.assert_awaited_once()
        await self.plugin.decorate_reply(event)
        self.plugin.make_records.assert_not_awaited()
        self.assertEqual(event.result.chain, [])

    async def test_failed_tool_does_not_report_success(self):
        self.plugin.cloud.convert.side_effect = PluginError("音色未配置")
        reply = await self.plugin.cover_tool(
            Event(), "https://audio.example.org/song.mp3"
        )
        self.assertEqual(reply, "音色未配置")

    async def test_real_record_builder_uses_cloud_tts_and_wav_only(self):
        del self.plugin.make_records
        event = Event()
        await self.plugin.decorate_reply(event)
        self.assertTrue(
            all(isinstance(part, main.Comp.Record) for part in event.result.chain)
        )
        self.assertTrue(Path(event.result.chain[0].file).exists())
        self.assertFalse(self.plugin.gate.active)

    async def test_second_chunk_failure_does_not_send_first_voice_or_remove_text(self):
        del self.plugin.make_records
        self.plugin.config["speech"]["max_characters"] = 30
        self.plugin.cloud.speech.side_effect = [
            providers.Audio(content=wav_bytes()),
            PluginError("second failed"),
        ]
        event = Event(
            result=Result([main.Comp.Plain("这是一段比较长的中文回复。" * 8)])
        )
        original = event.result.chain
        await self.plugin.decorate_reply(event)
        self.assertIs(event.result.chain, original)
        self.assertEqual(event.messages, [])
        self.assertFalse(self.plugin.gate.active)

    async def test_status_never_exposes_keys_or_requires_chat_credentials(self):
        event = Event("voice 状态")
        await self.plugin.voice_command(event)
        self.assertNotIn("fake-", event.messages[0])
        self.assertNotIn("DSH", event.messages[0])
        self.assertIn("只发语音", event.messages[0])


if __name__ == "__main__":
    unittest.main()
