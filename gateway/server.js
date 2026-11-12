import express from 'express';
import pino from 'pino';
import QRCode from 'qrcode';
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import os from 'os';
import { fileURLToPath } from 'url';
import makeWASocket, {
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    downloadMediaMessage,
    areJidsSameUser,
    makeCacheableSignalKeyStore,
    BufferJSON,
    generateMessageIDV2,
    decryptPollVote
} from '@whiskeysockets/baileys';
import { createExtras, normalizeRepeat } from './extras.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Variables de entorno y configuración
const PORT = parseInt(process.env.PORT || '5016', 10);
const PYTHON_WEBHOOK_URL = process.env.PYTHON_WEBHOOK_URL || 'http://127.0.0.1:5015/api/whatsapp/incoming';
const PYTHON_CONNECTION_URL = process.env.PYTHON_CONNECTION_URL || 'http://127.0.0.1:5015/api/whatsapp/connection';
// El resto de los avisos va al mismo servidor que PYTHON_WEBHOOK_URL (así un puerto distinto se respeta en todos)
const PYTHON_API = PYTHON_WEBHOOK_URL.replace(/\/incoming$/, '');
const PYTHON_SYNC_URL = process.env.PYTHON_SYNC_URL || `${PYTHON_API}/sync_batch`;
const PYTHON_STATUS_URL = process.env.PYTHON_STATUS_URL || `${PYTHON_API}/status`;
const PYTHON_AVATAR_URL = process.env.PYTHON_AVATAR_URL || `${PYTHON_API}/avatar`;
const PYTHON_REACTION_URL = process.env.PYTHON_REACTION_URL || `${PYTHON_API}/reaction`;

// En Android no hay carpeta personal utilizable: la app indica su carpeta privada
const USER_DATA_DIR = process.env.ADVANCEDWHATSAPP_DATA_DIR || path.join(os.homedir(), '.magsapp');
if (!fs.existsSync(USER_DATA_DIR)) fs.mkdirSync(USER_DATA_DIR, { recursive: true });
const AUTH_DIR = process.env.AUTH_DIR || path.join(USER_DATA_DIR, 'auth_info_baileys');
const MEDIA_DIR = process.env.MEDIA_DIR || path.join(USER_DATA_DIR, 'media');
const AVATARS_DIR = path.join(MEDIA_DIR, 'avatars');
const TOKEN = process.env.ADVANCEDWHATSAPP_TOKEN || '';
// Huella del server.js que corre: la app reinicia el gateway si quedó uno viejo de otra versión
const BUILD_ID = process.env.ADVANCEDWHATSAPP_BUILD || '';
const AUTH_HEADERS = { 'Content-Type': 'application/json', 'X-AdvancedWhatsapp-Token': TOKEN };
const LOG_FILE = path.join(USER_DATA_DIR, 'gateway.log');

if (!fs.existsSync(AUTH_DIR)) fs.mkdirSync(AUTH_DIR, { recursive: true });
if (!fs.existsSync(MEDIA_DIR)) fs.mkdirSync(MEDIA_DIR, { recursive: true });
if (!fs.existsSync(AVATARS_DIR)) fs.mkdirSync(AVATARS_DIR, { recursive: true });

// Logger
const logStream = fs.createWriteStream(LOG_FILE, { flags: 'a' });
logStream.on('error', () => {});
process.stdout.on('error', () => {});   // pipe cerrado (la app ya no lee): sin esto cada log reintentaba y fallaba en bucle
function logArg(a) {
    if (a instanceof Error) return a.stack || a.message;   // JSON.stringify(Error) daba "{}"
    if (a && typeof a === 'object') {
        try { return JSON.stringify(a); } catch (_) { return String(a); }
    }
    return String(a);
}
function writeLog(...args) {
    const timestamp = new Date().toISOString().replace('T', ' ').substring(0, 19);
    const msg = args.map(logArg).join(' ');
    const line = `[${timestamp}] ${msg}\n`;
    try { logStream.write(line); } catch (_) {}
    try { process.stdout.write(line); } catch (_) {}
    if (msg.includes('Bad MAC')) noteBadMac(msg);
}

// ── Auto-reparación de sesiones de cifrado corruptas ("Bad MAC") ──
// Si la sesión con un dispositivo se desincroniza, todos sus mensajes llegan vacíos y se pierden.
// Tras 3 fallos seguidos se borra esa sesión: Baileys pide el reenvío y se renegocia una nueva.
const badMacHits = new Map();   // dirección -> marcas de tiempo recientes
const lastSessionHeal = new Map();

function noteBadMac(text) {
    try {
        const m = /(\d+)\.(\d+) \[as awaitable\]/.exec(text);
        if (!m || !sock?.authState?.keys) return;
        const addr = `${m[1]}.${m[2]}`;
        const now = Date.now();
        const hits = (badMacHits.get(addr) || []).filter(t => now - t < 120000);
        hits.push(now);
        badMacHits.set(addr, hits);
        if (hits.length < 3 || now - (lastSessionHeal.get(addr) || 0) < 600000) return;
        lastSessionHeal.set(addr, now);
        badMacHits.delete(addr);
        Promise.resolve(sock.authState.keys.set({ session: { [addr]: null } }))
            .then(() => console.log(`[Baileys] 🔧 Sesión corrupta ${addr} eliminada; se renegociará con el próximo mensaje.`))
            .catch(err => console.log(`[Baileys] No se pudo reparar la sesión ${addr}: ${err.message}`));
    } catch (_) {}
}
console.log = writeLog;
console.error = writeLog;

const app = express();
// Autenticación por token: evita que otros procesos o páginas web controlen el gateway
app.use((req, res, next) => {
    // En Android otras apps del celular alcanzan este puerto: ahí /status (trae el número) también pide token
    if (req.path === '/status' && !process.env.ADVANCEDWHATSAPP_DATA_DIR) return next();
    const got = Buffer.from(String(req.get('X-AdvancedWhatsapp-Token') || ''));
    const want = Buffer.from(TOKEN);
    if (!TOKEN || got.length !== want.length || !crypto.timingSafeEqual(got, want)) {
        return res.status(401).json({ error: 'No autorizado' });
    }
    next();
});
app.use(express.json({ limit: '50mb' }));

// Estado de la conexión
let sock = null;
let currentQR = null;
let currentQRDataUrl = null;
let connectionStatus = 'disconnected';
let connectedPhone = null;
let connectedPushName = null;
let myPhone = null;
let myLid = null;
let myName = 'Yo';

// Errores internos de Baileys (descifrado fallido, etc.) al log: antes en 'silent' un mensaje perdido no dejaba rastro
const logger = pino({ level: 'error' }, { write: (s) => writeLog('[Baileys-int]', s.trim()) });
const sentBotMessageIds = new Set();
const downloadedAvatars = new Set();

// ─── ALMACÉN DE CONTACTOS E IDENTIDADES LID <-> TELÉFONO ─────────────────────
const LID_MAP_FILE = path.join(USER_DATA_DIR, 'lid_phone_map.json');
const LEGACY_LID_MAP_FILE = path.join(AUTH_DIR, 'lid_phone_map.json');
try {
    if (!fs.existsSync(LID_MAP_FILE) && fs.existsSync(LEGACY_LID_MAP_FILE)) {
        fs.copyFileSync(LEGACY_LID_MAP_FILE, LID_MAP_FILE);
    }
} catch (_) {}
const CONTACTS_FILE = path.join(USER_DATA_DIR, 'contacts_store.json');
const groupNames = {};
const groupQueue = new Set();
let isProcessingGroupQueue = false;
let lidPhoneMap = {};
let contactsStore = {};

function loadStores() {
    try {
        if (fs.existsSync(LID_MAP_FILE)) {
            lidPhoneMap = JSON.parse(fs.readFileSync(LID_MAP_FILE, 'utf-8'));
            console.log(`[Baileys] Mapa LID cargado (${Object.keys(lidPhoneMap).length} registros).`);
        }
    } catch (e) {
        console.error('[Baileys] Error cargando LID map:', e);
    }
    try {
        if (fs.existsSync(CONTACTS_FILE)) {
            contactsStore = JSON.parse(fs.readFileSync(CONTACTS_FILE, 'utf-8'));
            console.log(`[Baileys] Contactos cargados (${Object.keys(contactsStore).length} registros).`);
            
            // Poblar automáticamente lidPhoneMap desde contactsStore
            let addedLids = 0;
            for (const [k, v] of Object.entries(contactsStore)) {
                if (v && v.lid && v.phone && v.phone !== v.lid) {
                    const cLid = String(v.lid).replace('@lid', '').split(':')[0].trim();
                    const cPhone = String(v.phone).replace('@s.whatsapp.net', '').replace(/\D/g, '').trim();
                    if (cLid && cPhone && cPhone.length >= 7 && !lidPhoneMap[cLid]) {
                        lidPhoneMap[cLid] = cPhone;
                        addedLids++;
                    }
                }
            }
            if (addedLids > 0) {
                console.log(`[Baileys] Mapa LID complementado con ${addedLids} registros desde contactos.`);
                saveLidMap();
            }
        }
    } catch (e) {
        console.error('[Baileys] Error cargando contacts store:', e);
    }
}

function saveLidMap() {
    try {
        fs.writeFileSync(LID_MAP_FILE, JSON.stringify(lidPhoneMap, null, 2), 'utf-8');
    } catch (_) {}
}

function saveContactsStore() {
    try {
        fs.writeFileSync(CONTACTS_FILE, JSON.stringify(contactsStore, null, 2), 'utf-8');
    } catch (_) {}
}

loadStores();

// name = nombre de la agenda (o puesto a mano en Advanced WhatsApp); pushName = nombre de perfil de WhatsApp.
// La agenda manda: el pushName solo da nombre a contactos que no tienen uno guardado. Antes cada mensaje
// entrante pisaba la agenda con el perfil y dos contactos distintos ("Martin" y "Martin 1") quedaban iguales.
// Devuelve la entrada del contacto si cambió su nombre visible; si no, null.
function saveContactInfo(jidOrPhone, lid, name = '', pushName = '') {
    if (!jidOrPhone && !lid) return null;

    let cleanPhone = '';
    let cleanLid = '';

    if (jidOrPhone) {
        const str = String(jidOrPhone).trim();
        if (str.endsWith('@lid')) {
            cleanLid = str.replace('@lid', '').split(':')[0].replace(/\D/g, '').trim();
        } else {
            cleanPhone = str.replace('@s.whatsapp.net', '').replace(/\D/g, '').trim();
        }
    }
    if (lid) {
        cleanLid = String(lid).replace('@lid', '').split(':')[0].replace(/\D/g, '').trim();
    }

    // Prevenir que el nombre propio del bot/usuario contamine contactos ajenos
    const notSelf = (n) => (n === myName || n === `${myName} (Tú)` || n.toLowerCase() === 'yo') ? '' : n;
    const savedName = notSelf((name || '').trim());
    const profileName = notSelf((pushName || '').trim());

    if (cleanLid && cleanPhone && cleanPhone.length >= 7) {
        if (lidPhoneMap[cleanLid] !== cleanPhone) {
            lidPhoneMap[cleanLid] = cleanPhone;
            saveLidMap();
        }
    }
    // Completar la otra mitad (LID <-> teléfono) para que las dos entradas del contacto digan lo mismo
    if (cleanLid && !cleanPhone) cleanPhone = lidPhoneMap[cleanLid] || '';
    if (cleanPhone && !cleanLid) cleanLid = contactsStore[cleanPhone]?.lid || '';

    let changed = false;
    let renamed = null;
    // Si falta una de las dos entradas, nace como copia de la otra (mismo contacto)
    const seed = (cleanPhone && contactsStore[cleanPhone]) || (cleanLid && contactsStore[cleanLid]) || {};
    for (const key of [cleanPhone, cleanLid]) {
        if (!key) continue;
        const existing = contactsStore[key] || {};
        const prev = contactsStore[key] || seed;
        const next = { name: '', ...prev };
        if (savedName) {
            next.name = savedName;
            delete next.nameFromPush;
        } else if (profileName && (!next.name || next.nameFromPush)) {
            next.name = profileName;
            next.nameFromPush = true;
        }
        if (profileName) next.pushName = profileName;
        // Solo se completan datos conocidos: un dato que falta no borra el que ya estaba
        if (cleanPhone) next.phone = cleanPhone;
        if (cleanLid) next.lid = cleanLid;
        if (JSON.stringify(next) !== JSON.stringify(existing)) {
            contactsStore[key] = next;
            changed = true;
            if (next.name !== (prev.name || '')) renamed = next;
        }
    }
    if (changed) {
        saveContactsStore();
    }
    return renamed;
}

// Avisa a Python de los contactos cuyo nombre cambió, para que la lista de chats lo muestre
function reportRenamedContacts(entries) {
    const list = entries.filter(Boolean);
    if (list.length) dispatchSyncBatch({ chats: [], contacts: list, messages: [], rename_chats: true });
}

function isIndividualJid(jid) {
    return typeof jid === 'string' && (jid.endsWith('@s.whatsapp.net') || jid.endsWith('@lid'));
}

// Se procesan chats individuales y grupos. Comunidades, canales y difusiones se ignoran por completo para no gastar internet.
// De los grupos no se bajan fotos, y sus archivos, como todos, solo cuando el usuario los pide.
function isChatJid(jid) {
    return isIndividualJid(jid) || (typeof jid === 'string' && jid.endsWith('@g.us'));
}

// Para confirmar la lectura en un grupo WhatsApp pide quién mandó cada mensaje
const groupSenders = new Map();
const GROUP_SENDERS_MAX = 5000;
function rememberGroupSender(msg) {
    if (!msg.key?.remoteJid?.endsWith('@g.us') || msg.key.fromMe || !msg.key.participant) return;
    groupSenders.set(msg.key.id, msg.key.participant);
    if (groupSenders.size > GROUP_SENDERS_MAX) groupSenders.delete(groupSenders.keys().next().value);
}

