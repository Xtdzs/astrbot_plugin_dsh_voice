from __future__ import annotations

import asyncio
import base64
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .core import credential
from .errors import PluginError

RVC_VERSION = "c7a02f18043c32141e29fd5d10f91bb06f44ed0a7bc491817609ab9de41017e4"


@dataclass
class Audio:
    content: bytes | None = None
    url: str = ""
    extension: str = "wav"


def public_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise PluginError("请提供可公网访问的 http/https 音频直链。")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise PluginError("音频链接必须是公网地址。")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise PluginError("音频链接必须是公网地址。")
    except ValueError:
        pass
    return value


class CloudAudio:
    def __init__(self, config: dict, client: httpx.AsyncClient | None = None):
        self.config = config
        # No global Authorization header: API keys never follow audio downloads.
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15))
        self.pending: set[str] = set()

    async def json(
        self, method: str, url: str, key: str, payload=None, timeout=120, headers=None
    ):
        request_headers = {"Authorization": f"Bearer {key}", **(headers or {})}
        try:
            result = await self.client.request(
                method, url, json=payload, headers=request_headers, timeout=timeout
            )
            if not result.is_success:
                raise PluginError(
                    f"云端接口返回 HTTP {result.status_code}，请检查密钥、余额和接口权限。"
                )
            body = result.json()
            if not isinstance(body, dict):
                raise ValueError()
        except (httpx.HTTPError, ValueError):
            raise PluginError(
                "云端接口连接失败、超时或响应格式异常。请检查网络与配置。"
            ) from None
        base = body.get("base_resp") or {}
        if (
            base.get("status_code", 0) != 0
            or body.get("code")
            or body.get("status_code", 200) != 200
        ):
            # Do not echo provider messages: some contain the input or credentials.
            raise PluginError("云端拒绝了请求，请检查模型名、音色 ID、余额及输入限制。")
        return body

    def minimax_key(self):
        return credential(self.config.get("minimax", {}), "api_key", "MINIMAX_API_KEY")

    def rvc_settings(self):
        cfg = self.config.get("rvc", {})
        key = credential(cfg, "api_key", "REPLICATE_API_TOKEN")
        model = str(cfg.get("voice_model_url") or "").strip()
        if not model:
            raise PluginError(
                "请先配置 rvc.voice_model_url：角色 RVC 模型 ZIP 的公网直链（内含 .pth，可选 .index）。"
            )
        public_url(model)
        version = str(cfg.get("version") or RVC_VERSION)
        if not re.fullmatch(r"[a-f0-9]{64}", version):
            raise PluginError(
                "rvc.version 必须是与 ultimate_rvc 参数兼容的 64 位版本 ID。"
            )
        if (
            not -24 <= int(cfg.get("pitch", 0)) <= 24
            or not 0 <= float(cfg.get("index_rate", 0.3)) <= 1
        ):
            raise PluginError("RVC 的 pitch 需为 -24 至 24，index_rate 需为 0 至 1。")
        return cfg, key, model, version

    def speech_preflight(self):
        cfg = self.config.get("speech", {})
        if cfg.get("character_conversion", False):
            self.rvc_settings()
        if cfg.get("provider", "minimax") == "qwen":
            credential(self.config.get("qwen", {}), "api_key", "DASHSCOPE_API_KEY")
        else:
            self.minimax_key()

    async def speech(self, text: str) -> Audio:
        self.speech_preflight()
        cfg = self.config.get("speech", {})
        provider = cfg.get("provider", "minimax")
        limit = min(
            int(cfg.get("max_characters", 500)), 600 if provider == "qwen" else 9999
        )
        if not text or len(text) > limit:
            raise PluginError(f"本次朗读支持 1–{limit} 个字符，请缩短文本。")
        if not 0.5 <= float(cfg.get("speed", 1)) <= 2:
            raise PluginError("朗读速度需为 0.5 至 2。")
        if provider == "qwen":
            qwen = self.config.get("qwen", {})
            body = await self.json(
                "POST",
                str(
                    qwen.get("base_url") or "https://dashscope.aliyuncs.com/api/v1"
                ).rstrip("/")
                + "/services/aigc/multimodal-generation/generation",
                credential(qwen, "api_key", "DASHSCOPE_API_KEY"),
                {
                    "model": qwen.get("model") or "qwen3-tts-flash",
                    "input": {
                        "text": text,
                        "voice": qwen.get("voice") or "Cherry",
                        "language_type": "Auto",
                    },
                },
            )
            audio = body.get("output", {}).get("audio", {})
            if audio.get("url"):
                result = Audio(url=public_url(audio["url"]))
            elif audio.get("data"):
                try:
                    result = Audio(
                        content=base64.b64decode(audio["data"], validate=True)
                    )
                except ValueError:
                    raise PluginError("千问返回的音频编码异常。") from None
            else:
                raise PluginError("千问没有返回音频。")
        elif provider == "minimax":
            minimax = self.config.get("minimax", {})
            body = await self.json(
                "POST",
                str(minimax.get("base_url") or "https://api.minimax.cn").rstrip("/")
                + "/v1/t2a_v2",
                self.minimax_key(),
                {
                    "model": minimax.get("speech_model") or "speech-2.8-turbo",
                    "text": text,
                    "stream": False,
                    "output_format": "hex",
                    "voice_setting": {
                        "voice_id": minimax.get("voice_id") or "lovely_girl",
                        "speed": float(cfg.get("speed", 1)),
                    },
                    "audio_setting": {
                        "sample_rate": 32000,
                        "format": "wav",
                        "channel": 1,
                    },
                },
            )
            try:
                content = bytes.fromhex(body.get("data", {}).get("audio", ""))
                if not content:
                    raise ValueError()
                result = Audio(content=content)
            except (ValueError, TypeError):
                raise PluginError("MiniMax 没有返回有效音频。") from None
        else:
            raise PluginError("speech.provider 仅支持 minimax 或 qwen。")
        if cfg.get("character_conversion", False):
            return await self.convert(result)
        return result

    async def music(self, prompt: str, lyrics: str = "") -> Audio:
        if not 1 <= len(prompt) <= 2000 or len(lyrics) > 3500:
            raise PluginError("歌曲描述限 1–2000 字，歌词最多 3500 字。")
        if self.config.get("music", {}).get("character_conversion", False):
            self.rvc_settings()
        minimax = self.config.get("minimax", {})
        body = await self.json(
            "POST",
            str(minimax.get("base_url") or "https://api.minimax.cn").rstrip("/")
            + "/v1/music_generation",
            self.minimax_key(),
            {
                "model": minimax.get("music_model") or "music-3.0",
                "prompt": prompt,
                "lyrics": lyrics,
                "lyrics_optimizer": not bool(lyrics),
                "stream": False,
                "output_format": "url",
                "audio_setting": {
                    "sample_rate": 44100,
                    "bitrate": 128000,
                    "format": "mp3",
                },
            },
            timeout=float(self.config.get("music", {}).get("timeout_seconds", 600)),
        )
        url = body.get("data", {}).get("audio")
        if not isinstance(url, str) or not url:
            raise PluginError("音乐接口没有返回音频链接。")
        result = Audio(url=public_url(url), extension="mp3")
        if self.config.get("music", {}).get("character_conversion", False):
            return await self.convert(result)
        return result

    async def convert(self, source: Audio) -> Audio:
        cfg, key, model, version = self.rvc_settings()
        if source.url:
            source_value = public_url(source.url)
        elif source.content:
            if len(source.content) > 8 * 1024 * 1024:
                raise PluginError("待转换音频过大，请缩短语音或使用公网音频链接。")
            mime = "audio/wav" if source.extension == "wav" else "audio/mpeg"
            source_value = (
                f"data:{mime};base64," + base64.b64encode(source.content).decode()
            )
        else:
            raise PluginError("没有可转换的音频。")
        deadline_seconds = max(30, min(float(cfg.get("timeout_seconds", 900)), 3600))
        body = await self.json(
            "POST",
            "https://api.replicate.com/v1/predictions",
            key,
            {
                "version": version,
                "input": {
                    "song_input": source_value,
                    "rvc_model": model,
                    "pitch": int(cfg.get("pitch", 0)),
                    "index_rate": float(cfg.get("index_rate", 0.3)),
                    "output_format": "wav",
                },
            },
            timeout=60,
            headers={"Cancel-After": f"{int(deadline_seconds)}s"},
        )
        pid = body.get("id", "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", str(pid)):
            raise PluginError("RVC 接口未返回有效任务 ID。")
        self.pending.add(pid)
        terminal = False
        try:
            deadline = time.monotonic() + deadline_seconds
            while True:
                status = body.get("status")
                if status in ("failed", "canceled"):
                    terminal = True
                    raise PluginError(
                        "RVC 转换失败或被取消，请检查音色模型与音频格式。"
                    )
                if status == "succeeded":
                    terminal = True
                    output = body.get("output")
                    if not isinstance(output, str):
                        raise PluginError(
                            "RVC 返回格式不兼容，请使用配置说明中的模型版本。"
                        )
                    return Audio(url=public_url(output), extension="wav")
                if time.monotonic() >= deadline:
                    raise PluginError(
                        "RVC 转换超时，已尝试取消云端任务；可在 Replicate 控制台核查。"
                    )
                await asyncio.sleep(min(3, max(0, deadline - time.monotonic())))
                body = await self.json(
                    "GET",
                    f"https://api.replicate.com/v1/predictions/{pid}",
                    key,
                    timeout=30,
                )
        finally:
            if not terminal:
                await self.cancel(pid, key)
            self.pending.discard(pid)

    async def cancel(self, pid: str, key: str):
        try:
            await self.client.post(
                f"https://api.replicate.com/v1/predictions/{pid}/cancel",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
        except httpx.HTTPError:
            pass

    async def download(self, audio: Audio, destination: Path) -> Path:
        maximum = (
            max(1, min(int(self.config.get("limits", {}).get("max_audio_mb", 40)), 100))
            * 1024
            * 1024
        )
        if audio.content is not None:
            if not audio.content or len(audio.content) > maximum:
                raise PluginError("音频为空或超出大小限制。")
            destination.write_bytes(audio.content)
            return destination
        url = public_url(audio.url)
        try:
            for _ in range(6):
                host = urlsplit(url).hostname
                addresses = await asyncio.to_thread(
                    socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM
                )
                if not addresses or any(
                    not ipaddress.ip_address(item[4][0]).is_global for item in addresses
                ):
                    raise PluginError("云端音频地址不是公网地址。")
                # Replicate outputs may require authentication, only for its delivery host.
                headers = {}
                if host == "replicate.delivery" or host.endswith(".replicate.delivery"):
                    headers["Authorization"] = "Bearer " + credential(
                        self.config.get("rvc", {}), "api_key", "REPLICATE_API_TOKEN"
                    )
                async with self.client.stream(
                    "GET", url, headers=headers, follow_redirects=False, timeout=90
                ) as response:
                    if response.is_redirect:
                        url = public_url(
                            str(response.url.join(response.headers["location"]))
                        )
                        continue
                    if not response.is_success:
                        raise PluginError(
                            f"下载音频失败（HTTP {response.status_code}）。"
                        )
                    content_type = response.headers.get("content-type", "").lower()
                    if any(x in content_type for x in ("text/", "json", "html")):
                        raise PluginError("云端返回了网页而非音频文件。")
                    size = 0
                    with destination.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > maximum:
                                raise PluginError("音频超出配置的大小限制。")
                            handle.write(chunk)
                    if not size:
                        raise PluginError("云端音频文件为空。")
                    return destination
            raise PluginError("音频下载重定向过多。")
        except (httpx.HTTPError, OSError, KeyError):
            raise PluginError("音频下载失败，请检查网络和文件存储权限。") from None
        finally:
            if destination.exists() and destination.stat().st_size == 0:
                destination.unlink(missing_ok=True)

    async def close(self):
        if self.pending:
            key = credential(
                self.config.get("rvc", {}), "api_key", "REPLICATE_API_TOKEN"
            )
            await asyncio.gather(
                *(self.cancel(pid, key) for pid in tuple(self.pending)),
                return_exceptions=True,
            )
        await self.client.aclose()
