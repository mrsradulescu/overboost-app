#!/usr/bin/env python3
"""Make the in-car update package: dist/overboost-update.tar.gz (attach it to a GitHub release whose tag is the new
version). Holds the app's JavaScript and version.json - what the in-car "Update now" installs.
It cannot change the signal list, the screen layout or the icon: those need a full install from the laptop
(then raise "base" in app/version.json)."""
import json, sys, tarfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
ver = json.loads((ROOT / 'app/version.json').read_text())
out = ROOT / 'dist' / 'overboost-update.tar.gz'
out.parent.mkdir(exist_ok=True)
with tarfile.open(out, 'w:gz') as t:
    for f in sorted((ROOT / 'src').glob('*.js')):
        if f.name in ('loader.js',):
            continue          # the loader is part of the install, not of an update
        t.add(f, arcname=f.name)
    t.add(ROOT / 'app/version.json', arcname='version.json')
print(out, 'version', ver['version'], 'base', ver['base'])
