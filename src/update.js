'use strict';
/**
 * update.js - update OVERBOOST from a GitHub release, from the car (no laptop).
 *
 * The repo carries update/overboost-update.tar.gz and update/latest.json {version, sha256} (made by tools/make_update.py): the app's JavaScript and
 * version.json. It is unpacked into data/update/current; loader.js starts it instead of the installed code when
 * it is newer and made for the same base. Only .js / .json files are accepted, only from GitHub's own hosts.
 * What an update cannot change: the signal list, the screen layout, the icon - those need the laptop install.
 */
const fs = require('fs');
const path = require('path');
const https = require('https');
const { URL } = require('url');       // (the head unit's Node has no global URL)
const crypto = require('crypto');
const { spawn } = require('child_process');

const ROOT = process.env.OVERBOOST_ROOT || path.resolve(__dirname, '..', '..');
const SRC = process.env.OVERBOOST_SRC || __dirname;
const UPD = path.join(ROOT, 'data', 'update');
const HOSTS = /^(api\.github\.com|github\.com|codeload\.github\.com|raw\.githubusercontent\.com|objects\.githubusercontent\.com|release-assets\.githubusercontent\.com)$/;
const MAX = 8 * 1024 * 1024;
const ASSET = 'overboost-update.tar.gz';

const readJson = (f, d) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch (e) { return d; } };
const cmp = (a, b) => {
    const x = String(a).replace(/^v/, '').split('.').map(Number), y = String(b).replace(/^v/, '').split('.').map(Number);
    for (let i = 0; i < 3; i++) { const d = (x[i] || 0) - (y[i] || 0); if (d) return d; }
    return 0;
};
function mkdirs(d) { d.split(path.sep).reduce((p, s) => { const n = p ? path.join(p, s) : (s || path.sep); try { fs.mkdirSync(n); } catch (e) { /* exists */ } return n; }, ''); }
function rmrf(p) {
    let st; try { st = fs.lstatSync(p); } catch (e) { return; }
    if (st.isDirectory()) { fs.readdirSync(p).forEach((n) => rmrf(path.join(p, n))); fs.rmdirSync(p); } else fs.unlinkSync(p);
}
function get(url, opts, hops) {
    return new Promise((resolve, reject) => {
        let u;
        try { u = new URL(url); } catch (e) { return reject(new Error('bad link: ' + (e.message || e))); }
        if (u.protocol !== 'https:' || !HOSTS.test(u.hostname)) return reject(new Error('host not allowed: ' + u.hostname));
        const req = https.get({ host: u.hostname, path: u.pathname + u.search, headers: { 'User-Agent': 'overboost-updater', Accept: opts && opts.accept || '*/*' }, timeout: 20000 }, (res) => {
            if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
                res.resume();
                if ((hops || 0) >= 5) return reject(new Error('too many redirects'));
                return resolve(get(new URL(res.headers.location, url).toString(), opts, (hops || 0) + 1));
            }
            if (res.statusCode !== 200) { res.resume(); return reject(new Error('HTTP ' + res.statusCode)); }
            const chunks = []; let n = 0;
            res.on('data', (c) => { n += c.length; if (n > MAX) { req.abort(); reject(new Error('too big')); } else chunks.push(c); });
            res.on('end', () => resolve(Buffer.concat(chunks)));
            res.on('error', reject);
        });
        req.on('timeout', () => { req.abort(); reject(new Error('no answer (no internet?)')); });
        req.on('error', (e) => reject(new Error(e.code === 'ENOTFOUND' || e.code === 'EAI_AGAIN' ? 'no internet on the head unit' : (e.message || String(e)))));
    });
}
function run(cmd, args) {
    return new Promise((resolve, reject) => {
        const p = spawn(cmd, args); let out = '', err = '';
        p.stdout.on('data', (d) => { out += d; }); p.stderr.on('data', (d) => { err += d; });
        p.on('error', reject);
        p.on('close', (c) => (c === 0 ? resolve(out) : reject(new Error(cmd + ' failed: ' + err.slice(0, 120)))));
    });
}

