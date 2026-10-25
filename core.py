from __future__ import annotations

import hashlib
import json
import math
import os
import time

from .errors import PluginError

EMOTIONS = {
    "auto",
    "happy",
    "sad",
    "angry",
    "fearful",
    "disgusted",
    "surprised",
    "calm",
}
INTERJECTIONS = {
    "laughs",
    "chuckle",
    "coughs",
    "clear-throat",
    "groans",
    "breath",
    "pant",
    "inhale",
    "exhale",
    "gasps",
    "sniffs",
    "sighs",
    "snorts",
    "burps",
    "lip-smacking",
    "humming",
    "hissing",
    "emm",
    "sneezes",
}
ALLOWED_INTERJECTIONS = {"laughs", "chuckle", "sighs", "breath"}


def voice_style(emotion="auto", speed=1.0, volume=1.0, pitch=0) -> dict:
    """Validate model-selected expression and keep it within daily speaking ranges."""
    if not isinstance(emotion, str) or emotion not in EMOTIONS:
        raise PluginError(
            "情绪需为 auto、happy、sad、angry、fearful、disgusted、surprised 或 calm。"
        )
    result = {"emotion": emotion}
    for name, value, low, high in (
        ("speed", speed, 0.85, 1.2),
        ("volume", volume, 0.8, 1.2),
        ("pitch", pitch, -2, 2),
    ):
        try:
            if isinstance(value, bool):
                raise ValueError()
            value = float(value)
            if not math.isfinite(value) or (name == "pitch" and not value.is_integer()):
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise PluginError("语速、音量需为有限数字，音高需为整数。") from None
        result[name] = max(low, min(high, value))
    result["pitch"] = int(result["pitch"])
    return result


def credential(config: dict, name: str, env_name: str) -> str:
    value = str(config.get(name) or os.environ.get(env_name) or "").strip()
    if not value:
        raise PluginError(f"请在插件配置中填写 {name}，或设置环境变量 {env_name}。")
    return value


def conversation_key(origin: str, sender: str) -> str:
    # Separate each user in each platform conversation, including group chats.
    return hashlib.sha256(json.dumps([origin, sender]).encode()).hexdigest()


class RequestGate:
    """Fail fast instead of building an unbounded queue of paid requests."""

    def __init__(self, maximum: int = 2, cooldown: float = 5):
        self.maximum = max(1, maximum)
        self.cooldown = max(0, cooldown)
        self.active: set[str] = set()
        self.last: dict[str, float] = {}

    def enter(self, key: str, *, continuation: bool = False) -> None:
        now = time.monotonic()
        if key in self.active:
            raise PluginError("你上一个任务还在处理中，请等它完成。")
        if len(self.active) >= self.maximum:
            raise PluginError("当前任务较多，请稍后再试。")
        if not continuation and now - self.last.get(key, float("-inf")) < self.cooldown:
            raise PluginError("请求太快，请稍等几秒。")
        if len(self.last) > 2048:
            self.last = {k: v for k, v in self.last.items() if now - v < self.cooldown}
        self.active.add(key)
        self.last[key] = now

    def leave(self, key: str) -> None:
        self.active.discard(key)


def spoken_chunks(
    text: str,
    limit: int = 500,
    max_total: int = 2000,
    *,
    expressive: bool = False,
    interjections: bool = False,
) -> list[str]:
    """Prepare spoken prose without using or changing the user's language model."""
    import re

    text = re.sub(r"```[\s\S]*?(?:```|$)", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|>|[-*+] |\d+[.)] )\s*", "", text)
    text = re.sub(r"[*_~`]+", "", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    pauses = 0
    actions = 0

    def pause(match):
        nonlocal pauses
        try:
            value = float(match.group(1))
        except ValueError:
            return ""
        if not expressive or not math.isfinite(value) or pauses >= 8:
            return ""
        pauses += 1
        return f"<#{max(0.1, min(0.6, value)):.2f}#>"

    def action(match):
        nonlocal actions
        name = match.group(1).lower()
        if name not in INTERJECTIONS:
            return match.group(0)
        if (
            not expressive
            or not interjections
            or name not in ALLOWED_INTERJECTIONS
            or actions >= 2
        ):
            return ""
        actions += 1
        return f"({name})"

    text = re.sub(r"<#([^<>]*)#>", pause, text)
    text = re.sub(r"\(([a-zA-Z-]+)\)", action, text).strip()
    text = re.sub(r"(<#[^<>]*#>)(?:\s*<#[^<>]*#>)+", r"\1", text)
    # Pauses must sit between spoken content, never at a request boundary.
    text = re.sub(r"^(?:\s*<#[^<>]*#>)+|(?:<#[^<>]*#>\s*)+$", "", text).strip()
    if not text:
        return []
    if len(text) > max_total:
        raise PluginError(f"回复超过自动朗读的 {max_total} 字上限，保留文字回复。")
    limit = max(20, min(limit, 9999))
    chunks = []
    while len(text) > limit:
        boundary = max(text.rfind(mark, 0, limit) for mark in "。！？!?；;\n，, ")
        end = boundary + 1 if boundary >= limit // 2 else limit
        # Never split a provider control marker across separate HTTP requests.
        for marker in re.finditer(
            r"<#[^<>]*#>|\((?:laughs|chuckle|sighs|breath)\)", text
        ):
            if marker.start() < end < marker.end():
                end = marker.start() or marker.end()
                break
        chunk = re.sub(
            r"^(?:\s*<#[^<>]*#>)+|(?:<#[^<>]*#>\s*)+$", "", text[:end]
        ).strip()
        if chunk:
            chunks.append(chunk)
        text = text[end:].strip()
    if text:
        text = re.sub(r"^(?:\s*<#[^<>]*#>)+|(?:<#[^<>]*#>\s*)+$", "", text).strip()
        if text:
            chunks.append(text)
    return chunks
