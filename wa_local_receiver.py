#!/usr/bin/env python3
"""
WhatsApp Cloud API Bridge v4
- Debounce de mensajes (5s)
- Horario de cortesía (7AM-10PM)
- Error shielding (nunca filtra errores al lead)
- Historial persistente en Supabase
- Teléfono normalizado (sin +57)
- Sin OpenClaw — LLM directo con prompt del vendedor
- Notificación a Alejandro cuando lead quiere cerrar
- Icebreaker de Meta detectado
"""

import json
import os
import threading
import time
import http.server
import urllib.request
import urllib.parse
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta

# ============================================================
# CONFIG
# ============================================================

# WhatsApp Cloud API
WA_ACCESS_TOKEN = "WA_ACCESS_TOKEN_PLACEHOLDER"
WA_PHONE_NUMBER_ID = "1074934415702869"
WA_API_URL = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/messages"

# Supabase
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_KEY = "***_REDACTED_JWT"

# Admin (Alejandro)
ADMIN_NUMBER = "573504017710"

# Partners (socios) — no tratar como leads
PARTNER_NUMBERS = ["573146101467"]  # Fofo
ADMIN_NUMBERS = [ADMIN_NUMBER] + PARTNER_NUMBERS

# Bridge config
PORT = int(os.environ.get("PORT", "3201"))

# Colombia timezone
BOGOTA_TZ = timezone(timedelta(hours=-5))

# Debounce: {phone: {last_msg_time, timer, accumulated_text}}
debounce_map = {}
DEBOUNCE_SECONDS = 5

# Rate limit: {phone: [timestamps]}
rate_limit_map = {}
MAX_MESSAGES_PER_30MIN = 10  # Allow natural conversation flow
RATE_LIMIT_WINDOW = 1800  # 30 min

# After-hours response
AFTER_HOURS_MSG = ""  # Disabled: M.A.R.I.A. responds 24/7, no after-hours auto-reply

# iPhone product photos (Supabase permanent URLs)
IPHONE_PHOTOS = [
    "https://YOUR_PROJECT.supabase.co/storage/v1/object/public/product-images/iphone14promax/foto1.jpg",
    "https://YOUR_PROJECT.supabase.co/storage/v1/object/public/product-images/iphone14promax/foto2.jpg",
    "https://YOUR_PROJECT.supabase.co/storage/v1/object/public/product-images/iphone14promax/foto3.jpg",
]

# Photo request patterns
PHOTO_REQUEST_PATTERNS = [
    "foto", "fotos", "ver foto", "ver fotos", "muestrame", "muéstrame",
    "ver el telefono", "ver el teléfono", "ver el cel", "ver el iphone",
    "como se ve", "cómo se ve", "imagen", "imágenes", "imagenes",
    "quiero ver", "pasame fotos", "pásame fotos", "mandame fotos",
    "envia fotos", "envía fotos", "foto del iphone", "fotos del iphone",
    "verlo", "verlo en foto", "como esta", "cómo está",
    "foto del equipo", "fotos del equipo", "una foto", "una fotico",
]

# Icebreaker patterns from Meta ads (case-insensitive match)
# These are auto-generated messages that Meta sends when a lead clicks "Send Message" on an ad
ICEBREAKER_PATTERNS = [
    "hola! quiero más información",
    "hola! quiero mas informacion",
    "quiero más información",
    "quiero mas informacion",
    "me interesa el",
    "me interesa esta",
    "más información sobre",
    "mas informacion sobre",
    "me gustaría saber más sobre",
    "me gustaria saber mas sobre",
]

# ============================================================
# SYSTEM PROMPT — Vendedor iPhone
# ============================================================