function resolveContact(jid, pushName = '') {
    if (!jid) return { name: '', phone: null, isSelf: false };
    const isLid = jid.endsWith('@lid');
    const isGroup = jid.endsWith('@g.us');
    const clean = jid.split('@')[0].split(':')[0].replace(/\D/g, '');

    // 1. ¿Es el propio usuario (Auto-chat / Mensajes Guardados)?
    if ((myLid && clean === myLid) || (myPhone && clean === myPhone)) {
        return {
            name: `${myName} (Tú)`,
            phone: myPhone,
            isSelf: true
        };
    }

    if (isGroup) {
        return {
            name: groupNames[jid] || 'Grupo',
            phone: null,
            isSelf: false
        };
    }

    // 2. Buscar en almacén de contactos
    let resolvedPhone = isLid ? lidPhoneMap[clean] : clean;
    let contact = contactsStore[clean] || (resolvedPhone ? contactsStore[resolvedPhone] : null);

    let cName = contact?.name || '';
    if (cName === myName || cName === `${myName} (Tú)` || cName.toLowerCase() === 'yo') {
        cName = '';
    }
    let cPush = contact?.pushName || '';
    if (cPush === myName || cPush === `${myName} (Tú)` || cPush.toLowerCase() === 'yo') {
        cPush = '';
    }
    let pName = pushName || '';
    if (pName === myName || pName === `${myName} (Tú)` || pName.toLowerCase() === 'yo') {
        pName = '';
    }

    let finalName = cName || cPush || pName || '';
    if (!finalName && resolvedPhone && resolvedPhone.length >= 7) {
        finalName = `+${resolvedPhone}`;
    }
    if (!finalName) {
        finalName = isLid ? `Contacto (${clean.slice(-4)})` : `+${clean}`;
    }

    return {
        name: finalName,
        phone: resolvedPhone || (isLid ? null : clean),
        isSelf: false
    };
}

// ─── COLA CONTROLADA DE FOTOS DE PERFIL (AVATARES ANTI-SATURACIÓN) ───────────
const avatarQueue = [];
const avatarQueuedSet = new Set();
let isProcessingAvatarQueue = false;

function queueAvatarDownload(jid) {
    if (!sock || !jid) return;
    const cleanJid = jid.includes('@') ? jid : `${jid}@s.whatsapp.net`;
    if (downloadedAvatars.has(cleanJid) || avatarQueuedSet.has(cleanJid)) return;
    avatarQueuedSet.add(cleanJid);
    avatarQueue.push(cleanJid);
    if (!isProcessingAvatarQueue) {
        processAvatarQueue();
    }
}

async function processAvatarQueue() {
    if (isProcessingAvatarQueue) return;
    isProcessingAvatarQueue = true;

    while (avatarQueue.length > 0) {
        const cleanJid = avatarQueue.shift();
        avatarQueuedSet.delete(cleanJid);
        if (downloadedAvatars.has(cleanJid)) continue;

        try {
            const url = await sock.profilePictureUrl(cleanJid, 'image').catch(() => null);
            downloadedAvatars.add(cleanJid);
            if (url) {
                const cleanName = cleanJid.replace(/[@:]/g, '_');
                const filePath = path.join(AVATARS_DIR, `${cleanName}.jpg`);
                const res = await fetch(url, { signal: AbortSignal.timeout(20000) });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const buf = await res.arrayBuffer();
                fs.writeFileSync(filePath, Buffer.from(buf));
                console.log(`[Baileys] 📸 Foto descargada: ${cleanJid}`);

                fetch(PYTHON_AVATAR_URL, {
                    method: 'POST',
                    headers: AUTH_HEADERS,
                    body: JSON.stringify({ jid: cleanJid, avatar_path: filePath })
                }).catch(() => {});
            }
        } catch (_) {}

        // Pausa de 120ms entre descargas para no saturar Baileys ni la UI
        await new Promise(r => setTimeout(r, 120));
    }
    isProcessingAvatarQueue = false;
}

function setGroupName(jid, subject) {
    if (!jid || !subject || groupNames[jid] === subject) return;
    groupNames[jid] = subject;
    dispatchSyncBatch({ chats: [{ jid, name: subject, phone: null, last_message_time: 0, is_group: 1 }], messages: [] });
}

function queueGroupName(jid) {
    if (!sock || !jid || !jid.endsWith('@g.us') || groupNames[jid] || groupQueue.has(jid)) return;
    groupQueue.add(jid);
    if (!isProcessingGroupQueue) processGroupQueue();
}

// Una sola consulta trae todos los grupos con su nombre: se evita pedirlos de a uno
let groupsSyncedFor = null;
async function syncGroupList() {
    if (!sock || connectionStatus !== 'connected' || groupsSyncedFor === connectedPhone) return;
    groupsSyncedFor = connectedPhone;
    try {
        const groups = await sock.groupFetchAllParticipating();
        const chats = [];
        for (const g of Object.values(groups || {})) {
            if (g.isCommunity || !g.subject) continue;
            groupNames[g.id] = g.subject;
            chats.push({ jid: g.id, name: g.subject, phone: null, last_message_time: 0, is_group: 1 });
        }
        if (chats.length) dispatchSyncBatch({ chats, messages: [] });
        console.log(`[Baileys] Grupos sincronizados: ${chats.length}`);
    } catch (e) {
        groupsSyncedFor = null;   // se reintenta en la próxima conexión
        console.error('[Baileys] No se pudo traer la lista de grupos:', e.message);
    }
}

async function processGroupQueue() {
    isProcessingGroupQueue = true;
    while (groupQueue.size > 0) {
        const jid = groupQueue.values().next().value;
        groupQueue.delete(jid);
        try {
            const meta = await sock.groupMetadata(jid);
            if (meta?.subject) setGroupName(jid, meta.subject);
        } catch (_) {}
        await new Promise(r => setTimeout(r, 300));
    }
    isProcessingGroupQueue = false;
}

function fetchAndSaveAvatar(jid) {
    if (!isIndividualJid(jid)) return;
    queueAvatarDownload(jid);
}

// ─── EXTRACTOR RECURSIVO DE CONTENIDO Y MENSAJES CITADOS ─────────────────────
function extractMessageDetails(msg) {
    if (!msg || !msg.message) {
        return null;
    }

    if (msg.messageStubType === 2 || msg.messageStubType === 'CIPHERTEXT') {
        return null;
    }

    let m = msg.message;
    let depth = 0;

    while (m && depth < 6) {
        depth++;
        if (m.deviceSentMessage?.message) {
            m = m.deviceSentMessage.message;
            continue;
        }
        if (m.ephemeralMessage?.message) {
            m = m.ephemeralMessage.message;
            continue;
        }
        if (m.viewOnceMessage?.message) {
            m = m.viewOnceMessage.message;
            continue;
        }
        if (m.viewOnceMessageV2?.message) {
            m = m.viewOnceMessageV2.message;
            continue;
        }
        if (m.documentWithCaptionMessage?.message) {
            m = m.documentWithCaptionMessage.message;
            continue;
        }
        // La edición llega envuelta: se sigue hasta el protocolMessage, que trae la clave del mensaje original
        if (m.editedMessage?.message) {
            m = m.editedMessage.message;
            continue;
        }
        // Otros envoltorios: álbumes, video circular de un solo uso, stickers animados, menciones, etc.
        const wrapper = m.viewOnceMessageV2Extension || m.associatedChildMessage || m.lottieStickerMessage ||
                        m.groupMentionedMessage || m.botInvokeMessage || m.futureProofMessage;
        if (wrapper?.message) {
            m = wrapper.message;
            continue;
        }
        break;
    }

    // Protocolos internos, mensajes eliminados y ediciones
    if (m.protocolMessage) {
        const pm = m.protocolMessage;
        const pType = pm.type;
        if (pType === 0 || pType === 'REVOKE') {
            return {
                text: '🚫 Se eliminó este mensaje',
                mediaType: 'revoked',
                innerMessage: m,
                quotedText: null,
                quotedSender: null,
                revokeOf: pm.key?.id || null
            };
        }
        if ((pType === 14 || pType === 'MESSAGE_EDIT') && pm.editedMessage && pm.key?.id) {
            const edited = extractMessageDetails({ message: pm.editedMessage });
            if (!edited) return null;
            return { ...edited, editOf: pm.key.id };
        }
        return null;
    }

    if (m.reactionMessage || m.encReactionMessage || m.senderKeyDistributionMessage ||
        m.pollUpdateMessage || m.keepInChatMessage || m.pinInChatMessage || m.albumMessage ||
        m.secretEncryptedMessage || m.encEventResponseMessage || m.requestPhoneNumberMessage) {
        return null;
    }

    let text = '';
    let mediaType = 'text';
    let pollInfo = null;

    if (m.conversation) {
        text = m.conversation;
    } else if (m.extendedTextMessage?.text) {
        text = m.extendedTextMessage.text;
    } else if (m.imageMessage) {
        mediaType = 'image';
        text = m.imageMessage.caption || '';
    } else if (m.videoMessage) {
        mediaType = 'video';
        text = m.videoMessage.caption || '';
    } else if (m.ptvMessage) {
        // Video circular ("video nota")
        mediaType = 'video';
        text = m.ptvMessage.caption || '';
    } else if (m.audioMessage) {
        mediaType = 'audio';
        text = '🎵 Nota de voz';
    } else if (m.documentMessage) {
        mediaType = 'document';
        text = m.documentMessage.fileName || m.documentMessage.caption || 'Documento adjunto';
    } else if (m.stickerMessage) {
        mediaType = 'sticker';
        text = '🎨 Sticker';
    } else if (m.contactMessage) {
        mediaType = 'contact';
        text = `👤 Contacto: ${m.contactMessage.displayName || ''}`;
    } else if (m.contactsArrayMessage) {
        mediaType = 'contact';
        text = `👥 Contactos (${m.contactsArrayMessage.contacts?.length || 0})`;
    } else if (m.locationMessage) {
        mediaType = 'location';
        text = `📍 Ubicación: ${m.locationMessage.name || m.locationMessage.address || ''}`;
    } else if (m.pollCreationMessage || m.pollCreationMessageV2 || m.pollCreationMessageV3) {
        const poll = m.pollCreationMessage || m.pollCreationMessageV2 || m.pollCreationMessageV3;
        mediaType = 'poll';
        text = `📊 Encuesta: ${poll?.name || ''}`;
        pollInfo = { name: poll?.name || '', options: (poll?.options || []).map(o => o.optionName).filter(Boolean), multi: poll?.selectableOptionsCount !== 1 };
    } else if (m.buttonsResponseMessage) {
        text = m.buttonsResponseMessage.selectedDisplayText || m.buttonsResponseMessage.selectedButtonId || '';
    } else if (m.listResponseMessage) {
        text = m.listResponseMessage.title || m.listResponseMessage.singleSelectReply?.selectedRowId || '';
    } else if (m.templateButtonReplyMessage) {
        text = m.templateButtonReplyMessage.selectedDisplayText || '';
    } else if (m.interactiveResponseMessage?.body?.text) {
        text = m.interactiveResponseMessage.body.text;
    } else if (m.interactiveMessage) {
        // Mensajes de empresas con botones
        const im = m.interactiveMessage;
        text = [im.header?.title, im.body?.text, im.footer?.text].filter(Boolean).join('\n');
    } else if (m.templateMessage) {
        const t = m.templateMessage.hydratedTemplate || m.templateMessage.hydratedFourRowTemplate || {};
        text = [t.hydratedTitleText, t.hydratedContentText, t.hydratedFooterText].filter(Boolean).join('\n');
    } else if (m.buttonsMessage) {
        text = m.buttonsMessage.contentText || m.buttonsMessage.text || '';
    } else if (m.listMessage) {
        text = [m.listMessage.title, m.listMessage.description].filter(Boolean).join('\n');
    } else if (m.liveLocationMessage) {
        mediaType = 'location';
        text = '📍 Ubicación en tiempo real';
    } else if (m.eventMessage) {
        text = `📅 Evento: ${m.eventMessage.name || ''}`;
    } else if (m.groupInviteMessage) {
        text = `👥 Invitación al grupo «${m.groupInviteMessage.groupName || ''}»`;
    } else if (m.productMessage) {
        text = `🛍️ Producto: ${m.productMessage.product?.title || ''}`;
    } else if (m.orderMessage) {
        text = `🧾 Pedido${m.orderMessage.itemCount ? ` (${m.orderMessage.itemCount} artículos)` : ''}`;
    } else if (m.callLogMesssage || m.callLogMessage) {
        text = '📞 Llamada';
    }

    // Extraer mensaje citado (Reply / Quoted)
    let quotedText = null;
    let quotedSender = null;
    const ctx = m.extendedTextMessage?.contextInfo ||
                m.imageMessage?.contextInfo ||
                m.videoMessage?.contextInfo ||
                m.documentMessage?.contextInfo ||
                m.audioMessage?.contextInfo;

    if (ctx?.quotedMessage) {
        const qDetails = extractMessageDetails({ message: ctx.quotedMessage });
        if (qDetails) {
            quotedText = qDetails.text || (qDetails.mediaType === 'image' ? '📷 Foto' : '[Archivo]');
            const part = ctx.participant || '';
            if (part && (part === sock?.user?.id || areJidsSameUser(part, sock?.user?.id || ''))) {
                quotedSender = 'Tú';
            } else {
                quotedSender = part ? resolveContact(part).name : 'Contacto';
            }
        }
    }

    const cleanText = (text || '').trim();
    if (!cleanText && !['image', 'video', 'audio', 'document', 'sticker', 'contact', 'location', 'poll', 'revoked'].includes(mediaType) && !quotedText) {
        return null;
    }

    return { text: cleanText, mediaType, innerMessage: m, quotedText, quotedSender, poll: pollInfo };
}

// Claves "útiles" de un mensaje descartado, para el log (messageContextInfo siempre viene y no dice nada)
function messageKinds(msg) {
    if (!msg?.message) return `sin contenido (stub ${msg?.messageStubType ?? '-'})`;
    const keys = Object.keys(msg.message).filter(k => k !== 'messageContextInfo');
    return keys.length ? keys.join(',') : 'messageContextInfo';
}

// Descarga un archivo; si WhatsApp ya lo borró del servidor (403/404/410, enlaces vencidos de mensajes viejos)
// le pide al celular que lo vuelva a subir y reintenta. Así se pueden abrir fotos y audios de hace meses.
async function downloadWithReupload(waMessage) {
    try {
        return await downloadMediaMessage(waMessage, 'buffer', {});
    } catch (err) {
        const status = err?.status || err?.output?.statusCode || err?.response?.status;
        const expired = [403, 404, 410].includes(status) || /status code 4(03|04|10)|Failed to fetch stream/i.test(err?.message || '');
        if (!expired || !sock || connectionStatus !== 'connected' || !sock.updateMediaMessage) throw err;
        console.log(`[Baileys] Archivo vencido en el servidor (${err.message}); se le pide al celular que lo vuelva a subir...`);
        let timer;
        let updated;
        try {
            updated = await Promise.race([
                sock.updateMediaMessage(waMessage),
                new Promise((_, rej) => { timer = setTimeout(() => rej(new Error('El celular no respondió (tiene que estar con internet)')), 30000); })
            ]);
        } finally {
            clearTimeout(timer);
        }
        return await downloadMediaMessage(updated, 'buffer', {});
    }
}

// Notificar estado a Python
function notifyConnectionState(status, extra = {}) {
    try {
        const payload = {
            status,
            phone: connectedPhone,
            push_name: connectedPushName,
            has_qr: !!currentQRDataUrl,
            timestamp: Date.now(),
            ...extra
        };
        fetch(PYTHON_CONNECTION_URL, {
            method: 'POST',
            headers: AUTH_HEADERS,
            body: JSON.stringify(payload)
        }).catch(() => {});
    } catch (_) {}
}

