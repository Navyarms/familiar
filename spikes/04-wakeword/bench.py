"""Spike 4: sherpa-onnx keyword spotting for "hey Wisp" on CPU.

Detection rate: synthesize "hey Wisp" with several Kokoro voices/speeds (plus noise) and
count detections. False accepts: run the spotter over (a) synthesized non-wake speech,
(b) generated room-noise, and (c) any real recordings dropped into ./negatives/*.wav.

Run: python bench.py [--mic SECONDS]   (--mic also runs live from the default microphone)
Model: ../_downloads/kws/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01 (English, 3.3M params)
"""
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import os, sys, time, json, argparse, glob, itertools
import numpy as np, soundfile as sf
import sherpa_onnx

HERE = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(HERE, "..", "_downloads", "kws", "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")
KOKORO = os.path.join(HERE, "..", "_downloads", "kokoro")
SR = 16000

# Keywords file: one per line "tokens :boost #threshold @display" (tokens are BPE pieces from tokens.txt)
# We generate the token form with sherpa's text2token helper for "HEY WISP".
KEYWORDS_RAW = ["HEY WISP"]

def build_keywords_file():
    """Convert raw words to the BPE token format sherpa-onnx expects, using the model's bpe.model."""
    import sentencepiece as spm
    sp = spm.SentencePieceProcessor(); sp.load(os.path.join(MD, "bpe.model"))
    out = os.path.join(HERE, "keywords.txt")
    with open(out, "w", encoding="utf-8") as f:
        for kw in KEYWORDS_RAW:
            pieces = sp.encode(kw, out_type=str)
            f.write(" ".join(pieces) + f" :2.0 #0.25 @{kw.title().replace(" ", "")}\n")
    print("keywords.txt:", open(out, encoding="utf-8").read().strip())
    return out

