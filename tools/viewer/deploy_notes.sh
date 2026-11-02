#!/usr/bin/env bash
# 部署复盘页批注后端到自己的服务器（HIRO_VIEW_HOST）：/opt/hiro-notes/notes_server.py + systemd hiro-notes + nginx /hiro-view/api/ 转发（限速）。
# 重复运行是安全的：只更新程序、确保服务和 nginx 配置存在。
set -euo pipefail
: "${HIRO_VIEW_HOST:?请先设置 HIRO_VIEW_HOST（ssh 主机名）}"
HERE="$(cd "$(dirname "$0")" && pwd)"
scp -q "$HERE/notes_server.py" "$HIRO_VIEW_HOST":/tmp/notes_server.py
ssh "$HIRO_VIEW_HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
install -d -m 755 /opt/hiro-notes
install -m 644 /tmp/notes_server.py /opt/hiro-notes/notes_server.py
install -d -o www-data -g www-data -m 750 /var/lib/hiro-notes
cat > /etc/systemd/system/hiro-notes.service <<'UNIT'
[Unit]
Description=hiro-view notes API
After=network.target

[Service]
User=www-data
Group=www-data
Environment=HIRO_NOTES_PORT=19139
Environment=HIRO_NOTES_FILE=/var/lib/hiro-notes/notes.jsonl
ExecStart=/usr/bin/python3 /opt/hiro-notes/notes_server.py
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/hiro-notes
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now hiro-notes >/dev/null 2>&1
systemctl restart hiro-notes
echo 'limit_req_zone $binary_remote_addr zone=hiro_notes:1m rate=5r/s;' > /etc/nginx/conf.d/hiro-notes-rate.conf
SITE=${HIRO_NGINX_SITE:-/etc/nginx/sites-enabled/default}
if ! grep -q '/hiro-view/api/' "$SITE"; then
  cp "$SITE" "$SITE.bak-$(date +%Y%m%d-%H%M%S)"
  python3 - "$SITE" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
block='''    location ^~ /hiro-view/api/ {
        limit_req zone=hiro_notes burst=20 nodelay;
        client_max_body_size 64k;
        proxy_pass http://127.0.0.1:19139/;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        add_header Cache-Control "no-store" always;
    }
'''
i=s.index('    location = /hiro-view {')
open(p,'w').write(s[:i]+block+s[i:])
PY
fi
nginx -t 2>&1 | tail -1
systemctl reload nginx
sleep 1
systemctl is-active hiro-notes
REMOTE