// ─── ENTREGA CONFIABLE A LA APP (bandeja de salida en disco) ────────────────
// Si la ventana está colgada, cerrándose o arrancando, lo que no se pudo entregar queda en outbox.json
// y se reenvía en orden apenas la app vuelve a responder. Antes, tras 5 intentos el mensaje se perdía.
const OUTBOX_FILE = path.join(USER_DATA_DIR, 'outbox.json');
const OUTBOX_MAX = 5000;
const OUTBOX_MAX_REJECTS = 30;   // respuestas de error seguidas (unos 5 min) antes de descartar un aviso
let outbox = [];
let outboxFlushing = false;
try {
    if (fs.existsSync(OUTBOX_FILE)) outbox = JSON.parse(fs.readFileSync(OUTBOX_FILE, 'utf-8')) || [];
    if (outbox.length) console.log(`[Outbox] ${outbox.length} entregas pendientes de la sesión anterior.`);
} catch (_) { outbox = []; }

function saveOutbox() {
    try {
        const tmp = `${OUTBOX_FILE}.tmp`;
        fs.writeFileSync(tmp, JSON.stringify(outbox), 'utf-8');
        fs.renameSync(tmp, OUTBOX_FILE);
    } catch (e) {
        console.error('[Outbox] No se pudo guardar:', e.message);
    }
}

// info.httpStatus queda con el código HTTP de la última respuesta (0 = la app no respondió)
async function postToPython(url, payload, attempts = 3, info = {}) {
    for (let attempt = 1; attempt <= attempts; attempt++) {
        info.httpStatus = 0;
        try {
            const r = await fetch(url, {
                method: 'POST',
                headers: AUTH_HEADERS,
                body: JSON.stringify(payload),
                signal: AbortSignal.timeout(30000)
            });
            info.httpStatus = r.status;
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return true;
        } catch (err) {
            if (attempt === attempts) {
                console.error(`[Outbox] No se pudo entregar a ${url.split('/').pop()}: ${err.message}`);
                return false;
            }
            await new Promise(res => setTimeout(res, 800 * attempt));
        }
    }
    return false;
}

function enqueueOutbox(url, payload) {
    outbox.push({ url, payload });
    if (outbox.length > OUTBOX_MAX) outbox.splice(0, outbox.length - OUTBOX_MAX);
    saveOutbox();
}

// Entrega en orden: si ya hay pendientes, lo nuevo va detrás para no desordenar la conversación
async function deliverReliably(url, payload) {
    if (outbox.length) {
        enqueueOutbox(url, payload);
        flushOutbox();
        return false;
    }
    const ok = await postToPython(url, payload, 3);
    if (!ok) enqueueOutbox(url, payload);
    return ok;
}

async function flushOutbox() {
    if (outboxFlushing || !outbox.length) return;
    outboxFlushing = true;
    try {
        let sent = 0;
        while (outbox.length) {
            const item = outbox[0];
            const info = {};
            if (!(await postToPython(item.url, item.payload, 1, info))) {
                // La app respondió con error: si es un rechazo definitivo (o se repite demasiado) se descarta
                // ese aviso; si no, bloquearía para siempre todo lo que viene detrás. Sin respuesta = se espera.
                const st = info.httpStatus;
                if (st > 0 && st !== 401) {
                    item.rejects = (item.rejects || 0) + 1;
                    if ([400, 404, 413, 422].includes(st) || item.rejects >= OUTBOX_MAX_REJECTS) {
                        console.error(`[Outbox] Se descarta un aviso para ${item.url.split('/').pop()} (la app respondió HTTP ${st}, intento ${item.rejects}).`);
                        outbox.shift();
                        saveOutbox();
                        continue;
                    }
                }
                break;
            }
            outbox.shift();
            sent++;
            if (sent % 25 === 0) saveOutbox();
        }
        saveOutbox();
        if (sent) console.log(`[Outbox] ${sent} entregas pendientes enviadas a la app (${outbox.length} restantes).`);
    } finally {
        outboxFlushing = false;
    }
}
setInterval(flushOutbox, 10000);

// Cola de lotes de sincronización: se envían de a uno y, si la app no responde, pasan a la bandeja de salida
const syncQueue = [];
let syncPumping = false;

async function pumpSyncQueue() {
    if (syncPumping) return;
    syncPumping = true;
    while (syncQueue.length > 0) {
        const batch = syncQueue.shift();
        if (await deliverReliably(PYTHON_SYNC_URL, batch)) {
            console.log(`[Baileys] Lote sincronizado con Python (${batch.chats?.length || 0} chats, ${batch.messages?.length || 0} msgs).`);
        }
    }
    syncPumping = false;
}

// Despachar lote de sincronización a Python
function dispatchSyncBatch(batch) {
    syncQueue.push(batch);
    pumpSyncQueue();
}

// Baileys: 2 = SERVER_ACK (enviado), 3 = DELIVERY_ACK (entregado), 4/5 = READ/PLAYED (leído)
function ackToStatus(s) {
    if (s === 4 || s === 5 || s === 'READ' || s === 'PLAYED') return 'read';
    if (s === 3 || s === 'DELIVERY_ACK') return 'delivered';
    if (s === 2 || s === 'SERVER_ACK') return 'sent';
    return null;
}

// Avisa a Python el nuevo estado de un mensaje propio (solo sube: Python ignora retrocesos)
function reportMessageStatus(messageId, status) {
    fetch(PYTHON_STATUS_URL, {
        method: 'POST',
        headers: AUTH_HEADERS,
        body: JSON.stringify({ message_id: messageId, status })
    }).catch(err => console.error('[Baileys] Error reportando estado:', err.message));
}

// Lecturas hechas en otro dispositivo (celular): se juntan unos instantes y van en un solo aviso.
// Van por la entrega confiable (con bandeja de salida): si se perdieran, los chats quedarían "sin leer".
const PYTHON_READ_ELSEWHERE_URL = `${PYTHON_API}/read_elsewhere`;
const readElsewhereQueue = new Map();   // chat_jid -> { chat_jid, alt_jid, message_ids:Set, all, unread }
let readElsewhereTimer = null;

function queueReadElsewhere(jid, altJid, { messageId, all, unread } = {}) {
    if (!jid) return;
    const chatJid = preferPhoneJid(jid, altJid);
    let entry = readElsewhereQueue.get(chatJid);
    if (!entry) {
        // El chat puede estar guardado por LID o por teléfono: se mandan los dos
        entry = { chat_jid: chatJid, alt_jid: chatJid !== jid ? jid : (altJid || null),
                  message_ids: new Set(), all: false, unread: false };
        readElsewhereQueue.set(chatJid, entry);
    }
    if (messageId) entry.message_ids.add(messageId);
    if (all) { entry.all = true; entry.unread = false; }
    if (unread) { entry.unread = true; entry.all = false; }
    if (!readElsewhereTimer) readElsewhereTimer = setTimeout(flushReadElsewhere, 400);
}

function flushReadElsewhere() {
    readElsewhereTimer = null;
    if (!readElsewhereQueue.size) return;
    const chats = [...readElsewhereQueue.values()].map(e => ({ ...e, message_ids: [...e.message_ids] }));
    readElsewhereQueue.clear();
    const total = chats.reduce((n, c) => n + c.message_ids.length, 0);
    console.log(`[Baileys] Leídos en otro dispositivo: ${chats.length} chats, ${total} mensajes.`);
    deliverReliably(PYTHON_READ_ELSEWHERE_URL, { chats })
        .catch(err => console.error('[Baileys] Error avisando lecturas:', err.message));
}

// Eventos livianos para la ventana (presencia, llamadas, estados nuevos): si no llegan, no pasa nada grave
const PYTHON_BASE_URL = PYTHON_WEBHOOK_URL.replace(/\/incoming$/, '');
function postEvent(name, payload) {
    fetch(`${PYTHON_BASE_URL}/${name}`, {
        method: 'POST',
        headers: AUTH_HEADERS,
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(10000)
    }).catch(() => {});
}

// Un contacto puede llegar como LID o como teléfono: se prefiere el teléfono para no duplicar chats
function preferPhoneJid(jid, alt) {
    if (!jid || !jid.endsWith('@lid')) return jid;
    const phone = alt && alt.endsWith('@s.whatsapp.net')
        ? alt.split('@')[0].split(':')[0]
        : lidPhoneMap[jid.split('@')[0].split(':')[0]];
    return phone && phone.length >= 7 ? `${phone}@s.whatsapp.net` : jid;
}

function avatarPathFor(jid) {
    if (!jid) return null;
    const p = path.join(AVATARS_DIR, `${jid.replace(/[@:]/g, '_')}.jpg`);
    return fs.existsSync(p) ? p : null;
}

// Tipos de mensaje con archivo descargable
const MEDIA_TYPES = ['image', 'video', 'audio', 'document', 'sticker'];

// Contactos compartidos: nombre y teléfono sacados de la vCard, para poder escribirles o guardarlos
function serializeContactsRaw(msg) {
    try {
        const inner = extractMessageDetails(msg)?.innerMessage;
        const cards = inner?.contactMessage ? [inner.contactMessage]
            : (inner?.contactsArrayMessage?.contacts || []);
        const contacts = cards.map(c => {
            const vcard = c.vcard || '';
            const waid = vcard.match(/waid=(\d+)/i)?.[1];
            const tel = vcard.match(/^(?:item\d+\.)?TEL[^:]*:(.+)$/im)?.[1] || '';
            const phone = waid || tel.replace(/\D/g, '');
            return { name: c.displayName || '', phone };
        });
        return contacts.length ? JSON.stringify({ contacts }) : null;
    } catch (_) {
        return null;
    }
}

// Serializa el mensaje completo (con sus claves de cifrado) para poder descargar el archivo más tarde
function serializeMediaRaw(msg, mediaType) {
    if (mediaType === 'contact') return serializeContactsRaw(msg);
    if (!MEDIA_TYPES.includes(mediaType)) return null;
    try {
        return JSON.stringify(msg, BufferJSON.replacer);
    } catch (_) {
        return null;
    }
}

// ─── RECONEXIÓN ──────────────────────────────────────────────────────────────
// Un único temporizador: varias causas (cierre, logout, /disconnect) no deben abrir dos sockets a la vez.
let reconnectTimer = null;
let reconnectAttempts = 0;
let pairingPending = false;     // hay un código de vinculación pedido y todavía sin confirmar
let pairingMode = false;        // el socket actual se abrió para vincular con código
let cachedWaVersion = null;
// La app avisa si la ventana está en primer plano: solo entonces figuramos "en línea" (como WhatsApp Desktop).
// Estar siempre "en línea" hacía que el celular dejara de recibir notificaciones.
let desiredPresence = 'unavailable';
const callTracker = new Map();   // id de llamada -> { handled, logged }

function scheduleReconnect(ms) {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        initWhatsApp();
    }, ms);
}

function resetAuthState() {
    try {
        fs.rmSync(AUTH_DIR, { recursive: true, force: true });
        fs.mkdirSync(AUTH_DIR, { recursive: true });
    } catch (e) {
        console.error('[Baileys] No se pudieron borrar las credenciales:', e.message);
    }
}

