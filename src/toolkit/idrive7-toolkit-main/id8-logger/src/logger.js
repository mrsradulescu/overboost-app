'use strict';
/**
 * logger.js - the recorder behind the BC / ECU logger. No SDK in here, so it runs (and is tested) on a PC.
 *
 * Every value the app learns from the car (SOME/IP notifications, ECU reads) is written to a
 * JSON-lines file:
 *
 *   {"k":"file","t":812,"wall":"2026-10-29T00:55:01.123Z","app":"bclog","ver":2}   file header
 *   {"t":1203,"k":"sig","i":7,"name":"de.bmw...FrontCCU#statusPushButtonsState"}   name of signal 7
 *   {"t":1203,"i":7,"v":{...}}                                                     signal 7 = value
 *   {"t":5120,"k":"mark","n":3,"label":"BC pressed","kind":"action","btn":"BTN_MARK_BC"}
 *   {"t":9000,"k":"drop","n":12}                                                   lines dropped by the cap
 *   {"t":9100,"k":"did","ecu":"12","did":"F40C","h":"1A F8"}                       free-form note, see note()
 *
 * t = milliseconds since the app process started (monotonic; the unit's wall clock may be wrong).
 * A signal is written when its value changed (events: every time), at most once per `minMs`; the
 * newest value of a faster signal is written when the pause is over, so the end state is never lost.
 * Node 8 compatible (the head unit runs Node 8).
 */
const fs = require('fs');
const path = require('path');

const FILE_RE = /^bclog-\d{8}-\d{6}(?:-\d+)?\.jsonl$/;
const MAX_FILES = 60;
const QUIET_MAX = 15;          // a signal that changes more often than this per ~5 s is "noisy" (rpm, speed...)

const BASE = process.hrtime();
function monotonic() { const d = process.hrtime(BASE); return d[0] * 1000 + Math.floor(d[1] / 1e6); }

function stamp(d) {
    return d.toISOString().replace(/\..*$/, '').replace(/[-:]/g, '').replace('T', '-');
}

function replacer(k, x) {
    if (x && typeof x === 'object') {
        if (x.type === 'Buffer' && Array.isArray(x.data)) return '<' + x.data.length + ' bytes>';
        if (ArrayBuffer.isView(x)) return '<' + (x.length !== undefined ? x.length : x.byteLength) + ' bytes>';
        if (Array.isArray(x) && x.length > 64 && typeof x[0] === 'number') return '<' + x.length + ' numbers>';
    }
    return x;
}

/** JSON text of a value, long byte lists and huge payloads shortened. */
function pack(v, maxLen) {
    let s;
    try { s = JSON.stringify(v, replacer); } catch (e) { return '"<unserializable>"'; }
    if (s === undefined) return 'null';
    if (s.length > (maxLen || 3000)) return JSON.stringify({ _cut: s.length, head: s.slice(0, 600) });
    return s;
}

class Logger {
    /**
     * o.dir     folder for the log files (the app's data folder)
     * o.log     { info, warn, error }
     * o.minMs() / o.cap() / o.fileMB()   live settings: min. ms between two lines of one signal,
     *           max. lines per second (0 = no limit), size after which a new file is started
     * o.capMB   all log files together are kept below this (oldest are deleted)
     * o.now / o.wall   clocks (tests)
     */
    constructor(o) {
        this.dir = o.dir;
        this.log = o.log || console;
        this.clock = o.now || monotonic;
        this.wall = o.wall || (() => new Date());
        this.minMs = o.minMs || (() => 50);
        this.cap = o.cap || (() => 1000);
        this.fileMB = o.fileMB || (() => 10);
        this.capMB = o.capMB || 40;
        this.keys = new Map();
        this.nextId = 1;
        this.on = false;
        this.fd = null;
        this.file = null;
        this.fileSeq = 0;
        this.fileBytes = 0;
        this.buf = [];
        this.timer = null;
        this.tokens = 0;
        this.tokT = 0;
        this.markN = 0;
        this.lastQuiet = [];
        this.error = null;
        this.stats = { lines: 0, changes: 0, dropped: 0, droppedSec: 0, marks: 0 };
    }

    /* ---------------- files ---------------- */

