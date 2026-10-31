'use strict';
/**
 * OVERBOOST Engine Config in the kit's look (same fonts, theme, panels and buttons as the other apps).
 * The logic is engine.js (shared with the text screens): DISARMED by default (buttons only simulate),
 * arming needs a second press, and leaving the app disarms it again (main.js).
 *   pages: Control / Exhaust flap / Engine map
 */
const mgu = require('../../kit/mgu');
const G = require('../../kit/gauges');
const { withAlpha } = require('../../kit/canvas');

const W = 1600, H = 520;

mgu.run({
    async start(ctx) {
        const { ui, host } = ctx;
        const engine = host.svc && host.svc.engine;
        let page = 0;
        const GO = '#2fd27a';

        function row(cv, t, x, y, label, value, color) {
            cv.text(label, x, y, 24, t.dim);
            cv.text(String(value), x + 260, y, 28, color || t.text, { font: 'bold' });
        }
        function wrap(text, n) {
            const out = []; let line = '';
            String(text || '').split(/\s+/).forEach((w) => { if ((line + ' ' + w).trim().length > n) { out.push(line); line = w; } else line = (line + ' ' + w).trim(); });
            if (line) out.push(line);
            return out;
        }
        const okText = (v) => (v === true ? 'confirmed by the DME' : v === 'sim' ? 'simulated' : v === false ? 'NOT accepted' : '');

        function drawControl(cv, t, st) {
            const link = !!(st.diag && st.diag.available);
            const p = G.panel(cv, t, 24, G.TOP, 760, G.BOTTOM - G.TOP, 'Control');
            const col = st.armed ? t.crit : t.dim;
            cv.roundRect(48, 100, 712, 84, 20, withAlpha(st.armed ? t.crit : '#ffffff', st.armed ? 0.22 : 0.07));
            cv.text(st.armed ? 'ARMED' : 'DISARMED', 404, 158, 52, st.armed ? t.crit : t.text, { align: 'center', font: 'bold', spacing: 3 });
            cv.text(st.armed ? 'Buttons act on the real car' : 'Simulated: nothing is sent to the car', 404, 218, 24, col, { align: 'center' });
            G.button(cv, t, 48, 250, 712, 72, st.armed ? 'Disarm (simulate only)' : 'Arm control (send to the car)', () => {
                if (st.armed) { st.armed = false; return null; }
                st.note = 'Armed. Keep the car parked with the ignition on.'; st.armed = true; return null;
            }, st.armed ? { key: 'arm' } : { key: 'arm', confirm: true, color: t.crit });
            G.button(cv, t, 48, 340, 350, 64, 'Platform: ' + (st.platform === 'mhd' ? 'MHD' : 'Factory'), () => { st.platform = st.platform === 'mhd' ? 'stock' : 'mhd'; return null; }, { key: 'platform' });
            G.button(cv, t, 410, 340, 350, 64, st.busy ? 'Working...' : 'Detect factory flap', () => { engine.probe(); return null; }, { key: 'probe', busy: !!st.busy });
            const note = wrap(st.note || 'Arm only with the car parked. OEM hands the flap back to the engine control.', 56);
            note.slice(0, 2).forEach((l, i) => cv.text(l, 48, 450 + i * 28, 22, t.dim));
            G.panel(cv, t, 808, G.TOP, 768, G.BOTTOM - G.TOP, 'Status');
            row(cv, t, 836, 128, 'Platform', st.platform === 'mhd' ? 'MHD-flashed DME' : 'Factory DME');
            row(cv, t, 836, 178, 'Link', link ? 'ready' : 'waiting', link ? GO : t.warn);
            row(cv, t, 836, 228, 'Exhaust flap', st.flap + (okText(st.flapOk) ? '  (' + okText(st.flapOk) + ')' : ''));
            row(cv, t, 836, 278, 'Engine map', (st.map || '-') + (st.map && okText(st.mapOk) ? '  (' + okText(st.mapOk) + ')' : ''));
            row(cv, t, 836, 328, 'Max Cooling', st.cooling === null ? '-' : st.cooling ? 'on' : 'off');
            if (st.probe) wrap(typeof st.probe === 'string' ? st.probe : JSON.stringify(st.probe), 60).slice(0, 3).forEach((l, i) => cv.text(l, 836, 396 + i * 28, 22, t.dim));
        }

        function choice(cv, t, x, y, w, label, active, run, st) {
            G.button(cv, t, x, y, w, 84, label, run, { key: 'c:' + label, primary: active, busy: !!st.busy });
        }
        function drawFlap(cv, t, st) {
            G.panel(cv, t, 24, G.TOP, 1552, G.BOTTOM - G.TOP, 'Exhaust flap');
            cv.text('Control: ' + (st.armed ? 'ARMED' : 'DISARMED (simulated)') + '      Platform: ' + (st.platform === 'mhd' ? 'MHD' : 'Factory'), 56, 120, 26, st.armed ? t.crit : t.dim);
            const bw = 480;
            [['OEM', 'OEM - engine control'], ['OPEN', 'Open'], ['CLOSED', 'Closed']].forEach(([m, label], i) =>
                choice(cv, t, 56 + i * (bw + 24), 160, bw, label, st.flap === m, () => { engine.setFlap(m); return null; }, st));
            cv.text('Requested: ' + st.flap + (okText(st.flapOk) ? '   (' + okText(st.flapOk) + ')' : ''), 56, 300, 30, t.text, { font: 'bold' });
            wrap(st.note || '', 90).slice(0, 3).forEach((l, i) => cv.text(l, 56, 350 + i * 30, 22, t.dim));
        }
        function drawMap(cv, t, st) {
            G.panel(cv, t, 24, G.TOP, 1552, G.BOTTOM - G.TOP, 'Engine map and Max Cooling');
            cv.text('Control: ' + (st.armed ? 'ARMED' : 'DISARMED (simulated)') + '      Platform: ' + (st.platform === 'mhd' ? 'MHD' : 'Factory') + (st.platform === 'mhd' ? '' : '   - the map needs MHD'), 56, 120, 26, st.armed ? t.crit : t.dim);
            for (let n = 1; n <= 4; n++) choice(cv, t, 56 + (n - 1) * 372, 150, 348, 'Map ' + n, st.map === n, () => { engine.setMap(n); return null; }, st);
            choice(cv, t, 56, 262, 732, 'Max Cooling on', st.cooling === true, () => { engine.setCooling(true); return null; }, st);
            choice(cv, t, 812, 262, 732, 'Max Cooling off', st.cooling === false, () => { engine.setCooling(false); return null; }, st);
            cv.text('Map: ' + (st.map || '-') + (st.map && okText(st.mapOk) ? '  ' + okText(st.mapOk) : '') + '      Max Cooling: ' + (st.cooling === null ? '-' : st.cooling ? 'on' : 'off') + (st.cooling !== null && okText(st.coolingOk) ? '  ' + okText(st.coolingOk) : ''), 56, 392, 28, t.text, { font: 'bold' });
            wrap(st.note || 'MHD answers by echoing the command; there is no read-back of the active map.', 90).slice(0, 2).forEach((l, i) => cv.text(l, 56, 436 + i * 30, 22, t.dim));
        }

        async function render() {
            const t = ctx.theme(), cv = ctx.canvas(W, H).clear(t.bg);
            if (!engine) {
                G.panel(cv, t, 24, G.TOP, 1552, G.BOTTOM - G.TOP, 'Engine Config');
                cv.text('Diagnostics unavailable (no SOME/IP module)', 56, 140, 30, t.dim);
            } else {
                const st = engine.st;
                (page === 0 ? drawControl : page === 1 ? drawFlap : drawMap)(cv, t, st);
            }
            G.frame(cv, t, ['Control', 'Exhaust flap', 'Engine map'], page, [engine && engine.st.armed && { text: 'ARMED', color: 'crit' }]);
            await ui.image('MAIN_GFX', cv);
        }
        G.onConfirm(() => render());
        ctx.onTab((i) => { page = i; return render(); });
        ctx.loop('MAIN', 10, render);
    },
});