SYSTEM_PROMPT = """Eres María, vendedora de CCS724 por WhatsApp. Sos colombiana, natural, honesta, directa.

PRODUCTO ÚNICO: iPhone 14 Pro Max 256GB — $1.650.000 COP (precio fijo, NO se negocia, NO cambia jamás)
- Pantalla NUEVA, batería NUEVA al 100%, IMEI liberado de por vida
- Desbloqueo con clave numérica (NO tiene Face ID)
- Solo equipo, sin accesorios
- Colores: negro y morado oscuro
- Apple muestra "pieza desconocida" en ajustes — es normal, piezas cambiadas
- Entrega contraentrega en Bogotá: puede ser domicilio o recoger en el local
- Local físico: Calle 13 #15-67 Local D65 D66, Bogotá
- Efectivo o Nequi

REGLAS IRROMPIBLES:
1. PRECIO SIEMPRE $1.650.000 — nunca $1.680.000, nunca domicilio extra, nunca descuento
2. Solo iPhone 14 Pro Max — no mencionar otros productos, tiendas, catálogos
3. Mensajes CORTOS — 2-3 líneas máximo
4. NUNCA decir que es bot, IA, script, guion, ni nombrar a Alejandro internamente
5. No reiniciar conversación — usar contexto, no repetir pitch si ya lo sabe
6. Si el lead quiere comprar → pedir nombre completo, dirección en Bogotá o si prefiere recoger en el local
7. HONESTO siempre — si no tiene Face ID, decilo; si piezas cambiadas, decilo
8. "Vos" informal, no "usted". Emojis moderados (😊🙌👍)

FLUJO:
- Primer contacto (lead de anuncio Meta): "¡Hola! 😊 Te cuento rápido del iPhone 14 Pro Max: $1.650.000, pantalla y batería nuevas, IMEI liberado. Entrega en Bogotá. ¿Te interesa?"
- Primer contacto (lead espontáneo): mismo pitch corto, preguntar si le interesa
- Si pregunta Face ID: "Te soy honesta: no tiene Face ID. Se desbloquea con clave. Por eso el precio está tan bueno"
- Si pregunta colores: "Negro y morado oscuro 📱"
- Si pregunta pago: "Efectivo o Nequi, como te quede más fácil"
- Si pregunta dónde recoger: "Calle 13 #15-67 Local D65 D66, Bogotá. O te lo llevo a domicilio contraentrega 😊"
- Si quiere comprar: "¡Dale! 🎉 Pasame nombre completo, y si querés recoger en el local (Calle 13 #15-67) o domicilio en Bogotá"
- Si pregunta otra cosa: redirigir al iPhone con naturalidad

NUNCA enviar: errores técnicos, menús genéricos de tienda, "Bienvenido a CCS724", listas de tiendas, ni nada que no sea sobre el iPhone 14 Pro Max."""


# ============================================================
# PHONE NORMALIZATION
# ============================================================

def normalize_phone(phone: str) -> str:
    """Normalize phone: strip +, spaces, dashes. Always return without country code prefix if 57."""
    p = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
    # If starts with 57 and is 12+ digits, keep as is
    if p.startswith("57") and len(p) >= 12:
        return p
    # If starts with 3 and is 10 digits (local Colombian mobile), add 57
    if p.startswith("3") and len(p) == 10:
        return "57" + p
    return p


# ============================================================
# AUDIO TRANSCRIPTION (STT via OpenRouter/Whisper)
# ============================================================

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "sk-or-v1-PLACEHOLDER")
STT_MODEL = "openai/whisper-1"  # Available via OpenRouter


def download_whatsapp_media(media_id: str) -> bytes | None:
    """Download media file from WhatsApp Cloud API."""
    try:
        # Step 1: Get media URL
        url_req = urllib.request.Request(
            f"https://graph.facebook.com/v19.0/{media_id}",
            headers={"Authorization": f"Bearer {WA_ACCESS_TOKEN}"}
        )
        with urllib.request.urlopen(url_req, timeout=10) as resp:
            media_info = json.loads(resp.read().decode())
            media_url = media_info.get("url", "")
        
        if not media_url:
            print(f"[STT] No media URL for {media_id}")
            return None
        
        # Step 2: Download the actual file
        dl_req = urllib.request.Request(
            media_url,
            headers={"Authorization": f"Bearer {WA_ACCESS_TOKEN}"}
        )
        with urllib.request.urlopen(dl_req, timeout=30) as resp:
            audio_data = resp.read()
        
        print(f"[STT] Downloaded audio {media_id}: {len(audio_data)} bytes")
        return audio_data
        
    except Exception as e:
        print(f"[STT] Download failed for {media_id}: {e}")
        return None


