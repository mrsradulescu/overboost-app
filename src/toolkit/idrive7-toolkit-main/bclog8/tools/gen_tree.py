#!/usr/bin/env python3
"""gen_tree.py - writes the package tree of bclog8 (iDrive 8) from apps/bclog8/app.json.

    python3 tools/gen_tree.py            # (re)writes apps/bclog8/bclog8@<ver>/, mgu-app.json, mgu-repo.json

Run it again after changing the signals (tools/gen_bclog_signals.py --write): the SOME/IP manifest,
config/watch.json and the screen all come from app.json.

What an iDrive 8 app package needs (as the apps of IterioDev/car-apps, which run on real cars, and
mgu-store's deploy tool have it):
  package.json      main javascript/runner.js (the stock entry point, supplied by the head unit's build),
                    "mguHmi": ["ID8"]
  manifest.json     SOME/IP proxies: the platform set of iDrive 8 (EntryPoint2, RemoteHmi2, ResourceManager2,
                    UseCase*, Certification, AppStatus, ...) + the signals of app.json (read-only)
  permissions.json  ACL tables (masterAccessTable + masterRegistrationTable)
  rhmiData/         application.json (appletId, entry point at state 3000), uiDescriptions/ID8/ (application.xml,
                    uiDescription.json), strings
The package has no javascript/app.js: the head unit links src/*.js against the oap-sdk modules of a stock
app (mgu-app.json "sdk") and writes it. No BMW code is in this repository.
"""
import json, os, uuid, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
APPDIR = os.path.join(ROOT, 'apps', 'bclog8')
NS = uuid.UUID('6b1d3a52-8f0e-4c4b-9a77-0d0c4d4e5f10')

# the screen: (component name, kind, text) - kind "label" = a line of text, "button" = a row you can press
SCREEN = [
    ('LOG_STATUS', 'label', ''), ('LOG_STATUS2', 'label', ''), ('LOG_LAST', 'label', ''), ('LOG_FILE', 'label', ''),
    ('ECU_STATUS', 'label', 'ECU: off'), ('ECU_MENU', 'label', 'BC menu: -'),
    ('BTN_ECU', 'button', 'ECU reading: start'),
    ('BTN_LOG', 'button', 'Pause / resume logging'), ('BTN_SWEEP', 'button', 'ECU sweep: start'),
    ('BTN_NEWFILE', 'button', 'Start a new file'),
    ('BTN_MARK_BCLONG', 'button', 'MARK  BC held 5 s'),
    ('BTN_MARK_BCSHORT', 'button', 'MARK  BC short press'),
    ('BTN_MARK_BL1', 'button', 'SHOWS  1 blink (menu 1, maps)'),
    ('BTN_MARK_BL2', 'button', 'SHOWS  2 blinks (menu 2, valves)'),
    ('BTN_MARK_BL3', 'button', 'SHOWS  3 blinks (menu 3, ethanol)'),
    ('BTN_MARK_MM1', 'button', 'NEEDLE  position 1 (map 1)'), ('BTN_MARK_MM2', 'button', 'NEEDLE  position 2 (map 2)'),
    ('BTN_MARK_MM3', 'button', 'NEEDLE  position 3 (map 3)'), ('BTN_MARK_MM4', 'button', 'NEEDLE  position 4 (map 4)'),
    ('BTN_MARK_V0', 'button', 'NEEDLE  position 0 (valves OEM)'), ('BTN_MARK_V1', 'button', 'NEEDLE  position 1 (valves open)'),
    ('BTN_MARK_V2', 'button', 'NEEDLE  position 2 (valves closed)'),
    ('BTN_MARK_ETH', 'button', 'NEEDLE  ethanol reading shown'),
    ('BTN_MARK_X', 'button', 'MARK  custom'),
]
STATE_ID = 3000          # the entry point opens this state (application.json insertionStateId)
APPLET_OFFSET_NOTE = 'the app asks for appletId + 3000; the deploy tool keeps the ids on a unit apart'

