"""Message types. Keep this file dependency-free apart from pydantic."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

PROTOCOL_VERSION = 1

BrainState = Literal["booting", "downloading", "idle", "listening", "thinking", "speaking", "executing", "needs_confirm", "error"]


class _Msg(BaseModel):
    model_config = {"extra": "forbid"}


# ------------------------------------------------------------------ Brain -> Shell


class HelloMsg(_Msg):
    """First frame after the socket opens."""

    type: Literal["hello"] = "hello"
    protocol: int = PROTOCOL_VERSION
    brain_version: str
    persona: PersonaInfo | None = None


class PersonaInfo(_Msg):
    id: str
    name: str
    wake_phrase: str
    voice: str
    avatar_path: str | None = Field(None, description="Absolute path to the pack's VRM, or null for the built-in orb.")
    expressions: dict[str, ExpressionSpec] = Field(default_factory=dict, description="tag -> VRM expression mapping from the pack")


class ExpressionSpec(_Msg):
    vrm: str = Field(description="VRM expression name, e.g. 'happy'")
    weight: float = 1.0
    decay_s: float = 2.5


class StateMsg(_Msg):
    type: Literal["state"] = "state"
    state: BrainState
    detail: str | None = None


class TranscriptPartialMsg(_Msg):
    type: Literal["transcript.partial"] = "transcript.partial"
    text: str


class TranscriptFinalMsg(_Msg):
    type: Literal["transcript.final"] = "transcript.final"
    text: str


class ReplyTokenMsg(_Msg):
    """Streamed reply text with expression tags already stripped."""

    type: Literal["reply.token"] = "reply.token"
    turn_id: str
    text: str


class ReplyAudioMsg(_Msg):
    """One synthesized sentence. PCM is 16-bit little-endian mono, base64."""

    type: Literal["reply.audio"] = "reply.audio"
    turn_id: str
    sentence_id: int
    text: str
    sample_rate: int
    pcm16_b64: str
    duration_s: float
    envelope_hz: int = Field(50, description="Rate of `envelope` frames")
    envelope: list[float] = Field(default_factory=list, description="RMS amplitude 0..1 per frame, for lip sync")
    visemes: list[VisemeHint] = Field(default_factory=list, description="Coarse vowel hints spread over the sentence")


class VisemeHint(_Msg):
    t: float = Field(description="seconds from sentence start")
    shape: Literal["aa", "ih", "ou", "ee", "oh", "sil"]


class ReplyDoneMsg(_Msg):
    type: Literal["reply.done"] = "reply.done"
    turn_id: str
    text: str = Field(description="Full reply text, tags stripped")
    interrupted: bool = False
    stats: dict[str, float] = Field(default_factory=dict, description="ttft_s, tokens, tok_s, first_audio_s ...")


class ExpressionMsg(_Msg):
    type: Literal["expression"] = "expression"
    tag: str
    sentence_id: int | None = Field(None, description="Apply when this sentence starts playing; null = now")


class ToolRequestConfirmMsg(_Msg):
    type: Literal["tool.request_confirm"] = "tool.request_confirm"
    request_id: str
    tool: str
    args: dict
    description: str
    risk: Literal["low", "medium", "high"]


class ToolStartMsg(_Msg):
    """A tool is about to run (after confirmation if needed)."""

    type: Literal["tool.start"] = "tool.start"
    request_id: str
    tool: str
    args: dict
    description: str


class ToolInfo(_Msg):
    name: str
    group: str
    description: str
    permission: Literal["auto", "confirm", "blocked"]
    default_permission: Literal["auto", "confirm", "blocked"]
    risk: Literal["low", "medium", "high"]


class ToolsInfoMsg(_Msg):
    """Registered tools with their effective permission. Sent on connect and after settings changes."""

    type: Literal["tools.info"] = "tools.info"
    tools: list[ToolInfo]


class ToolResultMsg(_Msg):
    type: Literal["tool.result"] = "tool.result"
    request_id: str
    tool: str
    ok: bool
    summary: str


class ModelDownloadProgressMsg(_Msg):
    type: Literal["model.download_progress"] = "model.download_progress"
    item: str
    bytes_done: int
    bytes_total: int
    speed_bps: float = 0
    overall_done: int = 0
    overall_total: int = 0


class ModelReadyMsg(_Msg):
    type: Literal["model.ready"] = "model.ready"
    tier: str
    chat_model: str
    tts_model: str


class ModelsInfoMsg(_Msg):
    """Hardware probe + tier choice + what still needs downloading. Sent at boot."""

    type: Literal["models.info"] = "models.info"
    tier: str
    tier_reason: str
    hardware: dict
    required: list[ModelItemInfo]


class ModelItemInfo(_Msg):
    name: str
    bytes_total: int
    present: bool


class MicStateMsg(_Msg):
    """Voice pipeline mode for the avatar's mic indicator. `level` is 0..1 while listening."""

    type: Literal["mic.state"] = "mic.state"
    mode: Literal["off", "idle", "wake", "listening", "processing"]
    level: float = 0.0
    reason: str = Field("", description="wake | ptt | voice | no speech | nothing heard | cancelled")


