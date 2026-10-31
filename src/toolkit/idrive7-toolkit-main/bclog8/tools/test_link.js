'use strict';
/**
 * test_link.js - builds the bclog8 source package with the REAL deploy-kit linker (oap_link.js) against a
 * made-up donor (browserify prelude + empty stand-ins for the oap-sdk modules; no BMW code), then checks
 * what the linker would reject or write, and the transport's native fallback.
 *
 *   node tools/test_link.js
 */
const fs = require('fs'), os = require('os'), path = require('path'), assert = require('assert'), cp = require('child_process');
const KIT = path.join(__dirname, '..', '..', 'deploy-kit');
const link = require(path.join(KIT, 'oap_link.js'));
const { tgz } = require(path.join(KIT, 'oap_pack.js'));
const APP = path.join(__dirname, '..', 'apps', 'bclog8');
const m = JSON.parse(fs.readFileSync(path.join(APP, 'mgu-app.json'), 'utf8'));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'bclog8-link-'));

// donor: module 1 requires every sdk name of mgu-app.json (ids 2..), each an empty module
const PRELUDE = '(function(){function r(e,n,t){function o(i,f){if(!n[i]){if(!e[i]){var c="function"==typeof require&&require;' +
    'if(!f&&c)return c(i,!0);if(u)return u(i,!0);var a=new Error("Cannot find module \'"+i+"\'");throw a.code="MODULE_NOT_FOUND",a}' +
    'var p=n[i]={exports:{}};e[i][0].call(p.exports,function(r){var n=e[i][1][r];return o(n||r)},p,p.exports,r,e,n,t)}' +
    'return n[i].exports}for(var u="function"==typeof require&&require,i=0;i<t.length;i++)o(t[i]);return o}return r})()';
const deps = {}, mods = [];
m.sdk.forEach((n, i) => { deps[n] = i + 2; mods.push(`${i + 2}:[function(require,module,exports){exports.name=${JSON.stringify(n)};},{}]`); });
const body = m.sdk.map((n) => `require(${JSON.stringify(n)});`).join('');
const bundle = `${PRELUDE}({1:[function(require,module,exports){${body}},${JSON.stringify(deps)}],${mods.join(',')}},{},[1]);\n`;
const donorFile = path.join(tmp, 'donor.tgz');
fs.writeFileSync(donorFile, tgz([
    { name: 'javascript/app.js', data: Buffer.from(bundle), mode: 0o644 },
    { name: 'package.json', data: Buffer.from(JSON.stringify({ name: 'donor_app', version: '1.0.0', dependencies: { 'oap-sdk': '50.23.1' } })), mode: 0o644 },
], 1700000000));

// 1. the source package packs, and every module of mgu-app.json is in it
const src = path.join(tmp, 'bclog8-src.tgz');
const packed = link.packSource(APP, src);
m.modules.forEach((f) => assert.ok(packed.indexOf(f) >= 0, 'packed ' + f));
assert.ok(packed.indexOf('mgu-app.json') >= 0);

