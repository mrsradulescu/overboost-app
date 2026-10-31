#!/usr/bin/env python3
"""analyze_bclog.py - find the signals behind the BC button, the wheel and the cluster menus.

    python3 kit/tools/analyze_bclog.py bclog-*.jsonl            # the two reports below
    python3 kit/tools/analyze_bclog.py log.jsonl --find 37      # where does the number 37 (ethanol %, ...) appear?
    python3 kit/tools/analyze_bclog.py log.jsonl --pre 0.5 --post 4 --top 15 --out report.txt

Input: files written by the BC Logger app (copy them off the unit, see BCLOG-CITESTE.md).

1. ACTIONS (marks of kind "action": BC pressed, wheel up / down). For each label, the signals that
   changed in the window [mark - pre, mark + post] after most of the marks, but rarely change when
   nothing is going on (the baseline = all time outside any mark window). A signal that changes
   all the time (rpm, speed) is dropped: it would "hit" by chance.
2. STATES (marks of kind "state": Multimap 1..4, Valve 0..2). For each group, the signals whose value
   is the same at every mark of one label but differs between the labels: a signal that carries the
   position the cluster shows.

Tip for a good log: 30+ seconds without touching anything first, then every action 5 times with a
pause of 5+ seconds, one MARK before each.
"""
import argparse, bisect, collections, json, math, os, sys


class Session(object):
    def __init__(self, path):
        self.path = path
        self.names = {}                      # id -> name (per file: ids restart with the app)
        self.is_event = set()
        self.ev = []                         # (t, name, raw json text of the value)
        self.marks = []                      # dicts
        self.drops = 0
        self.wall = None
        self.t0 = None
        self.t1 = None
        self.bad = 0
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    self.bad += 1            # the last line may be cut when the app was killed
                    continue
                self._add(o, line)
        self.ev.sort(key=lambda x: x[0])
        self.times = [e[0] for e in self.ev]

    def _add(self, o, line):
        t = o.get('t')
        if isinstance(t, (int, float)):
            self.t0 = t if self.t0 is None else min(self.t0, t)
            self.t1 = t if self.t1 is None else max(self.t1, t)
        k = o.get('k')
        if k == 'file':
            self.wall = self.wall or o.get('wall')
        elif k == 'sig':
            self.names[o['i']] = o['name']
            if o.get('e'):
                self.is_event.add(o['name'])
        elif k == 'mark':
            self.marks.append(o)
        elif k == 'drop':
            self.drops += o.get('n', 0)
        elif 'i' in o and 'v' in o:
            name = self.names.get(o['i'])
            if name is not None:
                self.ev.append((o['t'], name, json.dumps(o['v'], sort_keys=True, separators=(',', ':'))))

    def span_s(self):
        return 0.0 if self.t0 is None else max(0.0, (self.t1 - self.t0) / 1000.0)

    def between(self, a, b):
        lo = bisect.bisect_left(self.times, a)
        hi = bisect.bisect_right(self.times, b)
        return self.ev[lo:hi]

    def value_at(self, t):
        """{name: raw value text} = the last value of every signal up to time t."""
        last = {}
        for (tt, n, v) in self.ev:
            if tt > t:
                break
            last[n] = v
        return last


def short(name, n=60):
    iface, _, field = name.partition('#')
    s = iface.split('.')[-1] + ('#' + field if field else '')
    return s if len(s) <= n else s[:n - 1] + '~'


def clipv(v, n=48):
    return v if len(v) <= n else v[:n - 1] + '~'


