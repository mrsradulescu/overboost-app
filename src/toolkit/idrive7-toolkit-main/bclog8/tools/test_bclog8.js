'use strict';
/**
 * test_bclog8.js - runs apps/bclog8/src/main.js against a stand-in for the iDrive 8 SDK (no BMW code):
 * start-up order, the AppStatus tile tap, the screen texts, presses on the MARK rows, the subscriptions
 * (fields, events, a failing interface that is retried) and the file that is written.
 *
 *   node tools/test_bclog8.js
 */
const fs = require('fs'), os = require('os'), path = require('path'), assert = require('assert'), Module = require('module');
const EventEmitter = require('events');

const ROOT = path.join(__dirname, '..', 'apps', 'bclog8');
const app = JSON.parse(fs.readFileSync(path.join(ROOT, 'app.json'), 'utf8'));
const tree = path.join(ROOT, 'bclog8@' + app.version);
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bclog8-'));
const pkg = path.join(tmp, 'pkg'), data = path.join(tmp, 'data');
fs.mkdirSync(data);
// the installed package: the tree + javascript/ with the app's modules
(function cp(a, b) { fs.mkdirSync(b); fs.readdirSync(a).forEach((f) => { const s = path.join(a, f), d = path.join(b, f); fs.statSync(s).isDirectory() ? cp(s, d) : fs.copyFileSync(s, d); }); })(tree, pkg);
fs.mkdirSync(path.join(pkg, 'javascript'));
fs.readdirSync(path.join(ROOT, 'src')).filter((f) => /\.js$/.test(f)).forEach((f) => fs.copyFileSync(path.join(ROOT, 'src', f), path.join(pkg, 'javascript', f)));

// quick ECU timings for the test (data/bclog-settings.json is read by the app)
fs.writeFileSync(path.join(data, 'bclog-settings.json'), JSON.stringify({ ecu: { gapMs: 1, settleMs: 150, blinkGapMs: 120, exitMs: 600, dtcEveryMs: 0, timeoutMs: 150, slowObd: [] } }));
process.env.BCLOG8_DATA = data;
process.env.BCLOG8_RETRY_MS = '60';
process.env.BCLOG8_TRIES = '3';

/* ---------------- stand-in for the SDK ---------------- */
const calls = [];
const handlers = {};            // 'iface#name' -> fn
const comps = {};               // component name -> { texts, clickHandlers }
let tileHandler = null;
const failIface = 'de.bmw.infotainment.misc.MDrive';
const TASU = 'com.harman.infotainment.systemfunctions.tasu.tasu.InternalTasu';
const sent = [];                // [sid, ...data] of every UDS request the app sent
const t0 = Date.now();
const milOn = () => { const t = (Date.now() - t0) % 1000; return (t > 400 && t < 430) || (t > 470 && t < 500); };   // two quick blinks a second = menu 2
function tasuProxy() {
    const send = (a) => {
        const d = Array.prototype.slice.call(a.udsData || []);
        sent.push([a.serviceID].concat(d));
        const uds = a.serviceID === 1 && d[0] === 1 ? [0x41, 1, milOn() ? 0x80 : 0, 0, 0, 0] :
            a.serviceID === 1 && d[0] === 0x0C ? [0x41, 0x0C, 0x1A, 0xF8] :
                a.serviceID === 1 && d[0] === 0x52 ? [0x41, 0x52, 0x40] : [0x7F, a.serviceID, 0x31];
        setTimeout(() => { if (handlers[TASU + '#sendTasuResponse']) handlers[TASU + '#sendTasuResponse']({ udsResponse: Buffer.from([0, 0, 0, 0, 0, 0, 0x12, 0, 0].concat(uds)), errorCode: 'NO_ERROR' }); }, 2);
        return Promise.resolve({ TasuErrorEnum: 'NO_ERROR' });
    };
    const p = { sendTasuRequest: send, sendTasuResponse: { subscribe: (fn) => { handlers[TASU + '#sendTasuResponse'] = fn; return Promise.resolve(); } } };
    p.waitUntilAvailable = () => Promise.resolve();
    return p;
}
const tries = {};

function comp(name) {
    return comps[name] || (comps[name] = {
        name, texts: {}, handlers: {},
        setValue: (row, cell, v, t) => { comps[name].texts[cell] = v; return Promise.resolve(); },
        on: (ev, fn) => { comps[name].handlers[ev] = fn; },
        updateResources: () => { calls.push('updateResources ' + name); return Promise.resolve(); },
    });
}
const rhmiApp = {
    init: () => { calls.push('rhmi.init'); return Promise.resolve(); },
    setActive: (b) => { calls.push('rhmi.setActive ' + b); return Promise.resolve(); },
    mapEntryState: (uuid, st) => { calls.push('mapEntryState ' + uuid + ' ' + (st && st.name)); return Promise.resolve(); },
    getByName: (n) => (n === 'MAIN' || /^(LOG_|BTN_|ECU_)/.test(n) ? comp(n) : null),
    openState: (n) => { calls.push('openState ' + n); return Promise.resolve(); },
    openEntryState: (u) => { calls.push('openEntryState ' + u); return Promise.resolve(); },
};

