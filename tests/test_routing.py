"""Verify addon delivery without invoking any chat model or cloud service."""

import asyncio
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

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
        self.plugin.gate = core.RequestGate(cooldown=0)
        self.plugin.tasks = set()
        self.plugin.closing = False
        self.plugin.cloud = types.SimpleNamespace(
            speech=AsyncMock(return_value=providers.Audio(content=wav_bytes())),
            close=AsyncMock(),
            download=AsyncMock(
                side_effect=lambda audio, path: (path.write_bytes(wav_bytes()), path)[1]
            ),
        )
        self.plugin.cloud.config = self.plugin.config
        self.record = main.Comp.Record(file="voice.wav", url="voice.wav")
        self.plugin.make_records = AsyncMock(return_value=[self.record])

    async def stream(self, event, chunks):
        async def source():
            for chunk in chunks:
                yield chunk

        await self.plugin.prepare_stream(event, None)
        await event.send_streaming(source(), True)

    async def test_plain_model_answer_stays_text_without_forced_tts(self):
        event = Event()
        original = event.result.chain
        await self.plugin.decorate_reply(event)
        self.assertIs(event.result.chain, original)
        self.plugin.make_records.assert_not_awaited()
        self.assertFalse(event.stopped)
        self.assertEqual(event.messages, [])
        self.assertFalse(hasattr(self.plugin, "dsh"))

    async def test_policy_preserves_persona_and_requests_model_choice(self):
        event = Event()
        req = types.SimpleNamespace(system_prompt="原来的角色设定")
        await self.plugin.prepare_stream(event, req)
        self.assertTrue(req.system_prompt.startswith("原来的角色设定"))
        self.assertIn("自主选择", req.system_prompt)
        self.assertIn("voice_speak", req.system_prompt)
        self.assertIn("选择文字时直接正常回复", req.system_prompt)
        self.plugin.make_records.assert_not_awaited()
        self.assertFalse(event.stopped)
        self.plugin.restore_stream(event)

    async def test_policy_and_stream_wrapper_are_not_added_twice(self):
        event = Event()
        req = types.SimpleNamespace(system_prompt="原人格")
        await self.plugin.prepare_stream(event, req)
        wrapped = event.send_streaming
        await self.plugin.prepare_stream(event, req)
        self.assertIs(event.send_streaming, wrapped)
        self.assertEqual(req.system_prompt.count(main.MEDIA_POLICY), 1)
        self.plugin.restore_stream(event)

    async def test_model_can_choose_voice_without_explicit_voice_request(self):
        event = Event("今天有点累")
        result = await self.plugin.speak_tool(event, "辛苦啦，先休息一会儿吧。")
        await self.plugin.decorate_reply(event)
        self.assertIn("语音已发送", result)
        self.assertEqual(event.messages, [[self.record]])
        self.assertEqual(event.result.chain, [])
        self.plugin.make_records.assert_awaited_once()

    async def test_next_turn_can_choose_text_after_previous_voice(self):
        first = Event()
        await self.plugin.speak_tool(first, "你好呀")
        second = Event()
        original = second.result.chain
        await self.plugin.decorate_reply(second)
        self.assertIs(second.result.chain, original)
        self.assertFalse(second.get_extra(main.PREFIX + "tool_audio", False))
        self.plugin.make_records.assert_awaited_once()

    async def test_old_fixed_mode_configuration_and_file_are_ignored(self):
        self.plugin.config["reply_mode"] = "voice"
        (self.plugin.root / "reply-modes.json").write_text(
            json.dumps({"old-user": "voice"}), encoding="utf-8"
        )
        with (
            patch.object(main.StarTools, "get_data_dir", return_value=self.plugin.root),
            patch.object(main, "CloudAudio", return_value=self.plugin.cloud),
        ):
            fresh = main.VoiceAddonPlugin(object(), self.plugin.config)
        event = Event()
        original = event.result.chain
        await fresh.decorate_reply(event)
        self.assertFalse(hasattr(fresh, "modes"))
        self.assertFalse(hasattr(fresh, "mode_path"))
        self.assertIs(event.result.chain, original)
        self.plugin.cloud.speech.assert_not_awaited()

    async def test_voice_failure_keeps_model_text_and_never_claims_success(self):
        self.plugin.make_records.side_effect = PluginError("余额不足")
        event = Event()
        result = await self.plugin.speak_tool(event, "你好")
        original = event.result.chain
        await self.plugin.decorate_reply(event)
        self.assertIn("本次音频未发送", result)
        self.assertNotIn("语音已发送", result)
        self.assertIs(event.result.chain, original)
        self.assertEqual(event.messages, [])

    async def test_text_not_suppressed_until_audio_delivery_succeeds(self):
        pending = asyncio.get_running_loop().create_future()

        async def delayed(*args):
            return await pending

        self.plugin.make_records.side_effect = delayed
        event = Event()
        task = asyncio.create_task(self.plugin.speak_tool(event, "你好"))
        await asyncio.sleep(0)
        original = event.result.chain
        await self.plugin.decorate_reply(event)
        self.assertIs(event.result.chain, original)
        pending.set_result([self.record])
        await task
        await self.plugin.decorate_reply(event)
        self.assertEqual(event.result.chain, [])

    async def test_successful_voice_tool_suppresses_text_but_preserves_image(self):
        image = main.Comp.Image(file="picture.png")
        event = Event(result=Result([main.Comp.Plain("看这张图"), image]))
        await self.plugin.speak_tool(event, "看这张图")
        await self.plugin.decorate_reply(event)
        self.assertEqual(event.result.chain, [image])
        self.assertEqual(event.messages, [[self.record]])

    async def test_existing_record_is_not_accompanied_by_transcript(self):
        event = Event(result=Result([main.Comp.Plain("你好"), self.record]))
        await self.plugin.decorate_reply(event)
        self.assertEqual(event.result.chain, [self.record])
        self.plugin.make_records.assert_not_awaited()

    async def test_plugin_messages_and_unallowed_users_are_not_changed(self):
        event = Event(result=Result([main.Comp.Plain("插件帮助")], llm=False))
        event.set_extra(main.PREFIX + "tool_audio", True)
        original = event.result.chain
        await self.plugin.decorate_reply(event)
        self.assertIs(event.result.chain, original)
        self.plugin.config["limits"]["allowed_users"] = ["99999"]
        reply = await self.plugin.speak_tool(Event(), "你好")
        self.assertIn("不在此插件", reply)
        self.plugin.make_records.assert_not_awaited()

    async def test_stream_without_audio_choice_sends_only_model_text(self):
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
        parts = [p for c in event.streamed for p in c.chain]
        self.assertEqual([p.text for p in parts], ["你好呀！"])
        self.plugin.make_records.assert_not_awaited()
        self.assertEqual(event.send_streaming, original)

    async def test_late_stream_voice_choice_does_not_leak_earlier_text(self):
        event = Event()

        async def source():
            yield main.MessageChain([main.Comp.Plain("我想安慰你")])
            self.assertEqual(event.streamed, [])
            await self.plugin.speak_tool(event, "辛苦啦")
            yield main.MessageChain(type="break")
            yield main.MessageChain([main.Comp.Plain("语音已经发给你了")])

        await self.plugin.prepare_stream(event, None)
        await event.send_streaming(source())
        self.assertEqual(event.messages, [[self.record]])
        self.assertEqual(event.streamed, [])

    async def test_failed_stream_audio_choice_falls_back_to_final_model_text(self):
        self.plugin.make_records.side_effect = PluginError("网络错误")
        event = Event()

        async def source():
            yield main.MessageChain([main.Comp.Plain("前言")])
            await self.plugin.speak_tool(event, "你好")
            yield main.MessageChain(type="break")
            yield main.MessageChain([main.Comp.Plain("先用文字跟你说，辛苦啦。")])

        await self.plugin.prepare_stream(event, None)
        await event.send_streaming(source())
        self.assertEqual(
            [p.text for c in event.streamed for p in c.chain],
            ["先用文字跟你说，辛苦啦。"],
        )
        self.assertEqual(event.messages, [])

    async def test_text_stream_waits_for_final_delivery_choice(self):
        released = asyncio.Event()
        event = Event()

        async def source():
            yield main.MessageChain([main.Comp.Plain("你好")])
            await released.wait()

        await self.plugin.prepare_stream(event, None)
        task = asyncio.create_task(event.send_streaming(source()))
        await asyncio.sleep(0)
        self.assertEqual(event.streamed, [])
        released.set()
        await task
        self.assertEqual(event.streamed[0].chain[0].text, "你好")
        self.plugin.make_records.assert_not_awaited()

    async def test_native_stream_voice_is_not_duplicated(self):
        event = Event()
        await self.stream(
            event,
            [
                main.MessageChain([self.record]),
                main.MessageChain([main.Comp.Plain("转录文字")]),
            ],
        )
        self.assertEqual([p for c in event.streamed for p in c.chain], [self.record])
        self.plugin.make_records.assert_not_awaited()

    async def test_native_voice_remains_authoritative_after_tool_round_break(self):
        event = Event()
        await self.stream(
            event,
            [
                main.MessageChain([self.record]),
                main.MessageChain(type="break"),
                main.MessageChain([main.Comp.Plain("声音发送后的文字确认")]),
            ],
        )
        self.assertEqual([p for c in event.streamed for p in c.chain], [self.record])
        self.plugin.make_records.assert_not_awaited()

    async def test_broken_stream_restores_sender_without_inventing_a_reply(self):
        event = Event()
        original = event.send_streaming

        async def broken():
            yield main.MessageChain([main.Comp.Plain("未完成")])
            raise RuntimeError("model failed")

        await self.plugin.prepare_stream(event, None)
        with self.assertRaises(RuntimeError):
            await event.send_streaming(broken())
        self.assertEqual(event.send_streaming, original)
        self.assertEqual(event.streamed, [])
        self.plugin.make_records.assert_not_awaited()

    async def test_repeated_tool_call_is_idempotent_after_success(self):
        event = Event()
        one = await self.plugin.speak_tool(event, "你好")
        two = await self.plugin.speak_tool(event, "你好")
        self.assertEqual(one, two)
        self.assertEqual(event.messages, [[self.record]])
        self.plugin.make_records.assert_awaited_once()

    async def test_repeated_failed_tool_call_does_not_retry_paid_request(self):
        self.plugin.make_records.side_effect = PluginError("余额不足")
        event = Event()
        one = await self.plugin.speak_tool(event, "你好")
        two = await self.plugin.speak_tool(event, "你好")
        self.assertEqual(one, two)
        self.plugin.make_records.assert_awaited_once()

    async def test_parallel_duplicate_tool_calls_do_not_send_twice(self):
        released = asyncio.Event()

        async def delayed(*args):
            await released.wait()
            return [self.record]

        self.plugin.make_records.side_effect = delayed
        event = Event()
        task = asyncio.create_task(self.plugin.speak_tool(event, "你好"))
        await asyncio.sleep(0)
        duplicate = await self.plugin.speak_tool(event, "你好")
        self.assertIn("正在发送", duplicate)
        released.set()
        await task
        self.assertEqual(event.messages, [[self.record]])
        self.plugin.make_records.assert_awaited_once()

    async def test_partial_audio_delivery_does_not_duplicate_text_or_restart(self):
        second = main.Comp.Record(file="second.wav", url="second.wav")
        self.plugin.make_records.return_value = [self.record, second]
        event = Event()
        original_send = event.send

        async def send(message):
            if message == [second]:
                raise RuntimeError("platform send failed")
            await original_send(message)

        event.send = send
        result = await self.plugin.speak_tool(event, "两段内容")
        self.assertIn("已有音频送出", result)
        await self.plugin.decorate_reply(event)
        self.assertEqual(event.messages, [[self.record]])
        self.assertEqual(event.result.chain, [])
        await self.plugin.speak_tool(event, "两段内容")
        self.plugin.make_records.assert_awaited_once()

    async def test_manual_speech_is_still_available_for_testing(self):
        event = Event("说话 今天天气真好")
        await self.plugin.say_command(event)
        self.assertEqual(event.messages, [[self.record]])
        self.assertTrue(event.stopped)

    async def test_old_mode_command_does_not_force_a_persistent_mode(self):
        event = Event("voice 语音")
        await self.plugin.voice_command(event)
        self.assertIn("模型自行选择", event.messages[0])
        self.plugin.make_records.assert_not_awaited()
        self.assertFalse((self.plugin.root / "reply-modes.json").exists())

    async def test_real_record_builder_runs_only_when_model_selects_voice(self):
        del self.plugin.make_records
        event = Event()
        await self.plugin.decorate_reply(event)
        self.plugin.cloud.speech.assert_not_awaited()
        await self.plugin.speak_tool(event, "你好")
        await self.plugin.decorate_reply(event)
        self.assertEqual(event.result.chain, [])
        self.assertTrue(Path(event.messages[0][0].file).exists())
        self.assertFalse(self.plugin.gate.active)

    async def test_second_chunk_generation_failure_preserves_original_text(self):
        del self.plugin.make_records
        self.plugin.config["speech"]["max_characters"] = 30
        self.plugin.cloud.speech.side_effect = [
            providers.Audio(content=wav_bytes()),
            PluginError("second failed"),
        ]
        event = Event()
        reply = await self.plugin.speak_tool(event, "这是一段比较长的中文回复。" * 8)
        original = event.result.chain
        await self.plugin.decorate_reply(event)
        self.assertIn("本次音频未发送", reply)
        self.assertIs(event.result.chain, original)
        self.assertEqual(event.messages, [])
        self.assertFalse(self.plugin.gate.active)

    async def test_legacy_song_configuration_never_starts_audio_work(self):
        self.plugin.config.update(
            singing={"base_url": "http://127.0.0.1:17860", "api_key": "old-key"},
            music={"enabled": True},
            cover={"songs": ["旧歌曲 | https://audio.example.org/song.mp3"]},
        )
        event = Event("voice 唱 旧歌曲")
        await self.plugin.voice_command(event)
        self.assertIn("说话和配置说明", event.messages[0])
        self.plugin.make_records.assert_not_awaited()
        self.plugin.cloud.speech.assert_not_awaited()
        for name in (
            "sing_command",
            "music_command",
            "cover_tool",
            "songs_tool",
            "compose_tool",
        ):
            self.assertFalse(hasattr(self.plugin, name))

    async def test_status_shows_autonomous_choice_and_never_exposes_keys(self):
        event = Event("voice 状态")
        await self.plugin.voice_command(event)
        self.assertNotIn("fake-", event.messages[0])
        self.assertNotIn("DSH", event.messages[0])
        self.assertIn("自主选择", event.messages[0])
        self.assertNotIn("只发语音", event.messages[0])


if __name__ == "__main__":
    unittest.main()