async function initWhatsApp() {
    try {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
        // Cerrar el socket anterior para no dejar dos conexiones vivas con los mismos listeners
        if (sock) {
            const old = sock;
            sock = null;
            try { old.ev.removeAllListeners(); } catch (_) {}
            try { old.end(undefined); } catch (_) {}
        }
        connectionStatus = 'connecting';
        notifyConnectionState(connectionStatus);

        const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
        if (!cachedWaVersion) {
            const latest = await fetchLatestBaileysVersion().catch(() => ({}));
            if (latest?.version) cachedWaVersion = latest.version;
        }
        const version = cachedWaVersion || undefined;

        const msgRetryCounterCache = {
            _cache: new Map(),
            get(key) { return this._cache.get(key); },
            set(key, val) {
                if (this._cache.size > 2500) this._cache.clear();
                this._cache.set(key, val);
            },
            del(key) { this._cache.delete(key); }
        };

        sock = makeWASocket({
            ...(version ? { version } : {}),
            logger,
            printQRInTerminal: false,
            auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger) },
            // Con código se presenta como un navegador real (la documentación de Baileys solo habla de nombres
            // propios para el QR), y el socket debe durar lo que tarda la persona en escribirlo: se cierra al
            // agotar los 6 QR, que por defecto duran 20 s.
            browser: pairingMode ? ['Windows', 'Chrome', '10.0.22631'] : ['Advanced WhatsApp Desktop', 'Chrome', '1.0.0'],
            ...(pairingMode ? { qrTimeout: 60000 } : {}),
            syncFullHistory: true,
            shouldSyncHistoryMessage: () => true,
            markOnlineOnConnect: false,
            generateHighQualityLinkPreview: true,
            msgRetryCounterCache,
            getMessage: async () => undefined
        });

        sock.ev.on('creds.update', saveCreds);

        // ── Sincronización de Identidades & Contactos ──
        sock.ev.on('chats.phoneNumberShare', ({ lid, jid }) => {
            saveContactInfo(jid, lid);
        });

        // Baileys: c.name = nombre de la agenda; c.notify / c.verifiedName = perfil de WhatsApp (no pisan la agenda)
        const processContacts = (contacts) => {
            const renamed = [];
            if (!Array.isArray(contacts)) return renamed;
            for (const c of contacts) {
                const id = c.id || c.jid;
                // Baileys 7 puede identificar al contacto por su LID y traer el teléfono aparte
                const isLidId = typeof id === 'string' && id.endsWith('@lid');
                const jidOrPhone = (isLidId && c.phoneNumber) ? c.phoneNumber : id;
                const lid = c.lid || (isLidId ? id : undefined);
                renamed.push(saveContactInfo(jidOrPhone, lid, c.name || '', c.notify || c.verifiedName || ''));
                if (id) {
                    fetchAndSaveAvatar(id);
                }
            }
            return renamed;
        };
        const processAndReportContacts = (contacts) => reportRenamedContacts(processContacts(contacts));

        sock.ev.on('contacts.upsert', processAndReportContacts);
        sock.ev.on('contacts.update', processAndReportContacts);
        sock.ev.on('contacts.set', ({ contacts }) => processAndReportContacts(contacts));

        // ── Sincronización de Historial de WhatsApp (messaging-history.set) ──
        sock.ev.on('messaging-history.set', async ({ chats, contacts, messages, isLatest }) => {
            const statusMsgs = (messages || []).filter(m => m.key?.remoteJid === 'status@broadcast');
            console.log(`[Baileys] 📦 Historial recibido: ${chats?.length || 0} chats, ${contacts?.length || 0} contactos, ${messages?.length || 0} mensajes (${statusMsgs.length} de estados, ${statusMsgs.filter(m => m.key.fromMe).length} propios)`);

            if (contacts) processContacts(contacts);

            const parsedChats = [];
            if (Array.isArray(chats)) {
                for (const ch of chats) {
                    if (!isChatJid(ch.id)) continue;
                    if (ch.id.endsWith('@g.us') && ch.name) groupNames[ch.id] = ch.name;
                    const resolved = resolveContact(ch.id, ch.name);
                    parsedChats.push({
                        jid: ch.id,
                        name: resolved.name,
                        phone: resolved.phone,
                        unread_count: ch.unreadCount || 0,
                        last_message_time: ch.conversationTimestamp ? Number(ch.conversationTimestamp) : 0,
                        is_group: ch.id.endsWith('@g.us') ? 1 : 0
                    });
                    // Descargar foto de perfil en segundo plano
                    fetchAndSaveAvatar(ch.id);
                }
            }

            const parsedMessages = [];
            if (Array.isArray(messages)) {
                for (const m of messages) {
                    if (m.key.remoteJid === 'status@broadcast') {
                        if (m.key.fromMe) trackMyStatus(m);
                        else trackContactStatus(m, false);
                        continue;
                    }
                    if (!isChatJid(m.key.remoteJid)) continue;
                    rememberGroupSender(m);
                    const details = extractMessageDetails(m);
                    if (!details) continue;
                    const { text, mediaType, quotedText, quotedSender } = details;
                    const remoteJid = m.key.remoteJid;
                    const isGroupMsg = remoteJid.endsWith('@g.us');
                    const resolved = resolveContact(isGroupMsg ? (m.key.participant || remoteJid) : remoteJid, m.pushName);
                    parsedMessages.push({
                        // Borrados y ediciones apuntan al mensaje original (Python decide si existe)
                        revoke_of: details.revokeOf || undefined,
                        edit_of: details.editOf || undefined,
                        message_id: details.editOf || details.revokeOf || m.key.id,
                        chat_jid: remoteJid,
                        sender_jid: m.key.participant || remoteJid,
                        sender_name: m.key.fromMe ? 'Yo' : resolved.name,
                        from_me: Boolean(m.key.fromMe),
                        text: text,
                        media_type: mediaType,
                        media_path: null,
                        media_raw: serializeMediaRaw(m, mediaType),
                        quoted_text: quotedText,
                        quoted_sender: quotedSender,
                        timestamp: Number(m.messageTimestamp || Math.floor(Date.now() / 1000)),
                        // En el historial los mensajes propios traen su estado real (entregado/leído)
                        status: m.key.fromMe ? (ackToStatus(m.status) || 'sent')
                            : (ackToStatus(m.status) === 'read' ? 'read' : 'delivered')
                    });
                }
            }

            dispatchSyncBatch({
                chats: parsedChats,
                contacts: Object.values(contactsStore),
                messages: parsedMessages
            });
        });

        // ── Actualizaciones de Chats ──
        sock.ev.on('chats.upsert', (newChats) => {
            if (!Array.isArray(newChats)) return;
            const batch = newChats.filter(ch => isChatJid(ch.id)).map(ch => {
                if (ch.id.endsWith('@g.us') && ch.name) groupNames[ch.id] = ch.name;
                const resolved = resolveContact(ch.id, ch.name);
                fetchAndSaveAvatar(ch.id);
                return {
                    jid: ch.id,
                    name: resolved.name,
                    phone: resolved.phone,
                    unread_count: ch.unreadCount || 0,
                    last_message_time: ch.conversationTimestamp ? Number(ch.conversationTimestamp) : 0,
                    is_group: ch.id.endsWith('@g.us') ? 1 : 0
                };
            });
            dispatchSyncBatch({ chats: batch, messages: [] });
        });


        // ── Estado de Conexión ──
        sock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;

            if (qr) {
                currentQR = qr;
                try {
                    currentQRDataUrl = await QRCode.toDataURL(qr, { width: 360, margin: 2 });
                    connectionStatus = 'qr_ready';
                    console.log('[Baileys] Código QR generado y listo.');
                    notifyConnectionState('qr_ready', { qr_data_url: currentQRDataUrl });
                } catch (qrErr) {
                    console.error('[Baileys] Error generando DataURL de QR:', qrErr);
                }
            }

            if (connection === 'close') {
                const statusCode = lastDisconnect?.error?.output?.statusCode;
                const loggedOut = statusCode === DisconnectReason.loggedOut;
                connectionStatus = 'disconnected';
                currentQR = null;
                currentQRDataUrl = null;
                connectedPhone = null;
                // Un código de vinculación deja credenciales a medio hacer: si el intento se corta, se empieza de cero
                const pairingCut = pairingPending && !loggedOut && statusCode !== DisconnectReason.restartRequired;
                if (pairingCut) {
                    console.log('[Baileys] Se cortó la vinculación por código; se reinician las credenciales.');
                    pairingPending = false;
                    resetAuthState();
                }
                if (statusCode !== DisconnectReason.restartRequired) pairingMode = false;
                if (loggedOut) {
                    // Se desvinculó desde el celular (o expiró la sesión): credenciales nuevas y QR nuevo.
                    // Antes quedaba "desconectado" para siempre y había que reiniciar la app.
                    console.log('[Baileys] Sesión cerrada desde el celular. Se genera un QR nuevo para volver a vincular.');
                    notifyConnectionState('logged_out');
                    resetAuthState();
                    downloadedAvatars.clear();
                    reconnectAttempts = 0;
                    scheduleReconnect(2000);
                } else {
                    // Reintentos con espera creciente (3 s, 6 s, 12 s… hasta 60 s) para no martillar sin internet
                    const restart = statusCode === DisconnectReason.restartRequired;
                    // Sin vincular todavía (QR vencido): QR nuevo enseguida, sin espera creciente
                    const linking = !state.creds?.registered;
                    const delay = restart ? 500 : linking ? 1000
                        : Math.min(60000, 3000 * 2 ** Math.min(reconnectAttempts, 5));
                    reconnectAttempts++;
                    console.log(`[Baileys] Conexión cerrada (status: ${statusCode}). Reintento en ${Math.round(delay / 1000)} s.`);
                    notifyConnectionState('disconnected', { retry_in: delay, pairing_cut: pairingCut });
                    scheduleReconnect(delay);
                }
            } else if (connection === 'open') {
                reconnectAttempts = 0;
                pairingPending = false;
                pairingMode = false;
                connectionStatus = 'connected';
                currentQR = null;
                currentQRDataUrl = null;
                const user = sock.user;
                connectedPhone = user ? (user.id || '').split(':')[0].replace(/\D/g, '') : null;
                connectedPushName = user ? (user.name || '') : null;

                myPhone = connectedPhone;
                myLid = user?.lid ? user.lid.split(':')[0].replace(/\D/g, '') : null;
                myName = connectedPushName || 'Yo';
                setTimeout(syncGroupList, 5000);   // deja pasar primero la sincronización inicial

                if (myLid && myPhone) {
                    saveContactInfo(myPhone, myLid, `${myName} (Tú)`, myName);
                }

                // Descargar propio avatar
                if (sock.user?.id) {
                    fetchAndSaveAvatar(sock.user.id);
                }

                console.log(`[Baileys] ✨ ¡CONECTADO! +${connectedPhone} (${connectedPushName}) | LID: ${myLid}`);
                notifyConnectionState('connected');
                sock.sendPresenceUpdate(desiredPresence).catch(() => {});
                setTimeout(flushOutbox, 1000);
                // Estados que vencieron mientras no había conexión
                setTimeout(processStatusSchedule, 5000);
                setTimeout(processMessageSchedule, 5000);
                setTimeout(requestStatusHistory, 8000);
            }
        });

        // ── Recibos de mensajes propios: enviado / entregado / leído ──
        sock.ev.on('messages.update', (updates) => {
            for (const u of updates || []) {
                if (!u.key?.id) continue;
                const st = ackToStatus(u.update?.status);
                if (!st) continue;
                if (u.key.fromMe) {
                    reportMessageStatus(u.key.id, st);
                } else if (st === 'read' && isChatJid(u.key.remoteJid)) {
                    // Mensaje recibido que leíste en el celular (u otro dispositivo vinculado)
                    queueReadElsewhere(u.key.remoteJid, u.key.remoteJidAlt, { messageId: u.key.id });
                }
            }
        });

        // ── Chat marcado como leído / no leído desde el celular (sincronización de WhatsApp) ──
        // Baileys solo manda 0 (leído) o -1 (marcado como no leído) desde esa sincronización;
        // los números positivos son sumas de mensajes nuevos y los cuenta la app sola.
        sock.ev.on('chats.update', (updates) => {
            for (const u of updates || []) {
                if (!isChatJid(u.id)) continue;
                if (u.unreadCount === 0) queueReadElsewhere(u.id, null, { all: true });
                else if (u.unreadCount === -1) queueReadElsewhere(u.id, null, { unread: true });
            }
        });
        sock.ev.on('message-receipt.update', (receipts) => {
            for (const r of receipts || []) {
                if (r.key?.remoteJid === 'status@broadcast') {
                    noteStatusView(r);
                    continue;
                }
                // En grupos el recibo es de un solo miembro: no marcamos "leído" para todo el grupo
                if (!r.key?.fromMe || !r.key?.id || (r.key.remoteJid || '').endsWith('@g.us')) continue;
                const rec = r.receipt || {};
                if (rec.readTimestamp || rec.playedTimestamp) reportMessageStatus(r.key.id, 'read');
                else if (rec.receiptTimestamp) reportMessageStatus(r.key.id, 'delivered');
            }
        });

        // ── Reacciones con emoji (texto vacío = reacción retirada) ──
        sock.ev.on('messages.reaction', (reactions) => {
            for (const r of reactions || []) {
                try {
                    const key = r.key || {};
                    if (!key.id || !isChatJid(key.remoteJid)) continue;
                    const rk = r.reaction?.key || {};
                    const isGroup = key.remoteJid.endsWith('@g.us');
                    const reactor = rk.fromMe ? 'me' : (rk.participant || (isGroup ? '' : key.remoteJid));
                    if (!reactor) continue;
                    const tsMs = Number(r.reaction?.senderTimestampMs || 0);
                    fetch(PYTHON_REACTION_URL, {
                        method: 'POST',
                        headers: AUTH_HEADERS,
                        body: JSON.stringify({
                            message_id: key.id,
                            chat_jid: key.remoteJid,
                            reactor_jid: reactor,
                            emoji: r.reaction?.text || '',
                            timestamp: tsMs ? Math.floor(tsMs / 1000) : Math.floor(Date.now() / 1000)
                        })
                    }).catch(err => console.error('[Baileys] Error enviando reacción:', err.message));
                } catch (e) {
                    console.error('[Baileys] Error procesando reacción:', e);
                }
            }
        });

        // ── "En línea" / "escribiendo…" de los contactos (solo de los chats que la app pidió seguir) ──
        sock.ev.on('presence.update', ({ id, presences }) => {
            try {
                if (!isIndividualJid(id)) return;
                const chatJid = preferPhoneJid(id);
                for (const [participant, p] of Object.entries(presences || {})) {
                    postEvent('presence', {
                        chat_jid: chatJid,
                        participant,
                        presence: p?.lastKnownPresence || 'unavailable',
                        last_seen: p?.lastSeen || null
                    });
                }
            } catch (_) {}
        });

        // ── Llamadas: aviso al sonar y registro de las perdidas (se atienden en el celular) ──
        sock.ev.on('call', async (calls) => {
            for (const c of calls || []) {
                try {
                    if (c.isGroup || !c.from) continue;
                    const fromJid = preferPhoneJid(c.from, c.callerPn ? `${String(c.callerPn).split('@')[0]}@s.whatsapp.net` : null);
                    if (!isIndividualJid(fromJid)) continue;
                    const resolved = resolveContact(fromJid);
                    if (resolved.isSelf) continue;
                    console.log(`[Baileys] 📞 Llamada ${c.status} de ${resolved.name} (${c.isVideo ? 'video' : 'voz'})`);
                    postEvent('call', {
                        call_id: c.id, chat_jid: fromJid, name: resolved.name, phone: resolved.phone,
                        status: c.status, is_video: Boolean(c.isVideo), offline: Boolean(c.offline)
                    });
                    // Llamada perdida (nadie atendió o colgaron antes): queda en el chat como en la app oficial
                    const seen = callTracker.get(c.id) || {};
                    if (c.status === 'accept' || c.status === 'reject') seen.handled = true;
                    callTracker.set(c.id, seen);
                    if (callTracker.size > 200) callTracker.delete(callTracker.keys().next().value);
                    const ended = c.status === 'timeout' || c.status === 'terminate';
                    if (ended && !seen.handled && !seen.logged) {
                        seen.logged = true;
                        await deliverReliably(PYTHON_WEBHOOK_URL, {
                            message_id: `call_${c.id}`,
                            upsert_type: 'notify',
                            chat_jid: fromJid,
                            sender_jid: fromJid,
                            push_name: resolved.name,
                            phone: resolved.phone,
                            from_me: false,
                            text: c.isVideo ? '📹 Videollamada perdida' : '📞 Llamada de voz perdida',
                            media_type: 'text',
                            timestamp: Math.floor((c.date ? new Date(c.date).getTime() : Date.now()) / 1000),
                            is_call: true
                        });
                    }
                } catch (e) {
                    console.error('[Baileys] Error procesando llamada:', e.message);
                }
            }
        });

        // ── Mensajes Entrantes y Salientes en Tiempo Real ──
        sock.ev.on('messages.upsert', async ({ messages, type }) => {
            for (const msg of messages) {
                if (msg.key.remoteJid === 'status@broadcast') {
                    if (msg.key.fromMe) trackMyStatus(msg);
                    else trackContactStatus(msg, type === 'notify');
                    continue;
                }
                if (!isChatJid(msg.key.remoteJid)) continue;
                rememberGroupSender(msg);
                // Voto de una encuesta: se descifra y se avisa a la app con el conteo nuevo (no es un mensaje)
                if (extras.onPollVote(msg)) continue;
                console.log(`[Baileys] Recibido (${type}): [${msg.key.remoteJid}] id=${msg.key.id} fromMe=${Boolean(msg.key.fromMe)}`);

                const fromMe = Boolean(msg.key.fromMe);
                // Un mismo contacto puede llegar por LID o por teléfono: se unifica al teléfono para no duplicar el chat
                const remoteJid = preferPhoneJid(msg.key.remoteJid, msg.key.remoteJidAlt);

                if (msg.key.id && sentBotMessageIds.has(msg.key.id)) {
                    continue;
                }

                const details = extractMessageDetails(msg);
                if (!details) {
                    // Antes se descartaba sin dejar rastro: ahora queda registrado (stub sin descifrar, reacción, etc.)
                    console.log(`[Baileys] Descartado (${type}): [${remoteJid}] id=${msg.key.id} fromMe=${fromMe} ${messageKinds(msg)}`);
                    continue;
                }
                const { text, mediaType, innerMessage, quotedText, quotedSender } = details;
                const pollView = details.poll ? extras.trackPoll(msg, details, remoteJid) : null;

                // Extraer correspondencia de teléfono y LID si Baileys la provee
                // Baileys 7 entrega el identificador alterno (LID <-> teléfono) en remoteJidAlt / participantAlt
                const altJids = [msg.key?.remoteJidAlt, msg.key?.participantAlt, msg.key?.senderPn, msg.key?.participantPn];
                const idJids = [remoteJid, msg.key?.participant];
                for (const alt of altJids) {
                    if (!alt) continue;
                    for (const id of idJids) {
                        if (!id) continue;
                        if (alt.endsWith('@s.whatsapp.net') && id.endsWith('@lid')) saveContactInfo(alt, id);
                        else if (alt.endsWith('@lid') && id.endsWith('@s.whatsapp.net')) saveContactInfo(id, alt);
                    }
                }

                const pushName = msg.pushName || '';
                // Guardar pushName ÚNICAMENTE si no es nuestro propio usuario (como perfil: no pisa la agenda)
                if (pushName && remoteJid && !fromMe && pushName !== myName) {
                    reportRenamedContacts([saveContactInfo(remoteJid, null, '', pushName)]);
                }

                // Los archivos NO se descargan al llegar el mensaje: se bajan solo cuando el usuario
                // hace clic (Descargar / Abrir / reproducir) vía /media/download usando media_raw.
                const mediaPath = null;

                const isGroupMsg = remoteJid.endsWith('@g.us');
                const resolved = resolveContact(isGroupMsg ? (msg.key.participant || remoteJid) : remoteJid, pushName);
                if (isGroupMsg) queueGroupName(remoteJid);
                fetchAndSaveAvatar(remoteJid);

                console.log(`[Baileys] Mensaje: [${remoteJid}] (de ${resolved.name}): ${text || `[${mediaType}]`}`);

                try {
                    const payload = {
                        // Borrado o edición: se aplica sobre el mensaje original, no aparece uno nuevo
                        message_id: details.editOf || details.revokeOf || msg.key.id,
                        edited: Boolean(details.editOf),
                        revoked: Boolean(details.revokeOf),
                        upsert_type: type,
                        // Al reconectar, Baileys junta el "leído en el celular" dentro del propio mensaje
                        already_read: !fromMe && ackToStatus(msg.status) === 'read',
                        chat_jid: remoteJid,
                        sender_jid: msg.key.participant || remoteJid,
                        push_name: (isGroupMsg && fromMe) ? '' : resolved.name,
                        chat_name: isGroupMsg ? (groupNames[remoteJid] || null) : null,
                        phone: isGroupMsg ? null : resolved.phone,
                        from_me: fromMe,
                        text: text,
                        media_type: mediaType,
                        media_path: mediaPath,
                        media_raw: serializeMediaRaw(msg, mediaType),
                        quoted_text: quotedText,
                        quoted_sender: quotedSender,
                        poll: pollView,
                        timestamp: Number(msg.messageTimestamp || Math.floor(Date.now() / 1000)),
                        is_group: remoteJid.endsWith('@g.us')
                    };

                    // Reintenta y, si la app no responde, queda en la bandeja de salida (no se pierde)
                    await deliverReliably(PYTHON_WEBHOOK_URL, payload);
                    // Respuestas automáticas (corren acá para que funcionen con la ventana cerrada)
                    extras.onIncoming({ jid: remoteJid, fromMe, isGroup: isGroupMsg, type, mediaType, timestamp: payload.timestamp,
                        revoked: payload.revoked, edited: payload.edited, isCall: false });
                } catch (e) {
                    console.error('[Baileys] Error despachando mensaje entrante:', e);
                }
            }
        });

    } catch (e) {
        console.error('[Baileys] Error fatal en initWhatsApp:', e);
        connectionStatus = 'disconnected';
        notifyConnectionState('disconnected', { error: e.message });
        // Antes un error acá (sin internet al arrancar, archivo de sesión ocupado) dejaba el gateway muerto
        const delay = Math.min(60000, 5000 * 2 ** Math.min(reconnectAttempts, 4));
        reconnectAttempts++;
        scheduleReconnect(delay);
    }
}

