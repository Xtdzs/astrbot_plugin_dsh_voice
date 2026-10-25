from __future__ import annotations

import hashlib
import json
import os
import time

from .errors import PluginError


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

    def enter(self, key: str) -> None:
        now = time.monotonic()
        if key in self.active:
            raise PluginError("你上一个任务还在处理中，请等它完成。")
        if len(self.active) >= self.maximum:
            raise PluginError("当前任务较多，请稍后再试。")
        if now - self.last.get(key, float("-inf")) < self.cooldown:
            raise PluginError("请求太快，请稍等几秒。")
        if len(self.last) > 2048:
            self.last = {k: v for k, v in self.last.items() if now - v < self.cooldown}
        self.active.add(key)
        self.last[key] = now

    def leave(self, key: str) -> None:
        self.active.discard(key)


def spoken_chunks(text: str, limit: int = 500, max_total: int = 2000) -> list[str]:
    """Prepare spoken prose without using or changing the user's language model."""
    import re

    text = re.sub(r"```[\s\S]*?(?:```|$)", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|>|[-*+] |\d+[.)] )\s*", "", text)
    text = re.sub(r"[*_~`]+", "", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    if len(text) > max_total:
        raise PluginError(f"回复超过自动朗读的 {max_total} 字上限，保留文字回复。")
    limit = max(20, min(limit, 9999))
    chunks = []
    while len(text) > limit:
        boundary = max(text.rfind(mark, 0, limit) for mark in "。！？!?；;\n，, ")
        end = boundary + 1 if boundary >= limit // 2 else limit
        chunks.append(text[:end].strip())
        text = text[end:].strip()
    if text:
        chunks.append(text)
    return chunks
