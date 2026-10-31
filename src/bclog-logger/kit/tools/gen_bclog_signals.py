#!/usr/bin/env python3
"""gen_bclog_signals.py - fill apps/bclog/app.json with the signals of YOUR unit's catalog.

The BC Logger listens to whatever app.json asks for. The starter app.json only has signals that
the other apps of this repo are known to use. To look for the BC button / steering-wheel / cluster
signals, let this script pick interfaces from the catalog of your head unit:

    # 1. copy the catalog from the unit (once)
    mkdir -p reference
    scp root@<unit>:/etc/oap/someip/someip-interfaces.json reference/

    # 2. see what would be listened to (changes nothing)
    python3 kit/tools/gen_bclog_signals.py --list
    python3 kit/tools/gen_bclog_signals.py --list --include 'kombi|cluster|tripcomp'     # your own filter
    python3 kit/tools/gen_bclog_signals.py --list --all                                  # every interface

    # 3. write apps/bclog/app.json ("signals" and "config.watch"; the rest of the file is kept)
    python3 kit/tools/gen_bclog_signals.py --write
    python3 kit/tools/gen_bclog_signals.py --write --iface de.bmw.infotainment.KombiInterface_2 --iface ...

    # 4. check the flags against the catalog, then build
    python3 kit/tools/check_signals.py
    python3 kit/tools/build.py bclog

What is picked: every field that has a change notifier (flag "s", plus "r" if it has a getter, so the
first value is read at start) and every event (broadcast) of the chosen interfaces. Nothing is
written to the car: the app has no methods and no setters.
"""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
CATALOG = os.path.join(ROOT, 'reference', 'someip-interfaces.json')
APP_JSON = os.path.join(ROOT, 'apps', 'bclog', 'app.json')

# In priority order: when --max-fields cuts the list, the first patterns win.
DEFAULT_INCLUDE = [
    r'controlelement', r'FrontCCU', r'steering', r'lenk', r'button', r'switch', r'taste', r'stalk', r'lever',
    r'kombi', r'cluster', r'instrument', r'tacho',
    r'boardcomputer|onboard|tripcomp|\.trip', r'drivingexcitement|drivemode|drivingmode|mdrive|sport',
    r'checkcontrol', r'fuel|ethanol|tank', r'engine|powertrain|\.cds',
]


def instances_of(db, iface):
    """Instance names the catalog lists for an interface; '1' (what every app here uses) if unclear."""
    raw = (db.get('instanceIDs') or {}).get(iface)
    names = []
    if isinstance(raw, str):
        names = [raw]
    elif isinstance(raw, list):
        names = [x for x in raw if isinstance(x, str)]
    elif isinstance(raw, dict):
        keys = list(raw.keys())
        vals = list(raw.values())
        if all(isinstance(v, (int, float, dict)) for v in vals):
            names = [str(k) for k in keys]
        elif all(isinstance(v, str) for v in vals):
            names = [str(k) for k in keys] if all(not str(k).isdigit() or len(str(k)) < 3 for k in keys) else vals
    names = [n for n in names if n and '${' not in n]
    return names or ['1']


def pick_interface(db, name):
    """-> {'fields': {field: flags}, 'events': [..], 'instances': [..]} or None if nothing to listen to."""
    i = db['interfaces'][name]
    fields = {}
    for f, fd in sorted((i.get('fields') or {}).items()):
        if not fd.get('notifier'):
            continue                                           # no change notification: nothing to subscribe to
        fields[f] = 'rs' if (fd.get('getter') or {}).get('id') else 's'
    events = sorted((i.get('events') or {}).keys())
    if not fields and not events:
        return None
    return {'fields': fields, 'events': events, 'instances': instances_of(db, name)}


def signals_from_picks(picks):
    """picks: {iface: pick} -> app.json "signals"."""
    out = {}
    for name, p in picks.items():
        e = {}
        e.update(p['fields'])
        if p['events']:
            e['_broadcasts'] = list(p['events'])
        inst = p['instances']
        if inst != ['1']:
            e['_instances'] = inst[0] if len(inst) == 1 else list(inst)
        e['_optional'] = 'log of this interface'
        out[name] = e
    return out