// ─── ESTADOS PROGRAMADOS ─────────────────────────────────────────────────────
// La cola vive en el gateway (no en la ventana) para que los estados se publiquen aunque se cierre Advanced WhatsApp.
// Un estado vencido mientras la PC estuvo apagada o sin conexión se publica tarde solo dentro del margen;
// pasado ese margen queda como "vencido" para no subir un "buen día" a la noche.
const SCHEDULE_FILE = path.join(USER_DATA_DIR, 'estados_programados.json');
const SCHEDULE_MEDIA_DIR = path.join(MEDIA_DIR, 'estados_programados');
const SCHEDULE_LATE_GRACE = 6 * 3600;       // segundos de atraso tolerados
const SCHEDULE_MAX_ATTEMPTS = 3;
const SCHEDULE_RETRY_DELAY = 120;           // segundos entre reintentos
if (!fs.existsSync(SCHEDULE_MEDIA_DIR)) fs.mkdirSync(SCHEDULE_MEDIA_DIR, { recursive: true });

let scheduledStatuses = [];
let isPublishingStatus = false;
try {
    if (fs.existsSync(SCHEDULE_FILE)) scheduledStatuses = JSON.parse(fs.readFileSync(SCHEDULE_FILE, 'utf-8'));
    // Un estado que quedó "publicando" al cerrarse el gateway vuelve a la cola
    for (const s of scheduledStatuses) if (s.state === 'sending') s.state = 'pending';
} catch (e) {
    console.error('[Estados] No se pudo leer la cola de estados programados:', e.message);
}

function saveSchedule() {
    try {
        const tmp = `${SCHEDULE_FILE}.tmp`;
        fs.writeFileSync(tmp, JSON.stringify(scheduledStatuses, null, 2), 'utf-8');
        fs.renameSync(tmp, SCHEDULE_FILE);
    } catch (e) {
        console.error('[Estados] No se pudo guardar la cola:', e.message);
    }
}

// Destinatarios: los contactos de la agenda (con nombre guardado, no el de perfil) más uno mismo,
// para que el estado también aparezca en el celular. Sin agenda sincronizada, todos los contactos conocidos.
function statusAudience() {
    const collect = (agendaOnly) => {
        const phones = new Set();
        for (const c of Object.values(contactsStore)) {
            if (!c?.phone) continue;
            if (agendaOnly && (c.nameFromPush || !String(c.name || '').trim())) continue;
            const p = String(c.phone).replace(/\D/g, '');
            if (p.length >= 7 && p !== myPhone) phones.add(p);
        }
        return phones;
    };
    let phones = collect(true);
    if (!phones.size) phones = collect(false);
    const list = [...phones].map(p => `${p}@s.whatsapp.net`);
    if (myPhone) list.push(`${myPhone}@s.whatsapp.net`);
    return list;
}

async function publishStatus(item) {
    const statusJidList = statusAudience();
    if (statusJidList.length < 2) throw new Error('No hay contactos para enviar el estado');
    const opts = { statusJidList, broadcast: true };
    let content;
    if (item.type === 'text') {
        content = { text: item.text };
        opts.backgroundColor = item.background || '#005c4b';
        opts.font = Number.isInteger(item.font) ? item.font : 0;
    } else {
        if (!fs.existsSync(item.media_path)) throw new Error(`Archivo no encontrado: ${item.media_path}`);
        const buffer = fs.readFileSync(item.media_path);
        content = item.type === 'video'
            ? { video: buffer, caption: item.text || undefined }
            : { image: buffer, caption: item.text || undefined };
    }
    const result = await sock.sendMessage('status@broadcast', content, opts);
    return { messageId: result?.key?.id || null, recipients: statusJidList.length };
}

async function runStatus(item) {
    item.state = 'sending';
    item.attempts = (item.attempts || 0) + 1;
    saveSchedule();
    try {
        const { messageId, recipients } = await publishStatus(item);
        item.state = 'sent';
        item.sent_at = Math.floor(Date.now() / 1000);
        item.message_id = messageId;
        item.recipients = recipients;
        item.error = null;
        console.log(`[Estados] ✅ Publicado ${item.id} (${item.type}) para ${recipients} contactos.`);
    } catch (e) {
        item.error = e.message || String(e);
        item.state = item.attempts >= SCHEDULE_MAX_ATTEMPTS ? 'failed' : 'pending';
        item.retry_at = Math.floor(Date.now() / 1000) + SCHEDULE_RETRY_DELAY;
        console.error(`[Estados] ❌ Falló ${item.id} (intento ${item.attempts}):`, item.error);
    }
    saveSchedule();
}

// Revisa la cola cada 20 s; publica de a uno para no saturar la conexión
async function processStatusSchedule() {
    if (isPublishingStatus || connectionStatus !== 'connected' || !sock) return;
    isPublishingStatus = true;
    try {
        const now = Math.floor(Date.now() / 1000);
        const due = scheduledStatuses
            .filter(s => s.state === 'pending' && s.at <= now && (!s.retry_at || s.retry_at <= now))
            .sort((a, b) => a.at - b.at);
        for (const item of due) {
            if (connectionStatus !== 'connected') break;
            if (now - item.at > SCHEDULE_LATE_GRACE && !item.attempts) {
                item.state = 'expired';
                item.error = 'La PC estaba apagada o sin conexión a la hora programada';
                saveSchedule();
                console.log(`[Estados] ⌛ Vencido ${item.id}: programado ${new Date(item.at * 1000).toISOString()}`);
                continue;
            }
            await runStatus(item);
        }
    } finally {
        isPublishingStatus = false;
    }
}

// ─── MENSAJES PROGRAMADOS ────────────────────────────────────────────────────
// Misma idea que los estados: la cola vive en el gateway y aplica el mismo margen de atraso.
// En vez de revisar la cola cada tantos segundos hay un único temporizador apuntado al próximo envío,
// así sale en hora y sin trabajo de fondo. El tope de un minuto cubre cambios de hora y suspensiones.
const MESSAGE_SCHEDULE_FILE = path.join(USER_DATA_DIR, 'mensajes_programados.json');
const MESSAGE_SCHEDULE_MEDIA_DIR = path.join(MEDIA_DIR, 'mensajes_programados');
const MESSAGE_MEDIA_KEYS = ['document_path', 'image_path', 'video_path', 'audio_path', 'sticker_path'];   // prioridad de /send
const MESSAGE_SEND_FIELDS = ['image_thumb', 'document_name', 'mimetype', 'audio_seconds', 'audio_waveform', 'quoted'];
const MESSAGE_TIMER_MAX_MS = 60000;
const MESSAGE_HISTORY_MAX = 100;
const MESSAGE_DONE_STATES = ['sent', 'failed', 'expired', 'canceled'];
if (!fs.existsSync(MESSAGE_SCHEDULE_MEDIA_DIR)) fs.mkdirSync(MESSAGE_SCHEDULE_MEDIA_DIR, { recursive: true });

let scheduledMessages = [];
let isSendingScheduledMessage = false;
let messageTimer = null;
try {
    if (fs.existsSync(MESSAGE_SCHEDULE_FILE)) scheduledMessages = JSON.parse(fs.readFileSync(MESSAGE_SCHEDULE_FILE, 'utf-8'));
    // Un mensaje que quedó "enviando" al cerrarse el gateway vuelve a la cola (conserva su id: WhatsApp no lo duplica)
    for (const m of scheduledMessages) if (m.state === 'sending') m.state = 'pending';
} catch (e) {
    console.error('[Programados] No se pudo leer la cola:', e.message);
}

function saveMessageSchedule() {
    try {
        const tmp = `${MESSAGE_SCHEDULE_FILE}.tmp`;
        fs.writeFileSync(tmp, JSON.stringify(scheduledMessages), 'utf-8');
        fs.renameSync(tmp, MESSAGE_SCHEDULE_FILE);
    } catch (e) {
        console.error('[Programados] No se pudo guardar la cola:', e.message);
    }
}

// El archivo de un mensaje ya enviado no se borra: lo sigue mostrando el chat
function removeScheduledMedia(item) {
    if (item.media_path && item.state !== 'sent') fs.rm(item.media_path, { force: true }, () => {});
}

function pruneMessageHistory() {
    const done = scheduledMessages.filter(m => MESSAGE_DONE_STATES.includes(m.state));
    if (done.length <= MESSAGE_HISTORY_MAX) return;
    const drop = new Set(done.sort((a, b) => a.at - b.at).slice(0, done.length - MESSAGE_HISTORY_MAX));
    drop.forEach(removeScheduledMedia);
    scheduledMessages = scheduledMessages.filter(m => !drop.has(m));
}

const scheduledDueAt = (m) => Math.max(m.at, m.retry_at || 0);

// Mensaje con repetición: tras salir (o vencer) vuelve a la cola para la próxima fecha, con un id de WhatsApp nuevo
function advanceRepeat(item) {
    if (!item.repeat) return false;
    const next = extras.nextRepeatAt(item.anchor_at || item.at, item.repeat, Math.floor(Date.now() / 1000));
    if (!next) return false;
    item.at = next;
    item.state = 'pending';
    item.attempts = 0;
    item.wa_id = null;
    item.retry_at = null;
    item.error = null;
    return true;
}

function armMessageTimer() {
    clearTimeout(messageTimer);
    messageTimer = null;
    // Sin conexión no hace falta temporizador: al conectar se revisa la cola
    if (isSendingScheduledMessage || connectionStatus !== 'connected') return;
    let next = Infinity;
    for (const m of scheduledMessages) if (m.state === 'pending') next = Math.min(next, scheduledDueAt(m));
    if (next === Infinity) return;
    messageTimer = setTimeout(processMessageSchedule, Math.min(Math.max(0, next * 1000 - Date.now()), MESSAGE_TIMER_MAX_MS));
}

function reportScheduledMessage(item) {
    deliverReliably(`${PYTHON_API}/scheduled`, {
        id: item.id, state: item.state, to: item.to, chat_name: item.chat_name, type: item.type,
        text: item.text, source_name: item.source_name, at: item.at, error: item.error || null
    }).catch(err => console.error('[Programados] Error avisando a la app:', err.message));
}

