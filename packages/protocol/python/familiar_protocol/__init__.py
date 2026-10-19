"""Shell <-> Brain protocol for Familiar.

Pydantic models are the source of truth. `python -m familiar_protocol.generate` writes
`packages/protocol/schema.json`; the TypeScript types are generated from that schema.

Transport: one WebSocket on 127.0.0.1 at a random port, `?token=<secret>` query auth. Every
frame is a JSON object with a `type` discriminator.
"""

from .messages import *
from .messages import PROTOCOL_VERSION, parse_brain_message, parse_shell_message  # noqa: F401
