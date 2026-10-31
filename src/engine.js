'use strict';
/**
 * engine.js - Engine Config (ported from idrive7-apps "Engine Config", the parts that are consistent there).
 *
 *   Exhaust flap, factory DME (Bosch MG1, DID 0x60FD):   open  2F 60FD 03 03E8   close 2F 60FD 03 2328   OEM 2F 60FD 00
 *   Exhaust flap, MHD-flashed DME:                       2F 02 nn   (00 OEM, 01 open, 02 closed)
 *   Engine map 1-4 (MHD):                                2F 01 nn   (nn = map - 1)
 *   Max Cooling (MHD):                                   2F 03 nn   (00 off, 01 on)
 * The MHD DME takes a command by echoing 6F <fn> <nn>; that echo is the only confirmation there is (MHD offers no
 * read-back of the map). The factory flap is read back with 22 62FD / 22 64FD.
 *
 * SAFETY, as in the original: DISARMED by default - then the buttons only simulate and nothing is sent to the car.
 * Arming is a separate, confirmed button, and is forgotten at every start. OEM always hands control back.
 */
const { Diag, hex, NRC } = require('./diag');

const DME = 0x12;
const MHD = { MAP: 0x01, FLAP: 0x02, COOLING: 0x03 };
const FLAP_DID = [0x60, 0xFD];
const tv = (pct) => { const r = Math.round(pct * 100) & 0xFFFF; return [(r >> 8) & 0xFF, r & 0xFF]; };

function create(signals, log) {
    const st = {
        diag: null, error: null, armed: false, platform: 'stock',      // 'stock' (BMW flap) | 'mhd'
        flap: 'OEM', flapOk: null, map: null, mapOk: null, cooling: null, coolingOk: null,
        note: '', busy: false, probe: null,
    };
    async function init(diag) {
        try { st.diag = diag || await new Diag(signals, log).init(); } catch (e) { st.error = String(e); }
        return st;
    }
    const link = () => !!(st.diag && st.diag.available);

    async function send(sid, data) {
        // only the reply to this very request: a positive one (sid + 0x40) or a refusal of the same service
        const mine = (u) => u[0] === sid + 0x40 || (u[0] === 0x7F && u[1] === sid);
        const r = await st.diag.physical(DME, sid, data, 4000, mine);
        if (!r || !r.length) return { ok: false, reason: 'no answer' };
        if (r[0] === 0x7F) return { ok: false, reason: NRC[r[2]] || ('NRC ' + hex(r[2])) };
        return { ok: true, sid: r[0], data: r.slice(1) };
    }
    async function mhd(fn, value) {
        const r = await send(0x2F, [fn, value]);
        const ok = !!r.ok && r.sid === 0x6F && r.data[0] === fn && r.data[1] === value;
        return { ok, detail: ok ? 'DME took it' : (r.reason || 'unexpected reply') };
    }
    async function guarded(label, fn) {
        if (st.busy) return;
        st.busy = true; st.note = 'Sending ' + label + '...';
        try { await fn(); } catch (e) { st.note = label + ': ' + String(e && e.message || e); log.warn(st.note); }
        st.busy = false;
    }

    /** read-only: does this DME expose the factory flap? (22 62FD / 22 64FD) */
    async function probe() {
        if (!link()) { st.note = 'Diagnostic link not ready (ignition on?)'; return; }
        await guarded('probe', async () => {
            const a = await send(0x22, [0x62, 0xFD]), b = await send(0x22, [0x64, 0xFD]);
            st.probe = { supported: !!(a.ok || b.ok), pct: a.ok && a.data.length >= 4 ? ((a.data[2] << 8) | a.data[3]) / 100 : null };
            st.note = st.probe.supported ? 'Factory flap found' + (st.probe.pct !== null ? ' (commanded ' + st.probe.pct + ' %)' : '') : 'No factory flap answer';
        });
    }

    async function setFlap(mode) {          // OEM | OPEN | CLOSED
        st.flap = mode; st.flapOk = null;
        if (!st.armed) { st.flapOk = 'sim'; st.note = 'Simulated (disarmed): nothing sent'; return; }
        if (!link()) { st.flapOk = false; st.note = 'Diagnostic link not ready'; return; }
        await guarded('flap ' + mode, async () => {
            let ok, detail;
            if (st.platform === 'mhd') ({ ok, detail } = await mhd(MHD.FLAP, mode === 'OEM' ? 0 : mode === 'OPEN' ? 1 : 2));
            else {
                const data = mode === 'OEM' ? FLAP_DID.concat([0x00]) : FLAP_DID.concat([0x03], tv(mode === 'OPEN' ? 10 : 90));
                const r = await send(0x2F, data);
                ok = !!r.ok && r.sid === 0x6F; detail = ok ? 'DME accepted' : (r.reason || 'rejected');
            }
            st.flapOk = ok; st.note = 'Flap ' + mode + ': ' + detail;
            log.info(st.note);
        });
    }
    async function setMap(n) {
        st.map = n; st.mapOk = null;
        if (!st.armed) { st.mapOk = 'sim'; st.note = 'Simulated (disarmed): nothing sent'; return; }
        if (st.platform !== 'mhd') { st.mapOk = false; st.note = 'Engine map needs the MHD platform'; return; }
        if (!link()) { st.mapOk = false; st.note = 'Diagnostic link not ready'; return; }
        await guarded('map ' + n, async () => { const r = await mhd(MHD.MAP, n - 1); st.mapOk = r.ok; st.note = 'Map ' + n + ': ' + r.detail; log.info(st.note); });
    }
    async function setCooling(on) {
        st.cooling = on; st.coolingOk = null;
        if (!st.armed) { st.coolingOk = 'sim'; st.note = 'Simulated (disarmed): nothing sent'; return; }
        if (st.platform !== 'mhd') { st.coolingOk = false; st.note = 'Max Cooling needs the MHD platform'; return; }
        if (!link()) { st.coolingOk = false; st.note = 'Diagnostic link not ready'; return; }
        await guarded('cooling', async () => { const r = await mhd(MHD.COOLING, on ? 1 : 0); st.coolingOk = r.ok; st.note = 'Max Cooling ' + (on ? 'on' : 'off') + ': ' + r.detail; log.info(st.note); });
    }
    return { st, init, probe, setFlap, setMap, setCooling };
}

module.exports = { create };
