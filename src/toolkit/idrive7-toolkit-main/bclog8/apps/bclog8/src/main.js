'use strict';
/**
 * bclog8 - BC Logger for iDrive 8 (MGU ID8, MainApp-21).
 *
 * A recorder with two sources, both READ ONLY, written with a time stamp to data/bclog-<date>-<time>.jsonl
 * (logger.js):
 *   - it LISTENS to SOME/IP fields and events (steering wheel, cluster, Check Control, rpm, speed...);
 *   - on the ECU button it READS the engine ECU over UDS (ecu.js through the head unit's own gateway,
 *     diag.js + transport.js): the check-engine lamp (the BC menu blinks it: 1 / 2 / 3 blinks = menu
 *     1 maps / 2 valves / 3 ethanol -> derived#bcmenu), ethanol %, rpm, fault memory, and on request a
 *     sweep for more data identifiers. ecu.js sends nothing but service 0x01, 0x22 and 0x19 02.
 * The MARK rows put a labelled line into the same file; tools/analyze_bclog.py lists what changed after
 * each kind of mark and decodes the BC menu.
 *
 * How an app starts on iDrive 8 (the way the apps in IterioDev/car-apps, which run on a real car, do it):
 *   - OnlineAppBackground, with its AppStatus service replaced so a tap on the tile (changeVisibility)
 *     opens the app's entry state; registerActive() once the service is up, or MainApp drops the app.
 *   - the RHMI application from RhmiApplicationFactory.create(uid, { appletIdOffset: 3000 }); the
 *     appletId, the entry point uuid come from rhmiData/application.json (the deploy tool may change the
 *     appletId at install time, so nothing is hard-coded here).
 *   - SOME/IP through someipBuilders.buildAndInitTypedProxy(interface, instance): p[field].subscribe(fn).
 *   - console output goes to DLT, which nobody reads on the car: the app keeps its own small log file.
 * Node 8 (ES2017): no optional chaining, no object spread.
 */
const fs = require('fs');
const path = require('path');
const util = require('util');
const RhmiManager = require('oap-sdk/src/rhmi/RhmiManager');
const RhmiResourcesLocator = require('oap-sdk/src/rhmi/RhmiResourcesLocator');
const MultiLanguageStringBundleFactory = require('oap-sdk/src/rhmi/MultiLanguageStringBundleFactory');
const RhmiApplicationFactory = require('oap-sdk/src/rhmi/RhmiApplicationFactory');
const ResourceManager = require('oap-sdk/src/rhmi/ResourceManager');
const CurrentVehicleUnits = require('oap-sdk/src/core/CurrentVehicleUnits');
const someipBuilders = require('oap-sdk/src/someip/someipBuilders');
const OnlineAppBackground = require('oap-sdk/src/core/OnlineAppBackground');
const { Logger } = require('./logger.js');
const { EcuLog } = require('./ecu.js');
const { Diag } = require('./diag.js');
const transportLib = require('./transport.js');

const APPLETID_OFFSET = 3000;
const RETRY_MS = Number(process.env.BCLOG8_RETRY_MS) || 30000;    // (the env names are for the tests)
const MAX_TRIES = Number(process.env.BCLOG8_TRIES) || 10;
const UID = process.getuid();
const HOME = '/var/opt/public/oap/oap_' + UID;
const DATA = process.env.BCLOG8_DATA || HOME + '/data';
const LOG_FILE = DATA + '/bclog8.log';
const STATE = 'MAIN';

// button, label in the log, kind (action = something I do; state = what the cluster shows), group
const MARKS = [
    ['BTN_MARK_BCLONG', 'BC held 5 s', 'action', ''],
    ['BTN_MARK_BCSHORT', 'BC short press', 'action', ''],
    ['BTN_MARK_BL1', '1 blink (menu 1)', 'state', 'menu'],
    ['BTN_MARK_BL2', '2 blinks (menu 2)', 'state', 'menu'],
    ['BTN_MARK_BL3', '3 blinks (menu 3)', 'state', 'menu'],
    ['BTN_MARK_MM1', 'Map 1 (needle 1)', 'state', 'multimap'],
    ['BTN_MARK_MM2', 'Map 2 (needle 2)', 'state', 'multimap'],
    ['BTN_MARK_MM3', 'Map 3 (needle 3)', 'state', 'multimap'],
    ['BTN_MARK_MM4', 'Map 4 (needle 4)', 'state', 'multimap'],
    ['BTN_MARK_V0', 'Valves 0 (OEM)', 'state', 'valve'],
    ['BTN_MARK_V1', 'Valves 1 (open)', 'state', 'valve'],
    ['BTN_MARK_V2', 'Valves 2 (closed)', 'state', 'valve'],
    ['BTN_MARK_ETH', 'Ethanol reading', 'state', 'ethanol'],
    ['BTN_MARK_X', 'Custom mark', 'action', ''],
];