def make_spotter(kw_file, threshold=0.25, score=2.0, threads=2):
    return sherpa_onnx.KeywordSpotter(
        tokens=os.path.join(MD, "tokens.txt"),
        encoder=os.path.join(MD, "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=os.path.join(MD, "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        joiner=os.path.join(MD, "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        num_threads=threads, max_active_paths=4, keywords_file=kw_file,
        keywords_score=score, keywords_threshold=threshold, num_trailing_blanks=1, provider="cpu")

def run_stream(kws, audio, chunk=1600):
    """Feed audio in 100 ms chunks like a mic; return list of (time_s, keyword) detections and compute RTF."""
    s = kws.create_stream()
    dets = []; t0 = time.perf_counter()
    for i in range(0, len(audio), chunk):
        s.accept_waveform(SR, audio[i:i+chunk])
        while kws.is_ready(s):
            kws.decode_stream(s)
            r = kws.get_result(s)
            if r:
                dets.append((round(i / SR, 2), r)); kws.reset_stream(s)
    tail = np.zeros(int(0.5 * SR), dtype=np.float32); s.accept_waveform(SR, tail); s.input_finished()
    while kws.is_ready(s):
        kws.decode_stream(s); r = kws.get_result(s)
        if r: dets.append((round(len(audio) / SR, 2), r)); kws.reset_stream(s)
    return dets, (time.perf_counter() - t0) / (len(audio) / SR)

def tts(text, voice, speed):
    from kokoro_onnx import Kokoro
    global _k
    try: _k
    except NameError: _k = Kokoro(os.path.join(KOKORO, "kokoro-v1.0.onnx"), os.path.join(KOKORO, "voices-v1.0.bin"))
    a, sr = _k.create(text, voice=voice, speed=speed, lang="en-us")
    n = int(len(a) * SR / sr)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)

def pad(a, pre=0.5, post=0.8):
    return np.concatenate([np.zeros(int(pre * SR), np.float32), a, np.zeros(int(post * SR), np.float32)])

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--mic", type=float, default=0); ap.add_argument("--threshold", type=float, default=0.25)
    args = ap.parse_args()
    kw_file = build_keywords_file()
    kws = make_spotter(kw_file, threshold=args.threshold)
    res = {"threshold": args.threshold}

    # --- positives -------------------------------------------------------------------
    voices = ["af_heart", "af_bella", "am_adam", "am_michael", "bf_emma", "bm_george", "af_sky", "am_fenrir"]
    speeds = [0.9, 1.0, 1.15]
    phrasings = ["Hey Wisp.", "Hey Wisp!", "hey wisp, what time is it?"]
    pos = []; rtfs = []
    os.makedirs(os.path.join(HERE, "pos"), exist_ok=True)
    for v, sp, ph in itertools.product(voices, speeds, phrasings):
        a = pad(tts(ph, v, sp))
        dets, rtf = run_stream(kws, a); rtfs.append(rtf)
        pos.append({"voice": v, "speed": sp, "text": ph, "detected": bool(dets), "dets": dets})
        print(f"  POS {v:11} x{sp:<4} {ph!r:32} -> {dets}")
    # noisy positives: add white noise at SNR 10 dB
    rng = np.random.default_rng(0); noisy = []
    for v in voices[:4]:
        a = pad(tts("Hey Wisp.", v, 1.0)); n = rng.normal(0, 1, len(a)).astype(np.float32)
        n *= np.sqrt(np.mean(a**2)) / np.sqrt(np.mean(n**2)) / np.sqrt(10)  # 10 dB SNR
        dets, _ = run_stream(kws, a + n); noisy.append(bool(dets)); print(f"  POS+noise10dB {v} -> {dets}")
    res["positives"] = {"n": len(pos), "detected": sum(p["detected"] for p in pos), "rate": sum(p["detected"] for p in pos) / len(pos),
                        "noisy_n": len(noisy), "noisy_detected": sum(noisy), "detail": pos}
    res["rtf_median"] = float(np.median(rtfs))

    # --- negatives: non-wake speech -------------------------------------------------
    negs_text = ["Hey, what's up with the weather today?", "I wish this would work.", "Hey Siri, play some music.",
                 "Okay Google, what time is it?", "A whisper in the wind is all it takes.", "Hey Chris, can you lisp?",
                 "The wisps of smoke drifted up.", "Hey, list the files.", "Hey, miss, where is the exit?", "Hey Lisa, open the door.",
                 "Please whisk the eggs.", "Hey this is a test of the system.", "He wished for a new bike.", "Hey, whisk it away quickly.",
                 "Alexa, set a timer.", "Let's wrap this up.", "Hey Bisque.", "Hey, is Wisconsin far?", "Hey, we split the bill.", "Hey wisp"]
    fa_speech = 0; speech_secs = 0; fa_detail = []
    for i, t in enumerate(negs_text):
        v = voices[i % len(voices)]
        a = pad(tts(t, v, 1.0)); speech_secs += len(a) / SR
        is_true_wake = t.lower().startswith("hey wisp")
        dets, _ = run_stream(kws, a)
        if dets and not is_true_wake:
            fa_speech += 1; fa_detail.append({"text": t, "voice": v, "dets": dets})
        print(f"  NEG {v:11} {t!r:45} -> {dets}{'  (true wake, expected)' if is_true_wake else ''}")
    # --- negatives: 1 hour of synthetic room noise (pink-ish noise + occasional clicks + hum) ---
    hours = 1.0; fa_noise = 0; seg = 60 * SR
    for m in range(int(hours * 60)):
        white = rng.normal(0, 1, seg).astype(np.float32)
        pink = np.cumsum(white); pink -= np.convolve(pink, np.ones(2000) / 2000, mode="same"); pink /= (np.abs(pink).max() + 1e-6)
        hum = 0.05 * np.sin(2 * np.pi * 60 * np.arange(seg) / SR)
        clicks = np.zeros(seg, np.float32); clicks[rng.integers(0, seg, 20)] = rng.uniform(0.3, 0.9, 20)
        a = (0.08 * pink + hum + clicks).astype(np.float32)
        dets, _ = run_stream(kws, a); fa_noise += len(dets)
    # --- negatives: any real recordings supplied ----------------------------------------
    real_secs = 0; fa_real = 0
    for f in glob.glob(os.path.join(HERE, "negatives", "*.wav")):
        a, sr = sf.read(f, dtype="float32");
        if a.ndim > 1: a = a.mean(1)
        if sr != SR: a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * SR / sr)), np.arange(len(a)), a).astype(np.float32)
        real_secs += len(a) / SR; dets, _ = run_stream(kws, a); fa_real += len(dets); print(f"  REAL {os.path.basename(f)} {len(a)/SR:.0f}s -> {len(dets)} detections")
    res["false_accepts"] = {"synthetic_speech_phrases": len(negs_text) - 1, "synthetic_speech_secs": round(speech_secs), "fa_speech": fa_speech, "fa_speech_detail": fa_detail,
                            "noise_hours": hours, "fa_noise": fa_noise, "real_secs": round(real_secs), "fa_real": fa_real}

    # --- live mic ---------------------------------------------------------------------
    if args.mic > 0:
        import sounddevice as sd
        print(f"listening on default mic for {args.mic:.0f}s, say 'hey Wisp'...")
        s = kws.create_stream(); got = []; t0 = time.time()
        def cb(indata, frames, t, status):
            s.accept_waveform(SR, indata[:, 0].copy())
            while kws.is_ready(s):
                kws.decode_stream(s); r = kws.get_result(s)
                if r: got.append(round(time.time() - t0, 2)); print("  DETECTED at", got[-1]); kws.reset_stream(s)
        with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=1600, callback=cb):
            time.sleep(args.mic)
        res["mic_detections"] = got

    print(json.dumps({k: v for k, v in res.items() if k != "positives"} | {"positive_rate": res["positives"]["rate"], "positives_noisy": f"{res['positives']['noisy_detected']}/{res['positives']['noisy_n']}"}, indent=1))
    json.dump(res, open(os.path.join(HERE, "results.json"), "w"), indent=2)

if __name__ == "__main__":
    main()
