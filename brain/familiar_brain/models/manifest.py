"""What each tier downloads. URLs, sizes and SHA256 are pinned so a bad mirror or partial file is caught.

Decision from Phase 0: one multimodal chat model (Qwen3-VL Instruct) per tier, Kokoro for TTS.
The llama.cpp runtime is also fetched here when it was not bundled with the installer.
"""

from __future__ import annotations

from dataclasses import dataclass

LLAMA_BUILD = "b11717"
_LL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BUILD}/"
_HF = "https://huggingface.co/"
_KK = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


@dataclass(frozen=True)
class Item:
    name: str  # file name on disk (under models/ or runtimes/)
    url: str
    size: int
    sha256: str | None  # None = not pinned (runtime zips change per build; we pin the build tag instead)
    kind: str  # chat | mmproj | tts | tts_voices | runtime


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

RUNTIME_CPU = Item(f"llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip", _LL + f"llama-{LLAMA_BUILD}-bin-win-cpu-x64.zip", 18 * 2**20, None, "runtime")
RUNTIME_VULKAN = Item(f"llama-{LLAMA_BUILD}-bin-win-vulkan-x64.zip", _LL + f"llama-{LLAMA_BUILD}-bin-win-vulkan-x64.zip", 30 * 2**20, None, "runtime")

# Tier -> chat model, projector, llama-server backend, GPU layers. T3/T4 use the 8B until 14B/32B VL
# variants are evaluated; Vulkan is used for all GPU tiers in v0.1 (CUDA build is a later optimization).
TIER_MODELS: dict[str, dict] = {
    "T0": {"chat": QWEN3VL_2B, "mmproj": QWEN3VL_2B_MM, "runtime": RUNTIME_CPU, "ngl": 0},
    "T1": {"chat": QWEN3VL_4B, "mmproj": QWEN3VL_4B_MM, "runtime": RUNTIME_CPU, "ngl": 0},
    "T2": {"chat": QWEN3VL_4B, "mmproj": QWEN3VL_4B_MM, "runtime": RUNTIME_VULKAN, "ngl": 99},
    "T3": {"chat": QWEN3VL_8B, "mmproj": QWEN3VL_8B_MM, "runtime": RUNTIME_VULKAN, "ngl": 99},
    "T4": {"chat": QWEN3VL_8B, "mmproj": QWEN3VL_8B_MM, "runtime": RUNTIME_VULKAN, "ngl": 99},
}


def items_for_tier(tier: str) -> list[Item]:
    t = TIER_MODELS[tier]
    return [t["runtime"], t["chat"], t["mmproj"], KOKORO, KOKORO_VOICES]
