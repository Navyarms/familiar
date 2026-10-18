"""Acoustic loopback test for the voice pipeline, no human needed.

Plays synthesized speech into an output device that is wired to an input device (VB-Audio Virtual
Cable: play to "CABLE Input", the brain captures from "CABLE Output"), and watches the brain's events.

Sequence:
  1. start the brain with FAMILIAR_MIC_DEVICE=<capture device>, wait for mic.state wake
  2. play "Hey Wisp, <question>"            -> expect listening, transcript.final, a spoken reply
  3. while the reply plays, play "Hey Wisp" -> expect reply.done interrupted (barge-in) + listening
  4. play "<second question>" (already listening) -> expect second transcript
Usage: uv run python scripts/voice_loopback.py [--out "CABLE Input"] [--in "CABLE Output"]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import websockets

HERE = Path(__file__).resolve().parent.parent
MODELS = Path(os.environ["LOCALAPPDATA"]) / "Familiar" / "models"


def tts(text: str, voice: str = "am_michael") -> tuple[np.ndarray, int]:
    from kokoro_onnx import Kokoro

    global _k
    if "_k" not in globals():
        _k = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
    a, sr = _k.create(text, voice=voice, speed=1.0, lang="en-us")
    return np.asarray(a, np.float32), sr


def out_candidates(name: str) -> list[int]:
    """Matching output devices, WASAPI first, then DirectSound, then MME (same physical device, several host APIs)."""
    rank = {"Windows WASAPI": 0, "Windows DirectSound": 1, "MME": 2}
    cands = [(rank.get(sd.query_hostapis(d["hostapi"])["name"], 9), i) for i, d in enumerate(sd.query_devices())
             if d["max_output_channels"] > 0 and name.lower() in d["name"].lower()]
    if not cands:
        raise SystemExit(f"no output device matching {name!r}")
    return [i for _, i in sorted(cands)]


_play_lock = asyncio.Lock()


async def play(audio: np.ndarray, sr: int, devices: list[int]) -> None:
    """Play through the first device that opens, resampling to its native rate (WASAPI shared mode will not resample)."""
    async with _play_lock:  # one stream at a time; overlapping sd.play calls stop each other
        for device in devices:
            native = int(sd.query_devices(device)["default_samplerate"])
            a = audio
            if native != sr:
                n = int(len(audio) * native / sr)
                a = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
            try:
                await asyncio.to_thread(sd.play, a, native, device=device, blocking=True)
                return
            except Exception as e:  # noqa: BLE001
                print(f"  play on device {device} failed: {str(e)[:80]}; trying next", flush=True)
        print("  playback failed on every candidate device", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="CABLE Input")
    ap.add_argument("--in", dest="inp", default="CABLE Output")
    ap.add_argument("--q1", default="Hey Wisp, what is the capital of France?")
    ap.add_argument("--q2", default="And what about Japan?")
    a = ap.parse_args()
    dev_out = out_candidates(a.out)
    print("playing into", [sd.query_devices(i)["name"] for i in dev_out], "| brain captures from", a.inp)

    env = dict(os.environ, FAMILIAR_MIC_DEVICE=a.inp)
    token = "devtoken"
    proc = subprocess.Popen([sys.executable, "-m", "familiar_brain", "--token", token, "--dev", "--no-stdin-watch"],
                            cwd=HERE, stdout=subprocess.PIPE, stderr=open(HERE / "loopback-brain.log", "w"), text=True, bufsize=1, env=env)
    port = None
    for line in proc.stdout:
        if line.startswith("READY "):
            port = int(line.split()[1])
            break
    t0 = time.time()
    results = {"wake_1": None, "final_1": None, "reply_1_first_audio": None, "barge_in": None, "wake_2": None, "final_2": None}

    def log(m):
        print(f"[{time.time()-t0:6.2f}] {m}", flush=True)

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws?token={token}", max_size=64 * 2**20) as ws:
            phase = "boot"
            audio_sentences = 0
            speaking_sent = False

            async def recv():
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=240))

            while True:
                m = await recv()
                t = m["type"]
                if t == "mic.state":
                    log(f"mic {m['mode']} ({m['reason']})")
                    if m["mode"] == "wake" and phase == "boot":
                        phase = "q1"
                        log(f">>> playing q1: {a.q1!r}")
                        au, sr = tts(a.q1)
                        asyncio.create_task(play(au, sr, dev_out))
                    elif m["mode"] == "listening" and phase == "q1" and results["wake_1"] is None:
                        results["wake_1"] = round(time.time() - t0, 2)
                    elif m["mode"] == "listening" and phase == "barge":
                        results["wake_2"] = round(time.time() - t0, 2)
                        phase = "q2"
                        log(f">>> playing q2: {a.q2!r}")
                        au, sr = tts(a.q2)
                        asyncio.create_task(play(au, sr, dev_out))
                elif t == "transcript.partial":
                    log(f"partial: {m['text']!r}")
                elif t == "transcript.final":
                    log(f"FINAL: {m['text']!r}")
                    if phase == "q1":
                        results["final_1"] = m["text"]
                    elif phase == "q2":
                        results["final_2"] = m["text"]
                elif t == "reply.token":
                    pass
                elif t == "reply.audio":
                    audio_sentences += 1
                    if phase == "q1" and not speaking_sent:
                        speaking_sent = True
                        results["reply_1_first_audio"] = round(time.time() - t0, 2)
                        await ws.send(json.dumps({"type": "playback", "speaking": True}))  # what the shell would do

                        async def barge():
                            await asyncio.sleep(1.5)
                            nonlocal phase
                            phase = "barge"
                            log(">>> playing barge-in: 'Hey Wisp'")
                            au, sr = tts("Hey Wisp.")
                            await play(au, sr, dev_out)

                        asyncio.create_task(barge())
                elif t == "reply.done":
                    log(f"reply.done interrupted={m['interrupted']} text={m['text'][:80]!r}")
                    if phase in ("barge", "q2") and m["interrupted"] and results["barge_in"] is None:
                        results["barge_in"] = round(time.time() - t0, 2)
                        await ws.send(json.dumps({"type": "playback", "speaking": False}))
                    if phase == "q2" and results["final_2"] is not None and not m["interrupted"]:
                        break
                elif t == "state":
                    log(f"state {m['state']} {m.get('detail') or ''}")
                elif t == "error":
                    log(f"ERROR {m}")
                    if m.get("fatal"):
                        break
                if time.time() - t0 > 200:
                    log("timeout")
                    break
    finally:
        proc.terminate()
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
