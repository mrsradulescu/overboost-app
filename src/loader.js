'use strict';
/**
 * loader.js - the app's real entry (source/javascript/app.js). Starts main.js from an update downloaded in the
 * car (data/update/current) when there is a newer one for this build, else the one that was installed.
 * An update that fails to start three times in a row is put aside and the installed version runs again.
 */
const fs = require('fs');
const path = require('path');
const Module = require('module');
const SRC = __dirname;
const ROOT = path.resolve(SRC, '..', '..');
const UPD = path.join(ROOT, 'data', 'update');
process.env.OVERBOOST_ROOT = ROOT;
process.env.OVERBOOST_SRC = SRC;
process.env.OVERBOOST_TRIES = path.join(UPD, 'tries');

const readJson = (f) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch (e) { return null; } };
const cmp = (a, b) => {
    const x = String(a).split('.').map(Number), y = String(b).split('.').map(Number);
    for (let i = 0; i < 3; i++) { const d = (x[i] || 0) - (y[i] || 0); if (d) return d; }
    return 0;
};
let entry = path.join(SRC, 'main.js');
try {
    const cur = path.join(UPD, 'current');
    const v = readJson(path.join(cur, 'version.json')), base = readJson(path.join(SRC, 'version.json'));
    let tries = 0;
    try { tries = parseInt(fs.readFileSync(process.env.OVERBOOST_TRIES, 'utf8'), 10) || 0; } catch (e) { /* none */ }
    if (v && base && v.base === base.base && cmp(v.version, base.version) > 0 && fs.existsSync(path.join(cur, 'main.js'))) {
        if (tries >= 3) {
            try { fs.renameSync(cur, path.join(UPD, 'bad-' + v.version + '-' + Date.now())); } catch (e) { /* ignore */ }
            try { fs.unlinkSync(process.env.OVERBOOST_TRIES); } catch (e) { /* ignore */ }
        } else {
            fs.writeFileSync(process.env.OVERBOOST_TRIES, String(tries + 1));
            entry = path.join(cur, 'main.js');
        }
    }
} catch (e) { entry = path.join(SRC, 'main.js'); }
// modules loaded from data/update find the SDK in the app's own node_modules
process.env.NODE_PATH = [path.resolve(SRC, '..', 'node_modules'), process.env.NODE_PATH || ''].filter(Boolean).join(path.delimiter);
Module._initPaths();
require(entry);
