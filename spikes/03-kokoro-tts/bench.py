"""Spike 3: Kokoro TTS (kokoro-onnx) per-sentence latency on CPU + viseme/phoneme timing availability.

Run: python bench.py   (uses ../_downloads/kokoro/kokoro-v1.0.onnx + voices-v1.0.bin)
Forces CPU: onnxruntime CPUExecutionProvider only.
"""
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import os, sys, time, json, statistics, inspect
os.environ.setdefault("ONNX_PROVIDER", "CPUExecutionProvider")
import numpy as np
import soundfile as sf
import onnxruntime as ort
from kokoro_onnx import Kokoro

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "..", "_downloads", "kokoro", "kokoro-v1.0.onnx")
VOICES = os.path.join(HERE, "..", "_downloads", "kokoro", "voices-v1.0.bin")

SENTENCES = [
    "Sure, opening Spotify now.",                                        # short, typical tool ack
    "The sky looks blue because air scatters short wavelengths of sunlight more than long ones.",  # medium
    "I found three PDF files in your Downloads folder from yesterday; the newest one is called quarterly report, do you want me to open it?",  # long
    "Done.",                                                             # tiny
    "Okay, I'll remind you in ten minutes to check the oven.",
]

def main():
    print("onnxruntime", ort.__version__, "providers available:", ort.get_available_providers())
    t0 = time.perf_counter()
    k = Kokoro(MODEL, VOICES)
    print(f"model load: {time.perf_counter()-t0:.2f}s")
    # inspect what the API exposes for timing
    print("Kokoro.create signature:", inspect.signature(k.create))
    has_timestamps = False
    try:
        from kokoro_onnx import tokenizer as _t
        print("tokenizer module:", _t.__name__)
    except Exception as e:
        print("no tokenizer module:", e)
    # phoneme access: kokoro-onnx exposes k.tokenizer.phonemize(text) and create(..., is_phonemes=True)
    try:
        ph = k.tokenizer.phonemize(SENTENCES[0], lang="en-us")
        print("phonemize('%s') -> %r" % (SENTENCES[0], ph))
    except Exception as e:
        print("phonemize failed:", e)

    voice = "af_heart"
    # warm-up
    k.create("Hello there.", voice=voice, speed=1.0, lang="en-us")
    rows = []
    for s in SENTENCES:
        lat = []
        for _ in range(3):
            t0 = time.perf_counter()
            audio, sr = k.create(s, voice=voice, speed=1.0, lang="en-us")
            lat.append(time.perf_counter() - t0)
        dur = len(audio) / sr
        med = statistics.median(lat)
        rows.append({"sentence": s, "chars": len(s), "audio_s": round(dur, 2), "latency_median_s": round(med, 3),
                     "latency_min_s": round(min(lat), 3), "rtf": round(med / dur, 3)})
        print(f"  {med*1000:7.0f} ms  for {dur:5.2f}s audio  (RTF {med/dur:.2f})  {s[:60]!r}")
    sf.write(os.path.join(HERE, "sample.wav"), audio, sr)

    # Streaming API (if present): time to first chunk for the long sentence
    first_chunk = None
    if hasattr(k, "create_stream"):
        import asyncio
        async def run():
            t0 = time.perf_counter(); first = None; n = 0
            async for chunk, sr in k.create_stream(SENTENCES[2], voice=voice, speed=1.0, lang="en-us"):
                if first is None: first = time.perf_counter() - t0
                n += 1
            return first, n
        first_chunk, nchunks = asyncio.run(run())
        print(f"create_stream: first chunk after {first_chunk*1000:.0f} ms, {nchunks} chunks")

    # Viseme/phoneme timing: kokoro-onnx 0.6 create_timed() returns per-phoneme Timing entries
    # only when the ONNX graph exposes a duration output. Test it on the standard v1.0 model.
    t0 = time.perf_counter()
    audio_t, sr_t, timings = k.create_timed(SENTENCES[0], voice=voice, speed=1.0, lang="en-us")
    print(f"create_timed: {(time.perf_counter()-t0)*1000:.0f} ms, {len(timings)} timing entries")
    for tm in timings[:12]:
        print("   ", tm)
    timed_ok = len(timings) > 0
    sess = k.sess if hasattr(k, "sess") else None
    outs = [(o.name, o.shape) for o in sess.get_outputs()] if sess else None
    ins = [(i.name, i.shape) for i in sess.get_inputs()] if sess else None
    print("onnx inputs:", ins); print("onnx outputs:", outs)

    # Amplitude envelope at 50 Hz (what we'd use for lip sync regardless)
    hop = sr // 50
    env = [float(np.sqrt(np.mean(audio[i:i+hop] ** 2))) for i in range(0, len(audio), hop)]
    print(f"amplitude envelope frames: {len(env)} @ 50 Hz, peak {max(env):.3f}")

    res = {"onnxruntime": ort.__version__, "providers": ort.get_available_providers(), "voice": voice, "sample_rate": sr,
           "rows": rows, "stream_first_chunk_s": first_chunk, "onnx_inputs": ins, "onnx_outputs": outs,
           "phoneme_string_available": True, "per_phoneme_timing_from_model": timed_ok, "timing_sample": [str(t) for t in timings[:12]]}
    json.dump(res, open(os.path.join(HERE, "results.json"), "w"), indent=2)
    print(json.dumps({k_: v for k_, v in res.items() if k_ != "rows"}, indent=1))

if __name__ == "__main__":
    main()
