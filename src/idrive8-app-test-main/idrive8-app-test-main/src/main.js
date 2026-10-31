'use strict';
/**
 * BC / ECU logger for the MGU21 (iDrive 8).
 *
 * Records what the car does while the BC button's tuning menus are used, so the commands can be
 * reproduced by another app:
 *
 *   signals  every change of the SOME/IP fields in watch.json (steering-wheel / controller state,
 *            cluster, Check Control, rpm, speed, drive mode...) - src/signals.js
 *   ECU      live reads of the engine ECU over UDS, read only: the MIL lamp (the BC menu blinks it:
 *            1 blink = menu 1 maps, 2 = menu 2 valves, 3 = menu 3 ethanol), rpm (the needle shows
 *            the position), ethanol %, fault memory, and every DID found by a sweep - src/ecu.js
 *   marks    buttons on the screen that put a labelled line into the log ("needle at position 3")
 *
 * Everything goes to data/bclog-<date>-<time>.jsonl (src/logger.js); tools/analyze_bclog.py
 * turns the files into a report. The app sends nothing but read requests to the car.
 */
const fs = require('fs');
const path = require('path');
const ROOT = path.resolve(__dirname, '..', '..');
const DATA = path.join(ROOT, 'data');
const LOG = path.join(DATA, 'bench.log');
function log(message) {
    const line = new Date().toISOString() + ' ' + message;
    try { fs.appendFileSync(LOG, line + '\n'); } catch (_) {}
    console.log(line);
}
process.on('uncaughtException', (e) => { log('FATAL ' + (e.stack || e)); process.exit(1); });
process.on('unhandledRejection', (e) => log('REJECTION ' + (e.stack || e)));
// Do not log argv: the launcher includes the vehicle's VIN there.
log('PROCESS_START uid=' + process.getuid());
const { OnlineApp } = require('oap-sdk');
const { Logger } = require('./logger');
const { EcuLog } = require('./ecu');
const app = new OnlineApp();
const description = JSON.parse(fs.readFileSync(path.join(ROOT, 'rhmiData', 'application.json')));
const entry = description.application.entryPoints[0].uuid;
let opening = null;
let openCount = 0;

/* ---------------- settings: data/bclog-settings.json, all optional ---------------- */
const SETTINGS = {
    autoStart: true,        // start logging when the app starts
    autoEcu: false,         // start reading the ECU when the app starts (default: only on the button)
    minMs: 20,              // min. ms between two lines of one signal
    cap: 1000,              // max. lines per second
    fileMB: 10,             // new file after
    capMB: 40,              // all log files together
    ecu: {},                // overrides of DEFAULTS in ecu.js (addr, gapMs, blinkGapMs, ...)
};
function readJson(file, fallback) {
    try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { return fallback; }
}
const saved = readJson(path.join(DATA, 'bclog-settings.json'), {});
Object.keys(SETTINGS).forEach((k) => { if (saved && saved[k] !== undefined && typeof saved[k] === typeof SETTINGS[k]) SETTINGS[k] = saved[k]; });

/* ---------------- marks: button -> [label in the log, kind, group] ---------------- */
const MARKS = {
    BTN_MARK_BCLONG: ['BC held 5 s', 'action', ''],
    BTN_MARK_BCSHORT: ['BC short press', 'action', ''],
    BTN_MARK_BL1: ['1 blink (menu 1)', 'state', 'menu'],
    BTN_MARK_BL2: ['2 blinks (menu 2)', 'state', 'menu'],
    BTN_MARK_BL3: ['3 blinks (menu 3)', 'state', 'menu'],
    BTN_MARK_MM1: ['Map 1 (needle 1)', 'state', 'multimap'],
    BTN_MARK_MM2: ['Map 2 (needle 2)', 'state', 'multimap'],
    BTN_MARK_MM3: ['Map 3 (needle 3)', 'state', 'multimap'],
    BTN_MARK_MM4: ['Map 4 (needle 4)', 'state', 'multimap'],
    BTN_MARK_V0: ['Valves 0 (OEM)', 'state', 'valve'],
    BTN_MARK_V1: ['Valves 1 (open)', 'state', 'valve'],
    BTN_MARK_V2: ['Valves 2 (closed)', 'state', 'valve'],
    BTN_MARK_ETH: ['Ethanol reading', 'state', 'ethanol'],
    BTN_MARK_X: ['Custom mark', 'action', ''],
};
const HINT = 'Hold BC 5 s, then short presses; the lamp blinks tell the menu';

