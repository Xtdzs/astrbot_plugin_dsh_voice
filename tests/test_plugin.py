from __future__ import annotations

import importlib.util
import io
import json
import socket
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "dsh_voice_test_package"
spec = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
)
package = importlib.util.module_from_spec(spec)
sys.modules[PACKAGE] = package
spec.loader.exec_module(package)
core = __import__(PACKAGE + ".core", fromlist=["*"])
providers = __import__(PACKAGE + ".providers", fromlist=["*"])
PluginError = __import__(PACKAGE + ".errors", fromlist=["*"]).PluginError


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\0\0" * 100)
    return buffer.getvalue()


def config():
    def defaults(schema):
        return {
            k: defaults(v["items"]) if v["type"] == "object" else v.get("default")
            for k, v in schema.items()
        }

    cfg = defaults(json.loads((ROOT / "_conf_schema.json").read_text("utf-8")))
    cfg["minimax"]["api_key"] = "fake-minimax-secret"
    cfg["qwen"]["api_key"] = "fake-qwen-secret"
    cfg["singing"].update(
        api_key="fake-singing-secret", base_url="https://singing.example.org"
    )
    cfg["limits"]["cooldown_seconds"] = 0
    return cfg


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = config()

    async def client(self, handler):
        api = providers.CloudAudio(
            self.cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        self.addAsyncCleanup(api.close)
        return api

    async def test_minimax_speech_hex_and_wav_request(self):
        def handler(request):
            data = json.loads(request.content)
            self.assertEqual(request.url.path, "/v1/t2a_v2")
            self.assertEqual(data["audio_setting"]["format"], "wav")
            self.assertEqual(data["voice_setting"]["voice_id"], "lovely_girl")
            self.assertFalse(data["stream"])
            self.assertEqual(
                request.headers["authorization"], "Bearer fake-minimax-secret"
            )
            return httpx.Response(
                200,
                json={
                    "base_resp": {"status_code": 0},
                    "data": {"audio": wav_bytes().hex()},
                },
            )

        api = await self.client(handler)
        self.assertEqual((await api.speech("你好")).content, wav_bytes())

    async def test_qwen_uses_native_api_and_url(self):
        self.cfg["speech"]["provider"] = "qwen"

        def handler(request):
            data = json.loads(request.content)
            self.assertEqual(data["input"]["voice"], "Cherry")
            self.assertIn("multimodal-generation", request.url.path)
            return httpx.Response(
                200,
                json={
                    "output": {"audio": {"url": "https://audio.example.org/voice.wav"}}
                },
            )

        api = await self.client(handler)
        self.assertEqual(
            (await api.speech("你好")).url, "https://audio.example.org/voice.wav"
        )

    async def test_legacy_rvc_options_are_ignored_without_extra_requests(self):
        self.cfg["speech"]["character_conversion"] = True
        self.cfg["rvc"] = {"api_key": "old-secret"}
        handler = unittest.mock.Mock(
            return_value=httpx.Response(
                200, json={"data": {"audio": wav_bytes().hex()}}
            )
        )
        api = await self.client(handler)
        await api.speech("你好")
        self.assertEqual(handler.call_count, 1)
        self.assertFalse(hasattr(api, "convert"))

    async def test_minimax_music_lyrics_optimizer_only_without_lyrics(self):
        self.cfg["music"]["enabled"] = True
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(
                200, json={"data": {"audio": "https://audio.example.org/song.mp3"}}
            )

        api = await self.client(handler)
        await api.music("关于秋天的民谣")
        await api.music("流行", "[Verse]\n自己的歌词")
        self.assertTrue(bodies[0]["lyrics_optimizer"])
        self.assertFalse(bodies[1]["lyrics_optimizer"])

    async def test_new_song_generation_is_disabled_before_any_paid_request(self):
        handler = unittest.mock.Mock()
        api = await self.client(handler)
        with self.assertRaisesRegex(PluginError, "历史接口权限"):
            await api.music("轻快流行")
        handler.assert_not_called()

    async def test_audio_download_does_not_leak_key_across_redirect(self):
        headers = []

        def handler(request):
            headers.append((request.url.host, request.headers.get("authorization")))
            if request.url.host == "singing.example.org":
                return httpx.Response(
                    302, headers={"location": "https://cdn.example.org/audio.wav"}
                )
            return httpx.Response(
                200, content=wav_bytes(), headers={"content-type": "audio/wav"}
            )

        api = await self.client(handler)
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(
                socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]
            ),
        ):
            path = await api.download(
                providers.Audio(
                    url="https://singing.example.org/v1/jobs/example/audio"
                ),
                Path(folder) / "audio.wav",
            )
            self.assertEqual(path.read_bytes(), wav_bytes())
        self.assertEqual(
            headers,
            [
                ("singing.example.org", "Bearer fake-singing-secret"),
                ("cdn.example.org", None),
            ],
        )

    async def test_reject_api_error_without_exposing_response(self):
        api = await self.client(
            lambda request: httpx.Response(
                200,
                json={"base_resp": {"status_code": 1004, "status_msg": "sk-private"}},
            )
        )
        with self.assertRaises(PluginError) as error:
            await api.speech("你好")
        self.assertNotIn("sk-private", str(error.exception))

    async def test_reject_long_tts_before_request(self):
        handler = unittest.mock.Mock()
        api = await self.client(handler)
        with self.assertRaises(PluginError):
            await api.speech("字" * 501)
        handler.assert_not_called()

    async def test_public_urls_reject_local_addresses(self):
        for url in (
            "file:///tmp/x",
            "http://127.0.0.1/x",
            "http://[::1]/x",
            "https://localhost/x",
            "https://user:pass@example.org/x",
        ):
            with self.subTest(url=url), self.assertRaises(PluginError):
                providers.public_url(url)


