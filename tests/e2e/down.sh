#!/bin/bash
T=${E2E_DIR:-/tmp/toolplattform-e2e}
[ -f $T/ngx/nginx.pid ] && nginx -c $T/nginx.conf -p $T/ngx -s stop 2>/dev/null
python3 - <<'PY'
import os, signal
for pid in filter(str.isdigit, os.listdir('/proc')):
    try: cmd = open(f'/proc/{pid}/cmdline','rb').read().replace(b'\0', b' ').decode()
    except Exception: continue
    if cmd.startswith('python') and any(k in cmd for k in ('mockapp.py','mock_authentik.py','mock_webhook.py','app/server.py','app/monitor.py','dev/dev.py')) and int(pid) != os.getpid():
        os.kill(int(pid), signal.SIGTERM)
PY
sleep 0.5