const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '~' : s);
const kb = (n) => (n < 1048576 ? Math.max(1, Math.round(n / 1024)) + ' KB' : (n / 1048576).toFixed(1) + ' MB');

/* ---------------- screen ---------------- */
let hmiVisible = false;
const shown = {};                 // what the HMI shows per component (writes only on a change)
function setText(rhmi, name, text) {
    if (!hmiVisible || shown[name] === text) return Promise.resolve();
    const c = rhmi.getByName(name);
    if (!c) return Promise.resolve();
    shown[name] = text;
    return Promise.resolve(c.setValue(0, 'Text', text, 'Text')).catch((e) => { shown[name] = null; log('TEXT_FAILED ' + name + ' ' + (e.stack || e)); });
}

async function showScreen(uuid, source) {
    if (opening) return opening;
    opening = (async () => {
        const state = app.rhmiApplication.getByName('MAIN');
        await app.rhmiApplication.mapEntryState(uuid, state);
        await state.updateResources();
        await app.rhmiApplication.openEntryState(uuid);
        openCount++;
        log('OPEN_OK source=' + source + ' entry=' + uuid + ' count=' + openCount);
    })().catch((e) => log('OPEN_FAILED ' + (e.stack || e))).then(() => { opening = null; });
    return opening;
}
function bindLifecycle() {
    app.on('appStatusProxyReady', () => app.registerActive()
        .then(() => log('ACTIVE')).catch((e) => log('ACTIVE_FAILED ' + e)));
    app.on('shutdown', () => {
        log('SHUTDOWN');
        setTimeout(() => process.exit(0), 100);
    });
    app.on('entryPointExecute', (uuid, reason, name) => {
        log('ENTRY_EXECUTE uuid=' + uuid + ' reason=' + reason + ' name=' + name);
        if (app.rhmiApplication) showScreen(uuid, 'event');
    });
}

function bindScreen(rhmi, state, onShown) {
    state.on('visible', (event) => log('STATE_VISIBLE ' + JSON.stringify(event)));
    rhmi.on('applicationStackChanged', (stack) => {
        log('STACK ' + JSON.stringify(stack));
        const visible = stack && Object.keys(stack).length > 0 && ('ROOT_APP' in stack);
        if (visible !== hmiVisible) {
            hmiVisible = visible;
            if (visible) { Object.keys(shown).forEach((k) => { shown[k] = null; }); onShown(); }
        }
        const p = visible ? app.registerHmiVisible() : app.unregisterHmiVisible();
        Promise.resolve(p).catch((e) => log('VISIBILITY_FAILED ' + e));
    });
}

/* ---------------- the logger ---------------- */
/** "de.bmw.infotainment.controlelements.FrontCCU#x" -> "FrontCCU#x" */
function shortName(name) {
    const i = name.indexOf('#');
    const iface = i < 0 ? name : name.slice(0, i), rest = i < 0 ? '' : name.slice(i);
    return iface.split('.').slice(-1)[0] + rest;
}

