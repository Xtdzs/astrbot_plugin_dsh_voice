"""Run the real DSH SDK against a local mock model; no paid API calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sdk-path", help="Optional unpacked SDK/runtime directory for development"
    )
    args = parser.parse_args()
    if args.sdk_path:
        sys.path.insert(0, str(Path(args.sdk_path).resolve()))
    from deepseek_harness import DeepSeekHarness

    requests = []

    class MockModel(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload["_test_path"] = self.path
            requests.append(payload)
            if "messages" in self.path:
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "offline-test",
                            "type": "message",
                            "role": "assistant",
                            "model": "deepseek-v4-flash",
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 5, "output_tokens": 0},
                        },
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "离线连接成功"},
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 5},
                    },
                    {"type": "message_stop"},
                ]
                body = "".join(
                    "event: "
                    + event["type"]
                    + "\ndata: "
                    + json.dumps(event, ensure_ascii=False)
                    + "\n\n"
                    for event in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if payload.get("stream"):
                chunks = [
                    {
                        "id": "offline-test",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": "离线连接成功",
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "offline-test",
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {
                            "prompt_tokens": 5,
                            "completion_tokens": 5,
                            "total_tokens": 10,
                        },
                    },
                ]
                body = (
                    "".join(
                        "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                        for chunk in chunks
                    )
                    + "data: [DONE]\n\n"
                ).encode()
                content_type = "text/event-stream"
            else:
                body = json.dumps(
                    {
                        "id": "offline-test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "离线连接成功",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                    }
                ).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockModel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="dsh-voice-check-") as folder:
            kwargs = dict(
                profile="sdk-minimal",
                dsh_home=str(Path(folder) / "home"),
                cwd=folder,
                patches=(
                    str(Path(__file__).resolve().parents[1] / "dsh_chat.patch.yml"),
                ),
                api_key="offline-test-key",
                base_url=f"http://127.0.0.1:{server.server_port}",
                model="deepseek-v4-flash",
                initialize_timeout_seconds=20,
                request_timeout_seconds=20,
                env={
                    "DSH_SYSTEM_PROMPT": "OFFLINE_CHARACTER_PERSONA",
                    "NO_PROXY": "127.0.0.1,localhost",
                    "no_proxy": "127.0.0.1,localhost",
                },
            )
            with DeepSeekHarness(**kwargs) as harness:
                response = harness.run(
                    "离线测试第一轮", session_id="voice-offline-test"
                )
                assert response.final_response == "离线连接成功", json.dumps(
                    response.events, ensure_ascii=True
                )
                assert response.finish_reason == "completed", response.finish_reason
                response = harness.run(
                    "离线测试第二轮", session_id="voice-offline-test"
                )
                assert response.final_response == "离线连接成功"
            with DeepSeekHarness(**kwargs) as harness:
                session = harness.resume_session("voice-offline-test")
                response = session.run("重启后继续")
                assert response.final_response == "离线连接成功"
            assert requests, "No model requests were captured"
            assert all(not req.get("tools") for req in requests), (
                "Unexpected host tools exposed to the model"
            )
            assert "OFFLINE_CHARACTER_PERSONA" in json.dumps(requests[0]), (
                "Persona missing"
            )
            assert "离线测试第一轮" in json.dumps(requests[-1], ensure_ascii=False), (
                "Conversation was not restored"
            )
            print(
                "PASS: native SDK startup, local mock model, multi-turn history, restart/resume, persona, zero host tools."
            )
            print("No cloud model or paid API was called.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
