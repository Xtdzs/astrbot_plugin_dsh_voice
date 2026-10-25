from __future__ import annotations

import asyncio
import base64
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .core import credential
from .errors import PluginError


@dataclass
class Audio:
    content: bytes | None = None
    url: str = ""
    extension: str = "wav"


def service_url(value: str) -> str:
    """Owner-configured service: HTTPS, or HTTP on the same machine only."""
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PluginError("翻唱服务地址不能包含账号、密码或查询参数。")
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1"):
        return value
    public_url(value)
    if parsed.scheme != "https":
        raise PluginError(
            "远程翻唱服务需使用 HTTPS；本机可使用 http://127.0.0.1:7860。"
        )
    return value


def local_service_audio(value: str, base: str) -> bool:
    parsed, service = urlsplit(value), urlsplit(base)
    return (
        parsed.scheme == service.scheme == "http"
        and parsed.netloc == service.netloc
        and parsed.hostname in ("127.0.0.1", "localhost", "::1")
        and parsed.path.startswith(service.path.rstrip("/") + "/v1/jobs/")
        and parsed.path.endswith("/audio")
        and not parsed.username
        and not parsed.password
    )


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

    def speech_preflight(self):
        cfg = self.config.get("speech", {})
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
        return result

    async def music(self, prompt: str, lyrics: str = "") -> Audio:
        if not self.config.get("music", {}).get("enabled", False):
            raise PluginError(
                "尚未启用新歌生成；MiniMax 音乐 API 仅限有历史接口权限的账号，指定歌曲请使用翻唱。"
            )
        if not 1 <= len(prompt) <= 2000 or len(lyrics) > 3500:
            raise PluginError("歌曲描述限 1–2000 字，歌词最多 3500 字。")
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
        return result

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
        base = str(self.config.get("singing", {}).get("base_url", ""))
        url = (
            audio.url if local_service_audio(audio.url, base) else public_url(audio.url)
        )
        try:
            for _ in range(6):
                host = urlsplit(url).hostname
                addresses = await asyncio.to_thread(
                    socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM
                )
                if not addresses or (
                    not local_service_audio(url, base)
                    and any(
                        not ipaddress.ip_address(item[4][0]).is_global
                        for item in addresses
                    )
                ):
                    raise PluginError("云端音频地址不是公网地址。")
                # The singing key is scoped to its configured origin, never redirects.
                headers = {}
                service = urlsplit(
                    str(self.config.get("singing", {}).get("base_url", ""))
                )
                parsed = urlsplit(url)
                if (
                    parsed.scheme == service.scheme == "https"
                    and parsed.netloc == service.netloc
                ) or local_service_audio(url, base):
                    key = credential(
                        self.config.get("singing", {}), "api_key", "SINGING_API_KEY"
                    )
                    headers["Authorization"] = "Bearer " + key
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
        await self.client.aclose()
