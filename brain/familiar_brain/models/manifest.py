"""What each tier downloads. URLs, sizes and SHA256 are pinned so a bad mirror or partial file is caught.

Decisions from Phase 0: one multimodal chat model (Qwen3-VL Instruct) per tier, Kokoro for TTS,
faster-whisper (CTranslate2 int8) for STT, sherpa-onnx for VAD + wake word.
The llama.cpp runtime is also fetched here when it was not bundled with the installer.
"""

from __future__ import annotations

from dataclasses import dataclass

LLAMA_BUILD = "b11717"
_LL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}/"
_HF = "https://huggingface.co/"
_KK = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
_SH = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"


@dataclass(frozen=True)
class Item:
    name: str  # file name on disk
    url: str
    size: int
    sha256: str | None  # None = not pinned (runtime zips change per build; we pin the build tag instead)
    kind: str  # chat | mmproj | tts | tts_voices | runtime | vad | kws | stt
    subdir: str = ""  # models/<subdir>/<name> for multi-file models and unpacked archives

    @property
    def archive(self) -> bool:
        return self.name.endswith((".zip", ".tar.bz2"))


QWEN3VL_2B = Item("Qwen3VL-2B-Instruct-Q4_K_M.gguf", _HF + "Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/main/Qwen3VL-2B-Instruct-Q4_K_M.gguf",
                  1107409952, "089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae", "chat")
QWEN3VL_2B_MM = Item("mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf", _HF + "Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf",
                     445053216, "f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82", "mmproj")
QWEN3VL_4B = Item("Qwen3VL-4B-Instruct-Q4_K_M.gguf", _HF + "Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/main/Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                  2497281664, "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a", "chat")
QWEN3VL_4B_MM = Item("mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf", _HF + "Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf",
                     453974304, "30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d", "mmproj")
QWEN3VL_8B = Item("Qwen3VL-8B-Instruct-Q4_K_M.gguf", _HF + "Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf",
                  5027784800, "67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2", "chat")
QWEN3VL_8B_MM = Item("mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf", _HF + "Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf",
                     752289728, "c6ba85508d82f42590e6eb77d5340369ab6fecf107a7561d809523d8aa5f3bfd", "mmproj")

KOKORO = Item("kokoro-v1.0.onnx", _KK + "kokoro-v1.0.onnx", 325532387,
              "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5", "tts")
KOKORO_VOICES = Item("voices-v1.0.bin", _KK + "voices-v1.0.bin", 28214398,
                     "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d", "tts_voices")

SILERO_VAD = Item("silero_vad.onnx", _SH + "asr-models/silero_vad.onnx", 643854,
                  "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6", "vad")

KWS_EN = Item("sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2",
              _SH + "kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2", 17626723, None, "kws",
              subdir="kws-gigaspeech-3.3M")

_WT = ("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab")
_WV = ("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913")


def _whisper(repo: str, subdir: str, model_size: int, model_sha: str, cfg_size: int, cfg_sha: str) -> list[Item]:
    base = _HF + f"Systran/{repo}/resolve/main/"
    return [
        Item("model.bin", base + "model.bin", model_size, model_sha, "stt", subdir),
        Item("config.json", base + "config.json", cfg_size, cfg_sha, "stt", subdir),
        Item(_WT[0], base + _WT[0], _WT[1], _WT[2], "stt", subdir),
        Item(_WV[0], base + _WV[0], _WV[1], _WV[2], "stt", subdir),
    ]


WHISPER_SMALL = _whisper("faster-whisper-small", "whisper-small", 483546902,
                         "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
                         2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828")
WHISPER_BASE = _whisper("faster-whisper-base", "whisper-base", 145217532,
                        "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9",
                        2309, "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a")

_EMB = _HF + "Xenova/bge-small-en-v1.5/resolve/main/"
EMBED = [
    Item("model_quantized.onnx", _EMB + "onnx/model_quantized.onnx", 34014426,
         "6c9c6101a956d62dfb5e7190c538226c0c5bb9cb27b651234b6df063ee7dbfe4", "embed", subdir="bge-small-en-v1.5"),
    Item("tokenizer.json", _EMB + "tokenizer.json", 711396,
         "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66", "embed", subdir="bge-small-en-v1.5"),
]

RUNTIME_CPU = Item(f"llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip", _LL + f"llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip", 18 * 2**20, None, "runtime")
RUNTIME_VULKAN = Item(f"llama-{LLAMA_BUILD}-bin-win-vulkan-x64.zip", _LL + f"llama-{LLAMA_BUILD}-bin-win-vulkan-x64.zip", 30 * 2**20, None, "runtime")

# Tier -> chat model, projector, llama-server backend, GPU layers, STT model. T3/T4 use the 8B until 14B/32B VL
# variants are evaluated; Vulkan is used for all GPU tiers in v0.1 (CUDA build is a later optimization).
TIER_MODELS: dict[str, dict] = {
    "T0": {"chat": QWEN3VL_2B, "mmproj": QWEN3VL_2B_MM, "runtime": RUNTIME_CPU, "ngl": 0, "stt": WHISPER_BASE},
    "T1": {"chat": QWEN3VL_4B, "mmproj": QWEN3VL_4B_MM, "runtime": RUNTIME_CPU, "ngl": 0, "stt": WHISPER_SMALL},
    "T2": {"chat": QWEN3VL_4B, "mmproj": QWEN3VL_4B_MM, "runtime": RUNTIME_VULKAN, "ngl": 99, "stt": WHISPER_SMALL},
    "T3": {"chat": QWEN3VL_8B, "mmproj": QWEN3VL_8B_MM, "runtime": RUNTIME_VULKAN, "ngl": 99, "stt": WHISPER_SMALL},
    "T4": {"chat": QWEN3VL_8B, "mmproj": QWEN3VL_8B_MM, "runtime": RUNTIME_VULKAN, "ngl": 99, "stt": WHISPER_SMALL},
}


def items_for_tier(tier: str) -> list[Item]:
    t = TIER_MODELS[tier]
    return [t["runtime"], t["chat"], t["mmproj"], KOKORO, KOKORO_VOICES, SILERO_VAD, KWS_EN, *t["stt"], *EMBED]
