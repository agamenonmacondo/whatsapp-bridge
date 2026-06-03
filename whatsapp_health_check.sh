#!/bin/bash
# WhatsApp Bridge Health Check
# Verifica que el webhook de WhatsApp funcione de punta a punta
# Si algo falla, lo reinicia y avisa por WhatsApp a Alejandro

LOG="/tmp/wa_health_check.log"
ALERT_PHONE="573504017710"
BRIDGE_URL="http://localhost:3201"
TUNNEL_URL="https://wa.ccs724.com/webhook"
TOKEN_FILE="/home/mod/.openclaw/workspace/wa_longlived_token.txt"
PHONE_ID="1074934415702869"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

send_alert() {
    local msg="$1"
    if [ -f "$TOKEN_FILE" ]; then
        local TOKEN=$(cat "$TOKEN_FILE")
        python3 -c "
import urllib.request, json
token = open('$TOKEN_FILE').read().strip()
data = json.dumps({'messaging_product': 'whatsapp', 'to': '$ALERT_PHONE', 'type': 'text', 'text': {'body': '''$msg'''}}).encode()
req = urllib.request.Request('https://graph.facebook.com/v19.0/$PHONE_ID/messages', data=data, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}, method='POST')
try: urllib.request.urlopen(req, timeout=10)
except: pass
" >> "$LOG" 2>&1
    fi
}

ALERTS=""
RESTARTED=""

# 1. Verificar Python Bridge (localhost)
BRIDGE_RESPONSE=$(curl -s -o /dev/null -w '%{http_code}' "$BRIDGE_URL/webhook?hub.mode=subscribe&hub.verify_token=your_webhook_verify_token&hub.challenge=HEALTH_CHECK" 2>/dev/null)
if [ "$BRIDGE_RESPONSE" != "200" ]; then
    log "❌ Bridge DOWN (HTTP $BRIDGE_RESPONSE)"
    ALERTS="$ALERTS\n❌ Bridge DOWN"
    systemctl --user restart whatsapp-bridge.service 2>/dev/null
    sleep 3
    NEW_RESPONSE=$(curl -s -o /dev/null -w '%{http_code}' "$BRIDGE_URL/webhook?hub.mode=subscribe&hub.verify_token=your_webhook_verify_token&hub.challenge=HC2" 2>/dev/null)
    if [ "$NEW_RESPONSE" = "200" ]; then
        log "✅ Bridge reiniciado OK"
        RESTARTED="$RESTARTED\n✅ Bridge reiniciado"
    else
        log "❌ Bridge NO responde tras reiniciar"
    fi
else
    log "✅ Bridge OK"
fi

# 2. Verificar Cloudflare Tunnel (desde internet)
TUNNEL_RESPONSE=$(curl -s -o /dev/null -w '%{http_code}' -H 'User-Agent: Meta-WhatsApp/1.0' "$TUNNEL_URL?hub.mode=subscribe&hub.verify_token=your_webhook_verify_token&hub.challenge=TUNNEL_HC" 2>/dev/null)
if [ "$TUNNEL_RESPONSE" != "200" ]; then
    log "❌ Tunnel DOWN (HTTP $TUNNEL_RESPONSE)"
    ALERTS="$ALERTS\n❌ Tunnel DOWN"
    systemctl --user restart cloudflared-wa.service 2>/dev/null
    sleep 5
    NEW_RESPONSE=$(curl -s -o /dev/null -w '%{http_code}' -H 'User-Agent: Meta-WhatsApp/1.0' "$TUNNEL_URL?hub.mode=subscribe&hub.verify_token=your_webhook_verify_token&hub.challenge=THC2" 2>/dev/null)
    if [ "$NEW_RESPONSE" = "200" ]; then
        log "✅ Tunnel reiniciado OK"
        RESTARTED="$RESTARTED\n✅ Tunnel reiniciado"
    else
        log "❌ Tunnel NO responde tras reiniciar"
    fi
else
    log "✅ Tunnel OK"
fi

# 3. Verificar token de WhatsApp
TOKEN_EXPIRY=$(python3 -c "
import urllib.request, json, time
try:
    token = open('$TOKEN_FILE').read().strip()
    req = urllib.request.Request('https://graph.facebook.com/v19.0/debug_token?input_token=' + token + '&access_token=' + token)
    resp = urllib.request.urlopen(req, timeout=10)
    d = json.loads(resp.read())['data']
    days_left = (d.get('expires_at', 0) - time.time()) / 86400
    print(f'{days_left:.0f}')
except: print('ERROR')
" 2>/dev/null)

if [ "$TOKEN_EXPIRY" = "ERROR" ]; then
    log "⚠️ No se pudo verificar token"
elif [ "$TOKEN_EXPIRY" -lt 7 ] 2>/dev/null; then
    log "⚠️ Token expira en $TOKEN_EXPIRY días - renovando"
    /home/mod/.openclaw/workspace/renew_wa_token.sh >> "$LOG" 2>&1
    ALERTS="$ALERTS\n⚠️ Token renovado (expiraba en $TOKEN_EXPIRY días)"
elif [ "$TOKEN_EXPIRY" -lt 14 ] 2>/dev/null; then
    log "⚠️ Token expira en $TOKEN_EXPIRY días"
    ALERTS="$ALERTS\n⚠️ Token expira en $TOKEN_EXPIRY días"
else
    log "✅ Token OK (expira en $TOKEN_EXPIRY días)"
fi

# Enviar alerta si hay problemas
if [ -n "$ALERTS" ]; then
    MSG="🚨 WhatsApp Health Check$(echo -e "$ALERTS")"
    if [ -n "$RESTARTED" ]; then
        MSG="$(echo -e "$MSG")$(echo -e "$RESTARTED")"
    fi
    send_alert "$MSG"
    log "Alerta enviada"
fi

# Limpiar logs viejos (7 días)
find /tmp/wa_*.log -mtime +7 -delete 2>/dev/null