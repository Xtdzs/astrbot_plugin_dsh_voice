"""Client for the separately deployed cloud Seed-VC singing service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .core import credential
from .errors import PluginError
from .providers import Audio, public_url

REFERENCE_TEXT = "你好呀，今天见到你真开心。窗外的阳光暖暖的，微风吹过树梢。我们一起听听音乐，聊聊有趣的事情吧。"


def song_library(config):
    songs = {}
    for entry in config.get("cover", {}).get("songs", []):
        name, separator, url = str(entry).partition("|")
        if not separator or not name.strip() or not url.strip():
            raise PluginError("歌单每项请填写：歌名 | 音频公网直链。")
        name = name.strip()
        if name.casefold() in songs:
            raise PluginError("歌单中有重复歌名，请为不同版本使用不同名称。")
        songs[name.casefold()] = (name, public_url(url.strip()))
    return songs


def resolve_song(config, value):
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return public_url(value)
    found = song_library(config).get(value.casefold())
    if not found:
        raise PluginError(
            "歌单中没有这首歌，请提供歌曲音频直链，或先在插件歌单中添加。"
        )
    return found[1]


class SeedSinger:
    def __init__(self, cloud, root: Path):
        self.cloud = cloud
        self.config = cloud.config
        self.root = root / "voice-reference"
        self.reference_lock = asyncio.Lock()

    def settings(self):
        cfg = self.config.get("singing", {})
        base = str(cfg.get("base_url") or "").strip().rstrip("/")
        if not base:
            raise PluginError(
                "请先配置 singing.base_url：部署包中的云端 Seed-VC 服务地址。"
            )
        parsed = urlsplit(public_url(base))
        if parsed.scheme != "https" or parsed.query or parsed.fragment:
            raise PluginError("翻唱服务地址需为 HTTPS 根地址，不包含查询参数。")
        key = credential(cfg, "api_key", "SINGING_API_KEY")
        return cfg, base, key

    async def request(self, method, path, timeout=120, **kwargs):
        _, base, key = self.settings()
        try:
            response = await self.cloud.client.request(
                method,
                base + path,
                headers={"Authorization": "Bearer " + key},
                follow_redirects=False,
                timeout=timeout,
                **kwargs,
            )
            if not response.is_success:
                raise PluginError(
                    f"翻唱服务返回 HTTP {response.status_code}，请检查地址、密钥、运行状态或任务容量。"
                )
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except (httpx.HTTPError, ValueError):
            raise PluginError("翻唱服务连接失败、超时或响应格式异常。") from None

    async def preflight(self):
        body = await self.request("GET", "/v1/health")
        if body.get("protocol") != "seedvc-cover-v1" or not body.get("ready"):
            raise PluginError("云端翻唱服务尚未就绪或接口不兼容，请按部署文档检查。")

    async def reference(self):
        cfg, _, _ = self.settings()
        reference_url = str(cfg.get("reference_url") or "").strip()
        if reference_url:
            identity = [public_url(reference_url)]
        else:
            self.cloud.speech_preflight()
            provider = self.config.get("speech", {}).get("provider", "minimax")
            voice = {
                k: v for k, v in self.config.get(provider, {}).items() if k != "api_key"
            }
            identity = [
                provider,
                voice,
                self.config.get("speech", {}).get("speed", 1),
                REFERENCE_TEXT,
            ]
        fingerprint = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / (fingerprint + ".audio")
        async with self.reference_lock:
            if target.is_file() and target.stat().st_size:
                return target
            temporary = target.with_suffix(".part")
            try:
                audio = (
                    Audio(url=reference_url)
                    if reference_url
                    else await self.cloud.speech(REFERENCE_TEXT)
                )
                await self.cloud.download(audio, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return target

    async def cancel(self, job_id):
        try:
            await self.request("DELETE", "/v1/jobs/" + job_id, timeout=10)
        except PluginError:
            pass

    async def cover(self, source_url):
        cfg, _, _ = self.settings()
        # Check credentials/service before a paid TTS sample request.
        await self.preflight()
        with tempfile.TemporaryDirectory(prefix="seedvc-") as folder:
            source = Path(folder) / "song.audio"
            await self.cloud.download(Audio(url=source_url), source)
            reference = await self.reference()
            with source.open("rb") as song, reference.open("rb") as sample:
                body = await self.request(
                    "POST",
                    "/v1/jobs",
                    files={
                        "song": ("song.audio", song, "application/octet-stream"),
                        "reference": (
                            "reference.audio",
                            sample,
                            "application/octet-stream",
                        ),
                    },
                )
            job_id = body.get("id")
            if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
                raise PluginError("翻唱服务没有返回有效任务 ID。")

            async def wait_result():
                current = body
                while True:
                    status = current.get("status")
                    if status == "succeeded":
                        _, base, _ = self.settings()
                        return Audio(url=base + "/v1/jobs/" + job_id + "/audio")
                    if status in ("failed", "canceled"):
                        raise PluginError(
                            "云端翻唱失败或已取消，请检查歌曲时长、参考音频和云端 GPU 日志。"
                        )
                    await asyncio.sleep(3)
                    current = await self.request("GET", "/v1/jobs/" + job_id)

            try:
                return await asyncio.wait_for(
                    wait_result(),
                    max(30, min(float(cfg.get("timeout_seconds", 1800)), 3600)),
                )
            except (asyncio.TimeoutError, TimeoutError):
                await self.cancel(job_id)
                raise PluginError(
                    "云端翻唱超时，已尝试取消任务，请检查云端状态后再重试。"
                ) from None
            except BaseException:
                await self.cancel(job_id)
                raise