def transcribe_audio(media_id: str) -> str | None:
    """Download audio from WhatsApp and transcribe with Whisper via OpenRouter."""
    try:
        # Download audio
        audio_data = download_whatsapp_media(media_id)
        if not audio_data:
            return None
        
        # Save to temp file (Whisper needs a file)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = tmp.name
        
        # Try OpenRouter Whisper first
        try:
            boundary = "----FormBoundary7MA4YWxkTrZu0gW"
            with open(tmp_path, "rb") as f:
                audio_content = f.read()
            
            body = (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"file\"; filename=\"audio.ogg\"\r\n"
                f"Content-Type: audio/ogg\r\n\r\n"
            ).encode() + audio_content + (
                f"\r\n--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"model\"\r\n\r\n"
                f"{STT_MODEL}\r\n"
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"language\"\r\n\r\n"
                f"es\r\n"
                f"--{boundary}--\r\n"
            ).encode()
            
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/audio/transcriptions",
                data=body,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                text = result.get("text", "").strip()
                if text:
                    print(f"[STT] OpenRouter transcription: {text[:100]}")
                    return text
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()[:300]
            print(f"[STT] OpenRouter failed ({e.code}): {err_body}")
        except Exception as e:
            print(f"[STT] OpenRouter error: {e}")
        
        # Fallback: try Groq Whisper (free, fast)
        # If OpenRouter doesn't support audio, we try direct OpenAI-compatible endpoint
        try:
            import subprocess
            result = subprocess.run(
                ["curl", "-s", "https://api.groq.com/openai/v1/audio/transcriptions",
                 "-H", f"Authorization: Bearer {os.environ.get('GROQ_API_KEY', '')}",
                 "-F", f"file=@{tmp_path}",
                 "-F", "model=whisper-large-v3",
                 "-F", "language=es"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                groq_result = json.loads(result.stdout)
                text = groq_result.get("text", "").strip()
                if text:
                    print(f"[STT] Groq transcription: {text[:100]}")
                    return text
        except Exception as e:
            print(f"[STT] Groq fallback failed: {e}")
        
        return None
        
    except Exception as e:
        print(f"[STT] Transcription failed: {e}")
        return None
    finally:
        # Clean up temp file
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except:
            pass


# ============================================================
# RATE LIMITING
# ============================================================

def can_send_message(phone: str) -> bool:
    """Check if we can send a message to this phone (max 2 per 30 min)."""
    now = time.time()
    if phone not in rate_limit_map:
        rate_limit_map[phone] = []
    # Clean old entries
    rate_limit_map[phone] = [t for t in rate_limit_map[phone] if now - t < RATE_LIMIT_WINDOW]
    return len(rate_limit_map[phone]) < MAX_MESSAGES_PER_30MIN


def record_message_sent(phone: str):
    """Record that a message was sent to this phone."""
    now = time.time()
    if phone not in rate_limit_map:
        rate_limit_map[phone] = []
    rate_limit_map[phone].append(now)


# ============================================================
# BUSINESS HOURS
# ============================================================

def is_business_hours() -> bool:
    """Check if current time is within business hours (7AM-10PM Bogotá)."""
    now_bogota = datetime.now(BOGOTA_TZ)
    return 7 <= now_bogota.hour < 22


# ============================================================
# SUPABASE — Lead Tracking + Conversation History
# ============================================================

def supabase_request(method: str, path: str, data: dict = None, query: str = "") -> dict:
    """Make a request to Supabase REST API."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if query:
        url += f"?{query}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if data is None:
        req = urllib.request.Request(url, headers=headers, method=method)
    else:
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method=method)

    if method in ("PATCH", "DELETE", "POST"):
        headers["Prefer"] = "return=representation"

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        print(f"[DB] HTTP {e.code}: {e.read().decode()[:200]}")
        return {}
    except Exception as e:
        print(f"[DB] Error: {e}")
        return {}


def upsert_lead(phone: str, name: str, text: str):
    """Create or update lead in Supabase."""
    phone = normalize_phone(phone)
    now = datetime.now(BOGOTA_TZ).isoformat()

    # Check existing
    existing = supabase_request("GET", "whatsapp_leads", query=f"select=id,messages_count&phone=eq.{phone}")

    if existing and len(existing) > 0:
        lead_id = existing[0]["id"]
        new_count = (existing[0].get("messages_count") or 0) + 1
        supabase_request("PATCH", "whatsapp_leads", data={
            "last_message": text[:500],
            "last_message_at": now,
            "messages_count": new_count,
            "updated_at": now,
        }, query=f"id=eq.{lead_id}")
        print(f"[DB] Lead updated: {name} ({phone}) - msg #{new_count}")
    else:
        supabase_request("POST", "whatsapp_leads", data={
            "phone": phone,
            "name": name,
            "last_message": text[:500],
            "last_message_at": now,
            "status": "new",
            "source": "whatsapp",
            "messages_count": 1,
        })
        print(f"[DB] ✨ New lead: {name} ({phone})")


def get_conversation_history(phone: str) -> list:
    """Get conversation history from Supabase (persistent, not RAM)."""
    phone = normalize_phone(phone)
    result = supabase_request("GET", "whatsapp_leads", query=f"select=conversation_history&phone=eq.{phone}&limit=1")
    if result and len(result) > 0:
        history = result[0].get("conversation_history")
        if history and isinstance(history, list):
            return history[-20:]  # Last 20 messages
    return []


def save_conversation_history(phone: str, history: list):
    """Save conversation history to Supabase."""
    phone = normalize_phone(phone)
    # Keep last 50 messages max
    history = history[-50:]
    supabase_request("PATCH", "whatsapp_leads", data={
        "conversation_history": history,
    }, query=f"phone=eq.{phone}")


def save_message_to_history(phone: str, role: str, content: str):
    """Add a single message to the conversation history in Supabase."""
    phone = normalize_phone(phone)
    history = get_conversation_history(phone)
    history.append({
        "role": role,
        "content": content,
        "ts": datetime.now(BOGOTA_TZ).isoformat(),
    })
    save_conversation_history(phone, history)


def get_lead_context(phone: str) -> dict:
    """Get lead info from Supabase."""
    phone = normalize_phone(phone)
    result = supabase_request("GET", "whatsapp_leads",
        query=f"select=name,status,interest,messages_count,created_at,last_message_at&phone=eq.{phone}&limit=1")
    if result and len(result) > 0:
        return result[0]
    return {}


# ============================================================
# WHATSAPP API
# ============================================================

def send_whatsapp_image(to: str, image_url: str, caption: str = "") -> dict:
    """Send an image message via WhatsApp Cloud API."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"link": image_url}
    }
    if caption:
        payload["image"]["caption"] = caption[:1024]  # WhatsApp caption limit

    data = json.dumps(payload).encode()

    req = urllib.request.Request(
        WA_API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            msg_id = result.get("messages", [{}])[0].get("id", "?")
            print(f"[SEND-IMG] ✓ {to}: {msg_id}")
            record_message_sent(normalize_phone(to))
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(f"[SEND-IMG] ✗ HTTP {e.code}: {body}")
        return {"error": body}
    except Exception as e:
        print(f"[SEND-IMG] ✗ Error: {e}")
        return {"error": str(e)}


def send_whatsapp_message(to: str, text: str) -> dict:
    """Send a text message via WhatsApp Cloud API."""
    data = json.dumps({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }).encode()

    req = urllib.request.Request(
        WA_API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            msg_id = result.get("messages", [{}])[0].get("id", "?")
            print(f"[SEND] ✓ {to}: {msg_id}")
            record_message_sent(normalize_phone(to))
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(f"[SEND] ✗ HTTP {e.code}: {body}")
        return {"error": body}
    except Exception as e:
        print(f"[SEND] ✗ Error: {e}")
        return {"error": str(e)}


def mark_as_read(msg_id: str):
    """Mark a message as read."""
    data = json.dumps({"messaging_product": "whatsapp", "status": "read"}).encode()
    try:
        req = urllib.request.Request(
            f"https://graph.facebook.com/v19.0/{msg_id}",
            data=data,
            headers={
                "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        urllib.request.urlopen(req, timeout=5)
    except:
        pass


# ============================================================
# ICEBREAKER DETECTION
# ============================================================

def is_meta_icebreaker(text: str) -> bool:
    """Detect if message is a Meta auto-generated icebreaker."""
    text_lower = text.lower().strip()
    return any(pattern in text_lower for pattern in ICEBREAKER_PATTERNS)


def is_photo_request(text: str) -> bool:
    """Detect if lead is asking for photos of the iPhone."""
    text_lower = text.lower().strip()
    # Must be short message (photo requests are usually brief)
    if len(text_lower) > 80:
        return False
    return any(pattern in text_lower for pattern in PHOTO_REQUEST_PATTERNS)


# ============================================================
# LLM — OpenClaw API (fast, always-on service)
# ============================================================

LLM_URL = "http://127.0.0.1:18789/v1/chat/completions"
LLM_TOKEN = "LLM_TOKEN_PLACEHOLDER"
LLM_MODEL = "ollama/glm-5.1:cloud"

def call_llm(phone: str, contact_name: str, text: str) -> str:
    """Call OpenClaw API with system prompt and conversation history."""
    phone = normalize_phone(phone)
    print(f"[LLM] Processing message from {contact_name} ({phone}): {text[:100]}")

    # Build messages with conversation history
    messages = []

    # System prompt with lead context
    lead_ctx = get_lead_context(phone)
    context_addon = ""
    if lead_ctx:
        name = lead_ctx.get('name', 'Desconocido')
        msgs = lead_ctx.get('messages_count', 1)
        status = lead_ctx.get('status', 'new')
        context_addon = f"\n\nContexto del lead: Nombre {name}, teléfono {phone}, mensajes previos {msgs}, estado {status}."

    messages.append({"role": "system", "content": SYSTEM_PROMPT + context_addon})

    # Conversation history from Supabase (keep last 10 messages for context)
    history = get_conversation_history(phone)
    for msg in history[-10:]:
        role = msg.get("role", "user")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": msg.get("content", "")})

    # Current message
    messages.append({"role": "user", "content": text})

    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 300,
    }).encode()

    req = urllib.request.Request(
        LLM_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {LLM_TOKEN}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    # Retry with backoff
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                # SANITIZE: never send internal errors or prompt leaks
                if not content or len(content.strip()) < 2:
                    return ""

                # Block any error-like responses
                blocked_patterns = ["no response", "error", "openclaw", "alejandro", "guion", "script", "traceback"]
                content_lower = content.lower()
                for pattern in blocked_patterns:
                    if pattern in content_lower and len(content) < 80:
                        print(f"[LLM] Blocked suspicious response: {content[:80]}")
                        return ""

                # Truncate to WhatsApp-friendly length
                if len(content) > 500:
                    trunc = content[:500]
                    last_end = max(trunc.rfind('.'), trunc.rfind('!'), trunc.rfind('?'))
                    if last_end > 100:
                        content = content[:last_end + 1]

                print(f"[LLM] Response: {content[:100]}...")
                return content.strip()

        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            print(f"[LLM] HTTP {e.code} (attempt {attempt+1}/3): {body}")
            if attempt < 2:
                time.sleep(2 ** attempt)
            continue
        except Exception as e:
            print(f"[LLM] Error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
            continue

    return ""  # ALWAYS return empty on failure — never expose errors to lead


# ============================================================
# CLOSE SALE DETECTION
# ============================================================

def detect_close_intent(text: str) -> bool:
    """Detect if the lead wants to close/buy."""
    close_keywords = ["quiero comprar", "me lo llevo", "cerramos", "dame los datos",
                       "cuándo me lo llevan", "quiero el iphone", "ya quiero",
                       "me interesa cerrar", "coordinemos", "ya pago",
                       "dame la dirección", "pasame los datos"]
    text_lower = text.lower()
    return any(kw in text_lower for kw in close_keywords)


def notify_admin_close(phone: str, contact_name: str):
    """Notify via Supabase when a lead wants to close — Hermes picks it up."""
    print(f"[ALERT] 🎉 LEAD QUIERE CERRAR: {contact_name} ({phone})")
    # Update lead status to 'closing' so Hermes can pick it up
    phone_norm = normalize_phone(phone)
    try:
        supabase_request("PATCH", "whatsapp_leads", data={
            "status": "closing",
            "interest": "iphone_buying",
        }, query=f"phone=eq.{phone_norm}")
        print(f"[DB] Lead {phone_norm} marked as 'closing'")
    except Exception as e:
        print(f"[DB] Failed to mark lead: {e}")


# ============================================================
# DEBOUNCE + MESSAGE PROCESSING
# ============================================================

debounce_lock = threading.Lock()


def process_after_debounce(phone: str, contact_name: str, text: str, msg_type: str, msg_id: str):
    """Process message after debounce window."""
    phone = normalize_phone(phone)

    # Business hours check disabled — M.A.R.I.A. responds 24/7 to leads from ads
    # After-hours messages are still saved for context
    # save_message_to_history and upsert_lead happen later in the flow

    # Rate limiting
    if not can_send_message(phone):
        print(f"[RATE] Skipping {phone}: rate limited")
        # Still save incoming message
        save_message_to_history(phone, "user", text)
        upsert_lead(phone, contact_name, text)
        return

    # Save user message to Supabase
    save_message_to_history(phone, "user", text)
    upsert_lead(phone, contact_name, text)

    # Mark as read
    if msg_id:
        mark_as_read(msg_id)

    # Skip non-meaningful messages
    if not text or text.startswith("[") or msg_type in ("reaction", "sticker"):
        print(f"[SKIP] No reply needed for type: {msg_type}")
        return

    # Partners/admin — don't treat as leads, still process but mark differently
    is_partner = phone in PARTNER_NUMBERS
    is_admin = phone == ADMIN_NUMBER
    if is_partner:
        print(f"[PARTNER] Fofo message — relaying to OpenClaw context")
        # Don't send sales pitch to partners, just acknowledge and relay
        if can_send_message(phone):
            send_whatsapp_message(phone, "Recibido 👍 Te responde Alejandro en un momento.")
        return
    if is_admin:
        print(f"[ADMIN] Alejandro message — processing normally for testing")

    # Detect photo request — send iPhone photos automatically
    if is_photo_request(text):
        print(f"[PHOTO] Lead {contact_name} ({phone}) pidió fotos del iPhone")
        # Send text first
        if can_send_message(phone):
            send_whatsapp_message(phone, "Te paso unas fotos 😊")
            time.sleep(1)
        # Send the 3 iPhone photos
        for i, photo_url in enumerate(IPHONE_PHOTOS):
            if can_send_message(phone):
                caption = ""
                if i == 0:
                    caption = "iPhone 14 Pro Max 256GB"
                send_whatsapp_image(phone, photo_url, caption)
                time.sleep(1.5)
        # Send follow-up text
        if can_send_message(phone):
            send_whatsapp_message(phone, "Está hermoso 📱 Pantalla y batería nuevas, IMEI libre. ¿Te interesa?")
        # Still save to history
        save_message_to_history(phone, "assistant", "[Envió fotos del iPhone 14 Pro Max]")
        return  # Don't also send LLM response — photos are the response

    # Detect Meta icebreaker — still go through LLM but with ad context
    if is_meta_icebreaker(text):
        # Add context that this came from a Meta ad click
        text = f"[Lead llegó por anuncio de Meta — primer contacto] {text}"
        print(f"[ICEBREAKER] Meta ad click from {contact_name} ({phone})")

    # Detect close intent
    if detect_close_intent(text):
        notify_admin_close(phone, contact_name)

    # Call LLM (always)
    response = call_llm(phone, contact_name, text)

    if response:
        save_message_to_history(phone, "assistant", response)

        # Split if too long for WhatsApp (1024 char limit)
        if len(response) > 1000:
            chunks = []
            while len(response) > 1000:
                split_at = response.rfind("\n\n", 0, 1000)
                if split_at == -1:
                    split_at = response.rfind("\n", 0, 1000)
                if split_at == -1:
                    split_at = 1000
                chunks.append(response[:split_at].strip())
                response = response[split_at:].strip()
            chunks.append(response.strip())
        else:
            chunks = [response.strip()]

        for chunk in chunks:
            if can_send_message(normalize_phone(phone)):
                send_whatsapp_message(phone, chunk)
                time.sleep(0.5)
    else:
        # ERROR SHIELDING: Never send errors to lead
        # If LLM fails, don't send any fallback message — just log it
        print(f"[SHIELD] LLM failed for {phone}, NOT sending fallback (M.A.R.I.A. handles retries)")
        last_history = get_conversation_history(phone)
        # Only send fallback if we haven't sent one recently
        # Removed fallback message — M.A.R.I.A. should handle all conversations without generic auto-replies


def handle_incoming_message(phone: str, contact_name: str, text: str, msg_type: str, msg_id: str, timestamp: str):
    """Handle incoming message with debounce logic."""
    phone_norm = normalize_phone(phone)

    with debounce_lock:
        now = time.time()
        if phone_norm in debounce_map:
            # Cancel existing timer
            if debounce_map[phone_norm].get("timer"):
                debounce_map[phone_norm]["timer"].cancel()
            debounce_map[phone_norm]["accumulated"] += "\n" + text
        else:
            debounce_map[phone_norm] = {"accumulated": text}

        # Set new timer
        timer = threading.Timer(
            DEBOUNCE_SECONDS,
            _fire_debounced_message,
            args=(phone_norm, contact_name, msg_type, msg_id)
        )
        debounce_map[phone_norm]["timer"] = timer
        debounce_map[phone_norm]["last_msg_time"] = now
        timer.start()


def _fire_debounced_message(phone: str, contact_name: str, msg_type: str, msg_id: str):
    """Called after debounce window expires — process the accumulated message."""
    with debounce_lock:
        entry = debounce_map.pop(phone, None)

    if not entry:
        return

    accumulated_text = entry["accumulated"]
    print(f"[DEBOUNCE] Processing for {phone}: {accumulated_text[:100]}")

    # Process in background thread
    threading.Thread(
        target=process_after_debounce,
        args=(phone, contact_name, accumulated_text, msg_type, msg_id),
        daemon=True
    ).start()


# ============================================================
# WEBHOOK HANDLER
# ============================================================

class WebhookHandler(BaseHTTPRequestHandler):
    """Handle incoming webhooks from Meta via Cloudflare tunnel."""

    def do_GET(self):
        """Webhook verification from Meta."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        mode = params.get("hub.mode", [""])[0]
        token = params.get("hub.verify_token", [""])[0]
        challenge = params.get("hub.challenge", [""])[0]

        if mode == "subscribe" and token == "your_webhook_verify_token":
            print(f"[WEBHOOK] ✓ Verified by Meta")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(challenge.encode())
        else:
            print(f"[WEBHOOK] ✗ Verification failed")
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")

    def do_POST(self):
        """Handle incoming messages from Meta or API requests."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # /send-image endpoint — send image via WhatsApp
        if path == "/send-image":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            try:
                data = json.loads(body)
            except:
                self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
                return

            phone = data.get("phone", "")
            image_url = data.get("image_url", "")
            caption = data.get("caption", "")

            if not phone or not image_url:
                self.wfile.write(json.dumps({"error": "Missing phone or image_url"}).encode())
                return

            phone = normalize_phone(phone)
            result = send_whatsapp_image(phone, image_url, caption)
            self.wfile.write(json.dumps(result).encode())
            return

        # /send-message endpoint — send text via WhatsApp
        if path == "/send-message":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            try:
                data = json.loads(body)
            except:
                self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
                return

            phone = data.get("phone", "")
            text = data.get("text", "")

            if not phone or not text:
                self.wfile.write(json.dumps({"error": "Missing phone or text"}).encode())
                return

            phone = normalize_phone(phone)
            result = send_whatsapp_message(phone, text)
            self.wfile.write(json.dumps(result).encode())
            return

        # Default: Meta webhook
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()

        # Always respond 200 quickly to Meta
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"EVENT_RECEIVED")

        try:
            data = json.loads(body)
        except:
            print(f"[WEBHOOK] Could not parse body")
            return

        # Process in background
        threading.Thread(target=handle_webhook_data, args=(data,), daemon=True).start()

    def log_message(self, format, *args):
        pass


def handle_webhook_data(data: dict):
    """Process webhook data from Meta."""
    if data.get("object") != "whatsapp_business_account":
        return

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # Incoming messages
            if value.get("messages"):
                for msg in value["messages"]:
                    from_number = msg.get("from", "")
                    msg_id = msg.get("id", "")
                    timestamp = msg.get("timestamp", "")
                    msg_type = msg.get("type", "text")

                    # Extract text
                    text = ""
                    if msg_type == "text" and msg.get("text"):
                        text = msg["text"].get("body", "")
                    elif msg_type == "interactive" and msg.get("interactive"):
                        interactive = msg["interactive"]
                        if interactive.get("type") == "button_reply":
                            text = interactive["button_reply"].get("title", "")
                        elif interactive.get("type") == "list_reply":
                            text = interactive["list_reply"].get("title", "")
                        else:
                            text = json.dumps(interactive)
                    elif msg_type == "image":
                        text = msg.get("image", {}).get("caption", "[Imagen]")
                    elif msg_type == "audio":
                        audio_id = msg.get("audio", {}).get("id", "")
                        if audio_id:
                            transcribed = transcribe_audio(audio_id)
                            text = transcribed if transcribed else "[Audio]"
                            print(f"[STT] Transcribed audio {audio_id}: {text[:100]}")
                        else:
                            text = "[Audio]"
                    elif msg_type == "document":
                        text = msg.get("document", {}).get("caption", "[Documento]")
                    elif msg_type == "location":
                        loc = msg.get("location", {})
                        text = f"[Ubicación] {loc.get('latitude')}, {loc.get('longitude')}"
                    elif msg_type == "reaction":
                        text = f"[Reacción]"
                    elif msg_type == "sticker":
                        text = "[Sticker]"
                    elif msg_type == "video":
                        text = msg.get("video", {}).get("caption", "[Video]")
                    else:
                        text = f"[{msg_type}]"

                    # Get contact name
                    contact_name = from_number
                    contacts = value.get("contacts", [])
                    if contacts:
                        contact_name = contacts[0].get("profile", {}).get("name", from_number)

                    # Log admin/partner messages but don't skip (for testing)
                    if normalize_phone(from_number) in ADMIN_NUMBERS:
                        role = "Alejandro" if normalize_phone(from_number) == ADMIN_NUMBER else "Partner"
                        print(f"[{role.upper()}] Message from {contact_name} ({from_number}) (not skipped for testing)")

                    print(f"\n{'='*60}")
                    print(f"[MSG] From: {from_number} ({contact_name})")
                    print(f"[MSG] Text: {text[:200]}")
                    print(f"[MSG] Type: {msg_type}")
                    print(f"{'='*60}")

                    # Handle with debounce
                    handle_incoming_message(from_number, contact_name, text, msg_type, msg_id, timestamp)

            # Status updates
            if value.get("statuses"):
                for status in value["statuses"]:
                    s = status.get("status", "")
                    if s in ("sent", "delivered", "read"):
                        pass  # Quiet
                    else:
                        print(f"[STATUS] {status.get('recipient_id')}: {s}")


# ============================================================
# MAIN
# ============================================================

def run_server():
    # Allow port reuse so we don't conflict with OpenClaw auto-restart
    import socketserver
    socketserver.TCPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    print(f"🚀 WhatsApp Bridge v4 running on port {PORT}")
    print(f"   Webhook: http://localhost:{PORT}/webhook")
    print(f"   LLM: {LLM_URL} ({LLM_MODEL})")
    print(f"   Admin: {ADMIN_NUMBER}")
    print(f"   Debounce: {DEBOUNCE_SECONDS}s")
    print(f"   Business hours: 7AM-10PM Bogotá")
    print(f"   Rate limit: {MAX_MESSAGES_PER_30MIN} msg/{RATE_LIMIT_WINDOW}s")
    print(f"   Error shielding: ON")
    print(f"   Conversation: Supabase (persistent)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped")


if __name__ == "__main__":
    run_server()