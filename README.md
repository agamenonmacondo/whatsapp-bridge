<<<<<<< HEAD
# whatsapp-bridge
WhatsApp Cloud API Bridge — webhook receiver, LLM routing, Supabase history
=======
# WhatsApp Cloud API Bridge

Bridge local en Python que recibe webhooks de **WhatsApp Cloud API (Meta)** y enruta mensajes a un LLM (OpenRouter / OpenClaw / Groq para transcripción de audio), con historial persistente en Supabase.

Diseñado originalmente como vendedor (M.A.R.I.A.) de iPhone 14 Pro Max en CCS724, pero agnóstico: el system prompt y el producto son configurables.

## Features

- ✅ Webhook HTTP en puerto configurable (default 8765)
- ✅ Verificación de webhook (`hub.mode=subscribe`)
- ✅ Debounce de mensajes (5s) para agrupar mensajes rápidos
- ✅ Horario de cortesía (7AM–10PM, configurable)
- ✅ Historial de conversación en **Supabase** (`whatsapp_leads` table)
- ✅ LLM vía OpenRouter o OpenClaw (modelo configurable)
- ✅ Transcripción de audio vía Groq (Whisper)
- ✅ **Error shielding**: nunca filtra errores al lead
- ✅ Notificación a admin cuando lead quiere cerrar
- ✅ Health check cada 5 min con auto-restart
- ✅ Systemd service incluido

## Quick start

### 1. Clonar e instalar dependencias

```bash
git clone https://github.com/agamenonmacondo/whatsapp-bridge.git
cd whatsapp-bridge
pip3 install -r requirements.txt
```

### 2. Configurar

Edita `wa_local_receiver.py` y reemplaza los placeholders:

| Variable | Dónde sacarla |
|---|---|
| `WA_ACCESS_TOKEN` | Meta Developers → App → WhatsApp → API Setup |
| `PHONE_NUMBER_ID` | Meta Developers → App → WhatsApp → API Setup |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| `LLM_TOKEN` (si usas OpenClaw) | `openclaw config` |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase project settings |
| `WEBHOOK_VERIFY_TOKEN` | El que definas y registres en Meta |

Crea la tabla en Supabase (SQL):

```sql
create table whatsapp_leads (
  id uuid primary key default gen_random_uuid(),
  phone text unique not null,
  name text,
  messages_count int default 0,
  conversation_history jsonb default '[]'::jsonb,
  status text default 'active',
  last_message_at timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
```

### 3. Correr el bridge

```bash
python3 wa_local_receiver.py
```

### 4. Exponer con HTTPS (Cloudflare Tunnel, ngrok, etc.)

Meta requiere HTTPS para webhooks. Ejemplo con Cloudflare:

```bash
cloudflared tunnel --url http://localhost:8765
```

Copia la URL `https://xxx.trycloudflare.com/webhook` y configúrala en Meta Developers.

### 5. (Opcional) Instalar como servicio systemd

```bash
cp whatsapp-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now whatsapp-bridge
```

### 6. (Opcional) Health check

```bash
cp whatsapp_health_check.sh ~/whatsapp_health_check.sh
chmod +x ~/whatsapp_health_check.sh
# Agregar a crontab cada 5 min:
*/5 * * * * /home/YOUR_USER/whatsapp_health_check.sh
```

## System prompt (vendedor)

El system prompt está embebido en `wa_local_receiver.py` en la variable `SYSTEM_PROMPT_MARIA`. Es el "cerebro" del vendedor. Modifícalo para tu producto:

```python
SYSTEM_PROMPT_MARIA = """
Eres M.A.R.I.A., vendedora de [TU TIENDA] por WhatsApp.
Producto: [TU PRODUCTO]
Precio: $[PRECIO]
Reglas: ...
"""
```

## Variables de entorno (recomendado en producción)

Para evitar tokens en el código, exporta antes de correr:

```bash
export WA_ACCESS_TOKEN="..."
export OPENROUTER_API_KEY="..."
export GROQ_API_KEY="..."
export LLM_TOKEN="..."
export SUPABASE_URL="https://xxx.supabase.co"
export SUPABASE_KEY="eyJ..."
python3 wa_local_receiver.py
```

## Estructura

```
whatsapp-bridge/
├── wa_local_receiver.py        # Bridge principal (1040 líneas)
├── wa_webhook.js               # Versión Node alternativa
├── whatsapp_health_check.sh     # Health check + auto-restart
├── whatsapp-bridge.service      # Systemd unit (user)
├── wa_cloud_api.json           # Config Meta Cloud API
├── wa_local_receiver_v4.py     # Backup v4 (legacy)
├── requirements.txt            # Dependencias Python
└── README.md
```

## Dependencias

Solo Python estándar + `requests` opcional. Si quieres usar la transcripción de Groq necesitas `curl` y la API key.

```txt
requests>=2.31.0
```

## License

MIT — hecho con cariño por Alejandro Sevilla Vélez para CCS724.
>>>>>>> d4e8f44 (feat: WhatsApp Cloud API Bridge v4)