const stubs = {
    'oap-sdk/src/rhmi/RhmiManager': class { init() { calls.push('rhmiManager.init'); return Promise.resolve(); } },
    'oap-sdk/src/rhmi/RhmiResourcesLocator': class { constructor(o) { assert.strictEqual(o.rhmiConfigKey, 'rhmiData'); } init() { return Promise.resolve(); } },
    'oap-sdk/src/rhmi/MultiLanguageStringBundleFactory': class { },
    'oap-sdk/src/rhmi/ResourceManager': class { constructor(id) { calls.push('ResourceManager ' + id); } init() { return Promise.resolve(); } },
    'oap-sdk/src/core/CurrentVehicleUnits': class { },
    'oap-sdk/src/rhmi/RhmiApplicationFactory': class {
        init() { return Promise.resolve(); }
        create(uid, cfg) { calls.push('factory.create ' + cfg.appletIdOffset); return Promise.resolve(rhmiApp); }
    },
    'oap-sdk/src/core/OnlineAppBackground': {
        create() {
            const a = new EventEmitter();
            a.extendedConfig = { get: () => 0 };
            a.appStatus = 'ACTIVE'; a.hmiStatus = 'DEFAULT'; a.audioStatus = 'DEFAULT';
            a.registerActive = () => { calls.push('registerActive'); return Promise.resolve(); };
            a.initialized = () => { calls.push('initialized'); return Promise.resolve(); };
            a.shutdown = () => Promise.resolve();
            a.start = () => { calls.push('onlineApp.start'); return a.createAppStatusService(); };
            return a;
        },
    },
    'oap-sdk/src/someip/someipBuilders': {
        buildTypedService(iface, inst, impl) {
            assert.strictEqual(iface, 'de.bmw.infotainment.telematic.oap.AppStatus');
            tileHandler = impl.changeVisibility;
            return Promise.resolve({ init: () => Promise.resolve({}) });
        },
        buildAndInitTypedProxy(iface, inst, opt) {
            tries[iface] = (tries[iface] || 0) + 1;
            if (iface === TASU) { assert.strictEqual(inst, 'SystemFunctionsInternalTasuInst1'); return Promise.resolve(tasuProxy()); }
            if (iface === failIface && tries[iface] < 2) return Promise.reject(new Error('provider not there yet'));
            const p = new Proxy({}, {
                get(t, field) {
                    if (field === 'then') return undefined;
                    if (/^missing/.test(String(field))) return undefined;
                    return t[field] || (t[field] = {
                        subscribe: (fn) => { handlers[iface + '#' + String(field)] = fn; return Promise.resolve(); },
                        getValue: () => Promise.resolve({ initial: true }),
                    });
                },
            });
            return Promise.resolve(p);
        },
    },
};
const real = Module._load;
Module._load = function (req) { if (stubs[req]) return stubs[req]; return real.apply(this, arguments); };

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
    require(path.join(pkg, 'javascript', 'main.js'));
    await wait(1400);   // start-up, subscriptions (one failing, retried), one screen refresh

    // start-up order, the appletId / entry uuid come from rhmiData/application.json
    const appJson = JSON.parse(fs.readFileSync(path.join(pkg, 'rhmiData', 'application.json'), 'utf8')).application;
    assert.ok(calls.indexOf('registerActive') >= 0, 'registerActive');
    assert.ok(calls.indexOf('ResourceManager ' + appJson.appletId) >= 0, 'ResourceManager gets the appletId of application.json');
    assert.ok(calls.indexOf('factory.create 3000') >= 0, 'appletIdOffset 3000');
    assert.ok(calls.indexOf('mapEntryState ' + appJson.entryPoints[0].uuid + ' MAIN') >= 0, 'entry state mapped');
    assert.ok(calls.indexOf('initialized') >= 0);

    // subscriptions: every field/event of every interface in app.json, the failing one after a retry
    const watch = app.config.watch;
    for (const iface of Object.keys(watch)) {
        for (const f of watch[iface].fields) assert.ok(typeof handlers[iface + '#' + f] === 'function', 'subscribed ' + iface + '#' + f);
        for (const f of watch[iface].events) assert.ok(typeof handlers[iface + '#' + f] === 'function', 'subscribed event ' + iface + '#' + f);
    }
    assert.ok(tries[failIface] >= 2, 'failing interface retried');

    // the ECU link: typed transport chosen, status on the screen
    assert.ok(/^ECU 12: off/.test(comps.ECU_STATUS.texts.Text), comps.ECU_STATUS.texts.Text);
    assert.ok(/TASU transport: typed/.test(fs.readFileSync(path.join(data, 'bclog8.log'), 'utf8')), 'typed transport used');

    // the screen
    assert.ok(/LOGGING/.test(comps.LOG_STATUS.texts.Text), comps.LOG_STATUS.texts.Text);
    assert.ok(/instances listened to/.test(comps.LOG_STATUS2.texts.Text), comps.LOG_STATUS2.texts.Text);
    assert.ok(comps.BTN_MARK_BCSHORT.handlers.action, 'BC row can be pressed');

    // tile tap -> entry state opens
    await tileHandler({ visible: true, entryPointUuid: appJson.entryPoints[0].uuid });
    await wait(50);
    assert.ok(calls.indexOf('openEntryState ' + appJson.entryPoints[0].uuid) >= 0, 'tile tap opens the entry state');

    // signals and marks
    const CCU = 'de.bmw.infotainment.controlelements.FrontCCU';
    handlers[CCU + '#statusPushButtonsState']({ btn: 'NONE' });
    await wait(70);
    comps.BTN_MARK_BCSHORT.handlers.action({});
    handlers[CCU + '#statusPushButtonsState']({ btn: 'PRESSED' });
    await wait(70);
    comps.BTN_MARK_MM2.handlers.action({});
    await wait(1200);
    assert.ok(/Mark #2/.test(comps.LOG_LAST.texts.Text), comps.LOG_LAST.texts.Text);
    // ECU reading on: the lamp blinks twice a second -> BC menu 2, ethanol/rpm are read
    comps.BTN_ECU.handlers.action({});
    let sawMenu2 = false;                              // the menu leaves again between two blink bursts: sample
    for (let i = 0; i < 40 && !sawMenu2; i++) { await wait(100); sawMenu2 = /2 \(valves\)/.test(comps.ECU_MENU.texts.Text || ''); }
    assert.ok(sawMenu2, comps.ECU_MENU.texts.Text);
    assert.strictEqual(comps.BTN_ECU.texts.PrimaryText, 'ECU reading: stop');
    comps.BTN_ECU.handlers.action({});
    await wait(100);
    assert.ok(sent.length > 5 && sent.every((r) => r[0] === 1 || r[0] === 0x22 || r[0] === 0x19), 'only read requests: ' + JSON.stringify(sent.slice(0, 5)));
    comps.BTN_LOG.handlers.action({});                 // pause
    comps.BTN_MARK_BCSHORT.handlers.action({});             // refused
    assert.ok(/NOT saved/.test(comps.LOG_LAST.texts.Text), comps.LOG_LAST.texts.Text);
    comps.BTN_LOG.handlers.action({});                 // resume -> new file
    comps.BTN_NEWFILE.handlers.action({});
    await wait(700);

    const files = fs.readdirSync(data).filter((f) => /^bclog-.*\.jsonl$/.test(f)).sort();
    assert.ok(files.length >= 2, 'files: ' + files);
    const lines = [].concat.apply([], files.map((f) => fs.readFileSync(path.join(data, f), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l))));
    assert.ok(lines.some((l) => l.k === 'mark' && l.label === 'BC short press'), 'BC mark in the file');
    assert.ok(lines.some((l) => l.k === 'sig' && /FrontCCU#statusPushButtonsState/.test(l.name)), 'signal named');
    assert.ok(lines.some((l) => l.v && l.v.btn === 'PRESSED'), 'PRESSED value logged');
    assert.ok(lines.some((l) => l.k === 'sig' && l.name === 'derived#bcmenu'), 'derived BC menu logged');
    assert.ok(lines.some((l) => l.k === 'sig' && l.name === 'ECU12#OBD 52'), 'ethanol PID logged');
    assert.ok(fs.existsSync(path.join(data, 'bclog8.log')), 'own log file');
    const own = fs.readFileSync(path.join(data, 'bclog8.log'), 'utf8');
    assert.ok(!/FAILED|FATAL|REJECTION/.test(own), own.slice(-600));
    console.log('bclog8 test OK: %d calls, %d files, %d lines, %d signals subscribed', calls.length, files.length, lines.length, Object.keys(handlers).length);
    process.exit(0);
})().catch((e) => { console.error('FAIL', e.stack || e); console.error(calls.join('\n')); process.exit(1); });