# SOME/IP interfaces every iDrive 8 app needs (the platform set the IterioDev apps' manifests name)
PLATFORM_PROXIES = {
    'de.bmw.infotainment.certification.Certification': '*',
    'de.bmw.infotainment.hmi.entrypoint2.EntryPoint2': '*',
    'de.bmw.infotainment.hmi.entrypoint2.EntryPointManager2': '*',
    'de.bmw.infotainment.hmi.remote2.RemoteHmi2': '*',
    'de.bmw.infotainment.hmi.remote2.RemoteHmiApplication2': '*',
    'de.bmw.infotainment.hmi.resourcemanagement2.ResourceBundle2': '*',
    'de.bmw.infotainment.hmi.resourcemanagement2.ResourceManager2': '*',
    'de.bmw.infotainment.hmi.resourcemanagement2.StringBundle2': '*',
    'de.bmw.infotainment.hmi.usecasemanagement.UseCaseContext': {
        'instances': '*', 'broadcasts': ['notify'], 'methods': ['setObservedContextKeys']},
    'de.bmw.infotainment.hmi.usecasemanagement.UseCaseInstance': '*',
    'de.bmw.infotainment.hmi.usecasemanagement.UseCaseProvider': '*',
    'de.bmw.infotainment.hmi.usecasemanagement.UseCaseRegistry': {
        'instances': '*', 'attributes': {'useCaseIds': 'r'}, 'broadcasts': ['registryUpdate'], 'methods': ['getProvider']},
    'de.bmw.infotainment.personalization.SessionManager': '*',
    'de.bmw.infotainment.personalization.UserAccountManager': '*',
    'de.bmw.infotainment.telematic.oap.AppStatus': '*',
    'de.bmw.infotainment.telematic.oap.WodControl': {
        'instances': '${userId}', 'methods': ['offerService', 'stopOfferingService']},
    'de.bmw.infotainment.telematic.oapcontroller.OAPController': '*',
    'org.genivi.nodestatemanager.Consumer': {
        'instances': 'NSMConsumer', 'broadcasts': ['ShutdownEvents', 'SessionStateChanged'],
        'methods': ['LifecycleRequestComplete', 'RegisterShutdownClient', 'UnRegisterShutdownClient']},
}
# The engine-ECU link (UDS through the head unit's gateway). Only added when the unit's catalog has it.
TASU_IFACE = 'com.harman.infotainment.systemfunctions.tasu.tasu.InternalTasu'
TASU_PROXY = {'instances': 'SystemFunctionsInternalTasuInst1', 'broadcasts': ['sendTasuResponse'], 'methods': ['sendTasuRequest']}
CATALOG = os.path.join(ROOT, 'reference', 'someip-interfaces.json')
APP_MODULES = ['src/main.js', 'src/logger.js', 'src/ecu.js', 'src/diag.js', 'src/transport.js']
# require names of the oap-sdk modules src/main.js uses (the head unit's linker must find each in a stock app)
SDK_MODULES = [
    'oap-sdk/src/rhmi/RhmiManager', 'oap-sdk/src/rhmi/RhmiResourcesLocator',
    'oap-sdk/src/rhmi/MultiLanguageStringBundleFactory', 'oap-sdk/src/rhmi/RhmiApplicationFactory',
    'oap-sdk/src/rhmi/ResourceManager', 'oap-sdk/src/core/CurrentVehicleUnits',
    'oap-sdk/src/someip/someipBuilders', 'oap-sdk/src/core/OnlineAppBackground',
]
DOMAIN_ACL = [   # (domain, interface) pairs with a masterAccessTable entry
    ('hmi.entrypoint2', 'de/bmw/infotainment/hmi/entrypoint2/EntryPointManager2'),
    ('hmi.resourcemanagement2', 'de/bmw/infotainment/hmi/resourcemanagement2/ResourceManager2'),
    ('hmi.resourcemanagement2', 'de/bmw/infotainment/hmi/resourcemanagement2/StringBundle2'),
    ('hmi.usecasemanagement', 'de/bmw/infotainment/hmi/usecasemanagement/UseCaseContext'),
    ('telematic.oapcontroller', 'de/bmw/infotainment/telematic/oapcontroller/OAPController'),
]


def u(what):
    return str(uuid.uuid5(NS, 'bclog8:' + what))


def wjson(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=None if compact else 2, ensure_ascii=False)
        f.write('\n')


def signal_proxies(signals):
    out = {}
    for iface, e in signals.items():
        inst = e.get('_instances', '1')
        ent = {'instances': inst}
        attrs = {k: v for k, v in e.items() if not k.startswith('_')}
        if attrs:
            ent['attributes'] = attrs
        if e.get('_broadcasts'):
            ent['broadcasts'] = list(e['_broadcasts'])
        out[iface] = ent        # read-only: no methods, no setters
    return out


