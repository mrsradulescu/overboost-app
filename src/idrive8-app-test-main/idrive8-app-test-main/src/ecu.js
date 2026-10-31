'use strict';
/**
 * ecu.js - reads a control unit (the engine ECU) over UDS and writes what changes to the log.
 *
 * READ ONLY. The only requests it can send are
 *     0x01 <pid>          OBD service 01 (live data, e.g. 01 = monitor status with the MIL bit)
 *     0x22 <did hi lo>    ReadDataByIdentifier
 *     0x19 0x02 <mask>    ReadDTCInformation (fault memory)
 * Anything else is refused inside send() before it reaches the transport (tested), so no session
 * change, no write, no routine, no clear.
 *
 * What it does
 *   poll    fast items every cycle (MIL, rpm, ethanol), the other items a few per cycle. An item
 *           that changed in the last `hotMs` is read every cycle too (the values that move when
 *           a BC menu changes). Each change goes to the log as ECU<addr>#<request> = {h, n}.
 *   sweep   reads a range of DIDs once (parked only), adds every DID that answers to the poll
 *           list and keeps the list in a file, so the next start already knows them.
 *   menu    the BC menu announces itself by blinking the check-engine lamp: 1 blink = menu 1
 *           (maps), 2 = menu 2 (valves), 3 = menu 3 (ethanol). The lamp is the MIL bit of
 *           OBD service 01, PID 01 (byte A, bit 7). Edges are grouped into bursts; a burst that ends
 *           becomes "derived#bcmenu" {blinks:n} plus an automatic mark, and a long silence
 *           becomes {blinks:0} (menu left). Raw MIL changes stay in the log too ("derived#mil"),
 *           so tools/analyze_bclog.py can regroup them with other timings.
 *
 * No SDK in here: the transport (diag.physical) and the recorder (logger.js) are passed in, so
 * the module runs and is tested on a PC. Node 8 compatible.
 */

const hex = (n, w) => n.toString(16).toUpperCase().padStart(w || 2, '0');
const hexList = (a) => a.map((x) => hex(x)).join(' ');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// requests this module may send: service id -> check of the data bytes
const ALLOWED = {
    0x01: (d) => d.length === 1,
    0x22: (d) => d.length === 2,
    0x19: (d) => d.length === 2 && d[0] === 0x02,
};
// refusals that mean "this ECU does not have it" (not worth asking again)
const GONE = [0x11, 0x12, 0x31, 0x7E, 0x7F];

const DEFAULTS = {
    addr: 0x12,                                   // DME
    fastObd: ['01', '0C', '52'],                  // 01 monitor status (MIL), 0C rpm, 52 ethanol %
    slowObd: ['04', '05', '0B', '0D', '0F', '11', '2F', '43', '45', '47', '4C', '5C', '5E'],
    dids: [],                                     // extra DIDs ('F40C') polled from the start
    slowPerCycle: 3,
    hotMs: 20000,                                 // an item that changed within this is read every cycle
    maxHot: 8,
    maxDids: 80,                                  // DIDs the sweep may add
    gapMs: 30,                                    // pause between two requests
    sweepGapMs: 50,
    timeoutMs: 1200,
    dtcEveryMs: 3000,
    blinkGapMs: 1000,                             // blinks closer than this belong to one burst
    settleMs: 1500,                               // this long after the last blink the burst is complete
    exitMs: 8000,                                 // this long without a blink: menu left
    sweeps: {                                     // name -> [first, last] DID
        'F100-F1FF': [0xF100, 0xF1FF],
        'F400-F4FF': [0xF400, 0xF4FF],
        'D000-D0FF': [0xD000, 0xD0FF],
        '4000-40FF': [0x4000, 0x40FF],
    },
};

