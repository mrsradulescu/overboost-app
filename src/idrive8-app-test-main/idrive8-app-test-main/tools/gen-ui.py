#!/usr/bin/env python3
"""app/ui.json -> app/application.xml, app/uiDescription.json, app/strings.json (python3 tools/gen-ui.py).

One state "MAIN": the labels, then the buttons, in a list; the title bar last. Only the widgets the
bench-tested screen already used (label, button, title). Component ids follow the order of ui.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = json.loads((ROOT / 'app/ui.json').read_text())
P = 'SID_ID8_BENCH_DEMO_'
strings = {P + 'TITLE': spec['title'], P + 'MAIN_TITLE': spec['title']}
xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<application>',
       '    <container id="1" type="state" widget="LT_State_Wide_List">']
components = {}
next_id = 2


def component(kind, name, text):
    global next_id
    cid = next_id
    next_id += 1
    strings[P + name] = text
    if kind == 'label':
        xml.append('            <component id="%d" type="label" widget="LT_ScrollLabel">' % cid)
        xml.append('                <property id="2" value="1"/>')
        xml.append('                <property id="3" value="1"/>')
        props = {'Visible': {'value': 1, 'type': 'UInt'}, 'Focusable': {'value': 1, 'type': 'UInt'}}
    else:
        xml.append('            <component id="%d" type="button">' % cid)
        xml.append('                <property id="2" value="1"/>')
        xml.append('                <property id="3" value="2"/>')
        props = {'Visible': {'value': 1, 'type': 'UInt'}, 'Focusable': {'value': 2, 'type': 'UInt'}}
    xml.append('            </component>')
    components[str(cid)] = {'type': kind, 'name': name, 'properties': props,
                            'rows': [{'Text': {'value': P + name, 'type': 'StringId'}}]}


list_group = next_id + len(spec['labels']) + len(spec['buttons']) + 2
xml.append('        <layoutGroup id="%d" name="List">' % list_group)
xml.append('            <property id="3" value="1"/>')
for name, text in spec['labels']:
    component('label', name, text)
for name, text in spec['buttons']:
    component('button', name, text)
xml.append('        </layoutGroup>')
title_id = next_id
xml.append('        <layoutGroup id="%d" name="Titlebar">' % (title_id + 1))
xml.append('            <component id="%d" type="title"/>' % title_id)
xml.append('        </layoutGroup>')
xml.append('    </container>')
xml.append('</application>')
components[str(title_id)] = {'type': 'title', 'name': 'MAIN_TITLE', 'rows': [{'Text': {'value': P + 'MAIN_TITLE', 'type': 'StringId'}}]}
(ROOT / 'app/application.xml').write_text('\n'.join(xml) + '\n')
model = {'schemaVersion': '1.3.0', 'containers': {'1': {'type': 'state', 'name': 'MAIN', 'components': components}}}
(ROOT / 'app/uiDescription.json').write_text(json.dumps(model, indent=1) + '\n')
(ROOT / 'app/strings.json').write_text(json.dumps(strings) + '\n')
print('components:', len(components) - 1)