def permissions():
    acl = []
    for domain, iface in DOMAIN_ACL:
        acl.append({
            '_typeName': 'joynr.infrastructure.DacTypes.MasterAccessControlEntry', 'uid': '${APPNAME}',
            'domain': domain, 'interfaceName': iface,
            'defaultRequiredTrustLevel': 'HIGH', 'possibleRequiredTrustLevels': [],
            'defaultRequiredControlEntryChangeTrustLevel': 'HIGH', 'possibleRequiredControlEntryChangeTrustLevels': [],
            'operation': '*', 'defaultConsumerPermission': 'YES', 'possibleConsumerPermissions': []})
    return {'masterAccessTable': acl, 'mediatorAccessTable': [], 'ownerAccessTable': [],
            'masterRegistrationTable': [], 'mediatorRegistrationTable': [], 'ownerRegistrationTable': [], 'domainRoleTable': []}


def rhmi(title):
    ids = {}
    nid = STATE_ID + 400
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<application>',
           '    <container id="1000" type="logical">',
           '        <layoutGroup id="1100">', '            <component id="1110" type="bookmark"/>', '        </layoutGroup>',
           '    </container>',
           '    <container id="%d" type="state" widget="LT_State_Wide_List">' % STATE_ID,
           '        <layoutGroup id="3200" name="Titlebar">',
           '            <component id="3220" type="title">', '                <property id="2" value="1"/>', '            </component>',
           '        </layoutGroup>',
           '        <layoutGroup id="3400" name="List">', '            <property id="3" value="1"/>']
    comps = {'3220': {'type': 'title', 'name': 'MAIN_TITLE', 'rows': [{'Text': {'value': 'SID_0', 'type': 'StringId'}}]}}
    cell = lambda v, t='Text': {'value': v, 'type': t}
    for name, kind, text in SCREEN:
        nid += 1
        ids[name] = nid
        if kind == 'label':
            xml += ['            <component id="%d" type="label" widget="LT_ScrollLabel">' % nid,
                    '                <property id="2" value="1"/>', '                <property id="3" value="0"/>', '            </component>']
            comps[str(nid)] = {'type': 'label', 'name': name,
                               'properties': {'Visible': cell(1, 'UInt'), 'Focusable': cell(0, 'UInt')},
                               'rows': [{'Text': cell(text)}]}
        else:
            xml += ['            <component id="%d" type="button" widget="LT_Button_MediaEntry_1Row">' % nid,
                    '                <property id="2" value="1"/>', '                <property id="3" value="3"/>', '            </component>']
            comps[str(nid)] = {'type': 'button', 'name': name,
                               'properties': {'Visible': cell(1, 'UInt'), 'Focusable': cell(3, 'UInt')},
                               'rows': [{'PrimaryText': cell(text), 'FrequencyText': cell(''),
                                         'FrequencyVisible': cell(False, 'Bool'), 'IconVisible': cell(False, 'Bool'),
                                         'ProgressVisible': cell(False, 'Bool'), 'IsPlaying': cell(False, 'Bool')}]}
    xml += ['        </layoutGroup>', '    </container>', '</application>', '']
    ui = {'schemaVersion': '1.3.0', 'containers': {
        '1000': {'type': 'logical', 'name': 'logicalContainer', 'components': {'1110': {'type': 'bookmark', 'name': 'bookmark'}}},
        str(STATE_ID): {'type': 'state', 'name': 'MAIN', 'components': comps}}}
    return '\n'.join(xml), ui, ids


