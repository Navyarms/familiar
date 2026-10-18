"""Spike 2: llama-server CPU benchmark for Qwen3 4B Q4_K_M.

Measures time-to-first-token, decode tokens/sec, prompt-processing speed, and
tool-call reliability on 20 sample tool tasks through the OpenAI-compatible API.

Usage:
  python bench.py --base http://127.0.0.1:8081 --label "qwen3-4b 16t" [--thinking]
Assumes llama-server is already running (see run_server.ps1).
"""
import argparse, json, time, statistics
import requests

TOOLS = [
    {"type": "function", "function": {"name": "open_app", "description": "Launch an application by name.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "App name, e.g. 'Spotify'"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "set_volume", "description": "Set system volume 0-100.",
        "parameters": {"type": "object", "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}}, "required": ["level"]}}},
    {"type": "function", "function": {"name": "focus_window", "description": "Bring a window to the front by (partial) title.",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "type_text", "description": "Type text into the focused window.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "press_hotkey", "description": "Press a key combination such as 'ctrl+s' or 'win+d'.",
        "parameters": {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]}}},
    {"type": "function", "function": {"name": "search_files", "description": "Search the user's files by name pattern and optional modified-within-days.",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "days": {"type": "integer"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "web_search", "description": "Search the web and return top results.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "set_timer", "description": "Set a reminder/timer.",
        "parameters": {"type": "object", "properties": {"seconds": {"type": "integer"}, "label": {"type": "string"}}, "required": ["seconds"]}}},
    {"type": "function", "function": {"name": "clipboard_write", "description": "Put text on the clipboard.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "remember", "description": "Store a fact about the user in long-term memory.",
        "parameters": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}}},
    {"type": "function", "function": {"name": "take_screenshot", "description": "Capture the screen and describe it.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "media_control", "description": "Control media playback.",
        "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["play", "pause", "next", "previous", "mute"]}}, "required": ["action"]}}},
]

# (user utterance, expected tool, validator over args)
TASKS = [
    ("Open Spotify", "open_app", lambda a: "spotify" in a.get("name", "").lower()),
    ("Turn the volume down to 30", "set_volume", lambda a: a.get("level") == 30),
    ("Mute the music", "media_control", lambda a: a.get("action") == "mute"),
    ("Skip this song", "media_control", lambda a: a.get("action") == "next"),
    ("Bring up the Chrome window", "focus_window", lambda a: "chrome" in a.get("title", "").lower()),
    ("Type 'see you at 5' for me", "type_text", lambda a: "see you at 5" in a.get("text", "").lower()),
    ("Save this file", "press_hotkey", lambda a: a.get("keys", "").lower().replace(" ", "") in ("ctrl+s", "control+s")),
    ("Show me the desktop", "press_hotkey", lambda a: "win" in a.get("keys", "").lower() and "d" in a.get("keys", "").lower()),
    ("Find the PDF I downloaded yesterday", "search_files", lambda a: "pdf" in a.get("pattern", "").lower() and (a.get("days") in (1, 2) or a.get("days") is None)),
    ("Look for any files named budget", "search_files", lambda a: "budget" in a.get("pattern", "").lower()),
    ("What's the weather in Seattle right now?", "web_search", lambda a: "seattle" in a.get("query", "").lower()),
    ("Who won the last World Cup?", "web_search", lambda a: "world cup" in a.get("query", "").lower()),
    ("Remind me in 10 minutes to check the oven", "set_timer", lambda a: a.get("seconds") == 600),
    ("Set a 45 second timer", "set_timer", lambda a: a.get("seconds") == 45),
    ("Copy 'hello world' to my clipboard", "clipboard_write", lambda a: "hello world" in a.get("text", "").lower()),
    ("Remember that my dog's name is Biscuit", "remember", lambda a: "biscuit" in a.get("fact", "").lower()),
    ("What's on my screen right now?", "take_screenshot", lambda a: True),
    ("Pause the video", "media_control", lambda a: a.get("action") == "pause"),
    ("Launch the calculator", "open_app", lambda a: "calc" in a.get("name", "").lower()),
    ("Set volume to max", "set_volume", lambda a: a.get("level") == 100),
]

SYSTEM = ("You are Wisp, a helpful desktop assistant that can operate the user's Windows PC. "
          "When the user asks you to do something you have a tool for, call the tool. "
          "Keep spoken replies short.")

# ~600 token filler to make the prefix realistic (persona prompt + memory)
FILLER = ("Context about the user: " + " ".join(f"fact {i}: the user likes topic number {i} and mentioned it on day {i}." for i in range(40)))


def chat(base, messages, tools=None, stream=False, thinking=False, max_tokens=256, timeout=900):
    body = {"model": "x", "messages": messages, "max_tokens": max_tokens, "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": thinking}}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        t0 = time.perf_counter(); first = None; n = 0; text = ""; usage = None; timings = None
        with requests.post(f"{base}/v1/chat/completions", json=body, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith(b"data: "):
                    continue
                payload = line[6:]
                if payload == b"[DONE]":
                    break
                ev = json.loads(payload)
                if ev.get("usage"):
                    usage = ev["usage"]
                if ev.get("timings"):
                    timings = ev["timings"]
                for ch in ev.get("choices", []):
                    d = ch.get("delta", {})
                    if d.get("content") or d.get("reasoning_content") or d.get("tool_calls"):
                        if first is None:
                            first = time.perf_counter()
                        n += 1
                        text += d.get("content") or ""
        t1 = time.perf_counter()
        return {"ttft": (first - t0) if first else None, "total": t1 - t0, "chunks": n, "text": text, "usage": usage, "timings": timings}
    r = requests.post(f"{base}/v1/chat/completions", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def bench_speed(base, thinking, runs=3):
    """Each run uses a distinct system prompt so the server must process the whole
    ~930-token prefix (cold prefix). A final run reuses the last prefix with a new
    user turn to measure the prompt-cached case (turn 2+ of a conversation)."""
    rows = []
    for i in range(runs + 1):
        cached = i == runs
        sess = runs - 1 if cached else i
        msgs = [{"role": "system", "content": f"[session {sess}] " + SYSTEM + "\n" + FILLER},
                {"role": "user", "content": ("Now in about 80 words, explain why grass is green." if cached
                                             else "In about 80 words, explain why the sky is blue.")}]
        res = chat(base, msgs, stream=True, thinking=thinking, max_tokens=160)
        t = res["timings"] or {}
        rows.append({"cached_prefix": cached, "ttft_s": res["ttft"], "total_s": res["total"],
                     "prompt_tokens": t.get("prompt_n"), "pp_tok_s": t.get("prompt_per_second"),
                     "gen_tokens": t.get("predicted_n"), "tg_tok_s": t.get("predicted_per_second")})
        print(f"  speed run {i}{' (cached prefix)' if cached else ''}: ttft={res['ttft']:.3f}s pp={t.get('prompt_n')}tok @ {t.get('prompt_per_second', 0):.1f} t/s  tg={t.get('predicted_n')}tok @ {t.get('predicted_per_second', 0):.1f} t/s", flush=True)
    return rows


def bench_tools(base, thinking):
    results = []
    for utt, expect, check in TASKS:
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": utt}]
        t0 = time.perf_counter()
        try:
            res = chat(base, msgs, tools=TOOLS, thinking=thinking, max_tokens=512)
            dt = time.perf_counter() - t0
            msg = res["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            name = calls[0]["function"]["name"] if calls else None
            args_raw = calls[0]["function"]["arguments"] if calls else None
            try:
                args = json.loads(args_raw) if args_raw else {}
                valid_json = True
            except Exception:
                args, valid_json = {}, False
            ok = (name == expect) and valid_json and check(args)
            results.append({"task": utt, "expected": expect, "got": name, "args": args_raw, "valid_json": valid_json, "ok": ok, "latency_s": dt,
                            "content": (msg.get("content") or "")[:120]})
            print(f"  [{'OK ' if ok else 'BAD'}] {utt!r:45} -> {name} {args_raw}  ({dt:.2f}s)", flush=True)
        except Exception as e:
            results.append({"task": utt, "expected": expect, "got": None, "ok": False, "error": str(e)})
            print(f"  [ERR] {utt!r}: {e}", flush=True)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8081")
    ap.add_argument("--label", default="run")
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--skip-tools", action="store_true")
    a = ap.parse_args()
    chat(a.base, [{"role": "user", "content": "hi"}], max_tokens=4, thinking=False)  # warm-up
    print(f"== {a.label} (thinking={a.thinking}) ==")
    speed = bench_speed(a.base, a.thinking)
    tools = [] if a.skip_tools else bench_tools(a.base, a.thinking)
    summary = {
        "label": a.label, "thinking": a.thinking,
        "ttft_cold_prefix_median_s": statistics.median([r["ttft_s"] for r in speed if not r["cached_prefix"]]),
        "ttft_cached_prefix_s": [r["ttft_s"] for r in speed if r["cached_prefix"]][0],
        "pp_tok_s_median": statistics.median([r["pp_tok_s"] for r in speed if not r["cached_prefix"]]),
        "tg_tok_s_median": statistics.median([r["tg_tok_s"] for r in speed]),
        "prompt_tokens": speed[0]["prompt_tokens"],
        "tool_ok": sum(1 for r in tools if r["ok"]), "tool_total": len(tools),
        "tool_latency_median_s": statistics.median([r["latency_s"] for r in tools if "latency_s" in r]) if tools else None,
    }
    print(json.dumps(summary, indent=2))
    out = a.out or f"results_{a.label.replace(' ', '_')}.json"
    json.dump({"summary": summary, "speed": speed, "tools": tools}, open(out, "w"), indent=2)
    print("wrote", out)
