// Mensajería extra del gateway: respuestas automáticas, repetición de mensajes programados, encuestas y envío a varios.
// Se carga desde server.js con createExtras(ctx). Todo corre acá (no en la ventana) para que funcione con la app cerrada.
// Los archivos de datos se escriben de forma atómica (.tmp + rename) y no usan nada que no exista en Android.
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';

// ─── REPETICIÓN DE MENSAJES PROGRAMADOS ──────────────────────────────────────
// repeat = { type: 'daily'|'weekly'|'monthly'|'yearly', days?: [0..6] (0 = domingo), every?: n }
export const REPEAT_TYPES = ['daily', 'weekly', 'monthly', 'yearly'];

export function normalizeRepeat(r) {
    if (!r || typeof r !== 'object' || !REPEAT_TYPES.includes(r.type)) return null;
    const out = { type: r.type, every: Math.max(1, Math.min(365, parseInt(r.every, 10) || 1)) };
    if (r.type === 'weekly') {
        const days = [...new Set((Array.isArray(r.days) ? r.days : []).map(Number).filter(d => d >= 0 && d <= 6))].sort();
        out.days = days;   // vacío = el mismo día de la semana que la fecha de inicio
    }
    return out;
}

function daysInMonth(y, m) { return new Date(y, m + 1, 0).getDate(); }

// Próxima fecha (epoch s) estrictamente posterior a "after", conservando la hora del día de "at". null si no hay repetición válida.
export function nextRepeatAt(at, repeat, after) {
    const r = normalizeRepeat(repeat);
    if (!r) return null;
    const base = new Date(at * 1000);
    const h = base.getHours(), mi = base.getMinutes(), s = base.getSeconds();
    const limit = Math.max(at, after);
    let cand;
    if (r.type === 'daily') {
        cand = new Date(base);
        do { cand = new Date(cand.getFullYear(), cand.getMonth(), cand.getDate() + r.every, h, mi, s); } while (cand / 1000 <= limit);
    } else if (r.type === 'weekly') {
        const days = r.days.length ? r.days : [base.getDay()];
        // Se camina día por día desde "limit": la repetición cada N semanas cuenta desde la semana de inicio
        const startWeek = new Date(base.getFullYear(), base.getMonth(), base.getDate() - base.getDay());
        let d = new Date(new Date(limit * 1000).getFullYear(), new Date(limit * 1000).getMonth(), new Date(limit * 1000).getDate(), h, mi, s);
        for (let i = 0; i < 800; i++, d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1, h, mi, s)) {
            if (d / 1000 <= limit || !days.includes(d.getDay())) continue;
            const wk = new Date(d.getFullYear(), d.getMonth(), d.getDate() - d.getDay());
            const weeks = Math.round((wk - startWeek) / (7 * 86400000));
            if (weeks % r.every === 0) return Math.floor(d / 1000);
        }
        return null;
    } else if (r.type === 'monthly') {
        let n = 0;
        do {
            n += r.every;
            const total = base.getMonth() + n;
            const y = base.getFullYear() + Math.floor(total / 12), mo = total % 12;
            cand = new Date(y, mo, Math.min(base.getDate(), daysInMonth(y, mo)), h, mi, s);
        } while (cand / 1000 <= limit && n < 12 * 200);
    } else {   // yearly: 29 de febrero cae el 28 en años no bisiestos
        let n = 0;
        do {
            n += r.every;
            const y = base.getFullYear() + n;
            cand = new Date(y, base.getMonth(), Math.min(base.getDate(), daysInMonth(y, base.getMonth())), h, mi, s);
        } while (cand / 1000 <= limit && n < 200);
    }
    return Math.floor(cand / 1000);
}

// ─── RESPUESTAS AUTOMÁTICAS: ¿la regla aplica ahora? ─────────────────────────
const toMin = (hhmm) => { const [h, m] = String(hhmm || '0:0').split(':'); return (parseInt(h, 10) || 0) * 60 + (parseInt(m, 10) || 0); };

// Regla de horario: días (0 = domingo; vacío = todos) y franja desde-hasta (puede cruzar la medianoche)
export function scheduleActive(rule, date = new Date()) {
    const days = Array.isArray(rule.days) ? rule.days : [];
    const from = toMin(rule.from || '00:00'), to = toMin(rule.to || '23:59');
    const now = date.getHours() * 60 + date.getMinutes();
    if (from === to) return !days.length || days.includes(date.getDay());
    if (from < to) return (!days.length || days.includes(date.getDay())) && now >= from && now < to;
    // Cruza la medianoche: la parte de después de las 00:00 pertenece al día anterior
    if (now >= from) return !days.length || days.includes(date.getDay());
    if (now < to) return !days.length || days.includes((date.getDay() + 6) % 7);
    return false;
}

