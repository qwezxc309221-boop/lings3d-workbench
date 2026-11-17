#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$APP_DIR/../scripts/environment.sh"
PYTHON="$LINGS_PYTHON"
mkdir -p "$APP_DIR/runtime"
# 使用文件锁，避免重复启动控制服务。
exec 9>"$APP_DIR/runtime/launch.lock"
flock 9
if ! curl --max-time 2 -fsS http://127.0.0.1:8080/api/state >/dev/null 2>&1; then
  if "$PYTHON" -c 'import socket;s=socket.socket();exit(s.connect_ex(("127.0.0.1",8080)))'; then
    zenity --error --text='8080 端口已被其他程序占用，请先关闭旧演示服务。' 2>/dev/null || true
    exit 1
  fi
  cd "$APP_DIR"
  "$PYTHON" - "$APP_DIR" <<'PYSTART'
from pathlib import Path
import os, subprocess, sys
root=Path(sys.argv[1])
with open(root/'runtime/server.log','ab',buffering=0) as stream:
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port','8080','--no-access-log'],
                          cwd=root,stdout=stream,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
                          start_new_session=True,env=dict(os.environ,PYTHONUNBUFFERED='1'))
(root/'runtime/server.pid').write_text(str(proc.pid))
PYSTART
  for attempt in {1..30}; do
    if curl --max-time 1 -fsS http://127.0.0.1:8080/api/state >/dev/null 2>&1; then break; fi
    sleep 0.5
  done
fi
flock -u 9
xdg-open http://localhost:8080 >/dev/null 2>&1 || true
