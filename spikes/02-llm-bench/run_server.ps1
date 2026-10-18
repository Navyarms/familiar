# Start llama-server (CPU build) for the benchmark.
# Usage: .\run_server.ps1 -Threads 16 [-Model path] [-Port 8081] [-Mmproj path]
param(
  [int]$Threads = 16,
  [string]$Model = "$PSScriptRoot\..\_downloads\gguf\Qwen3-4B-Q4_K_M.gguf",
  [int]$Port = 8081,
  [string]$Mmproj = ""
)
$exe = "$PSScriptRoot\..\_downloads\llamacpp\llama-server.exe"
$a = @("-m", $Model, "--port", $Port, "--host", "127.0.0.1", "-t", $Threads, "-c", "8192", "--jinja", "-ngl", "0", "-fa", "on")
if ($Mmproj) { $a += @("--mmproj", $Mmproj) }
& $exe @a