const errText = (e) => String(e && e.message || e).slice(0, 120);
function say() {
    const line = [new Date().toISOString()].concat([].slice.call(arguments)
        .map((x) => (typeof x === 'string' ? x : util.inspect(x, { depth: 3 }).slice(0, 1500)))).join(' ');
    try { console.error('[bclog8]', line); } catch (e) { /* ignore */ }
    try { if (fs.statSync(LOG_FILE).size > 256 * 1024) fs.writeFileSync(LOG_FILE, ''); } catch (e) { /* no file yet */ }
    try { fs.appendFileSync(LOG_FILE, line + '\n'); } catch (e) { /* no data folder yet */ }
}
const log = { info: say, warn: say, error: say };

/* ---------------- the package's own files ---------------- */
function packageRoot() {
    const here = typeof __dirname === 'string' ? __dirname : HOME + '/source/javascript';
    const cands = [path.join(here, '..'), here, HOME + '/source', HOME];
    for (let i = 0; i < cands.length; i++) {
        if (fs.existsSync(path.join(cands[i], 'rhmiData', 'application.json'))) return cands[i];
    }
    throw new Error('rhmiData/application.json not found near ' + here);
}
const readJson = (f, dflt) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch (e) { return dflt; } };

const ROOT = packageRoot();
const APP = readJson(path.join(ROOT, 'rhmiData', 'application.json'), {}).application || {};
const APPLET_ID = APP.appletId;
const ENTRY_UUID = APP.entryPoints && APP.entryPoints[0] && APP.entryPoints[0].uuid;
const CFG = readJson(path.join(ROOT, 'config', 'watch.json'), { watch: {}, minMs: 50, cap: 1000, fileMB: 10, capMB: 40, autoStart: true });
// optional overrides without a new install: data/bclog-settings.json (same keys as config/watch.json; "ecu" is merged)
const SAVED = readJson(DATA + '/bclog-settings.json', {});
Object.keys(SAVED || {}).forEach((k) => {
    if (k === 'watch') return;
    if (k === 'ecu' && SAVED.ecu && typeof SAVED.ecu === 'object') CFG.ecu = Object.assign({}, CFG.ecu || {}, SAVED.ecu);
    else if (typeof SAVED[k] === typeof CFG[k]) CFG[k] = SAVED[k];
});

/** "de.bmw.infotainment.controlelements.FrontCCU#statusX" -> "FrontCCU#statusX" */
function short(name) {
    const i = name.indexOf('#');
    const iface = i < 0 ? name : name.slice(0, i), rest = i < 0 ? '' : name.slice(i);
    return iface.split('.').slice(-1)[0] + rest;
}
const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '~' : s);
const kb = (n) => (n < 1048576 ? Math.max(1, Math.round(n / 1024)) + ' KB' : (n / 1048576).toFixed(1) + ' MB');

/* ---------------- recorder ---------------- */
const rec = new Logger({
    dir: DATA,
    log,
    minMs: () => Number(CFG.minMs),
    cap: () => Number(CFG.cap),
    fileMB: () => Number(CFG.fileMB),
    capMB: CFG.capMB || 40,
});
process.on('exit', () => { try { if (ecu) ecu.stop(); rec.stop(); } catch (e) { /* ignore */ } });

/* ---------------- subscriptions ---------------- */
const subs = { total: 0, done: 0, failed: 0, answering: 0, started: false, bad: [] };
const proxies = [];
let speed = null;                // km/h from Vehicle2, null = unknown (the ECU sweep only runs when parked)
let ecu = null;                  // EcuLog, once the diagnostic link is up
let ecuError = null;

