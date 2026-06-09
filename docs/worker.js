/**
 * Kengkongame AI Proxy Worker
 * Cloudflare Worker — วางโค้ดนี้ใน kengkongame-ai Worker แล้ว Deploy
 *
 * Secrets (ตั้งใน Settings → Variables and Secrets):
 *   GEMINI_API_KEY      — Google Gemini API key
 *   OPENAI_API_KEY      — OpenAI API key
 *   CLAUDE_API_KEY      — Anthropic Claude API key
 *   MEMBER_PASSWORD     — รหัสผ่านสมาชิก (default: Aa123456//++)
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });
    const url = new URL(request.url);
    if (url.pathname === '/api/analyze' && request.method === 'POST') {
      return handleAnalyze(request, env);
    }
    if (url.pathname === '/api/standings' && request.method === 'GET') {
      return handleStandings(url, env);
    }
    return new Response('Not Found', { status: 404 });
  }
};

async function handleAnalyze(request, env) {
  let body;
  try { body = await request.json(); }
  catch { return jsonError('Invalid request body', 400); }

  const { model, images = [], context = '', sources = '', password, verifyOnly, chatHistory = [] } = body;
  if (!model) return jsonError('Missing model', 400);

  // Password check for premium models
  const premiumModels = ['chatgpt', 'claude'];
  if (premiumModels.includes(model)) {
    const memberPass = env.MEMBER_PASSWORD || 'Aa123456//++';
    if (password !== memberPass) return jsonError('รหัสผ่านไม่ถูกต้อง', 401);
  }

  // verifyOnly — just checking password
  if (verifyOnly) {
    return jsonOk({ ok: true });
  }

  const isPremium = premiumModels.includes(model);
  const sysPrompt = buildSystemPrompt(context, sources, isPremium);

  try {
    let text;
    if (model === 'gemini') {
      text = await callGemini(images, sysPrompt, chatHistory, env.GEMINI_API_KEY);
    } else if (model === 'chatgpt') {
      text = await callChatGPT(images, sysPrompt, chatHistory, env.OPENAI_API_KEY);
    } else if (model === 'claude') {
      text = await callClaude(images, sysPrompt, chatHistory, env.CLAUDE_API_KEY);
    } else {
      return jsonError('Unknown model', 400);
    }
    return jsonOk({ text });
  } catch (e) {
    return jsonError(e.message || 'AI call failed', 500);
  }
}

// ── System Prompt ─────────────────────────────────────────
function buildSystemPrompt(context, sources, isPremium) {
  const base = `คุณเป็นผู้เชี่ยวชาญวิเคราะห์ราคาฟุตบอลและสถิติระดับมืออาชีพ ใช้ข้อมูลจากระบบ kengkongame.com

**แหล่งข้อมูลที่มีในการวิเคราะห์ครั้งนี้:** ${sources || 'รูปภาพที่อัปโหลด'}

**ข้อมูลแมตช์จากระบบ:**
${context || '(ไม่มีข้อมูลแมตช์จากระบบ)'}`;

  const geminiInstructions = `
เมื่อวิเคราะห์รูปภาพ:
1. อ่านราคาที่เห็นในทุกรูป (เจ้าบ้าน/เสมอ/เยือน, แฮนดิแคป, สูง/ต่ำ)
2. แปลงราคาเป็น implied probability %
3. เปรียบเทียบกับข้อมูลในระบบ
4. สรุปโอกาส % ของแต่ละผล
ตอบเป็นภาษาไทย ละเอียด เข้าใจง่าย`;

  const premiumInstructions = `
**การวิเคราะห์ระดับ Premium — ใช้ข้อมูลทั้งหมดที่มี:**

เมื่อวิเคราะห์ให้ครอบคลุมทุกมิติ:
1. **ราคาต่อรองจากรูปภาพ** — อ่านราคา HDP, 1X2, สูง/ต่ำ แปลงเป็น implied probability %
2. **การเปลี่ยนแปลงราคา** — ระบุว่าราคาขยับขึ้นหรือลง บ่งชี้อะไร
3. **สถิติทีม** — ฟอร์ม 5 นัดล่าสุด ผลงานบ้าน/เยือน
4. **Head-to-Head** — ผลประชันกัน 5 ครั้งล่าสุด
5. **อันดับตารางคะแนน** — ห่างกันกี่แต้ม แรงจูงใจในการชนะ
6. **ผู้เล่นสำคัญ** — ใครบาดเจ็บ/แบน/ติดโทษ กระทบแค่ไหน
7. **ความน่าเชื่อถือ** — confidence score และเหตุผล
8. **Value Bet** — ระบุว่าราคาไหน overvalue หรือ undervalue

**รายงานแหล่งข้อมูล (ต้องระบุท้ายรายงาน):**
สรุปว่าข้อมูลที่ใช้วิเคราะห์ดึงมาจากไหน เช่น:
- 📸 รูปภาพอัปโหลด: [ระบุว่าดูข้อมูลอะไรจากรูป]
- 📊 ข้อมูลระบบ kengkongame: [ระบุว่าใช้ข้อมูลแมตช์ใด สถิติใด]
- 🔢 ข้อมูลสถิติ: ฟอร์ม / H2H / อันดับ / ราคาต่อรอง

ตอบเป็นภาษาไทย ละเอียด เป็นระบบ ใช้หัวข้อชัดเจน`;

  return base + (isPremium ? premiumInstructions : geminiInstructions);
}

// ── Gemini ──────────────────────────────────────────────
async function callGemini(images, sysPrompt, chatHistory, apiKey) {
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

  // append chat history as text
  if (chatHistory.length) {
    parts.push({ text: '\n---\nประวัติการสนทนา:\n' + chatHistory.map(h=>`${h.role==='user'?'ผู้ใช้':'AI'}: ${h.content}`).join('\n') });
  }

  const userText = images.length
    ? `วิเคราะห์ราคาใน ${images.length} ภาพนี้รวมกัน พร้อมอธิบายเหตุผลและแสดง % ความน่าจะเป็นของแต่ละผล`
    : chatHistory[chatHistory.length - 1]?.content || 'กรุณาตอบคำถาม';
  parts.push({ text: userText });

  const res = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${apiKey}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contents: [{ role: 'user', parts }],
        systemInstruction: { parts: [{ text: sysPrompt }] },
        generationConfig: { maxOutputTokens: 3000 }
      })
    }
  );
  if (!res.ok) { const e = await res.json(); throw new Error(e.error?.message || `Gemini error ${res.status}`); }
  const data = await res.json();
  return data.candidates?.[0]?.content?.parts?.[0]?.text || 'ไม่ได้รับผลลัพธ์';
}

// ── ChatGPT-4 ────────────────────────────────────────────
async function callChatGPT(images, sysPrompt, chatHistory, apiKey) {
  if (!apiKey) throw new Error('OpenAI API key not configured');

  const messages = [{ role: 'system', content: sysPrompt }];

  // build image message
  if (images.length) {
    const content = [];
    images.forEach((img, i) => {
      if (i > 0) content.push({ type: 'text', text: `ภาพที่ ${i + 1}:` });
      const b64 = img.data.includes(',') ? img.data.split(',')[1] : img.data;
      content.push({ type: 'image_url', image_url: { url: `data:${img.mediaType || 'image/jpeg'};base64,${b64}` } });
    });
    content.push({ type: 'text', text: `วิเคราะห์ราคาใน ${images.length} ภาพนี้รวมกัน พร้อมอธิบายเหตุผล แสดง % ความน่าจะเป็น และสรุปแหล่งข้อมูล` });
    messages.push({ role: 'user', content });
  }

  // append chat history
  chatHistory.forEach(h => messages.push({ role: h.role, content: h.content }));

  // if chat mode (no images) — last user message already in history
  if (!images.length && chatHistory.length) {
    // already included
  }

  const res = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ model: 'gpt-4o', max_tokens: 3000, messages })
  });
  if (!res.ok) { const e = await res.json(); throw new Error(e.error?.message || `OpenAI error ${res.status}`); }
  const data = await res.json();
  return data.choices?.[0]?.message?.content || 'ไม่ได้รับผลลัพธ์';
}

// ── Claude ───────────────────────────────────────────────
async function callClaude(images, sysPrompt, chatHistory, apiKey) {
  if (!apiKey) throw new Error('Claude API key not configured');

  const msgs = [];

  if (images.length) {
    const content = [];
    images.forEach((img, i) => {
      if (i > 0) content.push({ type: 'text', text: `ภาพที่ ${i + 1}:` });
      const base64 = img.data.includes(',') ? img.data.split(',')[1] : img.data;
      content.push({ type: 'image', source: { type: 'base64', media_type: img.mediaType || 'image/jpeg', data: base64 } });
    });
    content.push({ type: 'text', text: `วิเคราะห์ราคาใน ${images.length} ภาพนี้รวมกัน พร้อมอธิบายเหตุผล แสดง % ความน่าจะเป็น และสรุปแหล่งข้อมูล` });
    msgs.push({ role: 'user', content });
  }

  // append chat history
  chatHistory.forEach(h => msgs.push({ role: h.role, content: h.content }));

  if (!msgs.length) return 'ไม่มีข้อความ';

  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: { 'x-api-key': apiKey, 'anthropic-version': '2023-06-01', 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: 'claude-opus-4-5', max_tokens: 3000, system: sysPrompt, messages: msgs })
  });
  if (!res.ok) { const e = await res.json(); throw new Error(e.error?.message || `Claude error ${res.status}`); }
  const data = await res.json();
  return data.content?.[0]?.text || 'ไม่ได้รับผลลัพธ์';
}

// ── Standings ────────────────────────────────────────────
async function handleStandings(url, env) {
  const leagueId = url.searchParams.get('league_id') || '39';
  const season = url.searchParams.get('season') || new Date().getFullYear().toString();
  const apiKey = env.RAPIDAPI_KEY;

  if (!apiKey) return jsonError('API key not configured', 500);

  try {
    const res = await fetch(
      `https://v3.football.api-sports.io/standings?league=${leagueId}&season=${season}`,
      { headers: { 'x-apisports-key': apiKey } }
    );
    if (!res.ok) throw new Error(`API error ${res.status}`);
    const data = await res.json();
    const standings = data.response?.[0]?.league?.standings?.[0] || [];

    const rows = standings.map(t => ({
      pos: t.rank,
      team: t.team.name,
      logo: t.team.logo,
      p: t.all.played,
      w: t.all.win,
      d: t.all.draw,
      l: t.all.lose,
      gf: t.all.goals.for,
      ga: t.all.goals.against,
      gd: t.goalsDiff,
      pts: t.points,
      form: (t.form||'').split('').slice(-5),
      description: t.description || ''
    }));

    return jsonOk({ standings: rows, season, league_id: leagueId });
  } catch (e) {
    return jsonError(e.message, 500);
  }
}

// ── Helpers ──────────────────────────────────────────────
function jsonOk(obj) {
  return new Response(JSON.stringify(obj), { headers: { ...CORS, 'Content-Type': 'application/json' } });
}
function jsonError(msg, status) {
  return new Response(JSON.stringify({ error: msg }), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });
}