def watch_from_signals(signals):
    """app.json "signals" -> "config.watch", which is what the app subscribes to at run time."""
    watch = {}
    for name, e in signals.items():
        inst = e.get('_instances', '1')
        watch[name] = {
            'instances': [inst] if isinstance(inst, str) else list(inst),
            'fields': [k for k in e if not k.startswith('_')],
            'events': list(e.get('_broadcasts') or []),
        }
    return watch


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--catalog', default=CATALOG, help='someip-interfaces.json copied from the unit')
    ap.add_argument('--app-json', default=APP_JSON)
    ap.add_argument('--list', action='store_true', help='print the interfaces that would be listened to')
    ap.add_argument('--write', action='store_true', help='write signals + config.watch into app.json')
    ap.add_argument('--include', action='append', help='regex on the interface name (repeatable); default: BC/cluster/buttons/drive-mode words')
    ap.add_argument('--exclude', action='append', help='regex on the interface name to leave out (repeatable)')
    ap.add_argument('--iface', action='append', default=[], help='an exact interface name (repeatable); adds to --include')
    ap.add_argument('--all', action='store_true', help='every interface of the catalog (use with --list; --write needs --max-fields)')
    ap.add_argument('--max-fields', type=int, default=400, help='stop adding interfaces above this many fields+events (default 400)')
    a = ap.parse_args()
    if not (a.list or a.write):
        ap.error('give --list or --write')

    if not os.path.exists(a.catalog):
        sys.exit('catalog not found: %s\n  copy it from the unit:  scp root@<unit>:/etc/oap/someip/someip-interfaces.json reference/' % a.catalog)
    db = json.load(open(a.catalog, encoding='utf-8'))
    ifaces = db.get('interfaces') or {}
    if not ifaces:
        sys.exit('no "interfaces" in the catalog - is this the right file?')

    pats = [re.compile(p, re.I) for p in (a.include or DEFAULT_INCLUDE)]
    exc = [re.compile(p, re.I) for p in (a.exclude or [])]

    def priority(n):
        if n in a.iface:
            return -1
        if a.all:
            return 0
        for k, p in enumerate(pats):
            if p.search(n):
                return k
        return None

    cand = []
    for n in sorted(ifaces):
        pr = priority(n)
        if pr is None or (n not in a.iface and any(p.search(n) for p in exc)):
            continue
        pk = pick_interface(db, n)
        if pk:
            cand.append((pr, n, pk))
    cand.sort(key=lambda x: (x[0], x[1]))
    missing = [n for n in a.iface if n not in ifaces]
    for n in missing:
        print('WARNING: not in the catalog: %s' % n, file=sys.stderr)

    chosen, total, skipped = {}, 0, []
    for pr, n, pk in cand:
        k = len(pk['fields']) + len(pk['events'])
        if total + k > a.max_fields and chosen:
            skipped.append((n, k))
            continue
        chosen[n] = pk
        total += k

    print('%d interfaces, %d fields + events%s' % (len(chosen), total, ('   (%d interfaces left out by --max-fields %d)' % (len(skipped), a.max_fields)) if skipped else ''))
    if a.list:
        w = max([len(n) for n in chosen] + [10])
        for n, pk in chosen.items():
            print('  %-*s  %3d fields  %3d events  instances %s   [catalog: %r]' % (
                w, n, len(pk['fields']), len(pk['events']), pk['instances'], (db.get('instanceIDs') or {}).get(n)))
        for n, k in skipped:
            print('  (left out) %s  %d' % (n, k))
    if not chosen:
        sys.exit('nothing matched - try --include or --all with --list')

    if a.write:
        app = json.load(open(a.app_json, encoding='utf-8'))
        sig = signals_from_picks(chosen)
        app['signals'] = sig
        cfg = app.setdefault('config', {})
        cfg['watch'] = watch_from_signals(sig)
        with open(a.app_json, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(app, f, indent=2, ensure_ascii=False)
            f.write('\n')
        print('wrote %s' % a.app_json)
        print('next:  python3 kit/tools/check_signals.py && python3 kit/tools/build.py bclog')


if __name__ == '__main__':
    main()
