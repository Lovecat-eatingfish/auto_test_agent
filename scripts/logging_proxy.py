"""Logging pass-through proxy: pi bridge -> this proxy -> real OpenAI-compatible API.

Logs request body size, time-to-first-SSE-chunk, chunk flow, so we can see
where a streaming call stalls. Reasoning text is dumped to a companion file
for diagnosing "model thinks forever" cases.

关键修复:上游流中断时必须向下游发送 SSE 终止事件 + 分块结束符,
否则 node bridge 会一直挂到整个阶段超时。
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

UPSTREAM = sys.argv[2] if len(sys.argv) > 2 else "https://token.sensenova.cn/v1"
API_KEY = os.environ.get("ATA_PROXY_KEY", "")
LOG = os.environ.get("ATA_PROXY_LOG", "D:/tmp/proxy_p2.log")
REASONING_LOG = os.environ.get("ATA_PROXY_REASONING", "D:/tmp/relay_reasoning.log")
# 上游 RPM 限流严重时,设置最小请求间隔(秒)做客户端节流,减少 429 重试风暴
MIN_INTERVAL = float(os.environ.get("ATA_PROXY_MIN_INTERVAL", "0") or 0)
_pace_lock = threading.Lock()
_last_req_at = [0.0]


def _pace() -> None:
    if MIN_INTERVAL <= 0:
        return
    with _pace_lock:
        wait = _last_req_at[0] + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_req_at[0] = time.time()


def log(text: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(text + "\n")


class Proxy(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port):
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _pace()
        t0 = time.time()
        req_id = time.strftime("%H%M%S")
        parsed = None
        try:
            open("D:/tmp/last_bridge_request.json", "wb").write(body)
            parsed = json.loads(body)
            n_msgs = len(parsed.get("messages", []))
            sizes = [len(str(m.get("content", ""))) for m in parsed.get("messages", [])]
            model = parsed.get("model")
            stream = parsed.get("stream")
            max_tokens = parsed.get("max_tokens")
            extra = {k: v for k, v in parsed.items()
                     if k not in ("model", "messages", "stream", "max_tokens", "tools",
                                  "temperature", "tool_choice", "parallel_tool_calls")}
            entry = (f"{time.strftime('%H:%M:%S')} #{req_id} POST {self.path} model={model} "
                     f"stream={stream} max_tokens={max_tokens} msgs={n_msgs} sizes={sizes} "
                     f"bytes={length} extra={json.dumps(extra, ensure_ascii=False)[:120]}\n")
        except Exception as exc:
            entry = f"{time.strftime('%H:%M:%S')} #{req_id} POST {self.path} parse-error {exc} bytes={length}\n"
        log(entry)

        auth = self.headers.get("Authorization") or (f"Bearer {API_KEY}" if API_KEY else None)
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = auth
        try:
            from urllib.parse import urljoin
            path = self.path
            if UPSTREAM.rstrip("/").endswith("/v1") and path.startswith("/v1/"):
                path = path[3:]  # 修正双重 /v1
            upstream = requests.post(UPSTREAM.rstrip("/") + path, data=body, headers=headers,
                                     stream=True, timeout=(10, 600))
        except Exception as exc:
            log(f"  upstream connect error: {exc}")
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        log(f"  upstream {upstream.status_code} after {round(time.time()-t0,1)}s")
        if upstream.status_code != 200:
            err_body = upstream.raw.read(2000) if upstream.raw else b""
            self.send_response(upstream.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            log(f"  upstream error body: {err_body[:200]!r}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")  # 避免 node undici 池化连接导致 worker 无法退出
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.close_connection = True

        def send_chunk(payload: bytes) -> bool:
            try:
                self.wfile.write(hex(len(payload))[2:].encode() + b"\r\n" + payload + b"\r\n")
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False

        first = True
        n_chunks = 0
        stats = {"reasoning": 0, "content": 0, "tool_calls": 0, "finish": None}
        reasoning_text = []
        tool_acc = {}  # index -> {"id","name","args": [str]}
        held = []  # 缓存的 tool_call/finish_reason 分片,流末尾重组后按序发给 bridge
        upstream_ok = True
        try:
            for line in upstream.iter_lines():
                if first:
                    log(f"  first SSE line after {round(time.time()-t0,1)}s: {line[:80]!r}")
                    first = False
                n_chunks += 1
                is_tool_or_finish = b"[DONE]" in line
                if line.startswith(b"data:") and not is_tool_or_finish:
                    try:
                        chunk = json.loads(line[5:].strip())
                        choices = chunk.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            if delta.get("reasoning_content"):
                                stats["reasoning"] += len(delta["reasoning_content"])
                                reasoning_text.append(delta["reasoning_content"])
                            if delta.get("content"):
                                stats["content"] += len(delta["content"])
                            tcs = delta.get("tool_calls") or []
                            if tcs:
                                is_tool_or_finish = True
                                for tc in tcs:
                                    stats["tool_calls"] += 1
                                    idx = tc.get("index", 0)
                                    acc = tool_acc.setdefault(idx, {"id": "", "name": "", "args": []})
                                    if tc.get("id"):
                                        acc["id"] = tc["id"]
                                    fn = tc.get("function") or {}
                                    if fn.get("name"):
                                        acc["name"] = fn["name"]
                                    if fn.get("arguments"):
                                        acc["args"].append(fn["arguments"])
                            if choices[0].get("finish_reason"):
                                is_tool_or_finish = True
                                stats["finish"] = choices[0]["finish_reason"]
                    except Exception:
                        pass
                if is_tool_or_finish:
                    held.append(line)  # 重组后再发,避免 bridge 逐小分片处理
                    continue
                payload = (line + b"\n\n") if line else b"\n"
                if not send_chunk(payload):
                    log(f"  downstream gone after {round(time.time()-t0,1)}s ({n_chunks} chunks)")
                    return
        except Exception as exc:
            upstream_ok = False
            log(f"  stream error after {round(time.time()-t0,1)}s: {exc}")
        # 重组:每个 tool call 发一个完整 delta;finish_reason 放最后
        if tool_acc:
            calls = []
            for idx, acc in sorted(tool_acc.items()):
                args_str = "".join(acc["args"])
                args_bytes = len(args_str.encode("utf-8"))
                try:
                    json.loads(args_str)
                    verdict = "valid"
                except Exception as exc:
                    verdict = f"INVALID({exc})"
                # node bridge 对超大参数(实测 >12KB)或不合 schema 的参数会静默丢弃,
                # 这里只记录告警;真正的规避手段是工具侧分批提交(见 phase_tools/prompt)
                flag = " !!TOO_BIG(会被bridge丢弃)" if args_bytes > 12000 else ""
                log(f"  tool[{idx}] {acc['name']} args={args_bytes}B json={verdict}{flag}")
                if "INVALID" in verdict and args_str:
                    with open(REASONING_LOG, "a", encoding="utf-8") as f:
                        f.write(f"\n===== {time.strftime('%H:%M:%S')} #{req_id} INVALID tool args "
                                f"({acc['name']}, {len(args_str)} chars) =====\n")
                        f.write(args_str[:4000] + "\n...TAIL...\n" + args_str[-2000:])
                calls.append({"index": idx, "id": acc["id"] or f"call_{req_id}_{idx}",
                              "type": "function",
                              "function": {"name": acc["name"], "arguments": args_str}})
            merged = json.dumps({"id": f"relay-{req_id}", "object": "chat.completion.chunk",
                                 "choices": [{"index": 0, "delta": {"tool_calls": calls},
                                              "finish_reason": ""}]}, ensure_ascii=False)
            if not send_chunk(b"data: " + merged.encode() + b"\n\n"):
                return
        # 重组后只发:merged(tool_calls) + finish_reason + [DONE];
        # 上游的碎片行不含信息,原样转发会显著放大响应体
        for line in held:
            if b"[DONE]" in line:
                if not send_chunk(b"data: [DONE]\n\n"):
                    return
                break
            try:
                chunk = json.loads(line[5:].strip())
                ch0 = (chunk.get("choices") or [{}])[0]
                if ch0.get("finish_reason"):
                    clean = {"choices": [{"index": 0, "delta": {},
                                          "finish_reason": ch0["finish_reason"]}]}
                    if not send_chunk(b"data: " + json.dumps(clean).encode() + b"\n\n"):
                        return
            except Exception:
                pass
        if not upstream_ok:
            # 必须给下游一个明确的流结束,否则 bridge 挂死到阶段超时
            err = json.dumps({"error": {"message": f"upstream stream broken: {stats}"}})
            send_chunk(b"data: " + err.encode() + b"\n\n")
            send_chunk(b"data: [DONE]\n\n")
        if reasoning_text:
            with open(REASONING_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%H:%M:%S')} #{req_id} "
                        f"({stats['reasoning']} chars) =====\n")
                f.write("".join(reasoning_text))
        log(f"  done: {n_chunks} chunks in {round(time.time()-t0,1)}s "
            f"finish={stats['finish']} stats={stats}")
        try:
            # 分块传输的终止符必须裸写,不能走 send_chunk(会被再次包一层帧)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18410
    open(LOG, "w").close()
    log(f"relay up on {port} -> {UPSTREAM}")
    Proxy(port).serve_forever()
