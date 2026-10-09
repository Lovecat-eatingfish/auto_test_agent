"""Minimal mock OpenAI-compatible /v1/chat/completions server (streaming + non-streaming).

Call script (per session):
  - 1st LLM call  -> tool_call(echo{text:"hello-from-mock"})
  - later calls   -> plain assistant text "MOCK_DONE"
Supports both stream:true (SSE) and stream:false.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _sse_chunk(payload_model, delta, finish=None, idx=0, cid="chatcmpl-x"):
    chunk = {
        "id": cid, "object": "chat.completion.chunk", "created": 0,
        "model": payload_model,
        "choices": [{"index": idx, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


class MockOpenAI:
    def __init__(self, port: int = 18321, script=None):
        self.port = port
        self.requests: list[dict] = []
        self._server = None
        # script(call_index, body) -> (delta_dict, finish) for streaming, or full message dict
        self.script = script or self._default_script

    @staticmethod
    def _default_script(n, body):
        if n == 1:
            return ({"role": "assistant", "tool_calls": [{
                "id": "call_1", "type": "function", "index": 0,
                "function": {"name": "echo", "arguments": json.dumps({"text": "hello-from-mock"})},
            }]}, None)
        return ({"role": "assistant", "content": "MOCK_DONE"}, "stop")

    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(body)
                n = len(outer.requests)
                model = body.get("model", "mock")
                result = outer.script(n, body)
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    delta, finish = result
                    role = delta.get("role", "assistant")
                    self.wfile.write(_sse_chunk(model, {"role": role}).encode())
                    if delta.get("tool_calls"):
                        self.wfile.write(_sse_chunk(model, {"tool_calls": delta["tool_calls"]}).encode())
                    if delta.get("content"):
                        self.wfile.write(_sse_chunk(model, {"content": delta["content"]}).encode())
                    self.wfile.write(_sse_chunk(model, {}, finish=finish or "stop").encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    delta, finish = result
                    message = {"role": "assistant", "content": delta.get("content")}
                    if delta.get("tool_calls"):
                        message["tool_calls"] = delta["tool_calls"]
                    payload = {
                        "id": f"chatcmpl-{n}", "object": "chat.completion", "created": 0,
                        "model": model,
                        "choices": [{"index": 0, "message": message, "finish_reason": finish or "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }
                    data = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                self.wfile.flush()

        try:
            # 显式拒绝端口复用,避免残留的旧 mock 进程静默接管请求(脚本计数错乱)
            ThreadingHTTPServer.allow_reuse_address = False
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as exc:
            raise RuntimeError(
                f"mock 端口 {self.port} 已被占用(可能有残留的 mock 进程),请先结束它: {exc}") from exc
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()


if __name__ == "__main__":
    m = MockOpenAI()
    m.start()
    print("mock openai on", m.port)
    import time
    time.sleep(3600)