class EcuLog {
    /**
     * o.diag      { physical(target, sid, data, timeoutMs, accept), available }   (src/diag.js)
     * o.rec       the Logger (value(), note(), mark())
     * o.log       { info, warn, error }
     * o.cfg       overrides of DEFAULTS (settings file); o.cfg.addr may be a number or '12'
     * o.speed()   km/h or null when unknown; the sweep only runs parked
     * o.file      path of the discovered-DID list (optional)
     * o.fs        fs-like { readFileSync, writeFileSync } for that file
     * o.onChange() called when the status changes (screen refresh)
     * o.now / o.sleep   clock / delay (tests)
     */
    constructor(o) {
        this.diag = o.diag;
        this.rec = o.rec;
        this.log = o.log || console;
        this.cfg = Object.assign({}, DEFAULTS, o.cfg || {});
        const a = this.cfg.addr;
        this.ecu = typeof a === 'string' ? parseInt(a, 16) : a;
        this.speed = o.speed || (() => null);
        this.file = o.file || null;
        this.fs = o.fs || null;
        this.onChange = o.onChange || (() => {});
        this.now = o.now || Date.now;
        this.sleep = o.sleep || sleep;
        this.running = false;
        this.loopP = null;
        this.sweep = null;
        this.state = 'off';
        this.error = null;
        this.stats = { reads: 0, ok: 0, nrc: 0, none: 0, changes: 0, cycles: 0 };
        this.cycleMs = 0;
        this.lastDtc = 0;
        this.dtcText = null;
        this.mil = null;
        this.dtcCount = null;
        this.menu = null;                 // null = nothing seen yet, 0 = left, 1..n = blinks of the last burst
        this.burst = null;
        this.settleT = null;
        this.exitT = null;
        this.slowIdx = 0;
        this.items = [];
        this.byKey = {};
        this.cfg.fastObd.forEach((p) => this.addObd(p, true));
        this.cfg.slowObd.forEach((p) => this.addObd(p, false));
        this.cfg.dids.forEach((d) => this.addDid(d, false));
        this.load();
    }

    /* ---------------- the item list ---------------- */

    add(key, sid, data, fast, label) {
        if (this.byKey[key]) return this.byKey[key];
        const it = { key, sid, data, fast: !!fast, label, dead: false, fails: 0, retryAt: 0, changed: -1e12, last: null, found: false };
        this.items.push(it);
        this.byKey[key] = it;
        return it;
    }
    addObd(pid, fast) {
        const p = parseInt(pid, 16);
        return this.add('01:' + hex(p), 0x01, [p], fast, 'OBD ' + hex(p));
    }
    addDid(did, fast) {
        const d = typeof did === 'string' ? parseInt(did, 16) : did;
        return this.add('22:' + hex(d, 4), 0x22, [d >> 8, d & 0xFF], fast, '22 ' + hex(d, 4));
    }
    signalName(it) { return 'ECU' + hex(this.ecu) + '#' + it.label; }

    load() {
        if (!this.file || !this.fs) return;
        try {
            const list = JSON.parse(this.fs.readFileSync(this.file, 'utf8'));
            if (Array.isArray(list)) list.forEach((d) => { if (/^[0-9A-Fa-f]{4}$/.test(String(d))) this.addDid(String(d), false).found = true; });
        } catch (e) { /* no list yet */ }
    }
    save() {
        if (!this.file || !this.fs) return;
        const list = this.items.filter((i) => i.found).map((i) => i.label.slice(3));
        try { this.fs.writeFileSync(this.file, JSON.stringify(list)); } catch (e) { this.log.warn('ecu: cannot save the DID list: ' + e); }
    }

    /* ---------------- transport ---------------- */

    /** The only way a request leaves this module. */
    async send(sid, data, timeoutMs, accept) {
        const ok = ALLOWED[sid];
        if (!ok || !ok(data)) throw new Error('refused: ' + hex(sid) + ' ' + hexList(data) + ' is not a read request');
        return this.diag.physical(this.ecu, sid, data, timeoutMs, accept);
    }

    /**
     * Ask for one item. Resolves { ok, payload, nrc, none }: payload = the data bytes after the
     * echoed service / identifier.
     */
    async readItem(it) {
        const sid = it.sid, d = it.data;
        const accept = (u) => (sid === 0x22 ? u[0] === 0x62 && u[1] === d[0] && u[2] === d[1] : u[0] === 0x41 && u[1] === d[0]) || (u[0] === 0x7F && u[1] === sid);
        this.stats.reads++;
        let uds;
        try { uds = await this.send(sid, d, this.cfg.timeoutMs, accept); } catch (e) {
            this.error = String(e && e.message || e);
            this.stats.none++;
            return { none: true };
        }
        if (!uds) {
            this.stats.none++;
            if (++it.fails >= 5) { it.fails = 0; it.retryAt = this.now() + 60000; }
            return { none: true };
        }
        if (uds[0] === 0x7F) {
            this.stats.nrc++;
            const nrc = uds[2];
            if (GONE.indexOf(nrc) >= 0) {
                if (!it.dead) { it.dead = true; this.log.info('ecu: ' + it.label + ' not supported (NRC ' + hex(nrc) + ')'); }
            } else if (++it.fails >= 5) { it.fails = 0; it.retryAt = this.now() + 30000; }
            return { nrc };
        }
        this.stats.ok++;
        it.fails = 0;
        return { ok: true, payload: uds.slice(sid === 0x22 ? 3 : 2) };
    }

