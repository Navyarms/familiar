"""Spike 7: minimal Brain stub — FastAPI + WebSocket, token auth, random port.

Protocol with the supervisor (Electron main process):
  * argv: --token <secret>  (port 0 => OS picks; we print READY <port> on stdout, flush)
  * GET  /health              -> {"ok": true, "pid": ...}
  * WS   /ws?token=<secret>   -> echoes {"type":"ping"} with {"type":"pong"}; sends a {"type":"state"} on connect
  * POST /crash               -> os._exit(3) so the supervisor's restart path can be tested
  * If the parent pipe closes (stdin EOF) we exit, so an orphaned brain never lingers.
"""
import argparse, asyncio, json, os, socket, sys, threading, time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI()
TOKEN = ""
STARTED = time.time()
PORT = 0

@app.on_event("startup")
async def _announce():
    # Only announce once uvicorn has actually bound the socket (printing before
    # uvicorn.run() raced the supervisor's first connection).
    print(f"READY {PORT}", flush=True)

@app.get("/health")
def health():
    return {"ok": True, "pid": os.getpid(), "uptime_s": round(time.time() - STARTED, 2), "frozen": bool(getattr(sys, "frozen", False))}

@app.post("/crash")
def crash():
    threading.Timer(0.1, lambda: os._exit(3)).start()
    return {"crashing": True}

@app.websocket("/ws")
async def ws(sock: WebSocket, token: str = Query("")):
    if token != TOKEN:
        await sock.close(code=4401); return
    await sock.accept()
    await sock.send_text(json.dumps({"type": "state", "state": "idle"}))
    try:
        while True:
            msg = json.loads(await sock.receive_text())
            if msg.get("type") == "ping":
                await sock.send_text(json.dumps({"type": "pong", "t": msg.get("t")}))
            else:
                await sock.send_text(json.dumps({"type": "echo", "msg": msg}))
    except WebSocketDisconnect:
        pass

def watch_stdin():
    """Exit when the parent closes our stdin (parent died or asked us to stop)."""
    try:
        for _ in sys.stdin:  # blocks until EOF
            pass
    except Exception:
        pass
    os._exit(0)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--token", default=""); ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(); TOKEN = a.token
    port = a.port
    if port == 0:
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    PORT = port
    threading.Thread(target=watch_stdin, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