def main():
    app = json.load(open(os.path.join(APPDIR, 'app.json'), encoding='utf-8'))
    ver, title = app['version'], app['title']
    tree = os.path.join(APPDIR, 'bclog8@' + ver)
    # drop an older tree so only the current one is in the repository
    for d in os.listdir(APPDIR):
        if d.startswith('bclog8@') and d != 'bclog8@' + ver and os.path.isdir(os.path.join(APPDIR, d)):
            import shutil
            shutil.rmtree(os.path.join(APPDIR, d))

    proxies = dict(PLATFORM_PROXIES, **signal_proxies(app['signals']))
    cfg = json.loads(json.dumps(app['config']))
    ecu = cfg.setdefault('ecu', {})
    if ecu.get('enabled', True):
        if os.path.exists(CATALOG):
            try:
                have = TASU_IFACE in (json.load(open(CATALOG, encoding='utf-8')).get('interfaces') or {})
            except Exception:
                have = None
            if have is False:
                print('WARNING: this unit\'s SOME/IP catalog has no InternalTasu - ECU reading is switched OFF in this build')
                ecu['enabled'] = False
        else:
            print('note: reference/someip-interfaces.json not found - TASU is added without checking the unit (step 8 fetches the catalog)')
    if ecu.get('enabled', True):
        proxies[TASU_IFACE] = TASU_PROXY
    manifest = {'proxies': proxies,
                'services': {'de.bmw.infotainment.telematic.oap.AppStatus': {'instances': ['${userId}']}}}
    wjson(os.path.join(tree, 'manifest.json'), manifest)
    wjson(os.path.join(tree, 'permissions.json'), permissions())
    wjson(os.path.join(tree, 'config', 'base.json'), {})
    wjson(os.path.join(tree, 'config', 'watch.json'), cfg)
    wjson(os.path.join(tree, 'npm-shrinkwrap.json'), {'name': 'bclog8', 'version': ver, 'lockfileVersion': 1})
    wjson(os.path.join(tree, 'package.json'), {
        'name': 'bclog8', 'version': ver, 'description': app['description'], 'private': False,
        'main': 'javascript/runner.js',
        'oapAppSettings': {
            'maxDiskUsage': app.get('maxDiskUsage', 104857600), 'cpuLimit': 20, 'memoryThresholdKB': 102400,
            'priority': 'PRIO_LOW', 'visibility': 'BACKGROUND', 'latencyClass': 'LOW_PRIO_BACKGROUND',
            'loggingId': app['loggingId'], 'userLoginRequired': False, 'someipManifest': 'manifest.json'},
        'files': ['config', 'rhmiData', 'package.json', 'npm-shrinkwrap.json', 'permissions.json', 'javascript', 'manifest.json'],
        'dependencies': {'oap-sdk': '50.23.1', 'oap-runner': '1.4.1'},
        'engines': {'node': '>=8.7.0'},
        'productName': title, 'author': 'owner', 'license': 'UNLICENSED',
        'mguHmi': ['ID8'],
        'store': {'description': app['description']},
    })
    xml, ui, ids = rhmi(title)
    rd = os.path.join(tree, 'rhmiData')
    os.makedirs(os.path.join(rd, 'uiDescriptions', 'ID8'), exist_ok=True)
    with open(os.path.join(rd, 'uiDescriptions', 'ID8', 'application.xml'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(xml)
    wjson(os.path.join(rd, 'uiDescriptions', 'ID8', 'uiDescription.json'), ui, compact=True)
    strings = {'SID_0': title, 'SID_1': title}
    for sub in (('strings',), ('variantGroups', 'Common', 'texts')):
        for lang in ('en_GB', 'en_US'):
            wjson(os.path.join(rd, *sub, lang + '.json'), strings)
    wjson(os.path.join(rd, 'application.json'), {
        'schemaVersion': '1.0.0',
        'application': {
            'uuid': u('app'), 'type': 'OnlineApp', 'name': 'bclog8', 'vendor': 'custom', 'version': ver,
            'defaultLanguage': 'en_GB',
            'uiDescriptions': [{'applicationFilePath': './uiDescriptions/ID8/application.xml',
                                'uiDescriptionFilePath': './uiDescriptions/ID8/uiDescription.json', 'hmi': ['ID8']}],
            'stringBundleUuid': u('strings'), 'resourceBundleUuid': u('resources'),
            'variantGroups': [{'stringsPath': './variantGroups/Common/texts', 'hmi': ['ID8'], 'brands': ['BMW']}],
            'entryPoints': [{
                'uuid': u('entry'), 'textId': 'SID_1', 'insertionStateId': STATE_ID, 'insertionFocusId': 0,
                'appearance': 'DEFAULT', 'insertionMode': 0, 'insertionOrder': 0, 'insertionPriority': 0,
                'insertionGroup': 0, 'isGroupHeader': False, 'availability': 'ENABLED',
                'icon': 'gid://GID_ICON_CCMSCREWWRENCH_V01'}],
            'appletId': 20000 + zlib.crc32(b'bclog8') % 6900,
            'nativeGids': ['GID_ICON_CCMSCREWWRENCH_V01'],
        }})
    wjson(os.path.join(APPDIR, 'mgu-app.json'), {
        'schema': 1, 'id': 'bclog8', 'version': ver, 'tree': 'bclog8@' + ver,
        'modules': APP_MODULES, 'sdk': SDK_MODULES})
    wjson(os.path.join(ROOT, 'mgu-repo.json'), {'schema': 1, 'components': [
        {'id': 'bclog8', 'kind': 'app', 'path': 'apps/bclog8', 'build': 'unit', 'title': title,
         'description': app['description'], 'mguHmi': ['ID8'], 'uninstallable': True}]})
    print('bclog8 %s: tree %s, %d interfaces, %d screen components' % (ver, os.path.relpath(tree, ROOT), len(app['signals']), len(ids)))


if __name__ == '__main__':
    main()
