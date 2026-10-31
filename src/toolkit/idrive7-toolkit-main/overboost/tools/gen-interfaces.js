'use strict';
// Adapted from cauliflowermossgrape/idrive7-apps (MIT); see LICENSES/idrive7-apps-MIT.txt.
/**
 * Generate ID8 interfaces and ACL using the unit's own firmware generators.
 *
 *   node gen-interfaces.js <someip-interfaces.json> <manifest.json> <userId> <outDir>
 *
 * On the head unit the catalog is /etc/oap/someip/someip-interfaces.json.
 * Node 8 compatible (runs on the unit at install time).
 */
const fs = require('fs');
const path = require('path');

// BMW's generators live inside MainApp's AppManagement bundle on the unit.
// No local BMW generator modules need to be distributed with this kit.
const APPMGMT = process.env.OAP_APPMGMT || '/usr/lib/oap-mainapp21/src/AppManagement/AppManagement.js';
let bundleReq = null, bundleText = null;
function fromFirmware(name) {
    if (!bundleText) bundleText = fs.readFileSync(APPMGMT, 'utf8');
    const m = new RegExp('"someip-interfaces-generator/' + name + '(?:\\.js)?":(\\d+)').exec(bundleText);
    if (!m) throw new Error(name + ' not found in ' + APPMGMT);
    if (!bundleReq) {
        const tail = /,\{\},\[\d+\]\)\s*;?\s*$/;
        if (!tail.test(bundleText)) throw new Error('unexpected AppManagement bundle format');
        // same bundle, but with no entry module, so only what we ask for runs
        bundleReq = new Function('require', 'return ' + bundleText.replace(tail, ',{},[])'))(require);
    }
    return bundleReq(m[1]);
}

/**
 * Take out of manifest.proxies what the unit's catalog does not have (an interface, or single
 * attributes / broadcasts / methods of it), so the app still installs on a unit that lacks some
 * of the signals it can listen to. Returns { name: reason } for what was left out. Only the
 * `optional` (names) limits this to the proxies the app can live without; every other proxy is
 * left alone, so a platform proxy missing from the catalog still stops the generator loudly.
 */
function dropMissing(db, manifest, optional) {
    const missing = {};
    const catalog = db.interfaces || {};
    Object.keys(manifest.proxies).forEach((name) => {
        if (optional.indexOf(name) < 0) return;
        const e = manifest.proxies[name];
        if (typeof e !== 'object' || e === null) return;
        const i = catalog[name];
        if (!i) { delete manifest.proxies[name]; missing[name] = 'interface not in the catalog'; return; }
        const drop = (list, have, what) => (list || []).filter((x) => {
            if (have && have[x]) return true;
            missing[name + '#' + x] = what + ' not in the catalog';
            return false;
        });
        if (e.attributes && i.fields) {
            Object.keys(e.attributes).forEach((f) => { if (!i.fields[f]) { delete e.attributes[f]; missing[name + '#' + f] = 'attribute not in the catalog'; } });
        }
        if (e.broadcasts && i.events) e.broadcasts = drop(e.broadcasts, i.events, 'broadcast');
        if (e.methods && i.methods) e.methods = drop(e.methods, i.methods, 'method');
        const empty = !(e.attributes && Object.keys(e.attributes).length) && !(e.broadcasts && e.broadcasts.length) && !(e.methods && e.methods.length);
        if (empty) delete manifest.proxies[name];
    });
    return missing;
}

function main(argv) {
    if (argv.length !== 4 || !/^\d+$/.test(argv[2])) {
        console.error('usage: node gen-interfaces.js <someip-interfaces.json> <manifest.json> <numeric UID> <outDir>');
        process.exit(2);
    }
    const [dbPath, manifestPath, userId, outDir] = argv;
    const db = JSON.parse(fs.readFileSync(dbPath, 'utf8'));
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8').replace(/\${userId}/g, String(userId)));
    // optional: the signals of watch.json (next to the manifest) and the diagnostic transport
    let optional = ['com.harman.infotainment.systemfunctions.tasu.tasu.InternalTasu'];
    try { optional = optional.concat(Object.keys(JSON.parse(fs.readFileSync(path.join(path.dirname(manifestPath), 'watch.json'), 'utf8')))); } catch (e) { /* none */ }
    const missing = dropMissing(db, manifest, optional);
    const ifs = fromFirmware('IFConfigGenerator').generate(db, manifest);
    const acl = fromFirmware('AclGenerator').generate(db, manifest);
    if (!fs.existsSync(outDir)) fs.mkdirSync(outDir);
    let n = 0;
    Object.keys(ifs).forEach((id) => {
        fs.writeFileSync(path.join(outDir, id.replace(/\./g, '_') + '.json'), JSON.stringify(ifs[id]));
        n++;
    });
    fs.writeFileSync(path.join(outDir, 'acl.json'), JSON.stringify(acl));
    // what this unit's catalog lacks, for the app's log (it runs without those signals)
    fs.writeFileSync(path.join(outDir, 'missing-interfaces.json'), JSON.stringify(missing));
    Object.keys(missing).forEach((k) => console.log('not in this unit\'s catalog, left out: ' + k + ' (' + missing[k] + ')'));
    console.log('generated ' + n + ' interface configs + acl.json (' + acl.offers.length + ' offers, ' + acl.requests.length + ' requests)');
}

if (require.main === module) main(process.argv.slice(2));
module.exports = { main, dropMissing };
