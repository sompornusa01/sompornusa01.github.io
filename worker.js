/**
 * Kengkongame AI Proxy Worker
 * Deploy: wrangler deploy
 *
 * Environment Variables (set via `wrangler secret put`):
 *   GEMINI_API_KEY      — Google Gemini API key
 *   OPENAI_API_KEY      — OpenAI API key (ChatGPT-4)
 *   CLAUDE_API_KEY      — Anthropic Claude API key
 *   MEMBER_PASSWORD     — Password for premium models (ChatGPT-4, Claude)
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

export default {
  async fetch(request, env) {
    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS });
    }

    const url = new URL(request.url);

    // Serve the static site (if routing all traffic through the worker)
    // For /api/analyze only
    if (url.pathname === '/api/analyze' && request.method === 'POST') {
      return handleAnalyze(request, env);
    }

    return new Response('Not Found', { status: 404 });
  }
};

async function handleAnalyze(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonError('Invalid request body', 400);
  }

  const { model, images, context, password, verifyOnly } = body;

  if (!model) return jsonError('Missing model', 400);

  // Premium model password check
  const premiumModels = ['chatgpt', 'claude'];
  if (premiumModels.includes(model)) {
    const memberPass = env.MEMBER_PASSWORD || 'Aa123456//++';
    if (password !== memberPass) {
      return jsonError('รหัสผ่านไม่ถูกต้อง', 401);
    }
  }

  // verifyOnly — just checking password, no AI call needed
  if (verifyOnly) {
    return new Response(JSON.stringify({ ok: true }), {
      headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
    });
  }

  if (!images || !images.length) return jsonError('Missing images', 400);

  try {
    let result;
    if (model === 'gemini') {
      result = await callGemini(images, context, env.GEMINI_API_KEY);
    } else if (model === 'chatgpt') {
      result = await callChatGPT(images, context, env.OPENAI_API_KEY);
    } else if (model === 'claude') {
      result = await callClaude(images, context, env.CLAUDE_API_KEY);
    } else {
      return jsonError('Unknown model', 400);
    }

    return new Response(JSON.stringify({ text: result }), {
      headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
    });
  } catch (e) {
    return jsonError(e.message || 'AI call failed', 500);
  }
}

// ── Gemini ──────────────────────────────────────────────
async function callGemini(images, context, apiKey) {
  if (!apiKey) throw new Error('Gemini API key not configured');

  const parts = [];
  images.forEach((img, i) => {
    if (i > 0) parts.push({ text: `ภาพที่ ${i + 1}:` });
    parts.push({
      inlineData: {
        mimeType: img.mediaType || 'image/jpeg',
        data: img.data.includes(',') ? img.data.split(',')[1] : img.data
      }
    });
  });
  parts.push({ text: buildPrompt(images.length, context) });

  const res = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${apiKey}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contents: [{ role: 'user', parts }],
        systemInstruction: { parts: [{ text: systemPrompt(context) }] },
        generationConfig: { maxOutputTokens: 2000 }
      })
    }
  );
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error?.message || `Gemini error ${res.status}`);
  }
  const data = await res.json();
  return data.candidates?.[0]?.content?.parts?.[0]?.text || 'ไม่ได้รับผลลัพธ์';
}

// ── ChatGPT-4 ────────────────────────────────────────────
async function callChatGPT(images, context, apiKey) {
  if (!apiKey) throw new Error('OpenAI API key not configured');

  const content = [];
  images.forEach((img, i) => {
    if (i > 0) content.push({ type: 'text', text: `ภาพที่ ${i + 1}:` });
    const b64 = img.data.includes(',') ? img.data.split(',')[1] : img.data;
    content.push({
      type: 'image_url',
      image_url: { url: `data:${img.mediaType || 'image/jpeg'};base64,${b64}` }
    });
  });
  content.push({ type: 'text', text: buildPrompt(images.length, context) });

  const res = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      model: 'gpt-4o',
      max_tokens: 2000,
      messages: [
        { role: 'system', content: systemPrompt(context) },
        { role: 'user', content }
      ]
    })
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error?.message || `OpenAI error ${res.status}`);
  }
  const data = await res.json();
  return data.choices?.[0]?.message?.content || 'ไม่ได้รับผลลัพธ์';
}

// ── Claude ───────────────────────────────────────────────
async function callClaude(images, context, apiKey) {
  if (!apiKey) throw new Error('Claude API key not configured');

  const content = [];
  images.forEach((img, i) => {
    if (i > 0) content.push({ type: 'text', text: `ภาพที่ ${i + 1}:` });
    const base64 = img.data.includes(',') ? img.data.split(',')[1] : img.data;
    content.push({
      type: 'image',
      source: { type: 'base64', media_type: img.mediaType || 'image/jpeg', data: base64 }
    });
  });
  content.push({ type: 'text', text: buildPrompt(images.length, context) });

  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      model: 'claude-opus-4-5',
      max_tokens: 2000,
      system: systemPrompt(context),
      messages: [{ role: 'user', content }]
    })
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error?.message || `Claude error ${res.status}`);
  }
  const data = await res.json();
  return data.content?.[0]?.text || 'ไม่ได้รับผลลัพธ์';
}

// ── Helpers ──────────────────────────────────────────────
function systemPrompt(context) {
  const top5 = context || '';
  return `คุณเป็นผู้เชี่ยวชาญวิเคราะห์ราคาฟุตบอลและสถิติ โดยใช้ข้อมูลจาก API-Football และ The Odds API
ข้อมูลแมตช์วันนี้จากระบบ (top 5):
${top5}

เมื่อได้รับรูปภาพที่มีราคาฟุตบอล (อาจมีหลายภาพ) ให้วิเคราะห์ทุกภาพรวมกัน:
1. อ่านราคาที่เห็นในทุกรูป (เจ้าบ้าน/เสมอ/เยือน, แฮนดิแคป, สูง/ต่ำ)
2. แปลงราคาเป็น implied probability %
3. เปรียบเทียบกับข้อมูลสถิติ ฟอร์ม H2H ในระบบ
4. อธิบายว่าทำไมราคาถึงออกมาแบบนี้
5. บอกว่าราคาไหน "value" หรือมีโอกาสที่ตลาดประเมินผิด
6. สรุปโอกาสเป็น % ของแต่ละผล (ชนะ/เสมอ/แพ้) ทุกคู่
ตอบเป็นภาษาไทย ละเอียด เข้าใจง่าย`;
}

function buildPrompt(imgCount, context) {
  return `วิเคราะห์ราคาใน ${imgCount} ภาพนี้รวมกัน พร้อมอธิบายเหตุผลและแสดง % ความน่าจะเป็นของแต่ละผล`;
}

function jsonError(msg, status) {
  return new Response(JSON.stringify({ error: msg }), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
  });
}
