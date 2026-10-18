"""Dev client: start the brain, wait for READY, connect, send a text turn, print what comes back.

Usage: uv run python scripts/ws_client.py "What's a good name for a cat?" [--keep-audio out.wav]
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


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="Hi Wisp! In two sentences, who are you?")
    ap.add_argument("--keep-audio", default=None)
    ap.add_argument("--timeout", type=float, default=240)
    a = ap.parse_args()
    token = "devtoken"
    proc = subprocess.Popen([sys.executable, "-m", "familiar_brain", "--token", token, "--dev", "--no-stdin-watch"],
                            cwd=HERE, stdout=subprocess.PIPE, stderr=sys.stderr, text=True, bufsize=1)
    port = None
    for line in proc.stdout:
        if line.startswith("READY "):
            port = int(line.split()[1])
            break
    print("brain on port", port)
    pcm = bytearray()
    sr = 24000
    t0 = time.time()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={token}", max_size=64 * 2**20) as ws:
            sent = False
            while time.time() - t0 < a.timeout:
                raw = await asyncio.wait_for(ws.recv(), timeout=a.timeout)
                m = json.loads(raw)
                t = m["type"]
                if t == "reply.audio":
                    pcm += base64.b64decode(m["pcm16_b64"])
                    sr = m["sample_rate"]
                    print(f"[{time.time()-t0:6.2f}] audio #{m['sentence_id']} {m['duration_s']:.2f}s env={len(m['envelope'])} vis={len(m['visemes'])} {m['text']!r}")
                elif t == "reply.token":
                    print(m["text"], end="", flush=True)
                elif t == "models.info":
                    print(f"[{time.time()-t0:6.2f}] tier {m['tier']} ({m['tier_reason']}); missing: {[r['name'] for r in m['required'] if not r['present']]}")
                else:
                    short = {k: v for k, v in m.items() if k not in ("hardware",)}
                    print(f"[{time.time()-t0:6.2f}] {json.dumps(short)[:300]}")
                if t == "state" and m["state"] == "idle" and not sent:
                    sent = True
                    print(f"[{time.time()-t0:6.2f}] >>> {a.text}")
                    await ws.send(json.dumps({"type": "text.send", "text": a.text, "speak": True}))
                if t == "reply.done":
                    break
                if t == "state" and m["state"] == "downloading":
                    print("models missing; sending models.download")
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
