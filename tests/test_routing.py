"""Exercise the plugin's routing with small AstrBot event/transport doubles."""

import importlib
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
        event_message_type=decorator,
        EventMessageType=types.SimpleNamespace(ALL=1),
    )
    star = types.ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = type("Star", (), {"__init__": lambda self, context: None})
    star.StarTools = Mock()
    components = types.ModuleType("astrbot.api.message_components")

    class Component:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    components.Record = type("Record", (Component,), {})
    components.File = type("File", (Component,), {})
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


class Event:
    def __init__(self, text="dsh 你好", sender="10001"):
        self.message_str = text
        self.sender = sender
        self.unified_msg_origin = "qq:group:20001"
        self.is_at_or_wake_command = True
        self.extras = {}
        self.stopped = False
        self.messages = []

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def get_sender_id(self):
        return self.sender

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain

    async def send(self, message):
        self.messages.append(message)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.plugin = object.__new__(main.DshVoicePlugin)
        self.plugin.config = config()
        self.plugin.root = Path(self.folder.name)
        self.plugin.media_dir = self.plugin.root
        self.plugin.gate = core.RequestGate(cooldown=0)
        self.plugin.tasks = set()
        self.plugin.closing = False
        self.plugin.dsh = types.SimpleNamespace(
            chat=AsyncMock(return_value="角色回复"), reset=AsyncMock()
        )
        self.plugin.cloud = types.SimpleNamespace(
            speech=AsyncMock(return_value=providers.Audio(content=wav_bytes())),
            convert=AsyncMock(return_value=providers.Audio(content=wav_bytes())),
            music=AsyncMock(return_value=providers.Audio(content=wav_bytes())),
            rvc_settings=Mock(),
            minimax_key=Mock(),
        )
        self.plugin.send_audio = AsyncMock()

    async def test_command_conversation_no_duplicate_autochat(self):
        self.plugin.config["auto_chat"] = True
        event = Event()
        await self.plugin.dsh_command(event)
        await self.plugin.auto_chat(event)
        self.assertTrue(event.stopped)
        self.plugin.dsh.chat.assert_awaited_once()
        self.assertEqual(event.messages, ["角色回复"])
        self.plugin.send_audio.assert_awaited_once()
        self.assertFalse(self.plugin.gate.active)

    async def test_speech_failure_preserves_text(self):
        self.plugin.cloud.speech.side_effect = PluginError("语音余额不足")
        event = Event()
        await self.plugin.dsh_command(event)
        self.assertEqual(event.messages, ["角色回复", "语音余额不足"])

    async def test_user_allowlist_blocks_paid_requests(self):
        self.plugin.config["limits"]["allowed_users"] = ["99999"]
        event = Event()
        await self.plugin.dsh_command(event)
        self.plugin.dsh.chat.assert_not_awaited()
        self.plugin.cloud.speech.assert_not_awaited()

    async def test_lyrics_newlines_preserved(self):
        event = Event("dsh 写歌 民谣 | [Verse]\n第一句\n第二句")
        await self.plugin.dsh_command(event)
        self.plugin.cloud.music.assert_awaited_once_with(
            "民谣", "[Verse]\n第一句\n第二句"
        )

    async def test_cover_uses_rvc_and_not_text_to_speech(self):
        event = Event("dsh 唱 https://audio.example.org/song.mp3")
        await self.plugin.dsh_command(event)
        self.plugin.cloud.convert.assert_awaited_once()
        self.plugin.cloud.speech.assert_not_awaited()
        self.plugin.send_audio.assert_awaited_once()

    async def test_status_never_exposes_keys(self):
        event = Event("dsh 状态")
        await self.plugin.dsh_command(event)
        self.assertNotIn("fake-", event.messages[0])
        self.assertIn("未验证", event.messages[0])

    async def test_record_uses_separate_wav_audio_component(self):
        self.plugin.cloud.download = AsyncMock(
            side_effect=lambda audio, path: (path.write_bytes(wav_bytes()), path)[1]
        )
        del self.plugin.send_audio
        event = Event()
        await self.plugin.send_audio(
            event, providers.Audio(content=wav_bytes()), song=False
        )
        self.assertEqual(type(event.messages[0][0]).__name__, "Record")
        self.assertTrue(Path(event.messages[0][0].file).exists())


if __name__ == "__main__":
    unittest.main()
