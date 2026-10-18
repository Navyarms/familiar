# Phase 0 spikes

Throwaway prototypes that answer the questions in PLAN.md section 4, Phase 0. Measured results
are in [docs/phase0-results.md](../docs/phase0-results.md). Nothing here is production code.

All Python spikes share one venv: `uv venv --python 3.12 .venv` then
`uv pip install --python .venv/Scripts/python.exe kokoro-onnx sherpa-onnx faster-whisper fastapi uvicorn websockets pyinstaller uiautomation openai soundfile numpy requests pillow sentencepiece`.
Everything is forced to CPU (no CUDA provider, `-ngl 0`) because the app must work on CPU-only PCs.

Downloads (not committed, see `_downloads/`, ~6 GB):

| What | Where from |
|---|---|
| llama.cpp b11717 Windows CPU build | github.com/ggml-org/llama.cpp releases |
| Qwen3-4B-Q4_K_M.gguf | huggingface.co/Qwen/Qwen3-4B-GGUF |
| Qwen3VL-4B-Instruct-Q4_K_M.gguf + mmproj Q8_0 | huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF |
| Seed-san.vrm (VRM Public License 1.0, by VirtualCast) | github.com/vrm-c/vrm-specification samples |
| sherpa-onnx-kws-zipformer-gigaspeech-3.3M | github.com/k2-fsa/sherpa-onnx releases/kws-models |
| kokoro-v1.0.onnx + voices-v1.0.bin | github.com/thewh1teagle/kokoro-onnx releases |

| Folder | Question | Run |
|---|---|---|
| `01-overlay-vrm` | Transparent always-on-top VRM window at 60 fps with click-through outside the avatar | `npm start` (Esc quits), or `..\.venv\Scripts\python verify.py` for the automated fps + click-through test |
| `02-llm-bench` | llama-server CPU: tok/s, TTFT, tool-call reliability; Qwen3-VL on CPU | `bash run_all.sh` (sequential; ~15 min) or `run_server.ps1` + `bench.py` by hand |
| `03-kokoro-tts` | Kokoro per-sentence latency, phoneme/viseme timing | `python bench.py` |
| `04-wakeword` | sherpa-onnx KWS "hey Wisp" detection rate and false accepts | `python bench.py [--mic 30]` |
| `05-stt` | faster-whisper small int8 latency on a 5 s clip | `python bench.py` |
| `06-uia` | Windows UI Automation: list and click Explorer / Settings elements by name | `python uia_spike.py` (opens and closes Explorer and Settings) |
| `07-pyinstaller-stub` | PyInstaller one-folder FastAPI stub spawned and supervised by Electron | `build_and_run.ps1` |

`run_cpu_chain.sh` runs every CPU-timed spike back to back so measurements never overlap.