async function runScheduledMessage(item) {
    item.state = 'sending';
    item.attempts = (item.attempts || 0) + 1;
    let delivered = false;
    // Un id fijo por mensaje: si un reintento repite un envío que sí había salido, WhatsApp no lo muestra dos veces
    item.wa_id = item.wa_id || generateMessageIDV2(sock?.user?.id);
    saveMessageSchedule();
    try {
        const body = { ...item.send, to: item.to, text: item.text };
        const result = await sendWhatsApp(body, { messageId: item.wa_id });
        const now = Math.floor(Date.now() / 1000);
        delivered = true;
        item.state = 'sent';
        item.sent_at = now;
        item.message_id = result?.key?.id || item.wa_id;
        item.error = null;
        item.retry_at = null;
        if (!advanceRepeat(item)) item.send = null;   // miniatura y onda ya no hacen falta (una repetición los conserva)
        console.log(`[Programados] ✅ Enviado ${item.id} (${item.type}) a ${item.to}`);
        // El gateway descarta el eco de lo que envía: la app se entera por acá
        deliverReliably(PYTHON_WEBHOOK_URL, {
            message_id: item.message_id,
            upsert_type: 'append',
            chat_jid: item.to,
            sender_jid: 'me',
            push_name: 'Yo',
            chat_name: item.chat_name || null,
            from_me: true,
            text: item.type === 'document' ? (item.text || item.source_name || '') : item.text,
            media_type: item.type,
            media_path: item.media_path,
            quoted_text: item.quoted_text || null,
            quoted_sender: item.quoted_sender || null,
            timestamp: now,
            is_group: item.to.endsWith('@g.us')
        }).catch(err => console.error('[Programados] Error avisando el envío a la app:', err.message));
    } catch (e) {
        item.error = e.message || String(e);
        item.retry_at = Math.floor(Date.now() / 1000) + SCHEDULE_RETRY_DELAY;
        // Si se cortó la conexión no cuenta como intento: sale al volver (o vence, si pasó el margen)
        if (connectionStatus !== 'connected') item.attempts -= 1;
        item.state = e.httpStatus === 404 || item.attempts >= SCHEDULE_MAX_ATTEMPTS ? 'failed' : 'pending';
        console.error(`[Programados] ❌ Falló ${item.id} (intento ${item.attempts}):`, item.error);
        // Una repetición que falló se avisa y sigue con la próxima fecha
        if (item.state === 'failed' && item.repeat) {
            reportScheduledMessage(item);
            advanceRepeat(item);
        }
    }
    pruneMessageHistory();
    saveMessageSchedule();
    if (item.state !== 'pending') reportScheduledMessage(item);
    else if (item.repeat && delivered) reportScheduledMessage({ ...item, state: 'sent', at: item.sent_at });
}

// De a uno y en orden. La cola se vuelve a mirar tras cada envío: lo programado o cancelado durante una subida larga cuenta.
async function processMessageSchedule() {
    if (isSendingScheduledMessage) return;
    if (connectionStatus !== 'connected' || !sock) return;
    isSendingScheduledMessage = true;
    try {
        while (connectionStatus === 'connected') {
            const now = Math.floor(Date.now() / 1000);
            let item = null;
            for (const m of scheduledMessages) {
                if (m.state === 'pending' && scheduledDueAt(m) <= now && (!item || m.at < item.at)) item = m;
            }
            if (!item) break;
            if (now - item.at > SCHEDULE_LATE_GRACE && !item.attempts) {
                if (advanceRepeat(item)) {   // una repetición vencida se salta y espera la próxima
                    saveMessageSchedule();
                    continue;
                }
                item.state = 'expired';
                item.error = 'El equipo estaba apagado o sin conexión a la hora programada';
                pruneMessageHistory();
                saveMessageSchedule();
                reportScheduledMessage(item);
                console.log(`[Programados] ⌛ Vencido ${item.id}: programado ${new Date(item.at * 1000).toISOString()}`);
                continue;
            }
            await runScheduledMessage(item);
        }
    } finally {
        isSendingScheduledMessage = false;
        armMessageTimer();
    }
}

// ─── MIS ESTADOS ACTIVOS (últimas 24 h) ──────────────────────────────────────
// WhatsApp copia a los dispositivos vinculados cada estado propio (publicado desde el celular o desde aquí)
// y avisa con un recibo cada vez que un contacto lo ve. Se guardan para listarlos, ver quién los vio y borrarlos.
const MY_STATUS_FILE = path.join(USER_DATA_DIR, 'mis_estados.json');
const STATUS_TTL = 24 * 3600;
let myStatuses = {};
let myStatusSaveTimer = null;
try {
    if (fs.existsSync(MY_STATUS_FILE)) myStatuses = JSON.parse(fs.readFileSync(MY_STATUS_FILE, 'utf-8'));
} catch (e) {
    console.error('[Estados] No se pudo leer mis_estados.json:', e.message);
}

function pruneMyStatuses() {
    const limit = Math.floor(Date.now() / 1000) - STATUS_TTL;
    for (const [id, s] of Object.entries(myStatuses)) if (s.timestamp < limit) delete myStatuses[id];
}

function saveMyStatuses() {
    clearTimeout(myStatusSaveTimer);
    myStatusSaveTimer = setTimeout(() => {
        pruneMyStatuses();
        try {
            fs.writeFileSync(MY_STATUS_FILE, JSON.stringify(myStatuses), 'utf-8');
        } catch (e) {
            console.error('[Estados] No se pudo guardar mis_estados.json:', e.message);
        }
    }, 1000);
}

function argbToHex(argb) {
    if (argb === undefined || argb === null) return null;
    return '#' + (Number(argb) >>> 0).toString(16).padStart(8, '0').slice(2);
}

function trackMyStatus(msg) {
    const details = extractMessageDetails(msg);
    if (!details || !msg.key?.id) {
        const kind = msg.message ? Object.keys(msg.message)[0] : 'sin contenido';
        console.log(`[Estados] Estado propio sin contenido legible ${msg.key?.id} (${kind})`);
        return;
    }
    const ts = Number(msg.messageTimestamp || Math.floor(Date.now() / 1000));
    if (ts < Math.floor(Date.now() / 1000) - STATUS_TTL) return;
    const { text, mediaType, innerMessage: m } = details;
    if (mediaType === 'revoked') {
        const revokedId = m.protocolMessage?.key?.id;
        if (revokedId && myStatuses[revokedId]) {
            delete myStatuses[revokedId];
            saveMyStatuses();
        }
        return;
    }
    const media = m.imageMessage || m.videoMessage;
    const thumb = media?.jpegThumbnail ? Buffer.from(media.jpegThumbnail).toString('base64') : null;
    const prev = myStatuses[msg.key.id] || {};
    myStatuses[msg.key.id] = {
        id: msg.key.id,
        type: mediaType === 'image' || mediaType === 'video' ? mediaType : 'text',
        text,
        background: argbToHex(m.extendedTextMessage?.backgroundArgb),
        timestamp: ts,
        thumb,
        media_raw: serializeMediaRaw(msg, mediaType),
        viewers: prev.viewers || {}
    };
    if (!prev.id) console.log(`[Estados] 📥 Estado propio registrado ${msg.key.id} (${myStatuses[msg.key.id].type}, ${new Date(ts * 1000).toISOString()})`);
    saveMyStatuses();
}

// Los estados publicados antes de que corriera este gateway no se vuelven a recibir solos: se le pide al
// celular el historial del chat de estados. Si responde, llegan por messaging-history.set (ON_DEMAND).
async function requestStatusHistory() {
    if (connectionStatus !== 'connected' || !sock?.fetchMessageHistory) return false;
    const known = Object.values(myStatuses).sort((a, b) => a.timestamp - b.timestamp)[0];
    const anchor = known
        ? { key: { remoteJid: 'status@broadcast', fromMe: true, id: known.id }, ts: known.timestamp * 1000 }
        : { key: { remoteJid: 'status@broadcast', fromMe: true, id: crypto.randomBytes(10).toString('hex').toUpperCase() }, ts: Date.now() };
    try {
        await sock.fetchMessageHistory(50, anchor.key, anchor.ts);
        console.log('[Estados] 🔎 Pedido al celular el historial de estados (respuesta llega como historial ON_DEMAND).');
        return true;
    } catch (e) {
        console.error('[Estados] No se pudo pedir el historial de estados:', e.message);
        return false;
    }
}

function noteStatusView(r) {
    const s = myStatuses[r.key?.id];
    const rec = r.receipt || {};
    const viewer = rec.userJid || r.key?.participant;
    if (!s || !viewer || !(rec.readTimestamp || rec.playedTimestamp)) return;
    const resolved = resolveContact(viewer);
    const key = resolved.phone || viewer.split('@')[0].split(':')[0];
    if (resolved.isSelf || key === myPhone || s.viewers[key]) return;
    s.viewers[key] = { name: resolved.name, ts: Number(rec.readTimestamp || rec.playedTimestamp) };
    saveMyStatuses();
}

// ─── ESTADOS DE LOS CONTACTOS (últimas 24 h) ─────────────────────────────────
// WhatsApp reparte a los dispositivos vinculados los estados de los contactos. Se guardan con su miniatura y
// las claves del archivo (la foto o el video se baja recién al abrirlo, como en la app oficial).
const CONTACT_STATUS_FILE = path.join(USER_DATA_DIR, 'estados_contactos.json');
let contactStatuses = {};
let contactStatusSaveTimer = null;
try {
    if (fs.existsSync(CONTACT_STATUS_FILE)) contactStatuses = JSON.parse(fs.readFileSync(CONTACT_STATUS_FILE, 'utf-8')) || {};
} catch (e) {
    console.error('[Estados] No se pudo leer estados_contactos.json:', e.message);
}

function contactStatusMediaPath(s) {
    const ext = s.type === 'video' ? 'mp4' : s.type === 'audio' ? 'ogg' : 'jpg';
    return path.join(MEDIA_DIR, 'estados_contactos', `${s.id}.${ext}`);
}

function pruneContactStatuses() {
    const limit = Math.floor(Date.now() / 1000) - STATUS_TTL;
    let removed = 0;
    for (const [id, s] of Object.entries(contactStatuses)) {
        if (s.timestamp < limit) {
            fs.rm(contactStatusMediaPath(s), { force: true }, () => {});
            delete contactStatuses[id];
            removed++;
        }
    }
    return removed;
}

function saveContactStatuses() {
    clearTimeout(contactStatusSaveTimer);
    contactStatusSaveTimer = setTimeout(() => {
        pruneContactStatuses();
        try {
            const tmp = `${CONTACT_STATUS_FILE}.tmp`;
            fs.writeFileSync(tmp, JSON.stringify(contactStatuses), 'utf-8');
            fs.renameSync(tmp, CONTACT_STATUS_FILE);
        } catch (e) {
            console.error('[Estados] No se pudo guardar estados_contactos.json:', e.message);
        }
    }, 1500);
}

function trackContactStatus(msg, live) {
    try {
        const id = msg.key?.id;
        if (!id) return;
        const now = Math.floor(Date.now() / 1000);
        const ts = Number(msg.messageTimestamp || now);
        if (ts < now - STATUS_TTL) return;
        const details = extractMessageDetails(msg);
        if (!details) return;
        if (details.mediaType === 'revoked') {
            const rid = details.revokeOf;
            if (rid && contactStatuses[rid]) {
                fs.rm(contactStatusMediaPath(contactStatuses[rid]), { force: true }, () => {});
                const sender = contactStatuses[rid].sender;
                delete contactStatuses[rid];
                saveContactStatuses();
                postEvent('contact_status', { sender, removed: rid });
            }
            return;
        }
        const type = ['image', 'video', 'audio'].includes(details.mediaType) ? details.mediaType : 'text';
        if (type === 'text' && !details.text) return;
        const participant = msg.key.participant || msg.participant;
        if (!participant) return;
        const alt = [msg.key.participantAlt, msg.key.remoteJidAlt, msg.key.participantPn]
            .find(j => typeof j === 'string' && j.endsWith('@s.whatsapp.net'));
        if (alt && participant.endsWith('@lid')) saveContactInfo(alt, participant);
        const senderJid = preferPhoneJid(participant, alt);
        const resolved = resolveContact(senderJid, msg.pushName || '');
        if (resolved.isSelf) return;
        if (msg.pushName && !msg.key.fromMe) saveContactInfo(senderJid, null, '', msg.pushName);

        const m = details.innerMessage;
        const media = m.imageMessage || m.videoMessage;
        const prev = contactStatuses[id] || {};
        contactStatuses[id] = {
            id,
            sender: senderJid.split('@')[0].split(':')[0],
            sender_jid: senderJid,
            participant,
            name: resolved.name,
            type,
            text: details.text,
            background: argbToHex(m.extendedTextMessage?.backgroundArgb),
            text_color: argbToHex(m.extendedTextMessage?.textArgb),
            font: m.extendedTextMessage?.font ?? null,
            timestamp: ts,
            seconds: m.videoMessage?.seconds || m.audioMessage?.seconds || null,
            thumb: media?.jpegThumbnail ? Buffer.from(media.jpegThumbnail).toString('base64') : null,
            media_raw: type === 'text' ? null : serializeMediaRaw(msg, type),
            seen: Boolean(prev.seen)
        };
        fetchAndSaveAvatar(senderJid);
        saveContactStatuses();
        if (!prev.id) {
            console.log(`[Estados] 👀 Estado de ${resolved.name} (${type})`);
            postEvent('contact_status', { sender: contactStatuses[id].sender, name: resolved.name, id, live: Boolean(live) });
        }
    } catch (e) {
        console.error('[Estados] Error registrando estado de contacto:', e.message);
    }
}

// ─── API REST DE CONTROL ─────────────────────────────────────────────────────

// Estados de contactos agrupados por persona: primero quien tiene estados sin ver, del más reciente al más viejo
app.get('/contact-statuses', (req, res) => {
    pruneContactStatuses();
    const groups = new Map();
    for (const s of Object.values(contactStatuses)) {
        const g = groups.get(s.sender) || { sender: s.sender, sender_jid: s.sender_jid, name: s.name, items: [] };
        const { media_raw, participant, ...pub } = s;
        g.items.push({ ...pub, has_media: Boolean(media_raw) });
        // El nombre más reciente gana (puede haberse guardado en la agenda después)
        g.name = resolveContact(s.sender_jid).name || g.name;
        groups.set(s.sender, g);
    }
    const contacts = [...groups.values()].map(g => {
        g.items.sort((a, b) => a.timestamp - b.timestamp);
        g.last_ts = g.items[g.items.length - 1].timestamp;
        g.unseen = g.items.filter(i => !i.seen).length;
        g.avatar_path = avatarPathFor(g.sender_jid);
        return g;
    });
    contacts.sort((a, b) => (Boolean(b.unseen) - Boolean(a.unseen)) || (b.last_ts - a.last_ts));
    res.json({ contacts });
});

