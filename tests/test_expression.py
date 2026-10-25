import asyncio
import json
import re
import unittest

import httpx

from test_plugin import config, core, providers, wav_bytes, PluginError
import test_routing as routing
from test_routing import Event


class ExpressionCoreTests(unittest.TestCase):
    def test_daily_ranges_are_clamped_and_defaults_are_neutral(self):
        self.assertEqual(
            core.voice_style(),
            {"emotion": "auto", "speed": 1.0, "volume": 1.0, "pitch": 0},
        )
        self.assertEqual(
            core.voice_style("happy", 9, 0, 12),
            {"emotion": "happy", "speed": 1.2, "volume": 0.8, "pitch": 2},
        )

    def test_invalid_numbers_and_unknown_emotions_are_rejected(self):
        for style in (
            {"speed": float("nan")},
            {"volume": float("inf")},
            {"pitch": 1.3},
            {"speed": True},
            {"emotion": "happy+sad"},
            {"pitch": None},
        ):
            with self.subTest(style=style), self.assertRaises(PluginError):
                core.voice_style(**style)

    def test_pause_and_action_budgets_keep_natural_text(self):
        text = (
            "<#99#>你好"
            + ("<#99#>呀" * 12)
            + "(chuckle)(sighs)(laughs)(humming)再见<#0.3#>"
        )
        output = "".join(core.spoken_chunks(text, expressive=True, interjections=True))
        self.assertLessEqual(output.count("<#"), 8)
        self.assertNotIn("<#99#>", output)
        self.assertLessEqual(
            sum(output.count("(" + x + ")") for x in core.ALLOWED_INTERJECTIONS), 2
        )
        self.assertNotIn("(humming)", output)
        self.assertFalse(output.startswith("<#"))
        self.assertFalse(output.endswith("#>"))
        self.assertIn("你好", output)

    def test_adjacent_or_invalid_pauses_do_not_reach_provider(self):
        result = core.spoken_chunks(
            "你好<#0.2#><#0.4#>呀<#nan#><#abc#>。", expressive=True
        )
        self.assertEqual(result, ["你好<#0.20#>呀。"])
        self.assertEqual(core.spoken_chunks("<#0.3#>", expressive=True), [])

    def test_unsupported_provider_never_reads_control_labels(self):
        self.assertEqual(
            core.spoken_chunks("(chuckle)你好<#0.3#>呀(sighs)"), ["你好呀"]
        )
        self.assertEqual(
            core.spoken_chunks(
                "(chuckle)你好<#0.3#>呀", expressive=True, interjections=False
            ),
            ["你好<#0.30#>呀"],
        )

    def test_chunk_boundaries_preserve_whole_control_tokens(self):
        text = "字" * 17 + "(chuckle)" + "呀" * 18 + "<#0.30#>" + "啦" * 35
        chunks = core.spoken_chunks(text, limit=20, expressive=True, interjections=True)
        self.assertTrue(all(len(x) <= 20 for x in chunks))
        for chunk in chunks:
            cleaned = re.sub(
                r"<#[^<>]*#>|\((?:chuckle|laughs|sighs|breath)\)", "", chunk
            )
            self.assertFalse(
                any(x in cleaned for x in ("<#", "#>", "chuckle", "(", ")"))
            )


class ExpressionProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = config()
        self.bodies = []

        def handler(request):
            self.bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"audio": wav_bytes().hex()}})

        self.api = providers.CloudAudio(
            self.cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        self.addAsyncCleanup(self.api.close)

    async def test_model_controls_reach_minimax_without_changing_voice(self):
        self.cfg["speech"]["speed"] = 0.5
        await self.api.speech(
            "你好", style={"emotion": "happy", "speed": 1.1, "volume": 1.05, "pitch": 1}
        )
        voice = self.bodies[0]["voice_setting"]
        self.assertEqual(
            voice,
            {
                "voice_id": "lovely_girl",
                "speed": 1.1,
                "vol": 1.05,
                "pitch": 1,
                "emotion": "happy",
            },
        )
        self.assertEqual(self.cfg["speech"]["speed"], 0.5)

    async def test_automatic_emotion_is_omitted_and_manual_speed_is_preserved(self):
        self.cfg["speech"]["speed"] = 0.5
        await self.api.speech("你好", style={})
        await self.api.speech("你好")
        self.assertNotIn("emotion", self.bodies[0]["voice_setting"])
        self.assertEqual(self.bodies[0]["voice_setting"]["speed"], 1.0)
        self.assertEqual(self.bodies[1]["voice_setting"]["speed"], 0.5)

    async def test_invalid_style_never_creates_a_paid_request(self):
        with self.assertRaises(PluginError):
            await self.api.speech("你好", style={"speed": float("nan")})
        self.assertEqual(self.bodies, [])

    async def test_qwen_receives_no_minimax_only_parameters(self):
        self.cfg["speech"]["provider"] = "qwen"

        def handler(request):
            self.bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "output": {"audio": {"url": "https://audio.example.org/voice.wav"}}
                },
            )

        await self.api.client.aclose()
        self.api.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await self.api.speech(
            "你好", style={"emotion": "happy", "speed": 1.1, "volume": 1.1, "pitch": 1}
        )
        self.assertEqual(
            set(self.bodies[0]["input"]), {"text", "voice", "language_type"}
        )


class ExpressionRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await routing.RoutingTests.asyncSetUp(self)

    async def test_model_style_defaults_reset_each_call(self):
        del self.plugin.make_records
        await self.plugin.speak_tool(
            Event(), "真开心呀", emotion="happy", speed=1.15, volume=1.1, pitch=1
        )
        self.assertEqual(
            self.plugin.cloud.speech.call_args.kwargs["style"]["emotion"], "happy"
        )
        await self.plugin.speak_tool(Event(), "你好呀")
        self.assertEqual(
            self.plugin.cloud.speech.call_args.kwargs["style"],
            {"emotion": "auto", "speed": 1.0, "volume": 1.0, "pitch": 0},
        )

    async def test_repeated_words_do_not_resend_even_if_style_changes(self):
        event = Event()
        await self.plugin.speak_tool(event, "你好", emotion="happy")
        await self.plugin.speak_tool(event, "你好", emotion="calm", speed=0.9)
        self.plugin.make_records.assert_awaited_once()
        self.assertEqual(event.messages, [[self.record]])

    async def test_parallel_sentences_are_sent_in_order(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def build(event, text, style):
            seen.append(text)
            if text == "前句":
                entered.set()
                await release.wait()
            return [self.record]

        self.plugin.make_records.side_effect = build
        event = Event()
        first = asyncio.create_task(
            self.plugin.speak_tool(event, "前句", emotion="surprised")
        )
        await entered.wait()
        second = asyncio.create_task(
            self.plugin.speak_tool(event, "后句", emotion="happy")
        )
        await asyncio.sleep(0)
        self.assertEqual(seen, ["前句"])
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(seen, ["前句", "后句"])
        self.assertEqual(len(event.messages), 2)

    async def test_qwen_removes_interjections_and_pauses_before_synthesis(self):
        del self.plugin.make_records
        self.plugin.config["speech"]["provider"] = "qwen"
        await self.plugin.speak_tool(Event(), "(chuckle)你好<#0.3#>呀(sighs)")
        self.assertEqual(self.plugin.cloud.speech.call_args.args[0], "你好呀")

    async def test_same_turn_emotion_changes_do_not_hit_user_cooldown(self):
        del self.plugin.make_records
        self.plugin.gate = core.RequestGate(cooldown=60)
        event = Event()
        await self.plugin.speak_tool(event, "真的嘛？", emotion="surprised")
        await self.plugin.speak_tool(event, "真替你开心！", emotion="happy")
        self.assertEqual(len(event.messages), 2)
        next_event = Event()
        reply = await self.plugin.speak_tool(next_event, "又一条消息")
        self.assertIn("请求太快", reply)
        self.assertEqual(next_event.messages, [])
        self.assertEqual(self.plugin.cloud.speech.await_count, 2)
