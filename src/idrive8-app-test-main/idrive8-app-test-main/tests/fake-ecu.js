'use strict';
// A pretend engine ECU for the tests: answers 01 <pid>, 22 <did> and 19 02 <mask> like a real one.
// model = { mil: () => 0|1, dtcs: [], pids: { '0C': [..bytes] }, dids: { 'F40C': [..bytes] }, silent: false, sent: [] }
function reply(model, sid, data) {
    const hex2 = (n) => n.toString(16).toUpperCase().padStart(2, '0');
    if (sid === 0x01) {
        const pid = hex2(data[0]);
        if (pid === '01') return [0x41, 0x01, (model.mil() ? 0x80 : 0) | (model.dtcs || []).length, 0x07, 0xE5, 0x00];
        if (model.pids && model.pids[pid]) return [0x41, data[0]].concat(model.pids[pid]());
        return [0x7F, 0x01, 0x31];
    }
    if (sid === 0x22) {
        const did = hex2(data[0]) + hex2(data[1]);
        if (model.dids && model.dids[did]) return [0x62, data[0], data[1]].concat(model.dids[did]());
        return [0x7F, 0x22, 0x31];
    }
    if (sid === 0x19) {
        const out = [0x59, 0x02, 0xFF];
        (model.dtcs || []).forEach((d) => out.push(d >> 16 & 255, d >> 8 & 255, d & 255, 0x09));
        return out;
    }
    return [0x7F, sid, 0x11];
}

/** src/diag.js-shaped: { available, physical() } */
function diag(model) {
    model.sent = model.sent || [];
    return {
        available: true,
        async physical(target, sid, data) {
            model.sent.push([sid].concat(data));
            await new Promise((r) => setImmediate(r));
            if (model.silent) return null;
            return reply(model, sid, data);
        },
    };
}

/** SOME/IP-shaped signals double for src/main.js: proxy / on / onAvailable / call answer as TASU. */
function signals(model, ecuAddr) {
    let onResponse = null;
    return {
        async watch() { return { available: true, error: null }; },
        async proxy() { return {}; },
        async on(p, name, fn) { if (name === 'sendTasuResponse') onResponse = fn; },
        async onAvailable(p, fn) { fn(true); },
        async call(p, method, params) {
            model.sent = model.sent || [];
            const data = Array.prototype.slice.call(params.udsData);
            model.sent.push([params.serviceID].concat(data));
            const uds = reply(model, params.serviceID, data);
            setImmediate(() => onResponse && onResponse({ errorCode: 'NO_ERROR', udsResponse: Buffer.from([0, 0, 0, 0, 0, 0, ecuAddr, 0, 0].concat(uds)) }));
            return 'NO_ERROR';
        },
    };
}

module.exports = { diag, signals, reply };
