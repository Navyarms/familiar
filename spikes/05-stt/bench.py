"""Spike 5: faster-whisper small int8 on CPU, latency for a ~5 s utterance.

The 5 s clip is synthesized with Kokoro (spike 3) so the test is self-contained and
reproducible; we also transcribe it to check accuracy. Run: python bench.py
"""
import os, time, json, statistics
import numpy as np, soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
CLIP = os.path.join(HERE, "clip_5s.wav")
TEXT = "Hey Wisp, can you open my downloads folder and find the PDF I saved yesterday afternoon?"

def make_clip():
    from kokoro_onnx import Kokoro
    k = Kokoro(os.path.join(HERE, "..", "_downloads", "kokoro", "kokoro-v1.0.onnx"),
               os.path.join(HERE, "..", "_downloads", "kokoro", "voices-v1.0.bin"))
    audio, sr = k.create(TEXT, voice="am_adam", speed=1.0, lang="en-us")
    # resample 24k -> 16k (whisper input) by simple linear interpolation
    n16 = int(len(audio) * 16000 / sr)
    a16 = np.interp(np.linspace(0, len(audio) - 1, n16), np.arange(len(audio)), audio).astype(np.float32)
    # pad/trim to exactly 5 s
    a16 = a16[:80000] if len(a16) >= 80000 else np.pad(a16, (0, 80000 - len(a16)))
    sf.write(CLIP, a16, 16000)
    print(f"clip written: {len(a16)/16000:.2f}s")

def main():
    if not os.path.exists(CLIP):
        make_clip()
    from faster_whisper import WhisperModel
    audio, sr = sf.read(CLIP, dtype="float32")
    results = {}
    for name in ["small", "base"]:
        t0 = time.perf_counter()
        m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=8)
        load = time.perf_counter() - t0
        lat = []; text = ""
        for i in range(4):
            t0 = time.perf_counter()
            segs, info = m.transcribe(audio, language="en", beam_size=1, vad_filter=False, without_timestamps=True)
            text = " ".join(s.text.strip() for s in segs)
            lat.append(time.perf_counter() - t0)
        # also beam 5 (default) once, to show the cost
        t0 = time.perf_counter(); segs, _ = m.transcribe(audio, language="en", beam_size=5, without_timestamps=True); list(segs); b5 = time.perf_counter() - t0
        results[name] = {"load_s": round(load, 2), "first_run_s": round(lat[0], 3), "warm_median_s": round(statistics.median(lat[1:]), 3),
                         "beam5_s": round(b5, 3), "transcript": text}
        print(f"{name} int8 cpu(8 threads): load {load:.2f}s, first {lat[0]:.3f}s, warm median {statistics.median(lat[1:]):.3f}s, beam5 {b5:.3f}s\n  -> {text!r}")
    results["reference"] = TEXT
    json.dump(results, open(os.path.join(HERE, "results.json"), "w"), indent=2)

if __name__ == "__main__":
    main()
