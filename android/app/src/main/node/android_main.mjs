// Punto de entrada del gateway en Android. nodejs-mobile trae Node 18 y Baileys 7 pide Node 20:
// acá se completan las funciones que faltan ANTES de cargar Baileys (por eso el import dinámico).
import { webcrypto } from 'crypto';
import { Blob, isUtf8 } from 'buffer';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { syncBuiltinESMExports } from 'module';

// En Android Node no lee TMPDIR (el proceso viene marcado como "seguro") y os.tmpdir() da /tmp, que no existe:
// Baileys escribe ahí la multimedia cifrada al enviar fotos. Se fuerza una carpeta propia antes de cargarlo.
{
    const tmp = process.env.TMPDIR || path.join(process.env.ADVANCEDWHATSAPP_DATA_DIR || os.homedir(), 'tmp');
    // Android puede vaciar la caché con la app abierta: se vuelve a crear si falta
    os.tmpdir = () => {
        if (!fs.existsSync(tmp)) { try { fs.mkdirSync(tmp, { recursive: true }); } catch {} }
        return tmp;
    };
    syncBuiltinESMExports();   // que `import { tmpdir } from 'os'` también vea el cambio
}

if (!globalThis.crypto) globalThis.crypto = webcrypto;

// nodejs-mobile viene sin ICU: su TextDecoder rechaza la opción `fatal` que usa el módulo WebAssembly de Baileys
const NativeTextDecoder = globalThis.TextDecoder;
globalThis.TextDecoder = class TextDecoder {
    #inner;
    #fatal;
    constructor(label = 'utf-8', options = {}) {
        this.#fatal = Boolean(options.fatal);
        this.#inner = new NativeTextDecoder(label, { ignoreBOM: options.ignoreBOM });
    }
    get encoding() { return this.#inner.encoding; }
    get fatal() { return this.#fatal; }
    get ignoreBOM() { return this.#inner.ignoreBOM; }
    decode(input, options) {
        if (this.#fatal && input && !options?.stream && !isUtf8(input)) {
            throw new TypeError('The encoded data was not valid for encoding utf-8');
        }
        return this.#inner.decode(input, options);
    }
};

if (!globalThis.File) {
    globalThis.File = class File extends Blob {
        constructor(parts, name, options = {}) {
            super(parts, options);
            this.name = String(name);
            this.lastModified = options.lastModified ?? Date.now();
        }
    };
}

if (!Array.prototype.toSorted) {
    const define = (name, fn) => Object.defineProperty(Array.prototype, name, { value: fn, writable: true, configurable: true });
    define('toSorted', function (cmp) { return [...this].sort(cmp); });
    define('toReversed', function () { return [...this].reverse(); });
    define('toSpliced', function (...a) { const c = [...this]; c.splice(...a); return c; });
    define('with', function (i, v) { const c = [...this]; c[i < 0 ? c.length + i : i] = v; return c; });
}
if (!Promise.withResolvers) {
    Promise.withResolvers = function () {
        let resolve, reject;
        const promise = new Promise((a, b) => { resolve = a; reject = b; });
        return { promise, resolve, reject };
    };
}
if (!Object.groupBy) {
    Object.groupBy = (items, fn) => {
        const out = Object.create(null);
        let i = 0;
        for (const item of items) (out[fn(item, i++)] ||= []).push(item);
        return out;
    };
}
if (!AbortSignal.any) {
    AbortSignal.any = (signals) => {
        const c = new AbortController();
        for (const s of signals) {
            if (s.aborted) { c.abort(s.reason); break; }
            s.addEventListener('abort', () => c.abort(s.reason), { once: true });
        }
        return c.signal;
    };
}

await import('./server.js');
