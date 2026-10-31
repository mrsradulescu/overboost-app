'use strict';
/**
 * transport.js - the way diag.js reaches the head unit's UDS gateway (InternalTasu): one proxy, one
 * method (sendTasuRequest), one event (sendTasuResponse), and the availability of the provider.
 *
 * Two ways to get at them; the first that works is used and the choice is written to bclog8.log:
 *
 *   typed    the SDK's own typed proxy: someipBuilders.buildAndInitTypedProxy(iface, instance), then
 *            p.sendTasuRequest(args) and p.sendTasuResponse.subscribe(fn). The apps that run on real
 *            cars use this for fields; for a METHOD and an EVENT it is the shape the SDK generates from
 *            the interface description, but it has not been seen on a unit yet.
 *   native   the head unit's native 'someip' module (buildProxy / callMethod / subscribe), the calls the
 *            idrive7 apps are proven with, with the interface description the install generated into
 *            oap_<uid>/someipInterfaces. The module name is built at run time, because the linker
 *            (oap_link.js) only accepts a literal require of app modules, Node builtins and the sdk
 *            names of mgu-app.json - the native module is neither; if the bundle cannot find it, this
 *            way reports that and the typed one is the only one left.
 *
 * cfg.mode: 'auto' (typed first, then native), 'typed' or 'native'.
 * Node 8: no optional chaining, no object spread.
 */
const fs = require('fs');
const path = require('path');

const errText = (e) => String(e && e.message || e).slice(0, 160);

function create(o) {
    const log = o.log || { info() {}, warn() {} };
    const mode = o.mode || 'auto';
    const home = o.home;                             // oap_<uid> folder (someipInterfaces lives in it)
    const builders = o.someipBuilders || null;
    const requireNative = o.requireNative || ((name) => require(name));
    const t = { kind: null, native: null, tried: [] };

    // The native binding deadlocks when buildProxy / init / subscribe run inside the app's own promise
    // chains next to the SDK's SOME/IP traffic: one call at a time, each from a fresh setImmediate tick.
    let chain = Promise.resolve();
    function tick(fn) {
        const run = () => new Promise((resolve, reject) => setImmediate(() => {
            try { Promise.resolve(fn()).then(resolve, reject); } catch (e) { reject(e); }
        }));
        const p = chain.then(run, run);
        chain = p.catch(() => {});
        return p;
    }

    function loadNative() {
        if (t.native) return t.native;
        const name = ['some', 'ip'].join('');
        t.native = requireNative(name);
        return t.native;
    }
    function nativeConfig(iface) {
        const f = iface.replace(/\./g, '_') + '.json';
        const dirs = [path.join(home, 'someipInterfaces')];
        for (let i = 0; i < dirs.length; i++) {
            try { return JSON.parse(fs.readFileSync(path.join(dirs[i], f), 'utf8')); } catch (e) { /* next */ }
        }
        throw new Error('no SOME/IP description ' + f + ' in ' + dirs[0]);
    }

    function viaTyped(iface, inst) {
        if (!builders || typeof builders.buildAndInitTypedProxy !== 'function') return Promise.reject(new Error('no typed proxy builder'));
        return tick(() => builders.buildAndInitTypedProxy(iface, String(inst), { timeoutMs: 10000 })).then((raw) => {
            const keys = Object.keys(raw || {}).slice(0, 40).join(',');
            log.info('typed TASU proxy members: ' + (keys || '(none enumerable)') + '; sendTasuRequest is ' + typeof (raw && raw.sendTasuRequest) +
                ', sendTasuResponse is ' + typeof (raw && raw.sendTasuResponse));
            const m = raw && raw.sendTasuRequest;
            if (!(typeof m === 'function' || (m && typeof m.call === 'function' && typeof m !== 'function'))) {
                throw new Error('typed proxy has no callable sendTasuRequest');
            }
            const ev = raw.sendTasuResponse;
            if (!ev || typeof ev.subscribe !== 'function') throw new Error('typed proxy has no subscribable sendTasuResponse');
            return { kind: 'typed', raw };
        });
    }
    function viaNative(iface, inst) {
        return tick(() => {
            const some = loadNative();
            const cfg = nativeConfig(iface);
            return some.buildProxy(cfg, String(inst)).then((p) => p.init().then(() => p));
        }).then((raw) => ({ kind: 'native', raw }));
    }

    /** -> proxy wrapper { kind, raw }; rejects with every reason when no way works. */
    function proxy(iface, inst) {
        const order = mode === 'typed' ? ['typed'] : mode === 'native' ? ['native'] : ['typed', 'native'];
        const reasons = [];
        return order.reduce((c, how) => c.then((found) => {
            if (found) return found;
            return (how === 'typed' ? viaTyped(iface, inst) : viaNative(iface, inst)).then((p) => {
                t.kind = p.kind;
                log.info('TASU transport: ' + p.kind);
                return p;
            }, (e) => { reasons.push(how + ': ' + errText(e)); log.warn('TASU ' + how + ' failed: ' + errText(e)); return null; });
        }), Promise.resolve(null)).then((p) => {
            if (!p) throw new Error('no way to reach the diagnostic gateway (' + reasons.join('; ') + ')');
            return p;
        });
    }

    /** Subscribe to the event `name` of a proxy; fn(value). */
    function on(p, name, fn, lg) {
        const guard = (v) => { try { fn(v); } catch (e) { if (lg) lg.warn(name + ': ' + errText(e)); } };
        if (p.kind === 'typed') return tick(() => p.raw[name].subscribe(guard));
        return tick(() => p.raw.subscribe({ name, onReceive: guard }));
    }

    /** fn(bool) now and on every change of the provider's availability. */
    function onAvailable(p, fn) {
        const raw = p.raw;
        if (typeof raw.subscribeToAvailability === 'function') {
            return tick(() => { try { return raw.subscribeToAvailability((a) => fn(!!a)); } catch (e) { return null; } })
                .then(() => { if (typeof raw.isAvailable === 'function' && raw.isAvailable()) fn(true); });
        }
        if (typeof raw.waitUntilAvailable === 'function') {
            Promise.resolve().then(() => raw.waitUntilAvailable()).then(() => fn(true), () => fn(false));
            return Promise.resolve();
        }
        fn(true);                       // no way to ask: the first request will tell
        return Promise.resolve();
    }

    /** Call a method; resolves with the provider's reply. */
    function call(p, method, params, timeoutMs) {
        if (p.kind === 'typed') {
            const m = p.raw[method];
            return tick(() => (typeof m === 'function' ? p.raw[method](params || {}) : m.call(params || {})));
        }
        return tick(() => p.raw.callMethod(method, params || {}, timeoutMs || 5000));
    }

    return { proxy, on, onAvailable, call, kind: () => t.kind };
}

module.exports = { create };