// Baja la foto, el video o el audio de un estado de contacto
app.post('/contact-statuses/media', async (req, res) => {
    try {
        const s = contactStatuses[req.body?.id];
        if (!s) return res.status(404).json({ error: 'Ese estado ya no está disponible (pasaron 24 h o lo borró).' });
        if (!s.media_raw) return res.status(404).json({ error: 'Ese estado no tiene archivo' });
        const mediaPath = contactStatusMediaPath(s);
        if (!fs.existsSync(mediaPath)) {
            fs.mkdirSync(path.dirname(mediaPath), { recursive: true });
            const buffer = await downloadWithReupload(JSON.parse(s.media_raw, BufferJSON.reviver));
            fs.writeFileSync(mediaPath, buffer);
        }
        res.json({ success: true, media_path: mediaPath });
    } catch (e) {
        console.error('[Estados] Error bajando estado de contacto:', e.message);
        res.status(502).json({ error: e.message || 'No se pudo descargar' });
    }
});

// Marca un estado como visto. Con receipt=true el contacto ve que lo viste (igual que en el celular).
app.post('/contact-statuses/seen', async (req, res) => {
    try {
        const ids = Array.isArray(req.body?.ids) ? req.body.ids : [req.body?.id];
        const keys = [];
        for (const id of ids) {
            const s = contactStatuses[id];
            if (!s) continue;
            if (!s.seen) keys.push({ remoteJid: 'status@broadcast', id, participant: s.participant, fromMe: false });
            s.seen = true;
        }
        saveContactStatuses();
        if (req.body?.receipt && keys.length && sock && connectionStatus === 'connected') {
            await sock.readMessages(keys).catch(err => console.error('[Estados] No se pudo enviar el visto:', err.message));
        }
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Primer plano / segundo plano de la ventana: "en línea" solo mientras se usa la app
app.post('/presence', async (req, res) => {
    desiredPresence = req.body?.available ? 'available' : 'unavailable';
    if (sock && connectionStatus === 'connected') {
        await sock.sendPresenceUpdate(desiredPresence).catch(() => {});
    }
    res.json({ success: true, presence: desiredPresence });
});

// "Escribiendo…" hacia un contacto y suscripción a su "en línea" al abrir su chat
app.post('/chat-presence', async (req, res) => {
    try {
        const { to, state, subscribe } = req.body || {};
        if (!to || !isIndividualJid(to)) return res.json({ success: true, skipped: true });
        if (!sock || connectionStatus !== 'connected') return res.status(503).json({ error: 'WhatsApp no está conectado.' });
        if (subscribe) await sock.presenceSubscribe(to).catch(() => {});
        if (state && ['composing', 'recording', 'paused'].includes(state)) {
            await sock.sendPresenceUpdate(state, to).catch(() => {});
        }
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/my-statuses', (req, res) => {
    pruneMyStatuses();
    const items = Object.values(myStatuses)
        .sort((a, b) => b.timestamp - a.timestamp)
        .map(({ media_raw, viewers, ...s }) => ({
            ...s,
            has_media: Boolean(media_raw),
            views: Object.keys(viewers).length,
            viewers: Object.values(viewers).sort((a, b) => b.ts - a.ts)
        }));
    res.json({ items });
});

// Vuelve a pedirle al celular los estados de las últimas 24 h
app.post('/my-statuses/sync', async (req, res) => {
    const ok = await requestStatusHistory();
    res.json({ success: ok, error: ok ? undefined : 'WhatsApp no está conectado o el pedido falló' });
});

// Descarga la foto o el video de uno de mis estados (para verlo en grande)
app.post('/my-statuses/media', async (req, res) => {
    try {
        const s = myStatuses[req.body?.id];
        if (!s?.media_raw) return res.status(404).json({ error: 'Ese estado no tiene archivo' });
        const ext = s.type === 'video' ? 'mp4' : 'jpg';
        const mediaPath = path.join(MEDIA_DIR, `estado_${s.id}.${ext}`);
        if (!fs.existsSync(mediaPath)) {
            const buffer = await downloadWithReupload(JSON.parse(s.media_raw, BufferJSON.reviver));
            fs.writeFileSync(mediaPath, buffer);
        }
        res.json({ success: true, media_path: mediaPath });
    } catch (e) {
        res.status(502).json({ error: e.message || 'No se pudo descargar' });
    }
});

// Elimina el estado para todos (igual que "Eliminar" en la app del celular)
app.post('/my-statuses/delete', async (req, res) => {
    try {
        const id = req.body?.id;
        if (!myStatuses[id]) return res.status(404).json({ error: 'Ese estado ya no está activo' });
        if (connectionStatus !== 'connected' || !sock) return res.status(503).json({ error: 'WhatsApp no está conectado.' });
        await sock.sendMessage('status@broadcast',
            { delete: { remoteJid: 'status@broadcast', fromMe: true, id } },
            { statusJidList: statusAudience(), broadcast: true });
        delete myStatuses[id];
        saveMyStatuses();
        console.log(`[Estados] 🗑️ Eliminado ${id}`);
        res.json({ success: true });
    } catch (e) {
        console.error('[Estados] Error eliminando estado:', e);
        res.status(500).json({ error: e.message });
    }
});

app.get('/status-schedule', (req, res) => {
    res.json({ items: scheduledStatuses, audience: statusAudience().length });
});

// Programa un estado. Cuerpo: { type: text|image|video, text, media_path, background, font, at (epoch s) }
// Sin "at" (o en el pasado) se publica en la próxima vuelta del programador.
app.post('/status-schedule', (req, res) => {
    try {
        const { type, text, media_path, background, font, at } = req.body || {};
        if (!['text', 'image', 'video'].includes(type)) return res.status(400).json({ error: 'Tipo inválido' });
        const msgText = String(text || '').trim();
        if (type === 'text' && !msgText) return res.status(400).json({ error: 'El estado de texto está vacío' });
        if (msgText.length > 700) return res.status(400).json({ error: 'El texto supera los 700 caracteres' });

        const id = crypto.randomBytes(6).toString('hex');
        let storedPath = null;
        if (type !== 'text') {
            if (!media_path || !fs.existsSync(media_path)) {
                return res.status(404).json({ error: `Archivo no encontrado: ${media_path}` });
            }
            // Copia propia: si el original se mueve o se borra (o Drive lo deja offline), el estado igual sale
            storedPath = path.join(SCHEDULE_MEDIA_DIR, `${id}${path.extname(media_path).toLowerCase()}`);
            fs.copyFileSync(media_path, storedPath);
        }
        const now = Math.floor(Date.now() / 1000);
        const item = {
            id, type,
            text: msgText,
            media_path: storedPath,
            source_name: media_path ? path.basename(media_path) : null,
            background: type === 'text' ? (background || '#005c4b') : null,
            font: type === 'text' ? (Number.isInteger(font) ? font : 0) : null,
            at: Number(at) > 0 ? Math.floor(Number(at)) : now,
            created_at: now,
            state: 'pending',
            attempts: 0
        };
        scheduledStatuses.push(item);
        saveSchedule();
        console.log(`[Estados] 🗓️ Programado ${id} (${type}) para ${new Date(item.at * 1000).toISOString()}`);
        setTimeout(processStatusSchedule, 500);
        res.json({ success: true, item });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Cambia fecha/hora o texto de un estado que todavía no salió
app.post('/status-schedule/update', (req, res) => {
    const { id, at, text } = req.body || {};
    const item = scheduledStatuses.find(s => s.id === id);
    if (!item) return res.status(404).json({ error: 'No existe ese estado programado' });
    if (!['pending', 'failed', 'expired', 'canceled'].includes(item.state)) {
        return res.status(409).json({ error: 'Ese estado ya se publicó o se está publicando' });
    }
    if (Number(at) > 0) item.at = Math.floor(Number(at));
    if (typeof text === 'string') {
        if (item.type === 'text' && !text.trim()) return res.status(400).json({ error: 'El estado de texto está vacío' });
        item.text = text.trim();
    }
    item.state = 'pending';
    item.attempts = 0;
    item.retry_at = null;
    item.error = null;
    saveSchedule();
    setTimeout(processStatusSchedule, 500);
    res.json({ success: true, item });
});

app.post('/status-schedule/cancel', (req, res) => {
    const item = scheduledStatuses.find(s => s.id === req.body?.id);
    if (!item) return res.status(404).json({ error: 'No existe ese estado programado' });
    if (item.state !== 'pending') return res.status(409).json({ error: 'Solo se cancelan estados pendientes' });
    item.state = 'canceled';
    saveSchedule();
    res.json({ success: true, item });
});

// Quita el estado de la lista (no borra el estado ya publicado en WhatsApp)
app.post('/status-schedule/remove', (req, res) => {
    const idx = scheduledStatuses.findIndex(s => s.id === req.body?.id);
    if (idx < 0) return res.status(404).json({ error: 'No existe ese estado programado' });
    if (scheduledStatuses[idx].state === 'sending') return res.status(409).json({ error: 'Se está publicando ahora' });
    const [item] = scheduledStatuses.splice(idx, 1);
    if (item.media_path) fs.rm(item.media_path, { force: true }, () => {});
    saveSchedule();
    res.json({ success: true });
});

// Las rutas del archivo y la miniatura quedan en el gateway: la lista lleva solo lo que se muestra
app.get('/message-schedule', (req, res) => {
    res.json({ items: scheduledMessages.map(({ send, wa_id, ...item }) => item) });
});

// Programa un mensaje. Cuerpo: el mismo de POST /send, más "at" (epoch s), "chat_name" y, para mostrarlo al enviarse,
// "quoted_text" y "quoted_sender". Con "consume" el archivo se mueve en vez de copiarse (ya es de la app).
app.post('/message-schedule', async (req, res) => {
    try {
        const body = req.body || {};
        const text = String(body.text || '').trim();
        const mediaKey = MESSAGE_MEDIA_KEYS.find(k => body[k]);
        const type = mediaKey ? mediaKey.replace('_path', '') : 'text';
        const now = Math.floor(Date.now() / 1000);
        if (!body.to || typeof body.to !== 'string') return res.status(400).json({ error: 'Falta el destinatario' });
        if (type === 'text' && !text) return res.status(400).json({ error: 'El mensaje está vacío' });
        if (!(Number(body.at) > now)) return res.status(400).json({ error: 'Elegí una fecha y hora futuras' });

        const id = crypto.randomBytes(6).toString('hex');
        const send = {};
        for (const k of MESSAGE_SEND_FIELDS) if (body[k]) send[k] = body[k];
        let storedPath = null;
        if (mediaKey) {
            const source = body[mediaKey];
            if (!fs.existsSync(source)) return res.status(404).json({ error: `Archivo no encontrado: ${source}` });
            // Copia propia: si el original se mueve o se borra, el mensaje igual sale
            storedPath = path.join(MESSAGE_SCHEDULE_MEDIA_DIR, `${id}${path.extname(source).toLowerCase()}`);
            let moved = false;
            if (body.consume) moved = await fs.promises.rename(source, storedPath).then(() => true, () => false);
            if (!moved) await fs.promises.copyFile(source, storedPath);
            send[mediaKey] = storedPath;
        }
        const to = body.to.includes('@') ? body.to : `${body.to.replace(/\D/g, '')}@s.whatsapp.net`;
        const item = {
            id, type, to,
            chat_name: body.chat_name || null,
            text,
            media_path: storedPath,
            source_name: mediaKey ? (body.document_name || path.basename(body[mediaKey])) : null,
            quoted_text: body.quoted_text || null,
            quoted_sender: body.quoted_sender || null,
            send,
            at: Math.floor(Number(body.at)),
            created_at: now,
            state: 'pending',
            attempts: 0,
            repeat: normalizeRepeat(body.repeat),
            anchor_at: Math.floor(Number(body.at))   // fecha de inicio: las repeticiones mensuales/anuales no se corren de día
        };
        scheduledMessages.push(item);
        saveMessageSchedule();
        armMessageTimer();
        console.log(`[Programados] 🗓️ ${id} (${type}) para ${new Date(item.at * 1000).toISOString()}`);
        res.json({ success: true, id });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Cambia la fecha/hora o el texto de un mensaje que no salió ("at" = ahora lo envía ya)
app.post('/message-schedule/update', (req, res) => {
    const { id, at, text, repeat } = req.body || {};
    const item = scheduledMessages.find(m => m.id === id);
    if (!item) return res.status(404).json({ error: 'No existe ese mensaje programado' });
    if (!['pending', 'failed', 'expired', 'canceled'].includes(item.state)) {
        return res.status(409).json({ error: 'Ese mensaje ya se envió o se está enviando' });
    }
    if (typeof text === 'string') {
        if (item.type === 'text' && !text.trim()) return res.status(400).json({ error: 'El mensaje está vacío' });
        item.text = text.trim();
    }
    if (Number(at) > 0) item.at = Math.floor(Number(at));
    if (repeat !== undefined) item.repeat = normalizeRepeat(repeat);
    if (Number(at) > 0 || repeat !== undefined) item.anchor_at = item.at;
    item.state = 'pending';
    item.attempts = 0;
    item.retry_at = null;
    item.error = null;
    saveMessageSchedule();
    armMessageTimer();
    res.json({ success: true });
});

app.post('/message-schedule/cancel', (req, res) => {
    const item = scheduledMessages.find(m => m.id === req.body?.id);
    if (!item) return res.status(404).json({ error: 'No existe ese mensaje programado' });
    if (item.state !== 'pending') return res.status(409).json({ error: 'Solo se cancelan mensajes pendientes' });
    item.state = 'canceled';
    saveMessageSchedule();
    armMessageTimer();
    res.json({ success: true });
});

// Quita el mensaje de la lista (no borra de WhatsApp uno ya enviado)
app.post('/message-schedule/remove', (req, res) => {
    const idx = scheduledMessages.findIndex(m => m.id === req.body?.id);
    if (idx < 0) return res.status(404).json({ error: 'No existe ese mensaje programado' });
    if (scheduledMessages[idx].state === 'sending') return res.status(409).json({ error: 'Se está enviando ahora' });
    const [item] = scheduledMessages.splice(idx, 1);
    removeScheduledMedia(item);
    saveMessageSchedule();
    armMessageTimer();
    res.json({ success: true });
});

app.get('/status', (req, res) => {
    res.json({
        build: BUILD_ID,
        pid: process.pid,
        status: connectionStatus,
        phone: connectedPhone,
        push_name: connectedPushName,
        has_qr: !!currentQRDataUrl
    });
});

app.get('/qr', (req, res) => {
    res.json({
        status: connectionStatus,
        qr_data_url: currentQRDataUrl,
        message: connectionStatus === 'connected' ? 'Ya está conectado.' : 'Esperando generación de QR...'
    });
});

// Vinculación con código de 8 dígitos, para cuando no se puede escanear el QR
// (un celular no puede escanear su propia pantalla).
app.post('/pairing-code', async (req, res) => {
    try {
        const phone = String(req.body?.phone || '').replace(/\D/g, '');
        if (phone.length < 8) return res.status(400).json({ error: 'Número inválido: usá el código de país, sin + ni espacios.' });
        if (connectionStatus === 'connected') return res.status(409).json({ error: 'Ya está conectado.' });
        // WhatsApp solo acepta el pedido cuando el socket ya está esperando vinculación.
        // Una cuenta ya vinculada que está reconectando nunca llega a este estado: no se le borran las credenciales.
        const waitForQr = async () => {
            for (let i = 0; i < 40 && connectionStatus !== 'qr_ready'; i++) {
                await new Promise(r => setTimeout(r, 500));
            }
            return connectionStatus === 'qr_ready' && sock;
        };
        const noConnection = { error: 'No se pudo contactar a WhatsApp. Revisá la conexión a internet.' };
        if (!await waitForQr()) return res.status(503).json(noConnection);
        // Cada código arranca con credenciales y socket nuevos, así dura el tiempo completo
        pairingPending = false;
        pairingMode = true;
        resetAuthState();
        await initWhatsApp();
        if (!await waitForQr()) {
            pairingMode = false;
            return res.status(503).json(noConnection);
        }
        pairingPending = true;
        const code = await sock.requestPairingCode(phone);
        console.log('[Baileys] Código de vinculación generado.');
        res.json({ success: true, code });
    } catch (e) {
        pairingMode = false;
        console.error('[Baileys] Error pidiendo código de vinculación:', e.message);
        res.status(500).json({ error: e.message });
    }
});

app.get('/contacts', (req, res) => {
    res.json(contactsStore);
});

app.post('/contact/update', (req, res) => {
    try {
        const { jid, name, phone } = req.body;
        if (!jid) return res.status(400).json({ error: 'Falta jid' });
        saveContactInfo(phone || jid, jid.endsWith('@lid') ? jid : null, name, '');
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/fetch-avatar', async (req, res) => {
    const { jid } = req.body;
    if (!jid) return res.status(400).json({ error: 'Falta jid' });
    const p = await fetchAndSaveAvatar(jid);
    res.json({ success: true, avatar_path: p });
});

// Envía un mensaje con el cuerpo de POST /send. Si algo falla lanza un error con httpStatus (404 = falta el archivo).
async function sendWhatsApp(body, extraOpts = {}) {
    const { to, text, image_path, image_thumb, image_url, document_path, document_name, mimetype,
            video_path, audio_path, audio_seconds, audio_waveform, sticker_path, quoted } = body;
    const msgText = (text || '').trim();
    const fail = (httpStatus, message) => Object.assign(new Error(message), { httpStatus });

    if (!to) throw fail(400, 'Falta parámetro "to"');
    if (connectionStatus !== 'connected' || !sock) throw fail(503, 'WhatsApp no está conectado.');

    let jid = to;
    if (!jid.includes('@')) {
        const clean = jid.replace(/\D/g, '');
        jid = `${clean}@s.whatsapp.net`;
    }

    // Respuesta a un mensaje: Baileys solo necesita la clave y un texto representativo para citarlo
    const sendOpts = { ...extraOpts };
    if (quoted?.message_id) {
        const qKey = { remoteJid: jid, id: quoted.message_id, fromMe: Boolean(quoted.from_me) };
        if (jid.endsWith('@g.us') && quoted.participant && !quoted.from_me) qKey.participant = quoted.participant;
        sendOpts.quoted = { key: qKey, message: { conversation: quoted.text || '' } };
    }

    const mediaFile = (p, missing) => {
        if (!fs.existsSync(p)) throw fail(404, `${missing}: ${p}`);
        return fs.readFileSync(p);
    };

    let result;
    if (document_path) {
        const buffer = mediaFile(document_path, 'Archivo no encontrado');
        const fileName = document_name || path.basename(document_path);
        result = await sock.sendMessage(jid, {
            document: buffer,
            mimetype: mimetype || 'application/octet-stream',
            fileName: fileName,
            caption: msgText || undefined
        }, sendOpts);
    } else if (image_path) {
        const buffer = mediaFile(image_path, 'Imagen no encontrada');
        result = await sock.sendMessage(jid, {
            image: buffer,
            caption: msgText || undefined,
            // En Android no hay sharp ni jimp: la miniatura la arma la app
            ...(image_thumb ? { jpegThumbnail: image_thumb } : {})
        }, sendOpts);
    } else if (video_path) {
        const buffer = mediaFile(video_path, 'Video no encontrado');
        result = await sock.sendMessage(jid, { video: buffer, caption: msgText || undefined }, sendOpts);
    } else if (audio_path) {
        const buffer = mediaFile(audio_path, 'Audio no encontrado');
        const audioMsg = { audio: buffer, mimetype: 'audio/ogg; codecs=opus', ptt: true };
        // Notas grabadas en la app: llegan con su duración y su onda (si no, Baileys calcula solo la duración)
        if (Number(audio_seconds) > 0) audioMsg.seconds = Math.max(1, Math.round(Number(audio_seconds)));
        if (Array.isArray(audio_waveform) && audio_waveform.length) {
            audioMsg.waveform = new Uint8Array(audio_waveform.map(v => Math.max(0, Math.min(100, Number(v) | 0))));
        }
        result = await sock.sendMessage(jid, audioMsg, sendOpts);
    } else if (sticker_path) {
        result = await sock.sendMessage(jid, {
            sticker: mediaFile(sticker_path, 'Sticker no encontrado'),
            // webp 512x512; si es animado la app lo avisa (no hay sharp/jimp para detectarlo acá)
            ...(body.sticker_animated ? { isAnimated: true } : {})
        }, sendOpts);
    } else if (image_url) {
        result = await sock.sendMessage(jid, {
            image: { url: image_url },
            caption: msgText || undefined
        }, sendOpts);
    } else {
        result = await sock.sendMessage(jid, { text: msgText }, sendOpts);
    }

    if (result?.key?.id) {
        sentBotMessageIds.add(result.key.id);
        if (sentBotMessageIds.size > 2000) {
            const first = sentBotMessageIds.values().next().value;
            sentBotMessageIds.delete(first);
        }
    }
    return result;
}

app.post('/send', async (req, res) => {
    try {
        const result = await sendWhatsApp(req.body || {});
        res.json({ success: true, message_id: result?.key?.id });
    } catch (e) {
        if (!e.httpStatus) console.error('[Baileys] Error enviando mensaje:', e);
        res.status(e.httpStatus || 500).json({ error: e.message });
    }
});

// Reacciona (o retira la reacción con emoji vacío) a un mensaje
app.post('/react', async (req, res) => {
    try {
        const { to, message_id, from_me, participant, emoji } = req.body || {};
        if (!to || !message_id) return res.status(400).json({ error: 'Faltan "to" o "message_id"' });
        if (connectionStatus !== 'connected' || !sock) {
            return res.status(503).json({ error: 'WhatsApp no está conectado.' });
        }
        const key = { remoteJid: to, id: message_id, fromMe: Boolean(from_me) };
        if (to.endsWith('@g.us') && participant && !from_me) key.participant = participant;
        await sock.sendMessage(to, { react: { text: emoji || '', key } });
        res.json({ success: true });
    } catch (e) {
        console.error('[Baileys] Error enviando reacción:', e);
        res.status(500).json({ error: e.message });
    }
});

// Envía la confirmación de lectura (tildes azules) de mensajes recibidos de un chat individual
app.post('/read', async (req, res) => {
    try {
        const { to, messages } = req.body || {};
        if (!to || !Array.isArray(messages) || !messages.length) {
            return res.status(400).json({ error: 'Faltan "to" o "messages"' });
        }
        if (!isChatJid(to)) return res.json({ success: true, skipped: true });
        if (connectionStatus !== 'connected' || !sock) {
            return res.status(503).json({ error: 'WhatsApp no está conectado.' });
        }
        const isGroup = to.endsWith('@g.us');
        const keys = messages
            .filter(id => typeof id === 'string' && id && (!isGroup || groupSenders.has(id)))
            .map(id => ({ remoteJid: to, id, fromMe: false, ...(isGroup ? { participant: groupSenders.get(id) } : {}) }));
        if (keys.length) await sock.readMessages(keys);
        res.json({ success: true, count: keys.length });
    } catch (e) {
        console.error('[Baileys] Error enviando confirmación de lectura:', e);
        res.status(500).json({ error: e.message });
    }
});

// Elimina un mensaje propio para todos (revoke)
app.post('/delete', async (req, res) => {
    try {
        const { to, message_id } = req.body || {};
        if (!to || !message_id) return res.status(400).json({ error: 'Faltan "to" o "message_id"' });
        if (connectionStatus !== 'connected' || !sock) {
            return res.status(503).json({ error: 'WhatsApp no está conectado.' });
        }
        await sock.sendMessage(to, { delete: { remoteJid: to, id: message_id, fromMe: true } });
        res.json({ success: true });
    } catch (e) {
        console.error('[Baileys] Error eliminando mensaje:', e);
        res.status(500).json({ error: e.message });
    }
});

// Fija (o desfija) un mensaje para todos los participantes del chat
app.post('/pin', async (req, res) => {
    try {
        const { to, message_id, from_me, participant, pinned } = req.body || {};
        if (!to || !message_id) return res.status(400).json({ error: 'Faltan "to" o "message_id"' });
        if (connectionStatus !== 'connected' || !sock) {
            return res.status(503).json({ error: 'WhatsApp no está conectado.' });
        }
        const key = { remoteJid: to, id: message_id, fromMe: Boolean(from_me) };
        if (to.endsWith('@g.us') && participant && !from_me) key.participant = participant;
        // type 1 = fijar para todos, 2 = desfijar; time = segundos (7 días)
        await sock.sendMessage(to, { pin: key, type: pinned ? 1 : 2, time: 604800 });
        res.json({ success: true });
    } catch (e) {
        console.error('[Baileys] Error fijando mensaje:', e);
        res.status(500).json({ error: e.message });
    }
});

app.post('/disconnect', async (req, res) => {
    try {
        console.log('[Baileys] Cerrando sesión y reiniciando claves para emparejamiento limpio...');
        if (sock) {
            await sock.logout().catch(() => {});
        }
        if (fs.existsSync(AUTH_DIR)) {
            fs.rmSync(AUTH_DIR, { recursive: true, force: true });
        }
        connectionStatus = 'disconnected';
        connectedPhone = null;
        currentQR = null;
        currentQRDataUrl = null;
        downloadedAvatars.clear();
        reconnectAttempts = 0;
        scheduleReconnect(1500);
        res.json({ success: true, message: 'Sesión cerrada. Esperando nuevo QR con sincronización completa.' });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Descarga bajo demanda de un archivo multimedia (historial o mensajes que fallaron al bajar)
app.post('/media/download', async (req, res) => {
    try {
        const { message_id, media_type, media_raw } = req.body || {};
        if (!message_id || !media_raw) {
            return res.status(400).json({ error: 'Faltan message_id o media_raw' });
        }
        const waMessage = JSON.parse(media_raw, BufferJSON.reviver);
        const buffer = await downloadWithReupload(waMessage);
        if (!buffer) return res.status(502).json({ error: 'Descarga vacía' });

        let ext = 'dat';
        if (media_type === 'image') ext = 'jpg';
        else if (media_type === 'audio') ext = 'ogg';
        else if (media_type === 'video') ext = 'mp4';
        else if (media_type === 'sticker') ext = 'webp';
        else if (media_type === 'document') {
            const fileName = waMessage.message?.documentMessage?.fileName || 'file.bin';
            ext = (fileName.split('.').pop() || 'bin').replace(/[^a-zA-Z0-9]/g, '').slice(0, 8) || 'bin';
        }

        const safeId = String(message_id).replace(/[^\w.-]/g, '_');
        const mediaPath = path.join(MEDIA_DIR, `media_${safeId}.${ext}`);
        fs.writeFileSync(mediaPath, buffer);
        res.json({ success: true, media_path: mediaPath });
    } catch (e) {
        console.error('[Baileys] Error descargando multimedia bajo demanda:', e.message);
        res.status(502).json({ error: e.message || 'No se pudo descargar el archivo' });
    }
});

// Los estados se guardan con un retraso de 1 s: al apagar se escriben ya para no perder los últimos
function saveBeforeExit() {
    saveOutbox();
    try { fs.writeFileSync(MY_STATUS_FILE, JSON.stringify(myStatuses), 'utf-8'); } catch (_) {}
    try { fs.writeFileSync(CONTACT_STATUS_FILE, JSON.stringify(contactStatuses), 'utf-8'); } catch (_) {}
}

app.post('/shutdown', (req, res) => {
    res.json({ message: 'Apagando gateway...' });
    setTimeout(() => {
        saveBeforeExit();
        process.exit(0);
    }, 500);
});

// Cuerpo malformado o demasiado grande (express.json): 400/413 en JSON, sin la traza de Express
app.use((err, req, res, next) => {
    if (res.headersSent) return next(err);
    const status = err?.status || err?.statusCode || 500;
    res.status(status).json({ error: status === 413 ? 'Cuerpo demasiado grande' : (status < 500 ? 'Solicitud inválida' : 'Error interno') });
});

// Un error suelto de Baileys (socket, descifrado) no debe tumbar el gateway entero
process.on('unhandledRejection', (err) => console.error('[Gateway] Promesa sin manejar:', err?.stack || err?.message || err));
process.on('uncaughtException', (err) => console.error('[Gateway] Excepción sin manejar:', err?.stack || err?.message || err));

// Si la app se cerró de golpe (cuelgue, apagado forzado), el gateway se apaga solo: así no queda un
// proceso huérfano con código viejo ocupando el puerto en el próximo arranque.
const PARENT_PID = parseInt(process.env.ADVANCEDWHATSAPP_PARENT_PID || '0', 10);
if (PARENT_PID > 0) {
    setInterval(() => {
        try {
            process.kill(PARENT_PID, 0);
        } catch (e) {
            if (e.code === 'ESRCH') {
                console.log('[Advanced WhatsApp Gateway] La app ya no está abierta: apagando el gateway.');
                saveBeforeExit();
                process.exit(0);
            }
        }
    }, 10000);
}

// Respuestas automáticas, encuestas, envío a varios y repetición de programados (gateway/extras.js)
const extras = createExtras({
    app, dataDir: USER_DATA_DIR, getSock: () => sock, isConnected: () => connectionStatus === 'connected' && Boolean(sock),
    sendWhatsApp, deliverReliably, postEvent, webhookUrl: PYTHON_WEBHOOK_URL, decryptPollVote, log: console.log,
    markSent: (id) => { sentBotMessageIds.add(id); }
});

// Iniciar servidor Express
const server = app.listen(PORT, '127.0.0.1', () => {
    console.log(`[Advanced WhatsApp Gateway] Servidor HTTP escuchando en http://127.0.0.1:${PORT} (build ${BUILD_ID || '-'})`);
    initWhatsApp();
    setInterval(processStatusSchedule, 20000);
});
server.on('error', (err) => {
    console.error(`[Advanced WhatsApp Gateway] No se pudo abrir el puerto ${PORT}: ${err.message}`);
    process.exit(1);
});