class CoreTests(unittest.TestCase):
    def test_gate_and_key_do_not_mix_users_or_ambiguous_strings(self):
        self.assertNotEqual(
            core.conversation_key("ab", "c"), core.conversation_key("a", "bc")
        )
        gate = core.RequestGate(maximum=1, cooldown=0)
        gate.enter("a")
        with self.assertRaises(PluginError):
            gate.enter("a")
        with self.assertRaises(PluginError):
            gate.enter("b")
        gate.leave("a")
        gate.enter("b")

    def test_spoken_chunks_keep_order_and_natural_boundaries(self):
        text = "今天太阳很好。" * 12
        chunks = core.spoken_chunks(text, limit=40)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 40 for chunk in chunks))
        self.assertTrue(all(chunk.endswith("。") for chunk in chunks))

    def test_spoken_chunks_skip_code_and_keep_link_label(self):
        text = "**你好**，看[这个例子](https://example.org)。\n```python\nprint('secret')\n```"
        self.assertEqual(core.spoken_chunks(text), ["你好，看这个例子。"])
        self.assertEqual(core.spoken_chunks("```python\nprint('x')\n```"), [])

    def test_long_reply_falls_back_without_silent_truncation(self):
        with self.assertRaises(PluginError):
            core.spoken_chunks("字" * 2001, max_total=2000)

    def test_addon_has_no_chat_sdk_or_chat_key_configuration(self):
        schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
        self.assertNotIn("dsh", schema)
        self.assertNotIn("auto_chat", schema)
        self.assertNotIn("reply_mode", schema)
        self.assertNotIn("rvc", schema)
        self.assertNotIn("seedvc", schema)
        self.assertNotIn("character_conversion", schema["speech"]["items"])
        self.assertNotIn("deepseek", (ROOT / "requirements.txt").read_text("utf-8"))
        self.assertFalse(hasattr(core, "DshEngine"))


if __name__ == "__main__":
    unittest.main()
