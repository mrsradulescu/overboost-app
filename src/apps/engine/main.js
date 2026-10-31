'use strict';
/**
 * OVERBOOST Engine Config: the idrive7-apps "Live Adjustments" screen (ui.js, unchanged: twin tailpipes with the
 * butterfly valves, the state word, OEM / Open / Close) on OVERBOOST's engine.js (DME over the diagnostic link).
 *   Exhaust Flap: always.   Engine Map, Max Cooling: with "Tuning software: MHD" in Settings.
 * Safety as before: "Exhaust Flap: Disarmed" (simulated, nothing is sent) is the setting at every start and when
 * the app is left; the commands leave only on a button press.
 */
const mgu = require('../../kit/mgu');
const G = require('../../kit/gauges');
const UI = require('./ui');

mgu.run({
    async start(ctx) {
        const { ui, settings, host } = ctx;
        const engine = host.svc && host.svc.engine;
        const st = engine ? engine.st : null;
        settings.set('arm', 'off');                       // never armed from an earlier session
        let page = 'Exhaust Flap', angle = 0.18;
        let probed = false;

        const sync = () => {
            if (!st) return;
            st.armed = settings.get('arm') === 'on';
            st.platform = settings.get('tuner') === 'mhd' ? 'mhd' : 'stock';
        };
        const pages = () => ['Exhaust Flap'].concat(st && st.platform === 'mhd' ? ['Engine Map', 'Max Cooling'] : []);
        const okOf = (v) => (v === true ? 'confirmed' : null);
        const acts = {
            oem: () => engine.setFlap('OEM'), open: () => engine.setFlap('OPEN'), close: () => engine.setFlap('CLOSED'),
            map: (n) => () => engine.setMap(n),
            cooling: (on) => () => engine.setCooling(on),
        };
        const target = (v) => {
            const s = v.confirmed !== 'UNKNOWN' ? v.confirmed : v.requested;
            return s === 'OPEN' ? 1 : s === 'CLOSED' ? 0 : s === 'OEM' ? 0.18 : 0.5;
        };

        async function render() {
            const t = ctx.theme(), cv = ctx.canvas(UI.W, UI.H);
            if (!st) { cv.clear(t.bg); cv.text('Diagnostics unavailable (no SOME/IP module)', 800, 260, 30, t.dim, { align: 'center' }); await ui.image('MAIN_GFX', cv); return; }
            sync();
            if (!probed && st.diag && st.diag.available && st.platform !== 'mhd') { probed = true; engine.probe().catch(() => {}); }
            const list = pages();
            if (list.indexOf(page) < 0) page = list[0];
            const v = {
                requested: st.flap, confirmed: st.flapOk === true ? st.flap : 'UNKNOWN', simulated: !st.armed,
                error: st.flapOk === false ? (st.note || 'not accepted') : null,
                pages: list, page,
                tune: {
                    armed: st.armed, tuner: st.platform, features: { map: true, cooling: true },
                    map: st.map, mapConfirmed: st.mapOk === true, readback: false,
                    cooling: st.cooling === null ? null : st.cooling ? 'ON' : 'OFF', rpm: null, minCoolingRpm: 0,
                    busy: !!st.busy, note: st.note || '', noteType: st.mapOk === false || st.coolingOk === false ? 'warn' : '',
                },
            };
            const tg = target(v);
            angle += (tg - angle) * 0.25;
            if (Math.abs(tg - angle) < 0.003) angle = tg;
            v.angle = angle;
            UI.draw(cv, G, t, v, acts);
            await ui.image('MAIN_GFX', cv);
        }

        ctx.onTab((i) => { const l = pages(); if (l[i]) page = l[i]; return render(); });
        ctx.loop('MAIN', 20, render);
    },
});