    openFile() {
        try {
            try { fs.mkdirSync(this.dir); } catch (e) { if (e.code !== 'EEXIST') throw e; }
            const d = this.wall(), base = 'bclog-' + stamp(d);
            let name = base + '.jsonl', n = 1;
            while (fs.existsSync(path.join(this.dir, name))) name = base + '-' + (n++) + '.jsonl';
            this.fd = fs.openSync(path.join(this.dir, name), 'a');
            this.file = name;
            this.fileBytes = 0;
            this.fileSeq++;
            const hdr = '{"k":"file","t":' + this.clock() + ',"wall":"' + d.toISOString() + '","app":"bclog","ver":2}\n';
            fs.writeSync(this.fd, hdr);
            this.fileBytes += hdr.length;
            this.prune();
            this.error = null;
            return true;
        } catch (e) {
            this.fd = null;
            this.error = String(e && e.message || e);
            this.log.error('bclog: cannot open a log file: ' + this.error);
            return false;
        }
    }

    closeFile() {
        if (this.fd === null) return;
        try { fs.closeSync(this.fd); } catch (e) { /* ignore */ }
        this.fd = null;
    }

    /** Keep the folder small: delete the oldest log files above the size / count cap (never the current one). */
    prune() {
        try {
            const files = fs.readdirSync(this.dir).filter((f) => FILE_RE.test(f) && f !== this.file).sort();
            const info = files.map((f) => { let sz = 0; try { sz = fs.statSync(path.join(this.dir, f)).size; } catch (e) { /* gone */ } return { f, sz }; });
            let total = this.fileBytes;
            info.forEach((x) => { total += x.sz; });
            while (info.length && (total > this.capMB * 1048576 || info.length + 1 > MAX_FILES)) {
                const old = info.shift();
                try { fs.unlinkSync(path.join(this.dir, old.f)); this.log.info('bclog: deleted old log ' + old.f); } catch (e) { /* ignore */ }
                total -= old.sz;
            }
        } catch (e) { this.log.warn('bclog: prune: ' + e); }
    }

    rotate() {
        this.closeFile();
        if (!this.openFile()) this.halt();
    }

    /** Writing failed (disk full / not writable): stop for good, the status line says why. */
    halt() {
        this.on = false;
        if (this.timer) { clearInterval(this.timer); this.timer = null; }
        this.buf = [];
        this.closeFile();
    }

    put(line) {
        this.buf.push(line);
        if (this.buf.length >= 400) this.flush();
    }

    flush() {
        if (this.stats.droppedSec > 0) {
            this.buf.push('{"t":' + this.clock() + ',"k":"drop","n":' + this.stats.droppedSec + '}');
            this.stats.droppedSec = 0;
        }
        if (!this.buf.length) return;
        if (this.fd === null) { this.buf = []; return; }
        const n = this.buf.length, s = this.buf.join('\n') + '\n';
        this.buf = [];
        try {
            fs.writeSync(this.fd, s);
            this.fileBytes += Buffer.byteLength(s);
            this.stats.lines += n;
        } catch (e) {
            this.error = String(e && e.message || e);
            this.log.error('bclog: write failed, logging stopped: ' + this.error);
            this.halt();
            return;
        }
        if (this.fileBytes > this.fileMB() * 1048576) this.rotate();
    }

    /* ---------------- control ---------------- */

    start() {
        if (this.on) return true;
        if (!this.openFile()) return false;
        this.on = true;
        this.tokens = this.cap();
        this.tokT = this.clock();
        this.timer = setInterval(() => this.flush(), 500);
        this.put('{"t":' + this.clock() + ',"k":"start"}');
        this.flush();
        return true;
    }

    stop() {
        if (!this.on) return;
        this.keys.forEach((st) => {
            if (st.timer) { clearTimeout(st.timer); st.timer = null; }
            if (st.pend !== null && st.pend !== st.last) this.emit(st, st.pend, this.clock(), true);
            st.pend = null;
        });
        this.on = false;
        this.put('{"t":' + this.clock() + ',"k":"stop"}');
        this.flush();
        if (this.timer) { clearInterval(this.timer); this.timer = null; }
        this.closeFile();
    }

    /** Start a new file now (marks the split in both files). */
    newFile() {
        if (!this.on) return false;
        this.flush();
        if (!this.on) return false;
        this.rotate();
        return this.on;
    }