/* One interface instance: build the typed proxy, subscribe every field / event. Resolves { ok, missing }. */
function watchOne(iface, inst, w) {
    const base = iface + (w.instances.length > 1 ? '@' + inst : '');
    return someipBuilders.buildAndInitTypedProxy(iface, String(inst), { timeoutMs: 10000 }).then((p) => {
        proxies.push(p);
        const wanted = [];
        (w.fields || []).forEach((f) => wanted.push([f, false]));
        (w.events || []).forEach((f) => wanted.push([f, true]));
        let missing = 0;
        return wanted.reduce((chain, item) => chain.then(() => new Promise((resolve) => setImmediate(resolve))).then(() => {
            const f = item[0], isEvent = item[1], t = p[f];
            if (!t || typeof t.subscribe !== 'function') { missing++; log.warn('no ' + (isEvent ? 'event ' : 'field ') + f + ' on ' + base); return null; }
            const onValue = (v) => {
                try {
                    rec.value(base + '#' + f, v, isEvent);
                    if (f === 'statusCarSpeed2' && v && typeof v.speedVehicleCentreOfGravity === 'number') speed = v.speedVehicleCentreOfGravity;
                } catch (e) { log.warn('value ' + f + ': ' + errText(e)); }
            };
            return Promise.resolve().then(() => t.subscribe(onValue)).then(() => {
                // first value without waiting for the next change (fields that have a getter)
                if (!isEvent && typeof t.getValue === 'function') {
                    return Promise.resolve().then(() => t.getValue()).then(onValue, () => null);
                }
                return null;
            }).catch((e) => { missing++; log.warn('subscribe ' + base + '#' + f + ': ' + errText(e)); });
        }), Promise.resolve()).then(() => ({ ok: true, missing }));
    });
}

function watchWithRetry(iface, inst, w, tries) {
    return watchOne(iface, inst, w).then((r) => { subs.answering++; return r; }, (e) => {
        log.warn('interface ' + iface + '@' + inst + ' (try ' + tries + '): ' + errText(e));
        if (tries >= MAX_TRIES) { subs.failed++; subs.bad.push(short(iface)); return null; }
        return new Promise((resolve) => setTimeout(resolve, RETRY_MS)).then(() => watchWithRetry(iface, inst, w, tries + 1));
    });
}

function subscribeAll() {
    const ifaces = Object.keys(CFG.watch || {});
    subs.total = ifaces.length;
    subs.started = true;
    // one interface at a time: the native SOME/IP binding does not like buildProxy calls running next to each other
    return ifaces.reduce((chain, iface) => chain.then(() => {
        const w = CFG.watch[iface] || {};
        w.instances = w.instances && w.instances.length ? w.instances : ['1'];
        return w.instances.reduce((c2, inst) => c2.then(() => watchWithRetry(iface, inst, w, 1)), Promise.resolve())
            .then(() => { subs.done++; });
    }), Promise.resolve()).then(() => log.info('subscribed: ' + subs.answering + ' instances, ' + subs.failed + ' failed'));
}

/* ---------------- ECU (read only) ---------------- */
const ECU_CFG = CFG.ecu || {};
function startEcu() {
    if (ECU_CFG.enabled === false) { ecuError = 'switched off in config'; return Promise.resolve(); }
    const transport = transportLib.create({ someipBuilders, home: HOME, log, mode: ECU_CFG.transport || 'auto' });
    const diag = new Diag(transport, log);
    diag.quiet = true;                         // the poll would flood bclog8.log with its own requests
    return diag.init().then(() => {
        if (!diag.p) { ecuError = diag.error || 'no diagnostic link'; return; }
        const cfg = Object.assign({}, ECU_CFG);
        ['enabled', 'transport', 'autoStart'].forEach((k) => { delete cfg[k]; });
        ecu = new EcuLog({
            diag, rec, log, cfg, speed: () => speed, file: DATA + '/bclog-dids.json', fs, onChange: () => refresh(),
        });
        log.info('ECU link ready (' + transport.kind() + '), ECU ' + ecu.status().ecu + ', ' + ecu.status().items + ' items');
        if (ECU_CFG.autoStart) { if (!rec.on) rec.start(); ecu.start(); }
    });
}

