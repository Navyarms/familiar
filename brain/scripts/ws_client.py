"""Dev client: start the brain, wait for READY, connect, send one or more text turns, print what comes back.

Usage:
  uv run python scripts/ws_client.py "What's a good name for a cat?" ["Open the calculator" ...] [--keep-audio out.wav]
                                     [--confirm allow|deny|none] [--no-speak]
"""

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import websockets

HERE = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # models emit emoji; Windows consoles are cp1252


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("texts", nargs="*", default=["Hi Wisp! In two sentences, who are you?"])
    ap.add_argument("--keep-audio", default=None)
    ap.add_argument("--confirm", choices=["allow", "deny", "none"], default="none", help="auto-answer tool confirmation cards")
    ap.add_argument("--no-speak", action="store_true")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--wait", type=float, default=1.0, help="seconds to wait after boot before the first prompt (lets idle memory work run)")
    a = ap.parse_args()
    token = "devtoken"
    proc = subprocess.Popen([sys.executable, "-m", "familiar_brain", "--token", token, "--dev", "--no-stdin-watch"],
                            cwd=HERE, stdout=subprocess.PIPE, stderr=open(HERE / "ws_client-brain.log", "w"), text=True, bufsize=1)
    port = None
    for line in proc.stdout:
        if line.startswith("READY "):
            port = int(line.split()[1])
            break
    print("brain on port", port)
    pcm = bytearray()
    sr = 24000
    t0 = time.time()
    queue = list(a.texts)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={token}", max_size=64 * 2**20) as ws:
            started = False
            while time.time() - t0 < a.timeout:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=a.timeout))
                t = m["type"]
                ts = f"[{time.time()-t0:6.2f}]"
                if t == "reply.audio":
                    pcm += base64.b64decode(m["pcm16_b64"])
                    sr = m["sample_rate"]
                    print(f"{ts} audio #{m['sentence_id']} {m['duration_s']:.2f}s {m['text']!r}")
                elif t == "reply.token":
                    print(m["text"], end="", flush=True)
                elif t == "models.info":
                    print(f"{ts} tier {m['tier']}; missing: {[r['name'] for r in m['required'] if not r['present']]}")
                elif t == "tools.info":
                    print(f"{ts} {len(m['tools'])} tools; confirm: {[x['name'] for x in m['tools'] if x['permission']=='confirm']}; blocked: {[x['name'] for x in m['tools'] if x['permission']=='blocked']}")
                elif t == "tool.request_confirm":
                    print(f"\n{ts} CONFIRM? {m['tool']} {m['args']} risk={m['risk']} -> {a.confirm}")
                    if a.confirm == "allow":
                        await ws.send(json.dumps({"type": "tool.confirm", "request_id": m["request_id"]}))
                    elif a.confirm == "deny":
                        await ws.send(json.dumps({"type": "tool.deny", "request_id": m["request_id"]}))
                elif t == "tool.start":
                    print(f"\n{ts} TOOL {m['tool']} {json.dumps(m['args'])}")
                elif t == "tool.result":
                    print(f"\n{ts} RESULT {m['tool']} ok={m['ok']}: {m['summary']}")
                elif t == "mic.state":
                    pass
                elif t in ("state", "hello", "model.ready", "transcript.final", "expression", "error", "reply.done"):
                    short = {k: v for k, v in m.items() if k not in ("hardware", "persona")}
                    print(f"\n{ts} {json.dumps(short)[:220]}")
                # Start once the voice stack has reported in (mic.state) or shortly after the models are ready; then
                # send the next prompt after each reply.done so we never interrupt a running turn.
                if not started and (t == "mic.state" or (t == "model.ready" and a.no_speak)):
                    started = True
                    await asyncio.sleep(a.wait)
                    send_next = True
                elif t == "memory.items":
                    print(f"\n{ts} MEMORY counts={m['counts']} items={[i['text'][:50] for i in m['items'][:6]]}")
                    send_next = False
                else:
                    send_next = started and t == "reply.done" and not str(m.get("turn_id", "")).startswith("announce")
                if send_next:
                    if not queue:
                        await asyncio.sleep(0.5)
                        break
                    text = queue.pop(0)
                    print(f"\n{ts} >>> {text}")
                    await ws.send(json.dumps({"type": "text.send", "text": text, "speak": not a.no_speak}))
                if t == "state" and m["state"] == "downloading":
                    await ws.send(json.dumps({"type": "models.download"}))
                if t == "error" and m.get("fatal"):
                    break
    finally:
        proc.terminate()
    if a.keep_audio and pcm:
        with wave.open(a.keep_audio, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(bytes(pcm))
        print("wrote", a.keep_audio, f"{len(pcm)/2/sr:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
