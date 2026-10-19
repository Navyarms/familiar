"""Write packages/protocol/schema.json from the pydantic models."""

import json
from pathlib import Path

from .messages import Envelope

OUT = Path(__file__).resolve().parents[2] / "schema.json"


def main() -> None:
    schema = Envelope.model_json_schema(mode="serialization")
    schema["title"] = "FamiliarProtocol"
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    OUT.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(schema.get('$defs', {}))} definitions)")


if __name__ == "__main__":
    main()