async function startLogger(rhmi) {
    const logger = { info: (m) => log('INFO ' + m), warn: (m) => log('WARN ' + m), error: (m) => log('ERROR ' + m) };
    const rec = new Logger({
        dir: DATA, log: logger,
        minMs: () => SETTINGS.minMs, cap: () => SETTINGS.cap, fileMB: () => SETTINGS.fileMB, capMB: SETTINGS.capMB,
    });
    process.on('exit', () => { try { rec.stop(); } catch (e) { /* ignore */ } });
    let signals = null;
    try { signals = require('./signals'); } catch (e) { log('WARN signals unavailable: ' + e); }

    const subs = { total: 0, done: 0, failed: 0, up: 0, started: false };
    const watchers = [];
    let speed = null;                 // km/h from Vehicle2, null = unknown
    let ecu = null;
    let flash = null;                 // { text, until }

    /* ---- SOME/IP fields -> log ---- */
    async function subscribeAll() {
        const watch = readJson(path.join(__dirname, '..', 'watch.json'), {});
        const missing = readJson(path.join(ROOT, 'someipInterfaces', 'missing-interfaces.json'), {});
        Object.keys(missing).forEach((i) => log('WARN this unit has no ' + i + ': ' + missing[i]));
        const ifaces = Object.keys(watch).filter((i) => !missing[i]);
        subs.total = ifaces.length;
        subs.started = true;
        for (const iface of ifaces) {
            const w = watch[iface] || {};
            const insts = [].concat(w.instances || '1').map(String);
            for (const inst of insts) {
                const base = iface + (insts.length > 1 ? '@' + inst : '');
                const handlers = {};
                Object.keys(w.attributes || {}).forEach((f) => {
                    handlers[f] = (v) => {
                        try {
                            rec.value(base + '#' + f, v, false);
                            if (f === 'statusCarSpeed2' && v && typeof v.speedVehicleCentreOfGravity === 'number') speed = v.speedVehicleCentreOfGravity;
                        } catch (e) { log('WARN value ' + f + ': ' + e); }
                    };
                });
                (w.broadcasts || []).forEach((f) => { handlers[f] = (v) => { try { rec.value(base + '#' + f, v, true); } catch (e) { log('WARN event ' + f + ': ' + e); } }; });
                if (!Object.keys(handlers).length) continue;
                try {
                    const s = signals ? await signals.watch(iface, inst, handlers, logger) : { error: 'no signals module' };
                    if (s.error) { subs.failed++; log('WARN not subscribed: ' + base + ': ' + s.error); } else watchers.push(s);
                } catch (e) { subs.failed++; log('WARN not subscribed: ' + base + ': ' + e); }
            }
            subs.done++;
        }
        subs.up = watchers.length;
        log('INFO subscribed: ' + watchers.length + ' of ' + subs.total + ' interfaces' + (subs.failed ? ', ' + subs.failed + ' failed' : ''));
    }

    /* ---- ECU reading ---- */
    async function initEcu() {
        if (!signals) return;
        const { Diag } = require('./diag');
        const diag = await new Diag(signals, logger).init();
        diag.quiet = true;                       // the poll would flood the log with its own requests
        const cfg = Object.assign({}, SETTINGS.ecu);
        ecu = new EcuLog({
            diag, rec, log: logger, cfg, speed: () => speed,
            file: path.join(DATA, 'bclog-dids.json'), fs, onChange: refresh,
        });
        refresh();
        if (SETTINGS.autoEcu) ecu.start();
    }

    /* ---- text on the screen ---- */
    const say = (text) => { flash = { text, until: Date.now() + 4000 }; refresh(); };
    function refresh() {
        const s = rec.status();
        const head = s.error ? 'STOPPED: ' + s.error
            : (s.on ? 'LOGGING  ' : 'PAUSED  ') + s.lines + ' lines, ' + s.signals + ' signals' + (s.dropped ? ', ' + s.dropped + ' dropped' : '') + (s.file ? '  ' + kb(s.bytes) : '');
        setText(rhmi, 'LOG_STATUS', clip(head, 90));
        let e;
        if (!ecu) e = 'ECU: ' + (signals ? 'diagnostic link starting...' : 'no SOME/IP module');
        else {
            const t = ecu.status();
            if (!t.running) e = 'ECU ' + t.ecu + ': off (' + t.items + ' items)';
            else if (t.sweep) e = 'ECU ' + t.ecu + ': sweep ' + t.sweep.name + ' ' + t.sweep.done + '/' + t.sweep.total + ', ' + t.sweep.found + ' answered';
            else e = 'ECU ' + t.ecu + ': ' + t.state + ', ' + t.ok + '/' + t.reads + ' ok, ' + t.items + ' items' + (t.cycleMs ? ', cycle ' + t.cycleMs + ' ms' : '') + (t.error ? ' - ' + t.error : '');
        }
        setText(rhmi, 'ECU_STATUS', clip(e, 110));
        let m = 'BC menu: -';
        if (ecu) {
            const t = ecu.status();
            const lamp = t.mil === null ? 'lamp ?' : t.mil ? 'lamp ON' : 'lamp off';
            m = 'BC menu: ' + (t.menu === null ? '-' : t.menu === 0 ? 'left' : t.menu + (t.menu === 1 ? ' (maps)' : t.menu === 2 ? ' (valves)' : t.menu === 3 ? ' (ethanol)' : '')) +
                '   ' + lamp + (t.dtcs ? ', ' + t.dtcs + ' DTC' : '') + (subs.started ? '   signals ' + subs.up + '/' + subs.total : '');
        }
        setText(rhmi, 'ECU_MENU', clip(m, 110));
        setText(rhmi, 'MESSAGE', flash && Date.now() < flash.until ? flash.text : HINT);
        setText(rhmi, 'BTN_LOG', rec.on ? 'Logging: pause' : 'Logging: resume');
        setText(rhmi, 'BTN_ECU', ecu && ecu.running ? 'ECU reading: stop' : 'ECU reading: start');
        setText(rhmi, 'BTN_SWEEP', ecu && ecu.sweep ? 'ECU sweep: stop' : 'ECU sweep: start');
    }
    setInterval(refresh, 1000);

    /* ---- buttons ---- */
    const on = (name, fn) => {
        const c = rhmi.getByName(name);
        if (c) c.on('action', () => Promise.resolve().then(fn).catch((e) => log('BUTTON_FAILED ' + name + ' ' + (e.stack || e))));
        else log('WARN no component ' + name);
    };
    on('BTN_LOG', () => {
        if (rec.on) { rec.stop(); say('Logging paused'); } else say(rec.start() ? 'Logging started' : 'Cannot write the log: ' + rec.error);
    });
    on('BTN_NEWFILE', () => say(rec.newFile() ? 'New file started' : 'Not logging'));
    on('BTN_ECU', () => {
        if (!ecu) return say('Diagnostic link not ready yet');
        if (ecu.running) { ecu.stop(); return say('ECU reading stopped'); }
        if (!rec.on && !rec.start()) return say('Cannot write the log: ' + rec.error);
        ecu.start();
        say('ECU reading started');
    });
    on('BTN_SWEEP', () => {
        if (!ecu) return say('Diagnostic link not ready yet');
        if (ecu.sweep) { ecu.stopSweep('stopped by hand'); return say('Sweep stopped'); }
        if (!ecu.running) { if (!rec.on && !rec.start()) return say('Cannot write the log: ' + rec.error); ecu.start(); }
        // the ranges in turn: the next press after one finished starts the next range
        const names = Object.keys(ecu.cfg.sweeps);
        ecu.sweepNext = ((ecu.sweepNext || 0)) % names.length;
        const why = ecu.startSweep(names[ecu.sweepNext]);
        if (why) return say(why);
        say('Sweep ' + names[ecu.sweepNext] + ' started');
        ecu.sweepNext++;
    });
    const counts = {};
    Object.keys(MARKS).forEach((btn) => {
        on(btn, () => {
            const m = MARKS[btn];
            const n = rec.mark(m[0], m[1], m[2], btn);
            if (!n) return say('Logging is paused - mark NOT saved');
            counts[btn] = (counts[btn] || 0) + 1;
            log('INFO mark #' + n + ' ' + m[0]);
            say('Mark #' + n + ': ' + m[0] + ' (' + counts[btn] + 'x)');
        });
    });

    if (SETTINGS.autoStart) rec.start();
    refresh();
    // not awaited: subscribing takes a while and the screen must not wait for it
    subscribeAll().then(refresh, (e) => log('ERROR subscribeAll ' + (e.stack || e)));
    initEcu().catch((e) => log('ERROR initEcu ' + (e.stack || e)));
    return { rec, refresh, get ecu() { return ecu; } };
}

async function main() {
    bindLifecycle();
    await app.start();
    log('SDK_STARTED reason=' + app.startReason);
    const rhmi = app.rhmiApplication;
    if (!rhmi) throw Error('SDK returned without RHMI application');
    const state = rhmi.getByName('MAIN');
    await rhmi.mapEntryState(entry, state);
    log('MAP_OK entry=' + entry + ' state=' + state.getId());
    let logger = null;
    bindScreen(rhmi, state, () => { if (logger) logger.refresh(); });
    await app.registerActive();
    await app.initialized();
    log('INITIALIZED');
    // the logger runs from the start, whether or not the screen is open
    logger = await startLogger(rhmi);
    const initial = app.getLastExecutedEntryPointUuid();
    if (initial && ['ENTRY_POINT', 'BOOKMARK', 'LUM'].indexOf(String(app.startReason)) >= 0) {
        await showScreen(initial, 'cold-start');
    }
    // Deliberately no timer or diagnostic auto-open: success needs a real execute.
}
main().catch((e) => { log('START_FAILED ' + (e.stack || e)); process.exit(1); });