/* ---------------- screen ---------------- */
let rhmi = null;                 // the RHMI application, once ready
const comps = {};
const shown = {};
function setText(name, text, button) {
    if (!rhmi || shown[name] === text) return;
    const c = comps[name] || (comps[name] = rhmi.getByName(name));
    if (!c) return;
    shown[name] = text;
    Promise.resolve(c.setValue(0, button ? 'PrimaryText' : 'Text', text, 'Text')).catch((e) => log.warn('text ' + name + ': ' + errText(e)));
}
let flash = null;
function refresh() {
    const s = rec.status();
    const head = s.error ? 'STOPPED: ' + s.error :
        (s.on ? 'LOGGING  ' : 'PAUSED  ') + s.lines + ' lines, ' + s.signals + ' signals' + (s.dropped ? ', ' + s.dropped + ' dropped' : '');
    setText('LOG_STATUS', clip(head, 64));
    setText('LOG_STATUS2', !subs.started ? 'Subscribing...' :
        subs.done < subs.total ? 'Subscribing ' + subs.done + '/' + subs.total + '...' :
            subs.answering + ' instances listened to' + (subs.failed ? ', ' + subs.failed + ' failed: ' + clip(subs.bad.join(' '), 30) : ''));
    let last;
    if (flash && Date.now() < flash.until) last = flash.text;
    else if (s.quiet.length) { const q = s.quiet[s.quiet.length - 1]; last = 'Last: ' + short(q.name) + '  ' + (q.ageMs / 1000).toFixed(1) + ' s ago'; }
    else last = 'Last: (no rare signal changed yet)';
    setText('LOG_LAST', clip(last, 64));
    setText('LOG_FILE', s.file ? clip(s.file + '  ' + kb(s.bytes), 64) : 'No file');
    let e, m = 'BC menu: -';
    if (!ecu) e = 'ECU: ' + (ecuError ? 'no link - ' + ecuError : 'diagnostic link starting...');
    else {
        const t = ecu.status();
        if (!t.running) e = 'ECU ' + t.ecu + ': off (' + t.items + ' items)';
        else if (t.sweep) e = 'ECU ' + t.ecu + ': sweep ' + t.sweep.name + ' ' + t.sweep.done + '/' + t.sweep.total + ', ' + t.sweep.found + ' answered';
        else e = 'ECU ' + t.ecu + ': ' + t.state + ', ' + t.ok + '/' + t.reads + ' ok, ' + t.items + ' items' + (t.error ? ' - ' + t.error : '');
        const lamp = t.mil === null ? 'lamp ?' : t.mil ? 'lamp ON' : 'lamp off';
        m = 'BC menu: ' + (t.menu === null ? '-' : t.menu === 0 ? 'left' : t.menu + (t.menu === 1 ? ' (maps)' : t.menu === 2 ? ' (valves)' : t.menu === 3 ? ' (ethanol)' : '')) +
            '   ' + lamp + (t.dtcs ? ', ' + t.dtcs + ' DTC' : '');
    }
    setText('ECU_STATUS', clip(e, 110));
    setText('ECU_MENU', clip(m, 110));
    setText('BTN_ECU', ecu && ecu.running ? 'ECU reading: stop' : 'ECU reading: start', true);
    setText('BTN_SWEEP', ecu && ecu.sweep ? 'ECU sweep: stop' : 'ECU sweep: start', true);
}
function tell(text) { flash = { text, until: Date.now() + 4000 }; refresh(); }

function onClick(name, fn) {
    const c = rhmi.getByName(name);
    if (!c) { log.warn('no RHMI component ' + name); return; }
    c.on('action', () => { try { fn(); } catch (e) { log.warn('click ' + name + ': ' + errText(e)); } });
}

function wireScreen() {
    onClick('BTN_LOG', () => {
        if (rec.on) { rec.stop(); tell('Logging paused'); } else { tell(rec.start() ? 'Logging started' : 'Cannot write the log: ' + rec.error); }
    });
    onClick('BTN_NEWFILE', () => { tell(rec.newFile() ? 'New file started' : 'Not logging'); });
    onClick('BTN_ECU', () => {
        if (!ecu) { tell('Diagnostic link not ready' + (ecuError ? ': ' + clip(ecuError, 40) : ' yet')); return; }
        if (ecu.running) { ecu.stop(); tell('ECU reading stopped'); return; }
        if (!rec.on && !rec.start()) { tell('Cannot write the log: ' + rec.error); return; }
        ecu.start();
        tell('ECU reading started');
    });
    onClick('BTN_SWEEP', () => {
        if (!ecu) { tell('Diagnostic link not ready yet'); return; }
        if (ecu.sweep) { ecu.stopSweep('stopped by hand'); tell('Sweep stopped'); return; }
        if (!ecu.running) { if (!rec.on && !rec.start()) { tell('Cannot write the log: ' + rec.error); return; } ecu.start(); }
        const names = Object.keys(ecu.cfg.sweeps);
        ecu.sweepNext = (ecu.sweepNext || 0) % names.length;
        const why = ecu.startSweep(names[ecu.sweepNext]);
        if (why) { tell(why); return; }
        tell('Sweep ' + names[ecu.sweepNext] + ' started');
        ecu.sweepNext++;
    });
    const counts = {};
    MARKS.forEach((m) => onClick(m[0], () => {
        const n = rec.mark(m[1], m[2], m[3], m[0]);
        if (!n) { tell('Logging is paused - mark NOT saved'); return; }
        counts[m[0]] = (counts[m[0]] || 0) + 1;
        log.info('mark #' + n + ' ' + m[1]);
        tell('Mark #' + n + ': ' + m[1] + ' (' + counts[m[0]] + 'x)');
    }));
    setInterval(refresh, 1000);
}