    /** A tap on a MARK button: "I am doing / seeing <label> right now". Returns the mark number or 0. */
    mark(label, kind, group, btn) {
        if (!this.on) return 0;
        const n = ++this.markN;
        let line = '{"t":' + this.clock() + ',"k":"mark","n":' + n + ',"label":' + JSON.stringify(label) +
            ',"kind":' + JSON.stringify(kind || 'action');
        if (group) line += ',"group":' + JSON.stringify(group);
        if (btn) line += ',"btn":' + JSON.stringify(btn);
        this.put(line + '}');
        this.flush();                  // a mark must survive a crash
        this.stats.marks++;
        return n;
    }

    /** A free-form line { t, k: kind, ...fields } (kind e.g. "did"). Returns false when not logging. */
    note(kind, fields, maxLen) {
        if (!this.on) return false;
        const o = { t: this.clock(), k: String(kind) };
        Object.keys(fields || {}).forEach((key) => { if (key !== 't' && key !== 'k') o[key] = fields[key]; });
        this.put(pack(o, maxLen));
        if (maxLen) this.flush();
        return true;
    }

    /* ---------------- signals ---------------- */

    /** A value (or event) arrived from the car. */
    value(name, v, isEvent) {
        const now = this.clock();
        let st = this.keys.get(name);
        if (!st) {
            st = { id: this.nextId++, name, isEvent: !!isEvent, last: null, lastT: -1e9, pend: null, timer: null, fileSeq: 0, win: now, n: 0, prevN: 0 };
            this.keys.set(name, st);
        }
        // how busy is this signal? (tumbling 5 s windows)
        if (now - st.win > 5000) { st.prevN = now - st.win > 10000 ? 0 : st.n; st.win = now; st.n = 0; }
        st.n++;
        if (!this.on) return;
        const s = pack(v);
        if (!st.isEvent) {
            if (s === st.last) { st.pend = null; return; }
            const min = Number(this.minMs()) || 0;
            if (min > 0 && now - st.lastT < min) {
                st.pend = s;
                if (!st.timer) st.timer = setTimeout(() => this.release(st), Math.max(1, min - (now - st.lastT)));
                return;
            }
        }
        this.emit(st, s, now, false);
    }

    release(st) {
        st.timer = null;
        if (this.on && st.pend !== null && st.pend !== st.last) this.emit(st, st.pend, this.clock(), false);
        st.pend = null;
    }

    emit(st, s, now, force) {
        st.pend = null;
        const cap = Number(this.cap()) || 0;
        if (cap > 0 && !force) {
            this.tokens = Math.min(cap, this.tokens + (now - this.tokT) * cap / 1000);
            this.tokT = now;
            if (this.tokens < 1) {
                this.stats.dropped++;
                this.stats.droppedSec++;
                st.last = s;
                st.lastT = now;
                return;
            }
            this.tokens -= 1;
        }
        if (st.fileSeq !== this.fileSeq) {
            this.put('{"t":' + now + ',"k":"sig","i":' + st.id + ',"name":' + JSON.stringify(st.name) + (st.isEvent ? ',"e":1' : '') + '}');
            st.fileSeq = this.fileSeq;
        }
        this.put('{"t":' + now + ',"i":' + st.id + ',"v":' + s + '}');
        st.last = s;
        st.lastT = now;
        this.stats.changes++;
        if (Math.max(st.prevN, st.n) <= QUIET_MAX) this.noteQuiet(st.name, now);
    }

    noteQuiet(name, t) {
        const a = this.lastQuiet;
        const i = a.findIndex((x) => x.name === name);
        if (i >= 0) a.splice(i, 1);
        a.push({ name, t });
        if (a.length > 4) a.shift();
    }

    /** For the screen. */
    status() {
        const now = this.clock();
        return {
            on: this.on, file: this.file, bytes: this.fileBytes, error: this.error,
            lines: this.stats.lines + this.buf.length, changes: this.stats.changes, dropped: this.stats.dropped,
            marks: this.stats.marks, signals: this.keys.size,
            quiet: this.lastQuiet.map((x) => ({ name: x.name, ageMs: now - x.t })),
        };
    }
}

module.exports = { Logger, pack, stamp, FILE_RE };