    /** Read an item and log it when it changed. */
    async poll(it) {
        const r = await this.readItem(it);
        if (!r.ok) return r;
        const p = r.payload;
        const v = { h: hexList(p) };
        if (p.length > 0 && p.length <= 4) v.n = p.reduce((acc, b) => acc * 256 + b, 0);
        const before = it.last;
        it.last = v.h;
        if (before !== null && before !== v.h) { it.changed = this.now(); this.stats.changes++; }
        this.rec.value(this.signalName(it), v, false);
        if (it.key === '01:01' && p.length) this.milUpdate(p[0]);
        return r;
    }

    /** Fault memory of the unit: logged as the list of "code:status". */
    async pollDtc() {
        this.lastDtc = this.now();
        const params = [0x02, 0x0C];
        let uds;
        try { uds = await this.send(0x19, params, 4000, (u) => u[0] === 0x59 || (u[0] === 0x7F && u[1] === 0x19)); } catch (e) { return; }
        if (!uds || uds[0] !== 0x59 || uds[1] !== 0x02) return;
        const list = [];
        for (let i = 3; i + 4 <= uds.length; i += 4) list.push(hex(((uds[i] << 16) | (uds[i + 1] << 8) | uds[i + 2]) >>> 0, 6) + ':' + hex(uds[i + 3]));
        this.dtcText = list.length + ' fault' + (list.length === 1 ? '' : 's');
        this.rec.value('ECU' + hex(this.ecu) + '#19 02', list, false);
    }

    /* ---------------- BC menu from the check-engine lamp ---------------- */

    milUpdate(a) {
        const mil = a & 0x80 ? 1 : 0, dtcs = a & 0x7F, t = this.now();
        const rising = this.mil === 0 && mil === 1;
        this.mil = mil;
        this.dtcCount = dtcs;
        this.rec.value('derived#mil', { on: mil, dtcs }, false);
        if (rising) this.edge(t);
    }

    edge(t) {
        if (this.burst && t - this.burst.last <= this.cfg.blinkGapMs) this.burst.count++;
        else this.burst = { count: 1, last: t };
        this.burst.last = t;
        clearTimeout(this.settleT);
        clearTimeout(this.exitT);
        this.settleT = setTimeout(() => this.settle(), this.cfg.settleMs);
    }

    settle() {
        this.settleT = null;
        if (!this.burst) return;
        const n = this.burst.count;
        this.burst = null;
        if (n !== this.menu) this.setMenu(n);
        clearTimeout(this.exitT);
        this.exitT = setTimeout(() => { this.exitT = null; if (this.menu) this.setMenu(0); }, this.cfg.exitMs);
    }

    setMenu(n) {
        this.menu = n;
        this.rec.value('derived#bcmenu', { blinks: n }, true);
        this.rec.mark(n ? 'BC menu ' + n + ' (auto, ' + n + ' blink' + (n === 1 ? '' : 's') + ')' : 'BC menu left (auto)', 'state', 'bcmenu', 'auto');
        this.log.info('BC menu: ' + (n || 'left'));
        this.onChange();
    }

    /* ---------------- run / stop ---------------- */

    start() {
        if (this.running) return true;
        this.running = true;
        this.state = 'starting';
        this.error = null;
        this.loopP = this.run().catch((e) => { this.error = String(e && e.message || e); this.log.error('ecu loop: ' + this.error); }).then(() => {
            this.running = false;
            this.state = 'off';
            this.onChange();
        });
        this.onChange();
        return true;
    }

    stop() {
        this.running = false;
        this.sweep = null;
        clearTimeout(this.settleT);
        clearTimeout(this.exitT);
        this.settleT = this.exitT = null;
        this.state = 'off';
        this.onChange();
        return this.loopP || Promise.resolve();
    }

