'use strict';
/**
 * BC Logger - a passive recorder for working out which signals the head unit sees when the BC
 * button, the steering-wheel thumb wheel or a menu on the instrument cluster is used.
 *
 *   It only LISTENS: it subscribes to the SOME/IP fields and events listed in app.json
 *   ("signals" + "config.watch", generated from your unit's catalog by kit/tools/gen_bclog_signals.py)
 *   and writes every change, with a timestamp, to data/bclog-<date>-<time>.jsonl (see logger.js).
 *   It sends nothing to the car: no UDS, no method calls, no writes.
 *
 *   The MARK buttons put a labelled line into the same file. The workflow is: tap the MARK for what
 *   you are about to do (or what the cluster shows), then do it on the car. kit/tools/analyze_bclog.py
 *   then lists the signals that changed right after each kind of mark.
 */
const mgu = require('./kit/mgu');
const { Logger } = require('./logger');

// button, label in the log, kind (action = something I do; state = what the cluster shows), group
const MARKS = [
    ['BTN_MARK_BC', 'BC button pressed', 'action', ''],
    ['BTN_MARK_UP', 'Wheel up', 'action', ''],
    ['BTN_MARK_DOWN', 'Wheel down', 'action', ''],
    ['BTN_MARK_MM1', 'Multimap 1', 'state', 'multimap'],
    ['BTN_MARK_MM2', 'Multimap 2', 'state', 'multimap'],
    ['BTN_MARK_MM3', 'Multimap 3', 'state', 'multimap'],
    ['BTN_MARK_MM4', 'Multimap 4', 'state', 'multimap'],
    ['BTN_MARK_V0', 'Valve 0 (OEM)', 'state', 'valve'],
    ['BTN_MARK_V1', 'Valve 1 (open)', 'state', 'valve'],
    ['BTN_MARK_V2', 'Valve 2 (closed)', 'state', 'valve'],
    ['BTN_MARK_ETH', 'Ethanol screen', 'state', 'ethanol'],
    ['BTN_MARK_X', 'Custom mark', 'action', ''],
];

/** "de.bmw.infotainment.controlelements.FrontCCU#statusX" -> "FrontCCU#statusX" */
function short(name) {
    const i = name.indexOf('#');
    const iface = i < 0 ? name : name.slice(0, i), rest = i < 0 ? '' : name.slice(i);
    return iface.split('.').slice(-1)[0] + rest;
}
const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '~' : s);
const kb = (n) => (n < 1048576 ? Math.max(1, Math.round(n / 1024)) + ' KB' : (n / 1048576).toFixed(1) + ' MB');

mgu.run({
    async start(ctx) {
        const { ui, settings, log } = ctx;
        const cfg = ctx.spec.config || {};
        const watch = cfg.watch || {};

        const rec = new Logger({
            dir: ctx.dataDir,
            log,
            minMs: () => Number(settings.get('minMs')),
            cap: () => Number(settings.get('cap')),
            fileMB: () => Number(settings.get('fileMB')),
            capMB: cfg.capMB || 40,
        });
        process.on('exit', () => { try { rec.stop(); } catch (e) { /* ignore */ } });

        /* ---------------- subscriptions ---------------- */
        const watchers = [];
        const subs = { total: 0, done: 0, failed: 0, started: false };

        async function subscribeAll() {
            const ifaces = Object.keys(watch);
            subs.total = ifaces.length;
            subs.started = true;
            for (const iface of ifaces) {
                const w = watch[iface] || {};
                const insts = w.instances && w.instances.length ? w.instances : ['1'];
                for (const inst of insts) {
                    const base = iface + (insts.length > 1 ? '@' + inst : '');
                    const handlers = {};
                    (w.fields || []).forEach((f) => { handlers[f] = (v) => { try { rec.value(base + '#' + f, v, false); } catch (e) { log.warn('value ' + f + ': ' + e); } }; });
                    (w.events || []).forEach((f) => { handlers[f] = (v) => { try { rec.value(base + '#' + f, v, true); } catch (e) { log.warn('event ' + f + ': ' + e); } }; });
                    if (!Object.keys(handlers).length) continue;
                    try {
                        const s = await ctx.signals.watch(iface, inst, handlers, log);
                        if (s.error) { subs.failed++; log.warn('not subscribed: ' + base + ': ' + s.error); } else watchers.push(s);
                    } catch (e) {
                        subs.failed++;
                        log.warn('not subscribed: ' + base + ': ' + e);
                    }
                }
                subs.done++;
            }
            log.info('subscribed: ' + watchers.length + ' of ' + subs.total + ' interfaces' + (subs.failed ? ', ' + subs.failed + ' failed' : ''));
        }

        /* ---------------- screen ---------------- */
        const shown = {};
        const setText = (name, text) => { if (shown[name] === text) return; shown[name] = text; ui.text(name, text); };
        let flash = null;                // { text, until }: the answer to the last tap

        function refresh() {
            const s = rec.status();
            let head;
            if (s.error) head = 'STOPPED: ' + s.error;
            else head = (s.on ? 'LOGGING  ' : 'PAUSED  ') + s.lines + ' lines, ' + s.signals + ' signals' + (s.dropped ? ', ' + s.dropped + ' dropped' : '');
            setText('LOG_STATUS', clip(head, 64));
            const up = watchers.filter((w) => w.available).length;
            setText('LOG_STATUS2', !subs.started ? 'Subscribing...' :
                subs.done < subs.total ? 'Subscribing ' + subs.done + '/' + subs.total + '...' :
                    watchers.length + ' interfaces listened to, ' + up + ' answering' + (subs.failed ? ', ' + subs.failed + ' failed (see log)' : ''));
            let last;
            if (flash && Date.now() < flash.until) last = flash.text;
            else if (s.quiet.length) { const q = s.quiet[s.quiet.length - 1]; last = 'Last: ' + short(q.name) + '  ' + (q.ageMs / 1000).toFixed(1) + ' s ago'; }
            else last = 'Last: (no rare signal changed yet)';
            setText('LOG_LAST', clip(last, 64));
            setText('LOG_FILE', s.file ? clip(s.file + '  ' + kb(s.bytes), 64) : 'No file');
        }
        setInterval(() => { if (ui.isVisible('MAIN')) refresh(); }, 1000);
        const say = (text) => { flash = { text, until: Date.now() + 4000 }; refresh(); };

        ui.onClick('BTN_LOG', () => {
            if (rec.on) { rec.stop(); say('Logging paused'); } else { say(rec.start() ? 'Logging started' : 'Cannot write the log: ' + rec.error); }
        });
        ui.onClick('BTN_NEWFILE', () => { say(rec.newFile() ? 'New file started' : 'Not logging'); });
        const counts = {};
        MARKS.forEach((m) => {
            ui.onClick(m[0], () => {
                const n = rec.mark(m[1], m[2], m[3], m[0]);
                if (!n) return say('Logging is paused - mark NOT saved');
                counts[m[0]] = (counts[m[0]] || 0) + 1;
                log.info('mark #' + n + ' ' + m[1]);
                say('Mark #' + n + ': ' + m[1] + ' (' + counts[m[0]] + 'x)');
            });
        });

        if (settings.get('autoStart')) rec.start();
        refresh();
        // not awaited: with a few hundred fields it takes a while, and the app must not block its start
        subscribeAll().then(refresh, (e) => log.error('subscribeAll', e));
    },
});
