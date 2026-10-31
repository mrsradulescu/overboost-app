'use strict';
// Exercise src/main.js against an SDK double and a pretend ECU, without native modules or an MGU.
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const EventEmitter = require('events');
const fake = require('./fake-ecu');
const source = fs.readFileSync(path.join(__dirname, '../src/main.js'), 'utf8');
const ui = JSON.parse(fs.readFileSync(path.join(__dirname, '../app/ui.json'), 'utf8'));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const flush = () => new Promise((resolve) => setImmediate(resolve));
const NAMES = ui.labels.concat(ui.buttons).map((x) => x[0]);

async function scenario(reason) {
    // the app lives in <root>/source/javascript; its data folder is <root>/data
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'id8-app-'));
    fs.mkdirSync(path.join(root, 'data'));
    fs.mkdirSync(path.join(root, 'source'));
    const settings = { ecu: { gapMs: 1, settleMs: 150, blinkGapMs: 120, exitMs: 600, dtcEveryMs: 0, timeoutMs: 100, slowObd: [] }, minMs: 0, cap: 0 };
    const t0 = Date.now();
    const model = { mil: () => { const t = Date.now() - t0; return (t % 1000 > 400 && t % 1000 < 430) || (t % 1000 > 470 && t % 1000 < 500) ? 1 : 0; } };
    let app, opens = 0, updates = 0;
    const texts = {}, written = {}, logs = [];
    const comps = {};
    const comp = (name) => {
        if (!comps[name]) {
            const c = new EventEmitter();
            c.setValue = async (row, field, text) => { texts[name] = text; };
            comps[name] = c;
        }
        return comps[name];
    };
    const state = new EventEmitter();
    state.getId = () => 1;
    state.updateResources = async () => { updates++; };
    class OnlineApp extends EventEmitter {
        constructor() {
            super();
            app = this;
            this.startReason = reason;
            this.rhmiApplication = new EventEmitter();
            this.rhmiApplication.getByName = (name) => (name === 'MAIN' ? state : NAMES.indexOf(name) >= 0 ? comp(name) : undefined);
            this.rhmiApplication.mapEntryState = async () => {};
            this.rhmiApplication.openEntryState = async () => { opens++; };
        }
        async start() {}
        async registerActive() {}
        async initialized() {}
        async registerHmiVisible() {}
        async unregisterHmiVisible() {}
        getLastExecutedEntryPointUuid() { return 'test-entry'; }
    }
    const mockFs = {
        readFileSync: (file) => {
            if (/application\.json$/.test(file)) return JSON.stringify({ application: { entryPoints: [{ uuid: 'test-entry' }] } });
            if (/bclog-settings\.json$/.test(file)) return JSON.stringify(settings);
            if (/watch\.json$/.test(file)) return JSON.stringify({ 'de.bmw.test.Iface': { instances: '1', attributes: { fieldA: 'rs' }, broadcasts: ['evB'] } });
            if (written[file] !== undefined) return written[file];
            throw Object.assign(new Error('ENOENT ' + file), { code: 'ENOENT' });
        },
        writeFileSync: (file, data) => { written[file] = data; },
        appendFileSync: (file, line) => logs.push(line),
    };
    const watched = {};
    const signals = Object.assign(fake.signals(model, 0x12), {
        watch: async (iface, inst, handlers) => { watched[iface] = handlers; return { available: true, error: null }; },
    });
    const real = (name) => require(path.join(__dirname, '../src', name));
    const modules = {
        fs: mockFs, path, 'oap-sdk': { OnlineApp },
        './logger': real('logger.js'), './ecu': real('ecu.js'), './diag': real('diag.js'), './signals': signals,
    };
    const exits = [];
    vm.runInNewContext(source, {
        require: (name) => { assert(name in modules, 'unexpected require ' + name); return modules[name]; },
        __dirname: path.join(root, 'source', 'javascript'), console: { log: () => {} }, setTimeout, setInterval, clearTimeout, clearInterval, Date,
        process: { on: () => {}, getuid: () => 12450, exit: (code) => { exits.push(code); } },
    });
    await sleep(30);
    const cold = reason === 'ENTRY_POINT' ? 1 : 0;
    assert.strictEqual(opens, cold, 'Only a real entry-point start should open a screen');
    app.emit('entryPointExecute', 'test-entry', 'USER', 'bench');
    await flush();
    assert.strictEqual(opens, cold + 1, 'Warm reopening should work');
    assert.strictEqual(updates, opens, 'Resources must be ready before opening');

    // the screen: texts only reach the HMI once the app is on screen
    assert.deepStrictEqual(Object.keys(texts), []);
    app.rhmiApplication.emit('applicationStackChanged', { ROOT_APP: 1 });
    await sleep(30);
    assert(/^LOGGING/.test(texts.LOG_STATUS), texts.LOG_STATUS);
    assert(/off/.test(texts.ECU_STATUS), texts.ECU_STATUS);
    assert.strictEqual(texts.BTN_ECU, 'ECU reading: start');

    // a signal from the car reaches the log
    watched['de.bmw.test.Iface'].fieldA({ x: 1 });
    watched['de.bmw.test.Iface'].evB({ y: 2 });

    // start the ECU reading from the screen; the lamp blinks twice a second -> menu 2
    comps.BTN_ECU.emit('action');
    await sleep(1700);
    assert.strictEqual(texts.BTN_ECU, 'ECU reading: stop');
    assert(/2 \(valves\)/.test(texts.ECU_MENU), texts.ECU_MENU);
    comps.BTN_MARK_MM3.emit('action');
    comps.BTN_MARK_BCSHORT.emit('action');
    await sleep(20);
    assert(/Mark #\d+: BC short press/.test(texts.MESSAGE), texts.MESSAGE);
    comps.BTN_ECU.emit('action');
    await sleep(60);
    assert.strictEqual(texts.BTN_ECU, 'ECU reading: start');
    comps.BTN_LOG.emit('action');                         // pause = close the file
    await sleep(30);

    const dir = path.join(root, 'data');
    const file = fs.readdirSync(dir).filter((f) => /^bclog-.*\.jsonl$/.test(f))[0];
    assert(file, 'a log file was written');
    const names = {}, lines = [];
    fs.readFileSync(path.join(dir, file), 'utf8').split('\n').filter(Boolean).forEach((l) => {
        const o = JSON.parse(l);
        if (o.k === 'sig') names[o.i] = o.name; else lines.push(o.i !== undefined ? { name: names[o.i], v: o.v } : o);
    });
    const has = (name) => lines.some((x) => x.name === name);
    assert(has('de.bmw.test.Iface#fieldA') && has('de.bmw.test.Iface#evB'), 'SOME/IP signals are logged');
    assert(lines.some((x) => x.name === 'derived#bcmenu' && x.v.blinks === 2), 'the double blink became menu 2');
    assert(lines.some((x) => x.k === 'mark' && x.btn === 'BTN_MARK_MM3'), 'screen marks are logged');
    assert(model.sent.length > 5 && model.sent.every((s) => s[0] === 0x01 || s[0] === 0x22 || s[0] === 0x19), 'only read requests were sent');
    assert(!logs.some((line) => /FAILED|FATAL|REJECTION|ERROR/.test(line)), logs.join(''));
    assert.deepStrictEqual(exits, []);
    return logs;
}

(async () => {
    await scenario('ENTRY_POINT');
    await scenario('AUTOSTART');
    console.log('PASS: cold launch, warm reopening, no forced autostart screen, logging from the start, ECU button, menu from the lamp, marks, read-only traffic');
    process.exit(0);
})().catch((error) => { console.error(error); process.exit(1); });