export function ruleApplies(rule, cfg, jid, isGroup, date = new Date()) {
    if (!rule || rule.enabled === false || !rule.text) return false;
    if (isGroup && rule.individual_only !== false) return false;
    if (Array.isArray(rule.exclude) && rule.exclude.includes(jid)) return false;
    if (Array.isArray(rule.only) && rule.only.length && !rule.only.includes(jid)) return false;
    if (rule.kind === 'manual') return cfg.active_rule === rule.id;
    return scheduleActive(rule, date);
}

// ─── MÓDULO ──────────────────────────────────────────────────────────────────
export function createExtras(ctx) {
    const { app, dataDir, getSock, isConnected, sendWhatsApp, deliverReliably, postEvent, webhookUrl, decryptPollVote, log } = ctx;
    const note = (...a) => (log || console.log)(...a);

    const readJson = (file, fallback) => {
        try { if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, 'utf-8')); } catch (e) { console.error(`[Extras] No se pudo leer ${path.basename(file)}:`, e.message); }
        return fallback;
    };
    const writeJson = (file, data) => {
        try {
            const tmp = `${file}.tmp`;
            fs.writeFileSync(tmp, JSON.stringify(data), 'utf-8');
            fs.renameSync(tmp, file);
        } catch (e) { console.error(`[Extras] No se pudo guardar ${path.basename(file)}:`, e.message); }
    };
    const toJid = (to) => (to.includes('@') ? to : `${to.replace(/\D/g, '')}@s.whatsapp.net`);
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const nowS = () => Math.floor(Date.now() / 1000);

    // El gateway descarta el eco de lo que él mismo envía: la app se entera de cada envío por acá
    function reportSent(jid, result, extra) {
        deliverReliably(webhookUrl, {
            message_id: result?.key?.id, upsert_type: 'append', chat_jid: jid, sender_jid: 'me', push_name: 'Yo',
            chat_name: extra.chat_name || null, from_me: true, text: extra.text || '', media_type: extra.media_type || 'text',
            media_path: extra.media_path || null, timestamp: nowS(), is_group: jid.endsWith('@g.us'), ...(extra.poll ? { poll: extra.poll } : {})
        }).catch(err => console.error('[Extras] Error avisando el envío a la app:', err.message));
    }

    // ── Respuestas automáticas ──
    const AUTO_FILE = path.join(dataDir, 'respuestas_automaticas.json');
    const AUTO_STATE_FILE = path.join(dataDir, 'respuestas_automaticas_estado.json');
    let autoCfg = readJson(AUTO_FILE, null) || { enabled: false, active_rule: null, rules: [] };
    let autoState = readJson(AUTO_STATE_FILE, {});   // "regla|jid" -> último envío (epoch s)
    let autoStateTimer = null;
    const autoPending = new Set();

    function cleanRule(r) {
        const strs = (a) => (Array.isArray(a) ? a.filter(x => typeof x === 'string' && x).slice(0, 500) : []);
        return {
            id: String(r.id || crypto.randomBytes(4).toString('hex')),
            name: String(r.name || 'Respuesta').slice(0, 80),
            enabled: r.enabled !== false,
            kind: r.kind === 'manual' ? 'manual' : 'schedule',
            text: String(r.text || '').slice(0, 2000),
            days: (Array.isArray(r.days) ? r.days : []).map(Number).filter(d => d >= 0 && d <= 6),
            from: /^\d{1,2}:\d{2}$/.test(r.from || '') ? r.from : '00:00',
            to: /^\d{1,2}:\d{2}$/.test(r.to || '') ? r.to : '23:59',
            individual_only: r.individual_only !== false,
            exclude: strs(r.exclude),
            only: strs(r.only),
            cooldown_hours: Math.max(0, Math.min(720, Number(r.cooldown_hours ?? 12) || 0))
        };
    }

    function saveAutoState() {
        clearTimeout(autoStateTimer);
        autoStateTimer = setTimeout(() => {
            const limit = nowS() - 30 * 86400;
            for (const k of Object.keys(autoState)) if (autoState[k] < limit) delete autoState[k];
            writeJson(AUTO_STATE_FILE, autoState);
        }, 1500);
    }

    // Llamado por server.js con cada mensaje entrante ya extraído
    async function onIncoming({ jid, fromMe, isGroup, type, mediaType, timestamp, revoked, edited, isCall }) {
        try {
            if (!autoCfg.enabled || fromMe || revoked || edited || isCall || type !== 'notify') return;
            if (!jid || jid === 'status@broadcast' || jid.endsWith('@broadcast') || jid.endsWith('@newsletter')) return;
            if (nowS() - Number(timestamp || 0) > 180) return;   // mensajes viejos de una reconexión: no se contestan
            const rule = (autoCfg.rules || []).find(r => ruleApplies(r, autoCfg, jid, isGroup));
            if (!rule) return;
            const key = `${rule.id}|${jid}`;
            if (autoPending.has(key)) return;
            const last = autoState[key] || 0;
            if (rule.cooldown_hours > 0 && nowS() - last < rule.cooldown_hours * 3600) return;
            autoPending.add(key);
            autoState[key] = nowS();   // se anota antes de enviar: dos mensajes seguidos no disparan dos respuestas
            saveAutoState();
            try {
                await wait(1500 + Math.random() * 2500);   // una persona no contesta al instante
                if (!isConnected()) { delete autoState[key]; return; }
                const result = await sendWhatsApp({ to: jid, text: rule.text });
                note(`[Respuestas] ↩️ "${rule.name}" enviada a ${jid}`);
                reportSent(jid, result, { text: rule.text });
            } catch (e) {
                delete autoState[key];
                console.error('[Respuestas] No se pudo enviar la respuesta automática:', e.message);
            } finally { autoPending.delete(key); }
        } catch (e) { console.error('[Respuestas] Error:', e.message); }
    }

    app.get('/auto-reply', (req, res) => res.json(autoCfg));
    app.post('/auto-reply', (req, res) => {
        try {
            const b = req.body || {};
            const rules = (Array.isArray(b.rules) ? b.rules : autoCfg.rules).slice(0, 50).map(cleanRule);
            const active = rules.some(r => r.id === b.active_rule && r.kind === 'manual') ? b.active_rule : null;
            autoCfg = { enabled: Boolean(b.enabled), active_rule: b.active_rule === undefined ? autoCfg.active_rule : active, rules };
            writeJson(AUTO_FILE, autoCfg);
            res.json({ success: true, config: autoCfg });
        } catch (e) { res.status(500).json({ error: e.message }); }
    });
    // Enciende/apaga un modo manual ("Estoy manejando") sin tocar el resto: { rule_id | null }
    app.post('/auto-reply/mode', (req, res) => {
        const id = req.body?.rule_id || null;
        if (id && !autoCfg.rules.some(r => r.id === id && r.kind === 'manual')) return res.status(404).json({ error: 'No existe ese modo' });
        autoCfg.active_rule = id;
        if (id) autoCfg.enabled = true;
        writeJson(AUTO_FILE, autoCfg);
        res.json({ success: true, config: autoCfg });
    });

    // ── Encuestas ──
    // polls[id] = { id, chat, creator, from_me, name, options, multi, secret(hex), votes: { voter: [opciones] } }
    const POLL_FILE = path.join(dataDir, 'encuestas.json');
    const polls = readJson(POLL_FILE, {});
    let pollTimer = null;
    const savePolls = () => {
        clearTimeout(pollTimer);
        pollTimer = setTimeout(() => {
            const ids = Object.keys(polls);
            if (ids.length > 500) ids.sort((a, b) => (polls[a].ts || 0) - (polls[b].ts || 0)).slice(0, ids.length - 500).forEach(i => delete polls[i]);
            writeJson(POLL_FILE, polls);
        }, 1000);
    };
    const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');
    const pollView = (p) => {
        const counts = p.options.map(() => ({ votes: 0, mine: false }));
        for (const [voter, picks] of Object.entries(p.votes || {})) {
            for (const name of picks) {
                const i = p.options.indexOf(name);
                if (i >= 0) { counts[i].votes++; if (voter === 'me') counts[i].mine = true; }
            }
        }
        return { id: p.id, chat_jid: p.chat, name: p.name, multi: p.multi, voters: Object.keys(p.votes || {}).filter(v => (p.votes[v] || []).length).length,
                 options: p.options.map((name, i) => ({ name, votes: counts[i].votes, mine: counts[i].mine })) };
    };
    const pollSecret = (msg) => {
        const s = msg?.message?.messageContextInfo?.messageSecret
            || msg?.message?.ephemeralMessage?.message?.messageContextInfo?.messageSecret
            || msg?.message?.deviceSentMessage?.message?.messageContextInfo?.messageSecret;
        return s ? Buffer.from(s).toString('hex') : null;
    };

    // Registra una encuesta creada (propia o recibida). "details.poll" lo arma extractMessageDetails.
    function trackPoll(msg, details, chatJid) {
        try {
            if (!details?.poll || !msg?.key?.id) return null;
            const secret = pollSecret(msg);
            const fromMe = Boolean(msg.key.fromMe);
            polls[msg.key.id] = {
                id: msg.key.id, chat: chatJid, from_me: fromMe, creator: fromMe ? null : (msg.key.participant || msg.key.remoteJid),
                creator_alts: [msg.key.participantAlt, msg.key.remoteJidAlt].filter(Boolean),
                name: details.poll.name, options: details.poll.options, multi: details.poll.multi, secret,
                votes: polls[msg.key.id]?.votes || {}, ts: Number(msg.messageTimestamp || nowS())
            };
            savePolls();
            return pollView(polls[msg.key.id]);
        } catch (e) { console.error('[Encuestas] Error registrando encuesta:', e.message); return null; }
    }

    // Voto de otra persona (o mío desde el celular): se descifra y se avisa a la app con el conteo nuevo
    function onPollVote(msg) {
        try {
            const pu = msg?.message?.pollUpdateMessage
                || msg?.message?.ephemeralMessage?.message?.pollUpdateMessage
                || msg?.message?.deviceSentMessage?.message?.pollUpdateMessage;
            if (!pu) return false;
            const p = polls[pu.pollCreationMessageKey?.id];
            if (!p || !p.secret || !pu.vote) return true;
            const sock = getSock();
            const meIds = [sock?.user?.id, sock?.user?.lid].filter(Boolean).map(j => j.replace(/:\d+@/, '@'));
            const fromMe = Boolean(msg.key.fromMe);
            const voters = fromMe ? meIds : [msg.key.participant, msg.key.remoteJid, msg.key.participantAlt, msg.key.remoteJidAlt].filter(Boolean);
            const creators = p.from_me ? meIds : [p.creator, ...(p.creator_alts || [])].filter(Boolean);
            let picked = null;
            outer: for (const creator of creators) {
                for (const voter of voters) {
                    try {
                        const dec = decryptPollVote(pu.vote, { pollCreatorJid: creator, pollMsgId: p.id, pollEncKey: Buffer.from(p.secret, 'hex'), voterJid: voter });
                        picked = (dec.selectedOptions || []).map(h => Buffer.from(h).toString('hex'));
                        break outer;
                    } catch (_) { /* par de identidades equivocado: se prueba el siguiente */ }
                }
            }
            if (!picked) { note(`[Encuestas] No se pudo descifrar un voto de ${p.id}`); return true; }
            const names = p.options.filter(o => picked.includes(sha(o)));
            p.votes[fromMe ? 'me' : (voters[0] || 'otro')] = names;
            savePolls();
            postEvent('poll', pollView(p));
            return true;
        } catch (e) { console.error('[Encuestas] Error procesando voto:', e.message); return true; }
    }

    app.post('/poll', async (req, res) => {
        try {
            const b = req.body || {};
            const name = String(b.name || '').trim();
            const options = [...new Set((Array.isArray(b.options) ? b.options : []).map(o => String(o).trim()).filter(Boolean))];
            if (!b.to || !name) return res.status(400).json({ error: 'Falta la pregunta o el destinatario' });
            if (options.length < 2 || options.length > 12) return res.status(400).json({ error: 'Una encuesta lleva entre 2 y 12 opciones distintas' });
            if (!isConnected()) return res.status(503).json({ error: 'WhatsApp no está conectado.' });
            const jid = toJid(String(b.to));
            const multi = Boolean(b.multiple);
            const result = await getSock().sendMessage(jid, { poll: { name, values: options, selectableCount: multi ? options.length : 1 } });
            if (result?.key?.id) ctx.markSent(result.key.id);
            const view = trackPoll({ key: { ...result.key, fromMe: true }, message: result.message, messageTimestamp: nowS() },
                { poll: { name, options, multi } }, jid);
            reportSent(jid, result, { text: `📊 Encuesta: ${name}`, media_type: 'poll', poll: view });
            res.json({ success: true, message_id: result?.key?.id, poll: view });
        } catch (e) { console.error('[Encuestas] Error creando encuesta:', e); res.status(500).json({ error: e.message }); }
    });
    app.post('/polls/get', (req, res) => {
        const ids = Array.isArray(req.body?.ids) ? req.body.ids.slice(0, 200) : [];
        res.json({ polls: ids.filter(i => polls[i]).map(i => pollView(polls[i])) });
    });

    // ── Envío a varios ──
    const SEND_KEYS = ['image_path', 'document_path', 'video_path', 'audio_path', 'sticker_path', 'image_url'];
    const SEND_FIELDS = ['text', 'image_thumb', 'document_name', 'mimetype', 'audio_seconds', 'audio_waveform', 'sticker_animated'];
    const jobs = [];
    let bulkRunning = false;

    async function runBulk() {
        if (bulkRunning) return;
        bulkRunning = true;
        try {
            for (;;) {
                const job = jobs.find(j => j.state === 'pending' || j.state === 'running');
                if (!job) break;
                job.state = 'running';
                for (const t of job.targets) {
                    if (job.canceled) break;
                    if (t.state !== 'pending') continue;
                    if (!isConnected()) { await wait(5000); if (!isConnected()) { t.state = 'failed'; t.error = 'Sin conexión'; continue; } }
                    if (job.sentCount > 0) await wait(job.delay_min * 1000 + Math.random() * (job.delay_max - job.delay_min) * 1000);
                    if (job.canceled) break;
                    try {
                        const result = await sendWhatsApp({ ...job.send, to: t.to });
                        t.state = 'sent';
                        job.sentCount++;
                        const mediaKey = SEND_KEYS.find(k => job.send[k]);
                        reportSent(t.to, result, { chat_name: t.chat_name, text: job.send.text || (mediaKey === 'document_path' ? job.send.document_name || '' : ''),
                            media_type: mediaKey ? mediaKey.replace('_path', '').replace('_url', '') : 'text', media_path: mediaKey && mediaKey.endsWith('_path') ? job.send[mediaKey] : null });
                    } catch (e) {
                        t.state = 'failed';
                        t.error = e.message || String(e);
                        console.error(`[Envío masivo] Falló ${t.to}:`, t.error);
                    }
                }
                for (const t of job.targets) if (t.state === 'pending') t.state = 'canceled';
                job.state = job.canceled ? 'canceled' : 'done';
                job.finished_at = nowS();
                postEvent('bulk', bulkView(job));
            }
        } finally {
            bulkRunning = false;
            while (jobs.length > 10) jobs.shift();
        }
    }
    const bulkView = (j) => ({ id: j.id, state: j.state, total: j.targets.length,
        sent: j.targets.filter(t => t.state === 'sent').length, failed: j.targets.filter(t => t.state === 'failed').length,
        targets: j.targets.map(({ to, chat_name, state, error }) => ({ to, chat_name, state, error: error || null })) });

    app.post('/send-many', (req, res) => {
        try {
            const b = req.body || {};
            const seen = new Set();
            const targets = (Array.isArray(b.targets) ? b.targets : []).map(t => (typeof t === 'string' ? { to: t } : t))
                .filter(t => t && typeof t.to === 'string' && t.to).map(t => ({ to: toJid(t.to), chat_name: t.chat_name || null, state: 'pending' }))
                .filter(t => !seen.has(t.to) && seen.add(t.to)).slice(0, 100);
            if (!targets.length) return res.status(400).json({ error: 'Elegí al menos un contacto' });
            const mediaKey = SEND_KEYS.find(k => b[k]);
            if (!mediaKey && !String(b.text || '').trim()) return res.status(400).json({ error: 'El mensaje está vacío' });
            if (mediaKey && mediaKey.endsWith('_path') && !fs.existsSync(b[mediaKey])) return res.status(404).json({ error: `Archivo no encontrado: ${b[mediaKey]}` });
            const send = {};
            for (const k of [...SEND_KEYS, ...SEND_FIELDS]) if (b[k]) send[k] = b[k];
            // Pausa al azar entre envíos para no parecer spam (mínimo 3 s)
            const dmin = Math.max(3, Number(b.delay_min) || 5);
            const job = { id: crypto.randomBytes(5).toString('hex'), state: 'pending', targets, send, delay_min: dmin,
                          delay_max: Math.max(dmin, Number(b.delay_max) || dmin + 5), sentCount: 0, created_at: nowS(), canceled: false };
            jobs.push(job);
            runBulk().catch(e => console.error('[Envío masivo] Error:', e.message));
            res.json({ success: true, id: job.id });
        } catch (e) { res.status(500).json({ error: e.message }); }
    });
    app.get('/send-many', (req, res) => res.json({ jobs: jobs.map(bulkView) }));
    app.post('/send-many/cancel', (req, res) => {
        const job = jobs.find(j => j.id === req.body?.id);
        if (!job) return res.status(404).json({ error: 'No existe ese envío' });
        job.canceled = true;
        res.json({ success: true });
    });

    return { onIncoming, trackPoll, onPollVote, nextRepeatAt, normalizeRepeat };
}
