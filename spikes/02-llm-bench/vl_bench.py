"""Qwen3-VL screenshot understanding on CPU via llama-server --mmproj.
Usage: python vl_bench.py --image screen_1280.png [--base http://127.0.0.1:8082]"""
import argparse, base64, json, time, requests
ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://127.0.0.1:8082"); ap.add_argument("--image", required=True); ap.add_argument("--label", default="vl")
a = ap.parse_args()
b64 = base64.b64encode(open(a.image, "rb").read()).decode()
ext = a.image.rsplit(".", 1)[-1].lower().replace("jpg", "jpeg")
body = {"model": "x", "max_tokens": 200, "temperature": 0.0, "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
            {"type": "text", "text": "Describe this screenshot in two sentences, then list up to 5 clickable UI elements you can see with their approximate location."}]}]}
t0 = time.perf_counter()
r = requests.post(f"{a.base}/v1/chat/completions", json=body, timeout=1800).json()
dt = time.perf_counter() - t0
t = r.get("timings", {})
out = {"label": a.label, "image": a.image, "total_s": dt, "prompt_tokens": t.get("prompt_n"), "pp_tok_s": t.get("prompt_per_second"),
       "prompt_ms": t.get("prompt_ms"), "gen_tokens": t.get("predicted_n"), "tg_tok_s": t.get("predicted_per_second"),
       "reply": r["choices"][0]["message"]["content"]}
print(json.dumps(out, indent=2))
json.dump(out, open(f"results_{a.label}.json", "w"), indent=2)
