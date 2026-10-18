#!/bin/bash
# Sequential CPU benchmark chain (do not run other CPU-heavy work at the same time).
cd "$(dirname "$0")"
PY=../.venv/Scripts/python.exe
SRV=../_downloads/llamacpp/llama-server.exe
G=../_downloads/gguf
start() { # port threads model [mmproj]
  if [ -n "$4" ]; then MM="--mmproj $4"; else MM=""; fi
  "$SRV" -m "$3" --port $1 --host 127.0.0.1 -t $2 -c 8192 --jinja -ngl 0 -fa on $MM > "server_$5.log" 2>&1 &
  SPID=$!
  for i in $(seq 1 120); do curl -sf http://127.0.0.1:$1/health >/dev/null && break; sleep 1; done
}
stop() { kill $SPID 2>/dev/null; sleep 2; taskkill //F //IM llama-server.exe >/dev/null 2>&1; sleep 1; }
echo "### 16t no-think (full-prefix version)"; start 8081 16 $G/Qwen3-4B-Q4_K_M.gguf "" 16t
$PY bench.py --label qwen3-4b_16t_nothink 2>&1 | tee bench_16t_nothink.log
echo "### 16t think"; $PY bench.py --label qwen3-4b_16t_think --thinking 2>&1 | tee bench_16t_think.log
stop
echo "### 8t"; start 8081 8 $G/Qwen3-4B-Q4_K_M.gguf "" 8t
$PY bench.py --label qwen3-4b_8t_nothink --skip-tools 2>&1 | tee bench_8t_nothink.log; stop
echo "### 4t"; start 8081 4 $G/Qwen3-4B-Q4_K_M.gguf "" 4t
$PY bench.py --label qwen3-4b_4t_nothink --skip-tools 2>&1 | tee bench_4t_nothink.log; stop
echo "### VL 16t"; start 8082 16 $G/Qwen3VL-4B-Instruct-Q4_K_M.gguf $G/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf vl16t
$PY bench.py --base http://127.0.0.1:8082 --label qwen3vl-4b_16t_nothink 2>&1 | tee bench_vl16t_nothink.log
$PY vl_bench.py --image ../06-uia/settings_window.png --label qwen3vl_settings_1408x862 2>&1 | tee vl_1280.log
$PY vl_bench.py --image screen_1920.png --label qwen3vl_desktop_1920x1080 2>&1 | tee vl_1920.log
stop
echo ALL_DONE
