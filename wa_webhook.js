const http = require('http');
const https = require('https');
const crypto = require('crypto');

// Config
const VERIFY_TOKEN = process.env.WA_VERIFY_TOKEN || 'your_webhook_verify_token';
const ACCESS_TOKEN = process.env.WA_ACCESS_TOKEN || 'WA_ACCESS_TOKEN_PLACEHOLDER';
const PHONE_NUMBER_ID = process.env.WA_PHONE_NUMBER_ID || '1074934415702869';
const OPENCLAW_PORT = 18789;

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  
  // GET - Webhook verification (Meta calls this to verify)
  if (req.method === 'GET' && url.pathname === '/webhook') {
    const mode = url.searchParams.get('hub.mode');
    const token = url.searchParams.get('hub.verify_token');
    const challenge = url.searchParams.get('hub.challenge');
    
    if (mode === 'subscribe' && token === VERIFY_TOKEN) {
      console.log(`[WEBHOOK] Verified! Challenge: ${challenge}`);
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end(challenge);
    } else {
      console.log(`[WEBHOOK] Verification failed. mode=${mode} token=${token}`);
      res.writeHead(403);
      res.end('Forbidden');
    }
    return;
  }
  
  // POST - Incoming messages from Meta
  if (req.method === 'POST' && url.pathname === '/webhook') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      console.log(`[WEBHOOK] Received: ${body.substring(0, 500)}...`);
      
      try {
        const parsed = JSON.parse(body);
        
        // Always respond 200 quickly to Meta
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        res.end('EVENT_RECEIVED');
        
        // Process notifications
        if (parsed.object === 'whatsapp_business_account') {
          for (const entry of parsed.entry || []) {
            for (const change of entry.changes || []) {
              const value = change.value;
              
              // Handle incoming messages
              if (value.messages) {
                for (const msg of value.messages) {
                  const from = msg.from;  // phone number
                  const msgId = msg.id;
                  const timestamp = msg.timestamp;
                  let text = '';
                  let mediaType = null;
                  let mediaId = null;
                  
                  if (msg.type === 'text' && msg.text) {
                    text = msg.text.body;
                  } else if (msg.type === 'interactive' && msg.interactive) {
                    if (msg.interactive.type === 'button_reply') {
                      text = msg.interactive.button_reply.title;
                    } else if (msg.interactive.type === 'list_reply') {
                      text = msg.interactive.list_reply.title;
                    }
                  } else if (msg.type === 'image') {
                    text = msg.image?.caption || '[Imagen]';
                    mediaType = 'image';
                    mediaId = msg.image?.id;
                  } else if (msg.type === 'video') {
                    text = msg.video?.caption || '[Video]';
                    mediaType = 'video';
                    mediaId = msg.video?.id;
                  } else if (msg.type === 'audio') {
                    text = '[Audio]';
                    mediaType = 'audio';
                    mediaId = msg.audio?.id;
                  } else if (msg.type === 'document') {
                    text = msg.document?.caption || '[Documento]';
                    mediaType = 'document';
                    mediaId = msg.document?.id;
                  } else if (msg.type === 'location') {
                    text = `[Ubicación] ${msg.location?.latitude}, ${msg.location?.longitude}`;
                  } else if (msg.type === 'contacts') {
                    text = '[Contactos]';
                  } else if (msg.type === 'sticker') {
                    text = '[Sticker]';
                    mediaType = 'sticker';
                    mediaId = msg.sticker?.id;
                  } else if (msg.type === 'reaction') {
                    text = `[Reacción] ${msg.reaction?.emoji}`;
                  } else {
                    text = `[${msg.type}]`;
                  }
                  
                  // Get contact name if available
                  let contactName = from;
                  if (value.contacts && value.contacts[0]) {
                    contactName = value.contacts[0].profile?.name || from;
                  }
                  
                  console.log(`[MSG] From: ${from} (${contactName}): ${text}`);
                  
                  // Forward to OpenClaw via API
                  forwardToOpenClaw(from, contactName, text, msg.type, msgId, timestamp);
                }
              }
              
              // Handle message status updates
              if (value.statuses) {
                for (const status of value.statuses) {
                  console.log(`[STATUS] ${status.recipient_id}: ${status.status} (${status.id})`);
                }
              }
            }
          }
        }
      } catch (e) {
        console.error(`[WEBHOOK] Parse error: ${e.message}`);
      }
    });
    return;
  }
  
  // GET - Send message endpoint (for OpenClaw to use)
  if (req.method === 'GET' && url.pathname === '/send') {
    const to = url.searchParams.get('to');
    const text = url.searchParams.get('text');
    
    if (!to || !text) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Missing to or text params' }));
      return;
    }
    
    sendWhatsAppMessage(to, text)
      .then(result => {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(result));
      })
      .catch(err => {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: err.message }));
      });
    return;
  }
  
  // POST - Send message endpoint  
  if (req.method === 'POST' && url.pathname === '/send') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const { to, text } = JSON.parse(body);
        if (!to || !text) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Missing to or text' }));
          return;
        }
        sendWhatsAppMessage(to, text)
          .then(result => {
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(result));
          })
          .catch(err => {
            res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: err.message }));
          });
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }
  
  // Health check
  if (req.method === 'GET' && url.pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', service: 'whatsapp-cloud-webhook', time: new Date().toISOString() }));
    return;
  }
  
  res.writeHead(404);
  res.end('Not found');
});