    /** One pass: all fast + hot items, a few of the others, now and then the fault memory. */
    async cycle() {
        const t0 = this.now();
        const live = this.items.filter((i) => !i.dead && i.retryAt <= t0);
        const hot = (i) => t0 - i.changed < this.cfg.hotMs;
        const hotItems = live.filter((i) => !i.fast && hot(i)).slice(0, this.cfg.maxHot);
        const rest = live.filter((i) => !i.fast && hotItems.indexOf(i) < 0);
        const batch = live.filter((i) => i.fast).concat(hotItems);
        for (let k = 0; k < this.cfg.slowPerCycle && rest.length; k++) batch.push(rest[this.slowIdx++ % rest.length]);
        for (let k = 0; k < batch.length && this.running && !this.sweep; k++) {
            await this.poll(batch[k]);
            await this.sleep(this.cfg.gapMs);
        }
        if (this.running && !this.sweep && this.cfg.dtcEveryMs > 0 && this.now() - this.lastDtc >= this.cfg.dtcEveryMs) await this.pollDtc();
        this.stats.cycles++;
        const dt = this.now() - t0;
        this.cycleMs = this.cycleMs ? Math.round(this.cycleMs * 0.8 + dt * 0.2) : dt;
        if (!batch.length) await this.sleep(500);
    }

    async run() {
        while (this.running) {
            try {
                if (!this.diag.available) {
                    this.state = 'no diagnostic link';
                    this.onChange();
                    await this.sleep(1000);
                    continue;
                }
                if (this.sweep) {
                    this.state = 'sweep ' + this.sweep.name;
                    await this.sweepStep();
                    await this.sleep(this.cfg.sweepGapMs);
                } else {
                    this.state = 'reading';
                    await this.cycle();
                }
            } catch (e) {
                this.error = String(e && e.message || e);
                this.log.warn('ecu: ' + this.error);
                await this.sleep(500);
            }
        }
    }

    /* ---------------- sweep ---------------- */

    /** Start reading a DID range once. Returns '' or the reason it did not start. */
    startSweep(name) {
        const range = this.cfg.sweeps[name];
        if (!range) return 'unknown range ' + name;
        if (!this.running) return 'start ECU reading first';
        const v = this.speed();
        if (v !== null && v !== undefined && v > 0) return 'park the car first (speed ' + v + ')';
        this.sweep = { name, next: range[0], last: range[1], total: range[1] - range[0] + 1, found: 0, silent: 0 };
        this.log.info('ecu: sweep ' + name + ' started');
        this.onChange();
        return '';
    }

    stopSweep(why) {
        if (!this.sweep) return;
        const s = this.sweep;
        this.sweep = null;
        this.rec.note('sweep', { ecu: hex(this.ecu), range: s.name, found: s.found, at: hex(s.next, 4), end: why || 'stopped' });
        this.log.info('ecu: sweep ' + s.name + ' ended (' + (why || 'stopped') + '), ' + s.found + ' DIDs answered');
        this.save();
        this.onChange();
    }

    async sweepStep() {
        const s = this.sweep;
        const v = this.speed();
        if (v !== null && v !== undefined && v > 0) return this.stopSweep('car moving');
        if (s.next > s.last) return this.stopSweep('done');
        const did = s.next++;
        if (this.byKey['22:' + hex(did, 4)]) return;
        const it = { sid: 0x22, data: [did >> 8, did & 0xFF], label: '22 ' + hex(did, 4), fails: 0, dead: false, retryAt: 0 };
        const r = await this.readItem(it);
        if (r.none) {
            if (++s.silent >= 8) this.stopSweep('ECU not answering');
            return;
        }
        s.silent = 0;
        if (!r.ok) return;
        s.found++;
        const known = this.items.filter((i) => i.found).length;
        if (known < this.cfg.maxDids) {
            const added = this.addDid(did, false);
            added.found = true;
            added.last = hexList(r.payload);
        }
        this.rec.note('did', { ecu: hex(this.ecu), did: hex(did, 4), h: hexList(r.payload) });
    }

    /* ---------------- for the screen ---------------- */

    status() {
        const live = this.items.filter((i) => !i.dead).length;
        return {
            running: this.running, state: this.state, error: this.error, ecu: hex(this.ecu),
            items: live, dead: this.items.length - live, reads: this.stats.reads, ok: this.stats.ok,
            nrc: this.stats.nrc, none: this.stats.none, changes: this.stats.changes, cycleMs: this.cycleMs,
            mil: this.mil, dtcs: this.dtcCount, dtcText: this.dtcText, menu: this.menu,
            sweep: this.sweep ? { name: this.sweep.name, done: this.sweep.next - (this.cfg.sweeps[this.sweep.name][0]), total: this.sweep.total, found: this.sweep.found } : null,
        };
    }
}

module.exports = { EcuLog, DEFAULTS, ALLOWED };