/* ---------------- RHMI ---------------- */
const rhmiManager = new RhmiManager();
const resourceManager = new ResourceManager(APPLET_ID);
const rhmiResourcesLocator = new RhmiResourcesLocator({ rhmiConfigKey: 'rhmiData' });
const multiLanguageStringBundleFactory = new MultiLanguageStringBundleFactory({ currentVehicleUnits: new CurrentVehicleUnits(), resourceManager, rhmiResourcesLocator });
const rhmiApplicationFactory = new RhmiApplicationFactory({ multiLanguageStringBundleFactory, resourceManager, rhmiManager, rhmiResourcesLocator });

function showMain(open) {
    return rhmi.getByName(STATE).updateResources().then(open || (() => rhmi.openState(STATE)))
        .then(() => log.info('opened ' + STATE))
        .catch((e) => log.warn('open ' + STATE + ' failed: ' + errText(e)));
}

// Tile tap: MainApp calls AppStatus.changeVisibility(visible, reason, entryPointUuid, ...). The SDK's
// background class ignores it, so the entry screen is opened here.
function onChangeVisibility(args) {
    const a = args || {};
    log.info('tile: ' + util.inspect(a, { depth: 1 }).slice(0, 200) + (rhmi ? '' : ' (screen not ready)'));
    if (a.visible && rhmi) showMain(() => rhmi.openEntryState(a.entryPointUuid || ENTRY_UUID));
    return Promise.resolve({});
}

// lifecycle towards MainApp: without registerActive() it drops the app
const onlineApp = OnlineAppBackground.create();
onlineApp.createAppStatusService = function () {
    const instanceName = '' + (process.getuid() - this.extendedConfig.get('someip', 'instanceIdOffset'));
    return someipBuilders.buildTypedService('de.bmw.infotainment.telematic.oap.AppStatus', instanceName, {
        changeVisibility: onChangeVisibility,
        configureStartWidgetApp: () => Promise.resolve({}),
        appStatus: { getValue: () => Promise.resolve(this.appStatus) },
        hmiStatus: { getValue: () => Promise.resolve(this.hmiStatus) },
        audioStatus: { getValue: () => Promise.resolve(this.audioStatus) },
    }).then((service) => service.init())
        .then((service) => { this.emit('appStatusProxyReady'); return service; });
};
// a clean shutdown only: an unexpected exit makes MainApp wipe the app's data folder
onlineApp.on('terminated', () => onlineApp.shutdown().catch(() => {}).then(() => { try { rec.stop(); } catch (e) { /* ignore */ } process.exit(0); }));
onlineApp.on('appStatusProxyReady', () => onlineApp.registerActive());

const startRhmi = () => rhmiResourcesLocator.init()
    .then(() => Promise.all([rhmiManager.init(), resourceManager.init(), rhmiApplicationFactory.init()]))
    .then(() => rhmiApplicationFactory.create(UID, { appletIdOffset: APPLETID_OFFSET }))
    .then((app) => app.init().then(() => app.setActive(true))
        .then(() => app.mapEntryState(ENTRY_UUID, app.getByName(STATE)))
        .then(() => { rhmi = app; wireScreen(); refresh(); log.info('screen ready'); }));

log.info('start uid ' + UID + ' appletId ' + APPLET_ID + ' entry ' + ENTRY_UUID + ' root ' + ROOT + ' signals ' + Object.keys(CFG.watch || {}).length);
if (CFG.autoStart !== false) rec.start();
onlineApp.start()
    .then(() => Promise.all([onlineApp.initialized(), startRhmi()]))
    .then(() => {
        subscribeAll().catch((e) => log.error('subscribeAll: ' + errText(e)));
        startEcu().catch((e) => { ecuError = errText(e); log.error('ECU link: ' + errText(e)); });
    })
    .catch((e) => log.error('init failed: ' + errText(e)));