function create(log, deps) {
    const getter = (deps && deps.get) || get;
    const base = readJson(path.join(SRC, 'version.json'), { version: '0.0.0', base: 0 });
    const cfgDef = readJson(path.join(SRC, 'update-default.json'), {});
    // the source is built into the app (update-default.json) and is never shown or changed on screen
    const cfg = { sources: cfgDef.repo ? [cfgDef.repo] : [], active: 0 };
    const st = { busy: false, msg: '', found: null, running: process.env.OVERBOOST_SRC && __dirname !== SRC ? 'updated' : 'installed' };
    const cur = () => readJson(path.join(UPD, 'current', 'version.json'), null);
    const repo = () => cfg.sources[cfg.active] || '';

    async function check() {
        if (st.busy) return;
        if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo())) { st.msg = 'Updates are not set up in this build'; return; }
        st.busy = true; st.found = null; st.msg = 'Checking for updates...';
        try {
            // update/latest.json in the repo: { version, sha256 }; the package sits next to it
            const raw = 'https://raw.githubusercontent.com/' + repo() + '/main/update/';
            const rel = JSON.parse((await getter(raw + 'latest.json')).toString('utf8'));
            if (!rel.version || !/^[0-9a-f]{64}$/.test(rel.sha256 || '')) throw new Error('latest.json is not valid');
            rel.tag_name = rel.version;
            const asset = { browser_download_url: raw + ASSET, size: 0 };
            const have = cur() ? cur().version : base.version;
            st.found = { tag: rel.tag_name, url: asset.browser_download_url, sha: rel.sha256, newer: cmp(rel.tag_name, have) > 0 };
            st.checkedAt = Date.now();
            st.msg = st.found.newer ? 'Update available: ' + rel.tag_name + ' (you have ' + have + ')' : 'Up to date (' + have + ')';
        } catch (e) { st.msg = 'Check failed: ' + (e.message || e); st.checkedAt = Date.now(); log.warn(st.msg); }
        st.busy = false;
    }
    async function install() {
        if (st.busy) return;
        if (!st.found || !st.found.newer) { st.msg = 'Check for updates first'; return; }
        st.busy = true; st.msg = 'Downloading ' + st.found.tag + '...';
        try {
            mkdirs(UPD);
            const buf = await getter(st.found.url);
            const sha = crypto.createHash('sha256').update(buf).digest('hex');
            if (sha !== st.found.sha) throw new Error('package does not match its checksum');
            const pkg = path.join(UPD, 'pkg.tar.gz'), nw = path.join(UPD, 'new');
            fs.writeFileSync(pkg, buf);
            const names = (await run('tar', ['tzf', pkg])).split('\n').filter(Boolean);
            const bad = names.filter((n) => /(^|\/)\.\.(\/|$)/.test(n) || n[0] === '/' || !/^[A-Za-z0-9_.\/-]+$/.test(n) || (n.slice(-1) !== '/' && !/\.(js|json)$/.test(n)));
            if (!names.length || bad.length) throw new Error('package rejected (' + (bad[0] || 'empty') + ')');
            rmrf(nw); mkdirs(nw);
            await run('tar', ['xzf', pkg, '-C', nw]);
            const v = readJson(path.join(nw, 'version.json'), null);
            if (!v || v.base !== base.base) throw new Error('package is for another base build (needs the laptop install)');
            if (cmp(v.version, st.found.tag) !== 0) throw new Error('package version ' + v.version + ' does not match release ' + st.found.tag);
            if (!fs.existsSync(path.join(nw, 'main.js'))) throw new Error('package has no main.js');
            const prev = path.join(UPD, 'previous');
            rmrf(prev);
            if (fs.existsSync(path.join(UPD, 'current'))) fs.renameSync(path.join(UPD, 'current'), prev);
            fs.renameSync(nw, path.join(UPD, 'current'));
            try { fs.unlinkSync(path.join(UPD, 'tries')); } catch (e) { /* none */ }
            try { fs.unlinkSync(pkg); } catch (e) { /* ignore */ }
            st.msg = 'Installed ' + v.version + ' (sha256 ' + sha.slice(0, 12) + '). Restart OVERBOOST to use it.';
            log.info('update installed ' + v.version + ' sha256 ' + sha);
            st.found.newer = false; st.pending = v.version;
        } catch (e) { st.msg = 'Install failed: ' + (e.message || e); log.warn(st.msg); }
        st.busy = false;
    }
    function remove() {
        try { rmrf(path.join(UPD, 'current')); try { fs.unlinkSync(path.join(UPD, 'tries')); } catch (e) { /* none */ } st.msg = 'Updates removed. Restart OVERBOOST: the installed version runs.'; st.pending = 'installed'; }
        catch (e) { st.msg = 'Remove failed: ' + e.message; }
    }
    return { st, cfg, base, check, install, remove, repo, cur };
}

module.exports = { create, cmp };