// Send WhatsApp message via Cloud API
async function sendWhatsAppMessage(to, text) {
  return new Promise((resolve, reject) => {
    const messageData = JSON.stringify({
      messaging_product: 'whatsapp',
      to: to,
      type: 'text',
      text: { body: text }
    });
    
    const options = {
      hostname: 'graph.facebook.com',
      path: `/v19.0/${PHONE_NUMBER_ID}/messages`,
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${ACCESS_TOKEN}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(messageData)
      }
    };
    
    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          if (result.messages) {
            console.log(`[SEND] Message sent to ${to}: ${result.messages[0].id}`);
            resolve(result);
          } else if (result.error) {
            console.error(`[SEND] Error: ${result.error.message}`);
            reject(new Error(result.error.message));
          } else {
            resolve(result);
          }
        } catch (e) {
          reject(new Error(`Parse error: ${data}`));
        }
      });
    });
    
    req.on('error', reject);
    req.write(messageData);
    req.end();
  });
}

// Forward incoming message to OpenClaw gateway
function forwardToOpenClaw(from, name, text, type, msgId, timestamp) {
  const payload = JSON.stringify({
    from: from,
    name: name,
    text: text,
    type: type,
    msgId: msgId,
    timestamp: timestamp,
    channel: 'whatsapp_cloud'
  });
  
  const options = {
    hostname: '127.0.0.1',
    port: OPENCLAW_PORT,
    path: '/api/v1/chat',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(payload)
    }
  };
  
  const req = http.request(options, (res) => {
    let data = '';
    res.on('data', chunk => data += chunk);
    res.on('end', () => {
      console.log(`[OPENCLAW] Forwarded: ${res.statusCode} ${data.substring(0, 200)}`);
    });
  });
  
  req.on('error', (e) => {
    console.error(`[OPENCLAW] Forward error: ${e.message}`);
  });
  
  req.write(payload);
  req.end();
}

const PORT = process.env.WA_WEBHOOK_PORT || 3200;
server.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 WhatsApp Cloud API Webhook running on port ${PORT}`);
  console.log(`   Webhook URL: http://localhost:${PORT}/webhook`);
  console.log(`   Send endpoint: http://localhost:${PORT}/send?to=573504017710&text=Hello`);
  console.log(`   Verify token: ${VERIFY_TOKEN}`);
});
