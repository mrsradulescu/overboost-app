#!/usr/bin/env python3
"""Compare the "snap" lines of bclog-*.jsonl: which ECU values differ between the labelled states.
Usage: python diff_snaps.py bclog-*.jsonl   (several files are read in order)"""
import json, sys
snaps = []
for f in sys.argv[1:]:
    for line in open(f, encoding='utf-8', errors='replace'):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get('k') == 'snap':
            snaps.append(r)
if not snaps:
    sys.exit('no snap lines found (state marks pressed while "ECU reading" was running?)')
print('%d snapshots: %s' % (len(snaps), ', '.join('%s(%d)' % (s['label'], s['n']) for s in snaps)))
keys = sorted({k for s in snaps for k in s['vals']})
shown = 0
for k in keys:
    vs = [s['vals'].get(k) for s in snaps]
    if len({v for v in vs if v is not None}) > 1:
        shown += 1
        print('\n' + k)
        for s, v in zip(snaps, vs):
            print('   %-22s %s' % (s['label'], v))
print('\n%d of %d values differ between snapshots' % (shown, len(keys)))
