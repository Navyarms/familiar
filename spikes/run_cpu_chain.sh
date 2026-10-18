#!/bin/bash
# Runs every CPU-timed spike sequentially so measurements do not overlap.
cd "$(dirname "$0")"
PY=$PWD/.venv/Scripts/python.exe
echo "### kokoro"; (cd 03-kokoro-tts && $PY bench.py 2>&1 | tee run.log)
echo "### stt";    (cd 05-stt && $PY bench.py 2>&1 | tee run.log)
echo "### kws";    (cd 04-wakeword && $PY bench.py 2>&1 | tee run.log)
echo "### pyinstaller"; (cd 07-pyinstaller-stub && powershell -NoProfile -ExecutionPolicy Bypass -File build_and_run.ps1 2>&1 | tee run.log)
echo "### llm";    (cd 02-llm-bench && bash run_all.sh 2>&1 | tee run_all.log)
echo CHAIN_DONE
