"""Tool-choice evaluation: does the model pick the right tool (or none) for a set of utterances? Dry run: nothing executes.

Starts llama-server with the tier's model, builds the exact prefix the agent uses (system prompt + few-shot + tool
schemas), and for each case asks for one step with tools. Records the first tool call and compares it to what we expect.

Usage: uv run python scripts/tool_eval.py [--filter words] [--runs 1]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time

from familiar_brain.agent.turn import FEW_SHOT, build_system_prompt
from familiar_brain.config import Paths, Settings
from familiar_brain.llm import LlamaServer, LlmClient, StreamStats, default_threads
from familiar_brain.models import ModelManager
from familiar_brain.persona import load_persona
from familiar_brain.tools import REGISTRY

# (utterance, acceptable tool names ("" = no tool), argument check or None)
CASES: list[tuple[str, set[str], callable | None]] = [
    # system
    ("Open Spotify.", {"open_app"}, lambda a: "spotify" in str(a.get("name", "")).lower()),
    ("Launch the calculator for me.", {"open_app"}, lambda a: "calc" in str(a.get("name", "")).lower()),
    ("Close Notepad.", {"close_app", "window_action"}, None),
    ("Turn the volume down to 20.", {"set_volume"}, lambda a: a.get("level") == 20),
    ("Mute everything.", {"set_mute"}, lambda a: a.get("muted") is True),
    ("Skip to the next song.", {"media_control"}, lambda a: a.get("action") == "next"),
    ("Pause the music.", {"media_control"}, lambda a: a.get("action") == "play_pause"),
    ("Lock my computer.", {"power"}, lambda a: a.get("action") == "lock"),
    ("Restart the PC.", {"power"}, lambda a: a.get("action") == "restart"),
    ("How much battery do I have left?", {"system_info"}, None),
    ("What's the volume at right now?", {"get_volume", "system_info"}, None),
    ("What day is it today?", {""}, None),  # time rides on the note; no tool needed
    ("What's the weather like outside?", {"get_weather"}, lambda a: not a.get("place")),  # user's own location
    ("Where am I right now?", {""}, None),  # location sits in the system prompt; no tool needed
    # windows
    ("Bring Chrome to the front.", {"focus_window"}, lambda a: "chrome" in str(a.get("title", "")).lower()),
    ("Snap the Notepad window to the right.", {"window_action"}, lambda a: a.get("action") == "snap_right"),
    ("Minimize Discord.", {"window_action"}, lambda a: a.get("action") == "minimize"),
    ("What windows do I have open?", {"list_windows"}, None),
    # files
    ("Find the PDF I downloaded yesterday.", {"search_files"}, lambda a: str(a.get("ext", "")).lower().strip(".") == "pdf" and (a.get("days") in (1, 2))),
    ("Look for any spreadsheets named budget.", {"search_files"}, lambda a: "budget" in str(a.get("query", "")).lower()),
    ("Open the file C:\\Users\\me\\Documents\\notes.txt.", {"open_file"}, lambda a: "notes.txt" in str(a.get("path", ""))),
    ("Write a file called todo.txt in my documents with the text buy milk.", {"write_file"}, lambda a: "todo" in str(a.get("path", "")).lower()),
    # web / weather
    ("What's the weather like in Chicago?", {"get_weather"}, lambda a: "chicago" in str(a.get("place", "")).lower()),
    ("Who won the last Super Bowl?", {"web_search"}, None),
    ("What's the latest news about SpaceX?", {"web_search"}, None),
    # clipboard
    ("What's on my clipboard?", {"clipboard_read"}, None),
    ("Copy the phrase hello world to my clipboard.", {"clipboard_write"}, lambda a: "hello world" in str(a.get("text", "")).lower()),
    # timers
    ("Set a timer for 15 minutes.", {"set_timer"}, lambda a: a.get("seconds") == 900),
    ("Remind me at 3 pm to call mom.", {"set_reminder"}, lambda a: "mom" in str(a.get("text", "")).lower()),
    ("Cancel my timers.", {"timers"}, lambda a: a.get("action") == "cancel"),
    # memory
    ("Remember that my sister's name is Dana.", {"remember"}, lambda a: "dana" in str(a.get("fact", "")).lower()),
    ("What do you remember about my job?", {"recall", ""}, None),
    # bluetooth / screen / input
    ("Turn on Bluetooth.", {"set_bluetooth"}, lambda a: a.get("action") == "on"),
    ("Is Bluetooth on?", {"set_bluetooth"}, lambda a: a.get("action") == "check"),
    ("Click the Save button.", {"click_element"}, lambda a: "save" in str(a.get("name", "")).lower()),
    ("Type hello there into this window.", {"type_text"}, lambda a: "hello there" in str(a.get("text", "")).lower()),
    ("Press control S.", {"press_keys"}, lambda a: "s" in str(a.get("keys", "")).lower()),
    ("Scroll down a bit.", {"scroll"}, lambda a: a.get("direction") == "down"),
    ("What does this error dialog say?", {"read_window_text", "find_elements", "get_foreground_window"}, None),
    ("What am I looking at right now?", {"get_foreground_window", "read_window_text", "find_elements"}, None),
    ("Take a look at my screen and describe the picture that's open.", {"look_at_screen"}, None),
    # no tool
    ("Tell me a joke about cats.", {""}, None),
    ("Thanks, that's all.", {""}, None),
    ("How do magnets work?", {""}, None),
    ("What's 17 times 23?", {""}, None),
]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default="")
    ap.add_argument("--runs", type=int, default=1)
    a = ap.parse_args()
    paths = Paths.default().ensure()
    settings = Settings.load(paths.settings_file)
    mm = ModelManager(paths, settings)
    persona = load_persona(paths, settings.persona_id)
    REGISTRY.configure(settings)
    tools = REGISTRY.openai_tools()
    srv = LlamaServer(mm.llama_server_exe(), mm.chat_model_path(), mm.mmproj_path(), threads=settings.llm_threads or default_threads(),
                      ctx=settings.llm_ctx, ngl=mm.ngl(), log_dir=paths.logs)
    await srv.start()
    llm = LlmClient(srv.base_url)
    system = build_system_prompt(persona, tools=True)
    prefix = [{"role": "system", "content": system}, *FEW_SHOT]
    t0 = time.monotonic()
    await llm.warm(prefix, tools=tools)
    print(f"prefix warmed in {time.monotonic()-t0:.1f}s; {len(tools)} tools")
    cases = [c for c in CASES if a.filter.lower() in c[0].lower()]
    ok = 0
    fails = []
    lat = []
    nudges = [0]
    try:
        for utt, expected, check in cases:
            hits = 0
            for _ in range(a.runs):
                now = time.strftime("%A %B %d %Y, %I:%M %p").replace(" 0", " ")
                note = f"\n\n(Private context, do not read aloud: It is now {now}.)" if re.search(r"\b(time|date|today|day|when|now)\b", utt, re.I) else ""
                msgs = [*prefix, {"role": "user", "content": utt + note}]
                stats = StreamStats()
                t1 = time.monotonic()
                content = ""
                async for tok in llm.stream(msgs, max_tokens=120, stats=stats, tools=tools, temperature=0.4):
                    content += tok
                lat.append(time.monotonic() - t1)
                nudged = ""
                if not stats.tool_calls and expected != {""}:
                    # mirror the production agent: an announce-only step gets one forced retry
                    from familiar_brain.agent.turn import ANNOUNCE_RE
                    first = re.split(r"[.!?\n]", content.strip(), maxsplit=1)[0].strip()
                    first = re.sub(r"^\[\w+\]\s*", "", first)
                    if len(first) <= 40 and ANNOUNCE_RE.match(first):
                        msgs2 = [*msgs, {"role": "assistant", "content": content.strip()},
                                 {"role": "user", "content": "(You announced an action but did not call the tool. Call it now.)"}]
                        stats = StreamStats()
                        async for _tok in llm.stream(msgs2, max_tokens=120, stats=stats, tools=tools, temperature=0.4, tool_choice="required"):
                            pass
                        nudged = " (after nudge)"
                        nudges[0] += 1
                call = stats.tool_calls[0] if stats.tool_calls else None
                name = call.name if call else ""
                try:
                    args = call.args() if call else {}
                except Exception:
                    args = {}
                good = name in expected and (check is None or not name or bool(check(args)))
                hits += good
                mark = "OK " if good else "BAD"
                print(f"  [{mark}] {utt[:52]:52} -> {name or '(no tool)'}{nudged} {json.dumps(args)[:70]}  {('| ' + content.strip()[:50]) if content.strip() else ''}")
                if not good:
                    fails.append((utt, name, args, sorted(expected), content.strip()[:80]))
            ok += hits / a.runs
    finally:
        await llm.aclose()
        srv.stop()
    n = len(cases)
    print(f"\n{ok:.0f}/{n} = {100*ok/n:.0f}%   median step latency {sorted(lat)[len(lat)//2]:.2f}s   nudges used: {nudges[0]}")
    if fails:
        print("failures:")
        for f in fails:
            print("  ", f)


if __name__ == "__main__":
    asyncio.run(main())
