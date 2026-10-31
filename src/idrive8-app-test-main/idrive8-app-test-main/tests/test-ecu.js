'use strict';
// logger.js + ecu.js against a pretend ECU: no car, no SDK.
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { Logger } = require('../src/logger');
const { EcuLog, ALLOWED } = require('../src/ecu');
const fake = require('./fake-ecu');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const quiet = { info() {}, warn() {}, error() {} };
const FAST = { gapMs: 1, sweepGapMs: 1, settleMs: 90, blinkGapMs: 60, exitMs: 300, dtcEveryMs: 0, timeoutMs: 100, slowObd: [] };

function setup(model, cfg, extra) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bclog-test-'));
    const rec = new Logger({ dir, log: quiet, minMs: () => 0, cap: () => 0 });
    assert(rec.start());
    const ecu = new EcuLog(Object.assign({ diag: fake.diag(model), rec, log: quiet, cfg: Object.assign({}, FAST, cfg) }, extra));
    const read = () => {
        rec.flush();
        const file = fs.readdirSync(dir).filter((f) => /\.jsonl$/.test(f)).sort()[0];
        const names = {}, out = [];
        fs.readFileSync(path.join(dir, file), 'utf8').split('\n').filter(Boolean).forEach((l) => {
            const o = JSON.parse(l);
            if (o.k === 'sig') names[o.i] = o.name;
            else if (o.i !== undefined) out.push({ t: o.t, name: names[o.i], v: o.v });
            else out.push(o);
        });
        return out;
    };
    return { dir, rec, ecu, read };
}

// a blinking lamp: n blinks of `on` ms with `off` ms between, starting at `at`
function lamp(model, schedule) {
    const t0 = Date.now();
    model.mil = () => { const t = Date.now() - t0; return schedule.some((s) => t >= s[0] && t < s[1]) ? 1 : 0; };
}

