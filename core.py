from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


class DshEngine:
    """Keep the synchronous SDK on one worker and retain isolated sessions."""

    def __init__(self, config: dict, root: Path, patch: Path, factory=None):
        self.config = config
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.patch = patch.resolve()
        self.factory = factory
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dsh-voice")
        self.harness = None
        self.loaded: set[str] = set()
        self.busy = False
        self.closed = False
        self.index_path = self.root / "session-index.json"
        try:
            self.index = json.loads(self.index_path.read_text("utf-8"))
            if not isinstance(self.index, dict):
                raise ValueError()
        except FileNotFoundError:
            self.index = {}
        except (ValueError, OSError):
            raise PluginError(
                "DSH 会话索引无法读取，请备份并检查 session-index.json。"
            ) from None

    def _save(self):
        temp = self.index_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.index), encoding="utf-8")
        temp.replace(self.index_path)

    def _open(self):
        if self.harness is not None:
            return
        key = credential(self.config, "api_key", "DEEPSEEK_API_KEY")
        factory = self.factory
        if factory is None:
            try:
                from deepseek_harness import DeepSeekHarness
            except ImportError:
                raise PluginError(
                    "未安装 deepseek-harness-sdk，请在 AstrBot 的 Python 环境安装 requirements.txt。"
                ) from None
            factory = DeepSeekHarness
        workspace = self.root / "workspace"
        workspace.mkdir(exist_ok=True)
        self.harness = factory(
            profile="sdk-minimal",
            patches=(str(self.patch),),
            dsh_home=str(self.root / "home"),
            cwd=str(workspace),
            api_key=key,
            base_url=str(self.config.get("base_url") or "https://api.deepseek.com"),
            model=str(self.config.get("model") or "deepseek-v4-flash"),
            max_tokens=int(self.config.get("max_tokens", 1024)),
            request_timeout_seconds=float(self.config.get("timeout_seconds", 120)),
            env={
                "DSH_SYSTEM_PROMPT": str(
                    self.config.get("persona")
                    or "你是一个友好的 QQ 角色机器人。使用简洁自然的中文回复。"
                )
            },
        )

    def _run(self, key: str, text: str) -> str:
        try:
            self._open()
            saved = self.index.get(key)
            sid = saved or f"qq-{uuid.uuid4().hex}"
            if saved and sid not in self.loaded:
                session = self.harness.resume_session(sid)
            else:
                session = self.harness.start_session(sid)
            self.loaded.add(sid)
            # Persist the id before sending; a failure never silently resets history.
            self.index[key] = sid
            self._save()
            response = session.run(text)
            if response.finish_reason not in ("completed", "max-tokens"):
                raise PluginError(
                    "DSH 未完成本次回复，请检查模型配置；会话异常时可使用 /dsh重置。"
                )
            result = response.final_response.strip()
            if not result:
                raise PluginError("DSH 返回了空回复，请检查模型配置。")
            return result
        except PluginError:
            raise
        except Exception:
            # Raw SDK exceptions may contain headers, prompts or runtime diagnostics.
            self._close_sync()
            raise PluginError(
                "DSH 调用失败或超时。请检查 Key、模型、网络及 SDK 版本；可用 /dsh重置 开始新会话。"
            ) from None

    async def chat(self, key: str, text: str) -> str:
        if self.closed:
            raise PluginError("插件正在关闭。")
        if self.busy:
            raise PluginError("DSH 正在处理另一条对话，请稍后再试。")
        self.busy = True
        job = asyncio.get_running_loop().run_in_executor(
            self.pool, self._run, key, text
        )

        # Cancellation of the coroutine must not allow a second SDK call to overlap.
        def finished(done):
            self.busy = False
            if not done.cancelled():
                done.exception()  # Consume an exception even if the caller was cancelled.

        job.add_done_callback(finished)
        try:
            # SDK 0.2.1rc2's notification iterator has no overall turn timeout.
            done, _ = await asyncio.wait(
                {job}, timeout=float(self.config.get("timeout_seconds", 120)) + 35
            )
            if not done:
                await asyncio.to_thread(self._close_sync)
                raise PluginError("DSH 对话超时，已关闭本次运行时，请稍后重试。")
            return job.result()
        except asyncio.CancelledError:
            await asyncio.to_thread(self._close_sync)
            raise

    async def reset(self, key: str):
        if self.busy:
            raise PluginError("DSH 正在处理对话，完成后再重置。")
        sid = self.index.pop(key, None)
        if sid:
            self.loaded.discard(sid)
        self._save()

    def _close_sync(self):
        harness, self.harness = self.harness, None
        self.loaded.clear()
        if harness is not None:
            try:
                harness.close()
            except Exception:
                pass

    async def close(self):
        self.closed = True
        await asyncio.to_thread(self._close_sync)
        self.pool.shutdown(wait=False, cancel_futures=True)