class ErrorMsg(_Msg):
    type: Literal["error"] = "error"
    code: str
    message: str
    fatal: bool = False


# ------------------------------------------------------------------ Shell -> Brain


class SummonMsg(_Msg):
    type: Literal["summon"] = "summon"


class PttStartMsg(_Msg):
    type: Literal["ptt.start"] = "ptt.start"


class PttStopMsg(_Msg):
    type: Literal["ptt.stop"] = "ptt.stop"


class TextSendMsg(_Msg):
    type: Literal["text.send"] = "text.send"
    text: str
    speak: bool = True


class InterruptMsg(_Msg):
    type: Literal["interrupt"] = "interrupt"


class ToolConfirmMsg(_Msg):
    type: Literal["tool.confirm"] = "tool.confirm"
    request_id: str


class ToolDenyMsg(_Msg):
    type: Literal["tool.deny"] = "tool.deny"
    request_id: str


class PersonaSwitchMsg(_Msg):
    type: Literal["persona.switch"] = "persona.switch"
    persona_id: str


class SettingsUpdateMsg(_Msg):
    type: Literal["settings.update"] = "settings.update"
    settings: dict


class KillSwitchMsg(_Msg):
    type: Literal["kill_switch"] = "kill_switch"


class PlaybackMsg(_Msg):
    """Shell tells the brain whether reply audio is currently playing (for barge-in and the speaking state)."""

    type: Literal["playback"] = "playback"
    speaking: bool


class DownloadStartMsg(_Msg):
    """Shell asks the brain to start (or resume) downloading what the tier needs."""

    type: Literal["models.download"] = "models.download"


BrainMessage = Annotated[
    HelloMsg
    | StateMsg
    | TranscriptPartialMsg
    | TranscriptFinalMsg
    | ReplyTokenMsg
    | ReplyAudioMsg
    | ReplyDoneMsg
    | ExpressionMsg
    | ToolRequestConfirmMsg
    | ToolStartMsg
    | ToolsInfoMsg
    | ToolResultMsg
    | ModelDownloadProgressMsg
    | ModelReadyMsg
    | ModelsInfoMsg
    | MicStateMsg
    | ErrorMsg,
    Field(discriminator="type"),
]

ShellMessage = Annotated[
    SummonMsg
    | PttStartMsg
    | PttStopMsg
    | TextSendMsg
    | InterruptMsg
    | ToolConfirmMsg
    | ToolDenyMsg
    | PersonaSwitchMsg
    | SettingsUpdateMsg
    | KillSwitchMsg
    | PlaybackMsg
    | DownloadStartMsg,
    Field(discriminator="type"),
]

_shell_adapter: TypeAdapter = TypeAdapter(ShellMessage)
_brain_adapter: TypeAdapter = TypeAdapter(BrainMessage)


def parse_shell_message(raw: str | bytes):
    return _shell_adapter.validate_json(raw)


def parse_brain_message(raw: str | bytes):
    return _brain_adapter.validate_json(raw)


class Envelope(BaseModel):
    """Only used to emit one combined JSON schema with both directions."""

    brain: BrainMessage
    shell: ShellMessage