def merge_windows(wins):
    out = []
    for a, b in sorted(wins):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def analyze(sessions, pre_s, post_s, top, w):
    pre, post = pre_s * 1000.0, post_s * 1000.0
    for s in sessions:
        w('== %s   wall %s   %.0f s, %d changes, %d signals, %d marks%s%s' % (
            os.path.basename(s.path), s.wall or '?', s.span_s(), len(s.ev), len(set(e[1] for e in s.ev)), len(s.marks),
            (', %d lines dropped by the cap (raise "Max lines per second" or "Min gap")' % s.drops) if s.drops else '',
            (', %d unreadable lines' % s.bad) if s.bad else ''))

    # ---- baseline: changes outside every mark window ----
    base_changes = collections.Counter()
    base_s = 0.0
    for s in sessions:
        if s.t0 is None:
            continue
        wins = merge_windows([(m['t'] - pre, m['t'] + post) for m in s.marks])
        inside = sum(max(0.0, min(b, s.t1) - max(a, s.t0)) for a, b in wins) / 1000.0
        base_s += max(0.0, s.span_s() - inside)
        starts = [x[0] for x in wins]
        for (t, n, v) in s.ev:
            i = bisect.bisect_right(starts, t) - 1
            if not (i >= 0 and wins[i][0] <= t <= wins[i][1]):
                base_changes[n] += 1
    w('')
    w('Baseline (time with no mark window): %.0f s%s' % (base_s, '   ** short: log 30+ s of nothing first **' if base_s < 20 else ''))
    noisy = sorted(((c / max(base_s, 1.0), n) for n, c in base_changes.items()), reverse=True)[:5]
    if noisy:
        w('Busiest signals at rest (ignored below): ' + ', '.join('%s %.1f/s' % (short(n, 36), r) for r, n in noisy))

    def chance(name):
        rate = base_changes.get(name, 0) / max(base_s, 1.0)
        return 1.0 - math.exp(-rate * (pre + post) / 1000.0)

    # ---- 1. actions ----
    labels = collections.OrderedDict()
    for s in sessions:
        for m in s.marks:
            if m.get('kind', 'action') == 'action':
                labels.setdefault(m['label'], []).append((s, m))
    w('')
    w('1. ACTIONS - signals that changed within -%.1f..+%.1f s of the mark' % (pre_s, post_s))
    if not labels:
        w('   (no action marks in the log)')
    for label, ms in labels.items():
        hits = collections.Counter()
        vals = collections.defaultdict(collections.Counter)
        for (s, m) in ms:
            seen = {}
            for (t, n, v) in s.between(m['t'] - pre, m['t'] + post):
                seen.setdefault(n, []).append(v)
            for n, vs in seen.items():
                hits[n] += 1
                vals[n][' > '.join(clipv(x, 30) for x in vs[:3])] += 1
        rows = []
        for n, h in hits.items():
            p = h / float(len(ms))
            c = chance(n)
            if p >= 0.5 and c < 0.8:
                rows.append((p - c, p, n, c))
        rows.sort(reverse=True)
        w('')
        w('  "%s"   (%d marks)' % (label, len(ms)))
        if not rows:
            w('    nothing stands out. The signal may not reach the head unit, or the marks were too early / late')
            w('    (try --pre 3 --post 1 if you tapped MARK after the action).')
        for lift, p, n, c in rows[:top]:
            ex = vals[n].most_common(2)
            w('    %3.0f%% of marks  (by chance %2.0f%%)  %-52s %s' % (p * 100, c * 100, short(n, 52) + ('  [event]' if any(n in s.is_event for s in sessions) else ''),
                                                                       '; '.join('%s x%d' % (k, c2) for k, c2 in ex)))

    # ---- 2. states ----
    groups = collections.OrderedDict()
    for s in sessions:
        for m in s.marks:
            if m.get('kind') == 'state':
                groups.setdefault(m.get('group') or '?', collections.OrderedDict()).setdefault(m['label'], []).append((s, m))
    w('')
    w('2. STATES - signals whose value at the mark is constant per label and different between labels')
    if not groups:
        w('   (no state marks in the log)')
    for g, labs in groups.items():
        snap = collections.defaultdict(dict)                 # name -> label -> set of values
        for label, ms in labs.items():
            for (s, m) in ms:
                for n, v in s.value_at(m['t'] + post).items():
                    snap[n].setdefault(label, set()).add(v)
        rows = []
        for n, per in snap.items():
            if len(per) < len(labs) or len(labs) < 2:
                continue
            if any(len(vs) != 1 for vs in per.values()):
                continue                                     # not constant within a label
            vals = [next(iter(vs)) for vs in per.values()]
            distinct = len(set(vals))
            if distinct < 2:
                continue
            rows.append((distinct, -chance(n), n, per))
        rows.sort(key=lambda x: (-x[0], -x[1]))
        w('')
        w('  group "%s"   labels: %s' % (g, ', '.join('%s (%d)' % (l, len(m)) for l, m in labs.items())))
        if len(labs) < 2:
            w('    only one label - nothing to compare (use --find <number shown> for a value such as ethanol %)')
            continue
        if not rows:
            w('    no signal separates these labels.')
        for distinct, nc, n, per in rows[:top]:
            tag = 'ALL %d differ' % len(labs) if distinct == len(labs) else '%d of %d differ' % (distinct, len(labs))
            w('    %-52s %s' % (short(n, 52), tag))
            for label, vs in per.items():
                w('        %-22s %s' % (label, clipv(next(iter(vs)), 70)))


FACTORS = [1, 0.1, 0.01, 0.02, 0.05, 0.2, 0.5, 0.25, 0.4, 2, 10, 100.0 / 255, 100.0 / 1023, 100.0 / 4095, 100.0 / 65535, 0.0625, 0.125]


def leaves(x, path=''):
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        yield path, float(x)
    elif isinstance(x, dict):
        for k, v in x.items():
            for r in leaves(v, path + '.' + str(k)):
                yield r
    elif isinstance(x, list):
        for i, v in enumerate(x[:64]):
            for r in leaves(v, path + '[%d]' % i):
                yield r


def find_number(sessions, target, tol, w):
    w('Numbers that are %g (+-%g) as logged, or after a common scale factor:' % (target, tol))
    found = collections.OrderedDict()
    for s in sessions:
        for (t, n, v) in s.ev:
            try:
                x = json.loads(v)
            except ValueError:
                continue
            for path, num in leaves(x):
                for f in FACTORS:
                    if abs(num * f - target) <= tol:
                        key = (n, path, f)
                        e = found.setdefault(key, [0, num])
                        e[0] += 1
                        break
    if not found:
        w('  none. Not seen by the head unit, or shown with another scale (try --tol 2).')
    for (n, path, f), (cnt, num) in sorted(found.items(), key=lambda kv: -kv[1][0])[:30]:
        w('  %-56s %-28s raw %g x %g  (%d times)' % (short(n, 56), path or '(value)', num, f, cnt))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+', help='bclog-*.jsonl')
    ap.add_argument('--pre', type=float, default=0.5, help='seconds before a mark to look at (default 0.5)')
    ap.add_argument('--post', type=float, default=4.0, help='seconds after a mark to look at (default 4)')
    ap.add_argument('--top', type=int, default=12, help='rows per label / group (default 12)')
    ap.add_argument('--find', type=float, help='list the signals that carry this number')
    ap.add_argument('--tol', type=float, default=0.6, help='tolerance for --find (default 0.6)')
    ap.add_argument('--out', help='also write the report to this file')
    a = ap.parse_args()
    lines = []

    def w(s=''):
        lines.append(s)
        print(s)

    sessions = [Session(p) for p in a.files]
    if a.find is not None:
        find_number(sessions, a.find, a.tol, w)
    else:
        analyze(sessions, a.pre, a.post, a.top, w)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