(async () => {
    // 1. only read requests can leave the module
    {
        const model = { mil: () => 0 };
        const { ecu, rec } = setup(model);
        for (const sid of [0x10, 0x14, 0x2E, 0x2F, 0x31, 0x3E, 0x27, 0x85]) {
            await assert.rejects(() => ecu.send(sid, [0x01, 0x02]), /refused/);
        }
        await assert.rejects(() => ecu.send(0x19, [0x04, 0xFF]), /refused/);          // not "report DTC by status mask"
        await assert.rejects(() => ecu.send(0x22, [0xF4]), /refused/);
        assert.deepStrictEqual(model.sent || [], [], 'nothing reached the transport');
        assert.deepStrictEqual(Object.keys(ALLOWED).sort(), ['1', '25', '34']);
        rec.stop();
    }

    // 2. polling logs a value once, again when it changes; unsupported items are asked once
    {
        let rpm = [0x0F, 0xA0];
        const model = { mil: () => 0, pids: { '0C': () => rpm.slice() } };
        const { ecu, read, rec } = setup(model, { fastObd: ['01', '0C', '52'] });
        ecu.start();
        await sleep(120);
        rpm = [0x12, 0x34];
        await sleep(120);
        await ecu.stop();
        rec.stop();
        const log = read();
        const rpmLines = log.filter((x) => x.name === 'ECU12#OBD 0C');
        assert.deepStrictEqual(rpmLines.map((x) => x.v.h), ['0F A0', '12 34'], 'one line per change');
        assert.strictEqual(rpmLines[0].v.n, 0x0FA0);
        assert.strictEqual(log.filter((x) => x.name === 'ECU12#OBD 52').length, 0, 'no data line for an unsupported PID');
        assert.strictEqual(model.sent.filter((s) => s[0] === 0x01 && s[1] === 0x52).length, 1, 'unsupported PID asked once');
        assert(ecu.status().dead >= 1);
    }

    // 3. the lamp: 1, 2, 3 blinks are menus 1, 2, 3; silence leaves the menu
    {
        const model = { mil: () => 0 };
        const { ecu, read, rec } = setup(model, { fastObd: ['01'], exitMs: 600 });
        const b = (start, n) => { const out = []; for (let i = 0; i < n; i++) out.push([start + i * 40, start + i * 40 + 20]); return out; };
        lamp(model, [].concat(b(30, 1), b(500, 2), b(1100, 3)));
        ecu.start();
        await sleep(2300);
        await ecu.stop();
        rec.stop();
        const log = read();
        const menu = log.filter((x) => x.name === 'derived#bcmenu').map((x) => x.v.blinks);
        assert.deepStrictEqual(menu, [1, 2, 3, 0], 'menus in order, then left');
        const marks = log.filter((x) => x.k === 'mark');
        assert.deepStrictEqual(marks.map((m) => m.label.replace(/ \(.*/, '')), ['BC menu 1', 'BC menu 2', 'BC menu 3', 'BC menu left']);
        assert(marks.every((m) => m.kind === 'state' && m.group === 'bcmenu'));
        const mil = log.filter((x) => x.name === 'derived#mil');
        assert(mil.length >= 12, 'raw lamp edges are logged: ' + mil.length);
    }

    // 4. a value that moves with the menu is promoted to every cycle
    {
        let v = 1;
        const model = { mil: () => 0, dids: { D001: () => [v] } };
        const { ecu, rec } = setup(model, { fastObd: ['01'], slowPerCycle: 1, dids: ['D001', 'D002', 'D003', 'D004', 'D005'] });
        ecu.start();
        await sleep(100);
        v = 2;
        await sleep(60);
        const before = model.sent.filter((s) => s[0] === 0x22 && s[1] === 0xD0 && s[2] === 0x01).length;
        await sleep(100);
        const after = model.sent.filter((s) => s[0] === 0x22 && s[1] === 0xD0 && s[2] === 0x01).length;
        await ecu.stop();
        rec.stop();
        const cycles = ecu.stats.cycles;
        assert(after - before >= 5, 'hot item read in (almost) every cycle: ' + (after - before));
        assert(cycles > 10);
    }

    // 5. sweep: parked only, adds the DIDs that answer, remembers them, stops at the end
    {
        const model = { mil: () => 0, dids: { F190: () => [0x57, 0x42], F1A1: () => [1] } };
        const store = {};
        const mem = { writeFileSync: (f, d) => { store[f] = d; }, readFileSync: (f) => { if (store[f] === undefined) throw new Error('ENOENT'); return store[f]; } };
        let speed = 50;
        const { ecu, read, rec } = setup(model, { fastObd: ['01'] }, { speed: () => speed, file: '/x/dids.json', fs: mem });
        ecu.start();
        assert.ok(/park the car/.test(ecu.startSweep('F100-F1FF')));
        speed = 0;
        assert.strictEqual(ecu.startSweep('F100-F1FF'), '');
        for (let i = 0; i < 100 && ecu.sweep; i++) await sleep(20);
        assert.strictEqual(ecu.sweep, null, 'sweep ended');
        await ecu.stop();
        rec.stop();
        assert.deepStrictEqual(JSON.parse(store['/x/dids.json']).sort(), ['F190', 'F1A1']);
        const log = read();
        assert.deepStrictEqual(log.filter((x) => x.k === 'did').map((x) => x.did), ['F190', 'F1A1']);
        const end = log.filter((x) => x.k === 'sweep')[0];
        assert.strictEqual(end.found, 2);
        assert.strictEqual(end.end, 'done');
        // the next start knows them
        const again = new EcuLog({ diag: fake.diag(model), rec, log: quiet, cfg: FAST, file: '/x/dids.json', fs: mem });
        assert(again.byKey['22:F190'] && again.byKey['22:F1A1']);
        // a moving car stops a running sweep
        const run2 = setup(model, { fastObd: ['01'] }, { speed: () => speed });
        run2.ecu.start();
        speed = 0;
        run2.ecu.startSweep('F400-F4FF');
        await sleep(15);
        speed = 30;
        await sleep(60);
        assert.strictEqual(run2.ecu.sweep, null);
        await run2.ecu.stop();
        run2.rec.stop();
        assert(run2.read().some((x) => x.k === 'sweep' && x.end === 'car moving'));
    }

    // 6. an ECU that does not answer: the sweep gives up, the poll backs off
    {
        const model = { mil: () => 0, silent: true };
        const { ecu, rec } = setup(model, { fastObd: ['01'] });
        ecu.start();
        assert.strictEqual(ecu.startSweep('F100-F1FF'), '');
        for (let i = 0; i < 100 && ecu.sweep; i++) await sleep(20);
        assert.strictEqual(ecu.sweep, null);
        await ecu.stop();
        rec.stop();
        assert(ecu.stats.none > 0 && ecu.stats.ok === 0);
    }

    // 7. fault memory is logged as a list
    {
        const model = { mil: () => 0, dtcs: [0x21E500, 0x480001] };
        const { ecu, read, rec } = setup(model, { fastObd: ['01'], dtcEveryMs: 1 });
        ecu.start();
        await sleep(80);
        await ecu.stop();
        rec.stop();
        const d = read().filter((x) => x.name === 'ECU12#19 02');
        assert.deepStrictEqual(d[0].v, ['21E500:09', '480001:09']);
        assert.strictEqual(d.length, 1);
    }

    // 8. gen-interfaces drops only optional proxies the catalog lacks
    {
        const { dropMissing } = require('../tools/gen-interfaces.js');
        const db = { interfaces: { A: { fields: { x: {} }, events: { e1: {} }, methods: { m1: {} } }, P: {} } };
        const manifest = { proxies: {
            A: { attributes: { x: 'rs', y: 'rs' }, broadcasts: ['e1', 'e2'], methods: ['m1', 'm2'] },
            B: { attributes: { z: 'rs' } }, P: '*', Platform: { attributes: { q: 'r' } },
        } };
        const missing = dropMissing(db, manifest, ['A', 'B']);
        assert.deepStrictEqual(manifest.proxies.A, { attributes: { x: 'rs' }, broadcasts: ['e1'], methods: ['m1'] });
        assert(!('B' in manifest.proxies));
        assert(manifest.proxies.Platform, 'a platform proxy is never dropped');
        assert.deepStrictEqual(Object.keys(missing).sort(), ['A#e2', 'A#m2', 'A#y', 'B']);
    }

    console.log('PASS: read-only guard, change logging, MIL menu detection, hot items, sweep, silent ECU, fault memory, optional interfaces');
})().catch((e) => { console.error(e); process.exit(1); });