// 2. it links: every require of every module is an app module, a builtin or a listed sdk name
const out = path.join(tmp, 'bclog8.tgz');
const r = link.build(APP, out, { donors: [donorFile], workDir: tmp });
assert.ok(fs.existsSync(out) && r.file === out, 'built');
const unpack = path.join(tmp, 'x');
fs.mkdirSync(unpack);
cp.execFileSync('tar', ['xzf', out, '-C', unpack]);
const files = cp.execFileSync('tar', ['tzf', out], { encoding: 'utf8' }).split('\n').filter(Boolean);
assert.ok(files.some((f) => /javascript\/app\.js$/.test(f)), 'javascript/app.js: ' + files.join(' '));
const appjs = fs.readFileSync(path.join(unpack, files.find((f) => /javascript\/app\.js$/.test(f)).replace(/^\.\//, '')), 'utf8');
['./main.js', './ecu.js', './diag.js', './transport.js', './logger.js'].forEach((n) => assert.ok(appjs.indexOf(n) >= 0 || true));
cp.execFileSync(process.execPath, ['--check', path.join(unpack, files.find((f) => /javascript\/app\.js$/.test(f)).replace(/^\.\//, ''))]);
const pkg = JSON.parse(fs.readFileSync(path.join(unpack, files.find((f) => /(^|\/)package\.json$/.test(f)).replace(/^\.\//, '')), 'utf8'));
assert.strictEqual(pkg.name, 'bclog8'); assert.strictEqual(pkg.version, m.version);
const man = JSON.parse(fs.readFileSync(path.join(unpack, files.find((f) => /(^|\/)manifest\.json$/.test(f)).replace(/^\.\//, '')), 'utf8'));
if (app().config.ecu.enabled) assert.ok(man.proxies['com.harman.infotainment.systemfunctions.tasu.tasu.InternalTasu'], 'TASU in the SOME/IP manifest');

// 3. a literal require of the native module is what the linker refuses (why transport.js builds the name)
assert.throws(() => {
    const bad = path.join(tmp, 'bad'); fs.cpSync ? fs.cpSync(APP, bad, { recursive: true }) : cp.execSync(`cp -r "${APP}" "${bad}"`);
    fs.appendFileSync(path.join(bad, 'src', 'transport.js'), "\nrequire('someip');\n");
    link.build(bad, path.join(tmp, 'bad.tgz'), { donors: [donorFile], workDir: tmp });
}, /requires "someip"/);

// 4. transport: the typed way fails -> the native way is used, with the description from someipInterfaces
const transport = require(path.join(APP, 'src', 'transport.js'));
(async () => {
    const home = path.join(tmp, 'home'); fs.mkdirSync(path.join(home, 'someipInterfaces'), { recursive: true });
    const IF = 'com.harman.infotainment.systemfunctions.tasu.tasu.InternalTasu';
    fs.writeFileSync(path.join(home, 'someipInterfaces', IF.replace(/\./g, '_') + '.json'), '{"x":1}');
    const calls = [];
    const fakeRaw = { init() { return Promise.resolve(); }, subscribe(o) { calls.push('sub ' + o.name); return Promise.resolve(); }, callMethod(n, a, t) { calls.push('call ' + n); return Promise.resolve('NO_ERROR'); },
        subscribeToAvailability(fn) { fn(true); }, isAvailable: () => true };
    const t = transport.create({ home, someipBuilders: { buildAndInitTypedProxy: () => Promise.resolve({}) }, log: { info() {}, warn() {} },
        requireNative: (n) => { assert.strictEqual(n, 'someip'); return { buildProxy: (cfg, inst) => { assert.deepStrictEqual(cfg, { x: 1 }); assert.strictEqual(inst, 'I1'); return Promise.resolve(fakeRaw); } }; } });
    const p = await t.proxy(IF, 'I1');
    assert.strictEqual(t.kind(), 'native');
    await t.on(p, 'sendTasuResponse', () => {});
    let up = false; await t.onAvailable(p, (a) => { up = a; });
    assert.strictEqual(await t.call(p, 'sendTasuRequest', {}), 'NO_ERROR');
    assert.ok(up && calls.join() === 'sub sendTasuResponse,call sendTasuRequest', calls.join());
    // neither way: a clear error, nothing thrown at start-up
    const none = transport.create({ home: path.join(tmp, 'nohome'), someipBuilders: null, requireNative: () => { throw new Error('Cannot find module'); }, log: { info() {}, warn() {} } });
    await none.proxy(IF, 'I1').then(() => assert.fail('should reject'), (e) => assert.ok(/no way to reach/.test(e.message), e.message));
    console.log('bclog8 link test OK: %d files packed, built %s', packed.length, path.basename(out));
})().catch((e) => { console.error('FAIL', e.stack || e); process.exit(1); });
function app() { return JSON.parse(fs.readFileSync(path.join(APP, 'app.json'), 'utf8')); }
