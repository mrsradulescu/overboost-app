'use strict';
/** OVERBOOST Update in the kit's look: check, install, restart, go back to the installed version. */
const mgu = require('../../kit/mgu');
const G = require('../../kit/gauges');
const { withAlpha } = require('../../kit/canvas');

const W = 1600, H = 520;

mgu.run({
    async start(ctx) {
        const { ui, host } = ctx;
        const upd = host.svc && host.svc.upd;

        function wrap(text, n) {
            const out = []; let line = '';
            String(text || '').split(/\s+/).forEach((w) => { if ((line + ' ' + w).trim().length > n) { out.push(line); line = w; } else line = (line + ' ' + w).trim(); });
            if (line) out.push(line);
            return out;
        }
        async function render() {
            const t = ctx.theme(), cv = ctx.canvas(W, H).clear(t.bg);
            if (!upd) { G.panel(cv, t, 24, G.TOP, 1552, G.BOTTOM - G.TOP, 'Update'); cv.text('Update is not available', 56, 140, 30, t.dim); }
            else {
                const st = upd.st, cur = upd.cur(), newer = !!(st.found && st.found.newer);
                G.panel(cv, t, 24, G.TOP, 700, G.BOTTOM - G.TOP, 'Versions');
                cv.text('Installed', 56, 130, 24, t.dim);
                cv.text('OVERBOOST ' + upd.base.version, 56, 176, 40, t.text, { font: 'bold' });
                cv.text('Downloaded', 56, 240, 24, t.dim);
                cv.text(cur ? cur.version : '-', 56, 286, 40, cur ? t.text : t.dim, { font: 'bold' });
                cv.text('Available', 56, 350, 24, t.dim);
                cv.text(st.found ? st.found.tag + (newer ? '   NEW' : '   (up to date)') : '-', 56, 396, 40, newer ? '#2fd27a' : t.dim, { font: 'bold' });
                G.panel(cv, t, 748, G.TOP, 828, G.BOTTOM - G.TOP, 'Update');
                wrap(st.msg || 'Press "Check for updates". The car needs an internet connection.', 62).slice(0, 3).forEach((l, i) => cv.text(l, 776, 130 + i * 32, 26, t.text));
                if (st.pending) cv.text('Restart OVERBOOST to use ' + st.pending, 776, 226, 24, t.warn);
                G.button(cv, t, 776, 244, 380, 72, st.busy ? 'Working...' : 'Check for updates', () => { upd.check(); return null; }, { key: 'check', busy: !!st.busy });
                G.button(cv, t, 1172, 244, 380, 72, newer ? 'Update now (' + st.found.tag + ')' : 'Update now', () => { upd.install(); return null; }, { key: 'install', primary: newer, busy: !!st.busy });
                G.button(cv, t, 776, 336, 380, 72, 'Restart OVERBOOST', () => { host.svc.restart(); return null; }, { key: 'restart', confirm: true });
                G.button(cv, t, 1172, 336, 380, 72, 'Installed version', () => { upd.remove(); return null; }, { key: 'revert', confirm: true, color: t.crit });
                cv.text('Restart: close OVERBOOST; open it again from the menu.', 776, 450, 22, t.dim);
            }
            G.frame(cv, t, ['Update'], 0, []);
            await ui.image('MAIN_GFX', cv);
        }
        G.onConfirm(() => render());
        ctx.loop('MAIN', 4, render);
    },
});
