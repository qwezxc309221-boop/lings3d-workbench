#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/../scripts/environment.sh"
"$LINGS_PYTHON" - "$APP_DIR/runtime/server.pid" <<'PY'
import os,signal,sys
from pathlib import Path
p=Path(sys.argv[1])
if p.exists():
    pid=int(p.read_text())
    command=Path('/proc')/str(pid)/'cmdline'
    if command.exists() and b'uvicorn\x00server:app' in command.read_bytes():
        os.kill(pid,signal.SIGINT)
        print('工作台正在停止；采集将暂停，已保存数据保留。')
PY
