# -*- coding: utf-8 -*-
"""id8_wizard.py - pagina "iDrive 8 - Ghid" a toolkit-ului.

Un ghid pas cu pas pentru iDrive 8 (MGU ID8): laptopul rulează in locul tau toate comenzile, cu
avertismente și cu ce trebuie să faci tu in mașina la fiecare pas:

  1 laptopul (Python, OpenSSH, Node.js, Administrator)      6 alege semnalele de ascultat
  2 unealta de root idrive7-8-ssh-root                       7 App Store (mgu-store), o singura data
  3 conecteaza mașina, citește starea (status)               8 instalează loggerul bclog8
  4 activeaza SSH (flash + enable)                           9 adu logurile pe laptop și analizeaza
  5 colecteaza date de la unitate (probe + catalog SOME/IP)

Fisierul nu face nimic singur: idrive7_toolkit_v0.2.py ii da App-ul și globalele lui prin install().
Tot ce se rulează apare in Protocol (panoul de jos), comanda cu comanda.
"""
import ctypes
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
import hashlib
import io
import tarfile
import threading
import time

VIN_RE = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b')
ARCHS = ('id8-bdc3', 'id8-bcp')
ARCH_HELP = ('id8-bdc3 și id8-bcp sunt două imagini de flash diferite. Unealta de root NU verifică dacă imaginea aleasă se '
             'potrivește unității și nicio arhivă nu spune cum le deosebești. „Citește starea” nu depinde de această alegere. '
             'Nu apăsa Flash până nu ai varianta confirmată de autorii uneltei (le poți da versiunea hardware HWEL din Protocol).')
DEPLOY_SH = '/var/opt/mgu-deploy/mgu-deploy.sh'
OAP_DATA = '/var/opt/public/oap'


def install(App, g):
    """Adauga paginea și joburile ei clasei App. g = globals() din fișierul principal."""
    ctk, tk = g['ctk'], g['tk']
    C, Abort, Dialog = g['C'], g['Abort'], g['Dialog']
    HERE, find_python, find_ssh = g['HERE'], g['find_python'], g['find_ssh']
    parse_connect, fmt_size, filedialog, webbrowser = g['parse_connect'], g['fmt_size'], g['filedialog'], g['webbrowser']
    SSH_TOOL_URL, STORE_URL, IS_WIN = g['SSH_TOOL_URL'], g['STORE_URL'], g['IS_WIN']

    ID8_DIR = os.path.join(HERE, 'id8')
    TOOL_DIR = os.path.join(ID8_DIR, 'ssh-root')
    LOG_DIR = os.path.join(ID8_DIR, 'loguri')
    PROBE_DIR = os.path.join(ID8_DIR, 'probe')
    BUILD_DIR = os.path.join(ID8_DIR, 'build')
    TOOLS_DIR = os.path.join(ID8_DIR, 'tools')
    PACK_DIR = os.path.join(HERE, 'bclog8')
    LOGGER_DIR = os.path.join(HERE, 'overboost')
    SDK_DIR = os.path.join(ID8_DIR, 'sdk')
    KIT_REMOTE = '/var/opt/id8-logger-kit'
    BACKUP_DIR = os.path.join(ID8_DIR, 'backup')

    # ------------------------------------------------------------------ ajutoare
    def pack_ok():
        return os.path.isfile(os.path.join(LOGGER_DIR, 'app', 'app.json'))

    def need_pack():
        if not pack_ok():
            raise Abort('Lipsește folderul "overboost" de langa toolkit (%s). Dezarhivează arhiva loggerului langa '
                        'fișierul .py al toolkit-ului.' % LOGGER_DIR)

    def sdk_ok():
        return os.path.isfile(os.path.join(SDK_DIR, 'index.js')) and os.path.isfile(os.path.join(SDK_DIR, 'package.json'))

    def built_ok():
        return os.path.isfile(os.path.join(LOGGER_DIR, 'dist', 'id8-bench-test.tar.gz'))

    def tool_dir():
        """Folderul care contine ssh_enable.py (in id8/ssh-root, cel mult doua niveluri)."""
        for root, dirs, files in os.walk(TOOL_DIR):
            depth = root[len(TOOL_DIR):].count(os.sep)
            if 'ssh_enable.py' in files:
                return root
            if depth >= 2:
                dirs[:] = []
        return None

    def py_or_abort(c):
        if not c.get('python'):
            raise Abort('Python nu a fost găsit. Instalează Python 3 de pe python.org (bifeaza "Add python.exe to PATH") '
                        'și porneste toolkit-ul din nou.')
        return c['python']

    def node_or_abort():
        node = shutil.which('node')
        if not node:
            raise Abort('Node.js lipsește (nodejs.org, versiunea LTS). Îl folosim ca să împachetăm loggerul.')
        return node

    def is_admin():
        if not IS_WIN:
            return True
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False

    def app_version():
        try:
            return json.load(open(os.path.join(LOGGER_DIR, 'app', 'app.json'), encoding='utf-8'))['version']
        except (OSError, ValueError, KeyError):
            return '0.1.0'

    def download(self, url, dest, minimum=500):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + '.part'
        self.log('Descarc %s ...' % url)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'idrive-toolkit'})
            with urllib.request.urlopen(req, timeout=90) as r, open(tmp, 'wb') as fh:
                shutil.copyfileobj(r, fh)
            if os.path.getsize(tmp) < minimum:
                raise ValueError('fișier prea mic')
            os.replace(tmp, dest)
        except Exception as e:  # noqa: BLE001
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise Abort('Nu am putut descarcă %s: %s (ai internet pe laptop?)' % (url, e))
        return dest

    def extract_zip(self, zpath, dest):
        """Dezarhivează fara folderul de sus; suprascrie fisierele, dar nu șterge 'data' (cheile deja generate)."""
        n = 0
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(zpath) as z:
            names = [i.filename for i in z.infolist() if not i.is_dir()]
            top = names[0].split('/')[0] if names and all(x.split('/')[0] == names[0].split('/')[0] for x in names) else ''
            for info in z.infolist():
                if info.is_dir():
                    continue
                parts = [p for p in info.filename.split('/') if p not in ('', '.')]
                if top and parts and parts[0] == top:
                    parts = parts[1:]
                if not parts or '..' in parts or ':' in info.filename:
                    continue
                target = os.path.join(dest, *parts)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, 'wb') as out:
                    shutil.copyfileobj(src, out)
                n += 1
        return n

    # ------------------------------------------------------------------ pagina
    def build_page_id8(self, f):
        pad = dict(sticky='ew', padx=12, pady=(0, 14))
        self.v_id8_arch = tk.StringVar(value=ARCHS[0])
        self.v_id8_vin = tk.StringVar()
        self.v_id8_backup = tk.BooleanVar(value=False)
        self.v_id8_persist = tk.BooleanVar(value=True)
        self.id8_chips = {}
        self.id8_ok = {}
        r = 0

        # 1 laptopul
        c = self.card(f, 'Pregătește laptopul', badge='1',
                      subtitle='Ai nevoie de Python 3, de clientul OpenSSH, de Node.js și de un cablu ENET. Pe Windows pornește '
                               'toolkit-ul ca Administrator (clic dreapta › Run as administrator), altfel adresa 169.254.x.x '
                               'a cablului ENET nu funcționează. Ce apare în roșu poți instala chiar de aici: apasă pe el.')
        c.grid(row=r, column=0, **pad)
        r += 1
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='w')
        for k, name in (('py', 'Python'), ('ssh', 'OpenSSH'), ('node', 'Node.js'), ('adm', 'Administrator'),
                        ('pack', 'Pachet OVERBOOST')):
            ch = self.chip(row, name)
            ch.pack(side='left', padx=(0, 6))
            ch.bind('<Button-1>', lambda e, k=k: self.id8_fix(k))
            self.id8_chips[k] = (ch, name)
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=1, column=0, sticky='w', pady=(12, 0))
        self.button(row, 'Verifică laptopul', lambda: self.start('Verific laptopul', self.job_id8_pc)).pack(side='left')
        self.button(row, 'Instalează ce lipsește', lambda: self.id8_fix(None), 'primary').pack(side='left', padx=8)

        # 2 unealta de root
        c = self.card(f, 'Unealta de root (idrive7-8-ssh-root)', badge='2',
                      subtitle='Unealta lui cauliflowermossgrape scrie în unitate și deschide SSH ca root. O descarc în '
                               'folderul id8/ssh-root de lângă toolkit sau o iau din arhiva .zip pe care o ai deja.')
        c.grid(row=r, column=0, **pad)
        r += 1
        self.lbl_id8_tool = self.label(c.body, '', 'small', wrap=True)
        self.lbl_id8_tool.grid(row=0, column=0, sticky='ew')
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=1, column=0, sticky='ew', pady=(12, 0))
        self.button(row, 'Descarcă de pe GitHub', lambda: self.start('Descarc unealta de root', lambda c: self.job_id8_tool(c, None))
                    ).pack(side='left')
        self.button(row, 'Alege arhiva .zip…', self.id8_pick_zip).pack(side='left', padx=8)
        self.button(row, 'GitHub ↗', lambda: webbrowser.open(SSH_TOOL_URL), 'ghost', action=False, width=90).pack(side='right')

        # 3 conecteaza si citeste
        c = self.card(f, 'Conectează mașina și citește starea', badge='3',
                      subtitle='Citirea nu schimbă nimic în mașină. Înainte să apeși butonul:')
        c.grid(row=r, column=0, **pad)
        r += 1
        b = c.body
        self.label(b, '•  Mașina parcată, contact pus (motorul poate fi oprit), de preferat încărcător conectat.\n'
                      '•  Cablu ENET între portul OBD/ENET al mașinii și laptop (adaptor USB-Ethernet, dacă laptopul nu are port).\n'
                      '•  VPN-ul și alte programe de diagnoză (ISTA, E-Sys, BimmerCode) închise.\n'
                      '•  După ce pui contactul, așteaptă vreo 30 de secunde ca gateway-ul mașinii să pornească.',
                   'small', wrap=True).grid(row=0, column=0, sticky='ew')
        row = ctk.CTkFrame(b, fg_color='transparent')
        row.grid(row=1, column=0, sticky='ew', pady=(14, 0))
        row.columnconfigure(1, weight=1)
        self.label(row, 'VIN', 'bold').grid(row=0, column=0, sticky='w', padx=(0, 14))
        vin_e = self.entry(row, self.v_id8_vin, width=210, mono=True)
        vin_e.grid(row=0, column=1, sticky='w')
        self.v_id8_vin.trace_add('write', lambda *a: self.v_id8_vin.set(self.v_id8_vin.get().upper()[:20]) if len(self.v_id8_vin.get()) > 20 or self.v_id8_vin.get() != self.v_id8_vin.get().upper() else None)
        self.label(row, 'Tip unitate', 'bold').grid(row=1, column=0, sticky='w', padx=(0, 14), pady=(8, 0))
        ctk.CTkOptionMenu(row, values=list(ARCHS), variable=self.v_id8_arch, height=32, fg_color=C.INPUT,
                          button_color=C.BTN, button_hover_color=C.BTN_HOVER, text_color=C.TEXT,
                          dropdown_fg_color=C.CARD, dropdown_text_color=C.TEXT, width=180
                          ).grid(row=1, column=1, sticky='w', pady=(8, 0))
        self.label(b, ARCH_HELP, 'small', wrap=True).grid(row=2, column=0, sticky='ew', pady=(8, 0))
        self.lbl_id8_status = self.label(b, '', 'small', wrap=True)
        self.lbl_id8_status.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        self.button(b, 'Citește starea (status)', self.id8_status, 'primary'
                    ).grid(row=4, column=0, sticky='w', pady=(12, 0))

        # 4 activeaza SSH
        c = self.card(f, 'Activează SSH (root)', badge='4', tone='danger',
                      subtitle='„Flash + Enable” reprogramează unitatea și îi poate șterge codarea. Se face o singură dată pe '
                               'mașină. După el unitatea repornește singură (1–2 minute, ecranul se stinge).')
        c.grid(row=r, column=0, **pad)
        r += 1
        b = c.body
        self.switch(b, self.v_id8_backup, 'Am salvat codarea unității (BimmerCode / ISTA / E-Sys)',
                    'Obligatoriu înainte de Flash. Dacă după flash setările arată resetate, o restaurezi cu același program.'
                    ).grid(row=0, column=0, sticky='ew')
        self.switch(b, self.v_id8_persist, 'Ține SSH deschis permanent, cu debug oprit',
                    'Recomandat: fără el SSH se închide la oprirea modului debug și rămâne un cod de eroare (B7F8E5).'
                    ).grid(row=1, column=0, sticky='ew', pady=(12, 0))
        self.lbl_id8_ssh = self.label(b, '', 'small', wrap=True)
        self.lbl_id8_ssh.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        row = ctk.CTkFrame(b, fg_color='transparent')
        row.grid(row=3, column=0, sticky='ew', pady=(12, 0))
        self.button(row, 'Flash + Enable (prima dată)', self.id8_flash, 'danger').pack(side='left')
        self.button(row, 'Enable (deja flashuit)', self.id8_enable).pack(side='left', padx=8)
        self.button(row, 'Închide SSH', self.id8_close, 'ghost').pack(side='left')
        self.button(row, 'Găsește cheia generată', lambda: self.start('Caut cheia SSH', self.job_id8_findkey),
                    'ghost').pack(side='right')

        # backup
        c = self.card(f, 'Backup al unității (după ce SSH merge)', badge='B',
                      subtitle='Salvează pe laptop, într-o arhivă cu data și ora, configurația unității (/etc) și aplicațiile instalate '
                               '(/var/opt), înainte să modifici ceva. Nu e o imagine completă a unității și nu include codarea '
                               'mașinii: codarea se salvează separat, cu BimmerCode, ÎNAINTE de Flash (pasul 4).')
        c.grid(row=r, column=0, **pad)
        r += 1
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.button(row, 'Fă backup acum', lambda: self.start('Backup unitate', self.job_id8_backup, 'conn'), 'primary').pack(side='left')
        self.button(row, 'Deschide id8/backup', lambda: self.open_dir(BACKUP_DIR, True), 'ghost', action=False).pack(side='left', padx=8)

        # 5 colecteaza date
        c = self.card(f, 'Colectează date de la unitate', badge='5',
                      subtitle='Doar citire: rulează id8-probe.sh pe unitate (platforma, aplicațiile, interfețele SOME/IP) și '
                               'copiază catalogul de semnale pe laptop. Rezultatul se salvează în id8/probe; trimite-mi '
                               'fișierul id8-probe-….txt.')
        c.grid(row=r, column=0, **pad)
        r += 1
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.button(row, 'Rulează proba + aduce catalogul', lambda: self.start('Colectez date', self.job_id8_probe, 'conn')
                    ).pack(side='left')
        self.button(row, 'Deschide id8/probe', lambda: self.open_dir(PROBE_DIR, True), 'ghost', action=False).pack(side='left', padx=8)

        # 6 semnale (doar pentru pachetul vechi bclog8/mgu-store; loggerul de la pasul 8 are semnalele fixate)
        c = self.card(f, 'Alege semnalele de ascultat (opțional, vechiul pachet bclog8)', badge='6',
                      subtitle='Nu mai e necesar pentru loggerul de la pasul 8 (id8-logger), care are deja o listă fixă de '
                               'semnale. Rămâne util doar dacă vrei să încerci în continuare și vechiul pachet bclog8 prin '
                               'mgu-store. „Previzualizează” nu schimbă nimic; „Scrie în pachet” actualizează lista și '
                               'regenerează pachetul bclog8.')
        c.grid(row=r, column=0, **pad)
        r += 1
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.button(row, 'Previzualizează', lambda: self.start('Previzualizez semnalele', lambda c: self.job_id8_signals(c, False), 'conn')
                    ).pack(side='left')
        self.button(row, 'Scrie în pachet', lambda: self.start('Scriu semnalele în pachet', lambda c: self.job_id8_signals(c, True), 'conn')
                    ).pack(side='left', padx=8)

        # 7 instalatorul (opțional - loggerul de la pasul 8 nu mai trece prin mgu-deploy)
        c = self.card(f, 'Instalatorul de aplicații (mgu-deploy) - opțional', badge='7',
                      subtitle='NU mai e necesar pentru loggerul de la pasul 8: acela se instalează direct, fără mgu-deploy '
                               'și fără mgu-store (pe unitatea ta, mgu-deploy nu a găsit modulele SDK de care avea nevoie '
                               'vechiul pachet bclog8). Lasă pasul ăsta neatins dacă nu ai alt motiv să-l folosești.')
        c.grid(row=r, column=0, **pad)
        r += 1
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.button(row, 'Instalează mgu-deploy', self.id8_deploy_install, 'primary').pack(side='left')
        self.button(row, 'Repornește unitatea', self.id8_reboot).pack(side='left', padx=8)
        self.button(row, 'Verifică dacă e instalat', lambda: self.start('Verific mgu-deploy', self.job_id8_deploy_check, 'conn'),
                    'ghost').pack(side='right')

        # 8 logger (id8-logger: SDK extras direct de pe unitate, fără mgu-store)
        c = self.card(f, 'Instalează aplicația OVERBOOST', badge='8',
                      subtitle='Fără mgu-store: SDK-ul se extrage dintr-o aplicație care rulează deja pe unitate (nimic nu '
                               'rulează din ea, se citește doar tabela ei de module), pachetul se construiește pe laptop, '
                               'apoi se verifică și se instalează direct pe unitate cu propriul script (mgu.sh), cu backup '
                               'automat înainte de orice schimbare. OVERBOOST înlocuiește pe loc vechiul „BC / ECU Logger” '
                               '(același 8d). Citirea (M Vitals, Fault Scanner) nu modifică nimic; Engine Config trimite comenzi '
                               'doar după ce îl armezi în aplicație.')
        c.grid(row=r, column=0, **pad)
        r += 1
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.button(row, '8a. Extrage SDK de pe unitate', lambda: self.start('Extrag SDK-ul', self.job_id8_extract_sdk, 'conn')
                    ).pack(side='left')
        self.button(row, '8b. Construiește pachetul', lambda: self.start('Construiesc pachetul', self.job_id8_build)
                    ).pack(side='left', padx=8)
        row2 = ctk.CTkFrame(c.body, fg_color='transparent')
        row2.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        self.button(row2, '8c. Verifică (check)', lambda: self.start('Verific pe unitate', self.job_id8_logger_check, 'conn')
                    ).pack(side='left')
        self.button(row2, '8d. Instalează', self.id8_logger_install, 'primary').pack(side='left', padx=8)
        self.button(row2, 'Repornește unitatea', self.id8_reboot).pack(side='left', padx=8)
        self.button(row2, '8e. Repară după repornire', self.id8_logger_persist, 'ghost').pack(side='left', padx=8)
        self.button(row2, 'Dezinstalează', self.id8_logger_remove, 'ghost').pack(side='right')
        row3 = ctk.CTkFrame(c.body, fg_color='transparent')
        row3.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        self.button(row3, '8f. Testează internetul unității', lambda: self.start('Testez internetul unității', self.job_id8_net_check, 'conn'), 'ghost').pack(side='left')

        # 10 motorul GPU (mgu-render), doar mod "strat"
        c = self.card(f, 'Design: motorul GPU (mgu-render) - experimental', badge='10',
                      subtitle='Pentru designul cu cadrane și bare (ca pe iDrive 7). Se instalează DOAR motorul GPU, în modul „strat” '
                               'peste ecran: fără Store, fără aplicațiile lui, fără modificări la pornirea HMI-ului. Motorul vine gata compilat (release oficial '
                               'mgu-store 0.6.8, Docker nu e necesar) și are nevoie de pasul 7 (mgu-deploy). Testat de autori '
                               'doar în emulator pe iDrive 8: după instalare rulează testul 10d și trimite-mi rezultatul.')
        c.grid(row=r, column=0, **pad)
        r += 1
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.button(row, '10a. Verifică motorul (gata compilat)', lambda: self.start('Verific motorul GPU', self.job_id8_render_build)
                    ).pack(side='left')
        self.button(row, '10b. Instalează mgu-deploy 0.7', self.id8_deploy070_install).pack(side='left', padx=8)
        row3 = ctk.CTkFrame(c.body, fg_color='transparent')
        row3.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        self.button(row3, '10c. Instalează motorul', self.id8_render_install, 'primary').pack(side='left')
        self.button(row3, '10d. Testează motorul', lambda: self.start('Testez motorul GPU', self.job_id8_render_check, 'conn')
                    ).pack(side='left', padx=8)
        self.button(row3, 'Dezinstalează motorul', self.id8_render_remove, 'ghost').pack(side='right')

        # 9 loguri
        c = self.card(f, 'După înregistrare: adu logurile și analizează', badge='9',
                      subtitle='În mașină (parcată, motorul poate fi oprit, contact pus) deschide „OVERBOOST”, apasă „ECU reading: start” și '
                               'lasă 30 s fără să atingi nimic. Apoi pe volan: ține BC ≥5 s (meniul 1, 1 clipire), BC scurt (meniul 2, '
                               '2 clipiri), BC scurt (meniul 3, 3 clipiri); în fiecare meniu schimbă pozițiile (hărți 1–4, valve 0/1/2). '
                               'Aplicația detectează singură clipirile; rândurile MARK/NEEDLE sunt doar o confirmare a ta (apeși rândul '
                               'când vezi pozițiile). „ECU sweep” caută date suplimentare, doar cu mașina oprită. Apoi aici: '
                               'adu logurile și analizează-le.')
        c.grid(row=r, column=0, **pad)
        r += 1
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.button(row, 'Adu logurile', lambda: self.start('Aduc logurile', self.job_id8_logs, 'conn'), 'primary').pack(side='left')
        self.button(row, 'Analizează', lambda: self.start('Analizez logurile', self.job_id8_analyze)).pack(side='left', padx=8)
        self.button(row, 'Deschide id8/loguri', lambda: self.open_dir(LOG_DIR, True), 'ghost', action=False).pack(side='left')
        self.id8_refresh()
        self.root.after(700, lambda: threading.Thread(target=self.id8_scan, daemon=True).start())

    def id8_refresh(self):
        """Reafișează starea (unealta, cheia) pe pagină."""
        td = tool_dir()
        self.lbl_id8_tool.configure(
            text=('✔ Găsită: %s' % td) if td else 'Nu e descărcată încă. Folosește unul dintre butoane.',
            text_color=g['C'].GREEN_TEXT if td else g['C'].MUTED)
        key = self.v_key.get().strip().strip('"')
        have = bool(key) and os.path.isfile(key)
        self.lbl_id8_ssh.configure(
            text=('✔ Cheie SSH încărcată: %s  (IP unitate %s)' % (key, self.v_ip.get().strip() or '?')) if have else
            'Încă nu există o cheie SSH. Se generează la pasul 4 și se încarcă automat.',
            text_color=g['C'].GREEN_TEXT if have else g['C'].MUTED)

    def id8_chip(self, key, ok, text=None):
        ch, name = self.id8_chips[key]
        self.id8_ok[key] = bool(ok)
        self.set_chip(ch, text or name, 'ok' if ok else 'bad')
        ch.configure(cursor='arrow' if ok else 'hand2')

    # ------------------------------------------------------------------ pasul 1
    def id8_scan(self, log=False):
        """Verifică laptopul și colorează marcajele. log=True scrie rezultatul în Protocol."""
        res = {}
        py = find_python()
        res['py'] = bool(py)
        ssh = find_ssh()
        res['ssh'] = bool(ssh)
        node = shutil.which('node')
        res['node'] = bool(node)
        res['adm'] = is_admin()
        res['pack'] = pack_ok()
        names = {'py': 'Python', 'ssh': 'OpenSSH', 'node': 'Node.js', 'adm': 'Administrator', 'pack': 'Pachet OVERBOOST'}
        for k, ok in res.items():
            txt = names[k] + ('' if ok else (' – nu ești Administrator' if k == 'adm' else ' lipsește'))
            self.ui(self.id8_chip, k, ok, txt)
        if log:
            for k, ok in res.items():
                self.log('%s: %s' % (names[k], 'în regulă' if ok else 'LIPSEȘTE'), 'ok' if ok else 'warn')
            if py:
                self.run(py + ['--version'], label='python --version')
            if node:
                self.run([node, '--version'], label='node --version')
        return res

    def job_id8_pc(self, c):
        res = self.id8_scan(log=True)
        if all(res.values()):
            self.log('Laptopul e gata.', 'ok')
        else:
            self.log('Apasă pe marcajul roșu sau pe „Instalează ce lipsește”.', 'warn')

    def id8_fix(self, key=None):
        if self.busy:
            return
        if key is not None and self.id8_ok.get(key, False):
            return
        missing = [k for k in ('py', 'ssh', 'node', 'adm', 'pack') if not self.id8_ok.get(k, True)] if key is None else [key]
        if not missing:
            self.alert('Laptopul', 'Totul e în regulă. Dacă tocmai ai instalat ceva, apasă „Verifică laptopul”.', 'info')
            return
        what = {'py': 'Python 3 (winget)', 'ssh': 'clientul OpenSSH (funcție Windows, cere Administrator)',
                'node': 'Node.js LTS (winget)',
                'adm': 'repornesc toolkit-ul ca Administrator (Windows îți cere o confirmare)',
                'pack': None}
        lines = ['•  ' + what[k] for k in missing if what[k]]
        if 'pack' in missing:
            lines.append('•  Pachetul id8-logger nu se poate instala de aici: dezarhivează folderul „overboost” lângă toolkit.')
        if not IS_WIN:
            self.alert('Instalare', 'Instalarea automată merge doar pe Windows. Instalează manual:\n\n' + '\n'.join(lines), 'info')
            return
        todo = [k for k in missing if k != 'pack']
        if not todo:
            self.alert('Pachet OVERBOOST', lines[0][3:], 'warn')
            return
        if not self.ask('Instalez ce lipsește?', 'Voi face următoarele:\n\n' + '\n'.join(lines) +
                        '\n\nProgramele se descarcă de pe internet (winget). Durează câteva minute.', yes='Instalează'):
            return
        self.start('Instalez ce lipsește', lambda c: self.job_id8_fix(c, todo),
                   then=self.id8_relaunch_admin if 'adm' in todo else None)

    def refresh_path(self):
        if not IS_WIN:
            return
        try:
            import winreg
        except ImportError:
            return
        parts = []
        for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
                          (winreg.HKEY_CURRENT_USER, 'Environment')):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    parts.append(os.path.expandvars(winreg.QueryValueEx(k, 'Path')[0]))
            except OSError:
                pass
        if parts:
            os.environ['PATH'] = ';'.join(parts + [os.environ.get('PATH', '')])

    def job_id8_fix(self, c, todo):
        def winget(pkg, name):
            if not shutil.which('winget'):
                webbrowser.open('https://www.python.org/downloads/' if pkg.startswith('Python') else 'https://nodejs.org/')
                raise Abort('winget nu există pe acest Windows. Am deschis pagina de descărcare a programului %s; '
                            'instalează-l de acolo și apasă „Verifică laptopul”.' % name)
            rc, _ = self.run(['winget', 'install', '-e', '--id', pkg, '--silent', '--accept-package-agreements',
                              '--accept-source-agreements'], label='winget install %s' % pkg)
            if rc not in (0, 0x8A15002B - (1 << 32), -1978335189):   # 0 = ok, 0x8A15002B = deja instalat/actualizat
                raise Abort('Instalarea %s a eșuat (cod %d). Vezi Protocol.' % (name, rc))
        failed = []
        for k in todo:
            try:
                if k == 'py':
                    winget('Python.Python.3.12', 'Python')
                elif k == 'node':
                    winget('OpenJS.NodeJS.LTS', 'Node.js')
                elif k == 'ssh':
                    if not is_admin():
                        raise Abort('OpenSSH se instalează doar ca Administrator. Repornește toolkit-ul ca Administrator '
                                    '(marcajul „Administrator”) și apasă din nou.')
                    rc, _ = self.run(['powershell', '-NoProfile', '-Command',
                                      'Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0'],
                                     label='Add-WindowsCapability OpenSSH.Client')
                    if rc:
                        raise Abort('Instalarea OpenSSH a eșuat (cod %d). Încearcă din Setări › Aplicații › Funcții opționale.' % rc)
            except Abort as e:
                failed.append(str(e))
                self.log(str(e), 'err')
        self.refresh_path()
        res = self.id8_scan(log=False)
        if failed:
            raise Abort(failed[0])
        left = [k for k in todo if k != 'adm' and not res.get(k)]
        if left:
            self._notice = ('Aproape gata', 'S-a instalat, dar toolkit-ul nu vede încă programul. Închide și pornește din nou '
                            'toolkit-ul (ca Administrator), apoi apasă „Verifică laptopul”.', 'info')
        self.log('Instalare terminată.', 'ok')

    def id8_relaunch_admin(self):
        if is_admin():
            return
        try:
            exe = sys.executable
            frozen = getattr(sys, 'frozen', False)
            params = '' if frozen else '"%s"' % os.path.abspath(sys.argv[0])
            ok = ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe, params, HERE, 1) > 32
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            self.root.after(300, self.root.destroy)
        else:
            self.alert('Administrator', 'Nu am putut reporni ca Administrator. Închide toolkit-ul și pornește-l cu clic dreapta › '
                                        'Run as administrator.', 'warn')

    # ------------------------------------------------------------------ pasul 2
    def id8_pick_zip(self):
        p = filedialog.askopenfilename(title='Alege arhiva idrive7-8-ssh-root (.zip)', parent=self.root,
                                       filetypes=[('Arhiva zip', '*.zip'), ('Toate fisierele', '*.*')])
        if p:
            self.start('Dezarhivez unealta de root', lambda c, p=p: self.job_id8_tool(c, p))

    def job_id8_tool(self, c, zip_path):
        py = py_or_abort(c)
        if not zip_path:
            zip_path = download(self, SSH_TOOL_URL + '/archive/refs/heads/main.zip', os.path.join(ID8_DIR, 'ssh-root.zip'), 10000)
        n = extract_zip(self, zip_path, TOOL_DIR)
        td = tool_dir()
        if not td:
            raise Abort('Arhiva nu contine ssh_enable.py - e arhiva corecta (idrive7-8-ssh-root)?')
        self.log('%d fișiere dezarhivate in %s' % (n, td), 'ok')
        rc, _ = self.run(py + ['-m', 'pip', 'install', '--quiet', 'cryptography'], cwd=td, label='pip install cryptography')
        if rc and not IS_WIN:
            rc, _ = self.run(py + ['-m', 'pip', 'install', '--quiet', '--break-system-packages', 'cryptography'], cwd=td,
                             label='pip install --break-system-packages cryptography')
        if rc:
            raise Abort('Nu am putut instala "cryptography" (pip a eșuat, vezi Protocol). Ai internet pe laptop?')
        self.ui(self.id8_refresh)
        self.log('Unealta de root e gata.', 'ok')

    # ------------------------------------------------------------------ pasul 3
    def id8_tool_cmd(self, c, td, action, arch, extra=()):
        py = py_or_abort(c)
        return py + [os.path.join(td, 'ssh_enable.py'), action, '--arch', arch] + list(extra)

    def id8_status(self):
        if self.busy:
            return
        arch = self.v_id8_arch.get()
        self.start('Citesc starea unității', lambda c, a=arch: self.job_id8_status(c, a))

    def job_id8_status(self, c, arch):
        td = tool_dir()
        if not td:
            raise Abort('Întâi pasul 2: descarcă sau alege unealta de root.')
        rc, lines = self.run(self.id8_tool_cmd(c, td, 'status', arch), cwd=td)
        text = '\n'.join(lines)
        m = re.search(r'VIN:\s*([A-HJ-NPR-Z0-9]{17})\b', text)      # exact linia "| VIN: ..." a uneltei
        vin = m.group(1) if m else ''
        if vin:
            self.ui(self.v_id8_vin.set, vin)
        self.id8_state = {'ok': rc == 0, 'arch': arch, 'vin': vin or None}
        if rc:
            raise Abort('Nu am putut citi unitatea (exit %d). Verifică: cablul ENET, contactul pus, ca nu rulează alt program de '
                        'diagnoză, că toolkit-ul e pornit ca Administrator. Detalii în Protocol.' % rc)
        svk = [x.strip() for x in lines if re.search(r'SVK|software|version|P18|MGU', x, re.I)][:3]
        msg = ('✔ Unitatea răspunde. VIN citit din mașină: %s.%s' % (vin or 'nu a fost raportat', ('  ' + ' | '.join(svk)) if svk else ''))
        self.ui(lambda: self.lbl_id8_status.configure(text=msg, text_color=C.GREEN_TEXT))
        self.log(msg, 'ok')
        ipm = re.search(r'MGU IP:\s*(\d{1,3}(?:\.\d{1,3}){3})', text)       # caseta „Verbunden” din stânga arată și unitatea iDrive 8
        self.ui(lambda: self.set_conn('on', '%s\nVIN %s' % (ipm.group(1) if ipm else '-', vin or '-')))

    # ------------------------------------------------------------------ pasul 4
    def id8_vin_ok(self):
        vin = self.v_id8_vin.get().strip().upper()
        if not VIN_RE.fullmatch(vin):
            self.alert('VIN', 'VIN-ul trebuie să aibă 17 caractere. Apasă mai întâi "Citește starea" ca să-l citesc din mașină, '
                              'sau scrie-l (e scris pe parbriz / în talon). Trebuie să fie exact cel al mașinii: se scrie în unitate.',
                       'warn')
            return None
        return vin

    def id8_gate(self, vin, arch):
        """Condiții obligatorii înainte de Flash/Enable. -> mesaj de eroare sau None."""
        if IS_WIN and not is_admin():
            return 'Toolkit-ul trebuie pornit ca Administrator (clic dreapta › Run as administrator), altfel conexiunea ENET nu merge.'
        if not find_ssh():
            return 'Lipsește clientul OpenSSH. Apasă pe „OpenSSH” la pasul 1 ca să-l instalez.'
        if not find_python():
            return 'Lipsește Python (pasul 1).'
        st = getattr(self, 'id8_state', None)
        if not st or not st.get('ok') or st.get('arch') != arch:
            return ('Mai întâi apasă „Citește starea (status)” la pasul 3, cu același tip de unitate (%s). Așa mă asigur că unitatea '
                    'răspunde și că VIN-ul e al mașinii.' % arch)
        if st.get('vin') and st['vin'] != vin:
            return ('VIN-ul scris (%s) NU e cel citit din mașină (%s). VIN-ul se scrie în unitate la Flash, deci trebuie să fie '
                    'identic. Corectează-l sau apasă din nou „Citește starea”.' % (vin, st['vin']))
        return None

    def id8_flash(self):
        if self.busy:
            return
        if not tool_dir():
            self.alert('Flash', 'Întâi pasul 2: unealta de root.', 'warn')
            return
        if not self.v_id8_backup.get():
            self.alert('Flash', 'Bifează mai întâi ca ai salvat codarea unității (pasul 4). Flash-ul poate șterge codarea.', 'warn')
            return
        vin = self.id8_vin_ok()
        if not vin:
            return
        arch = self.v_id8_arch.get()
        err = self.id8_gate(vin, arch)
        if err:
            self.alert('Flash', err, 'warn')
            return
        tail = vin[-6:]
        res = Dialog(self, 'Flash + Enable pe %s?' % vin,
                     'Ce urmează:\n'
                     '  • unitatea (%s) este REPROGRAMATĂ cu un pachet de debug (~131 KB);\n'
                     '  • repornește singură (~1–2 min, ecranul se stinge): nu opri contactul și nu scoate cablul;\n'
                     '  • se generează cheia SSH și se deschide SSH.\n\n'
                     'Poate șterge codarea unității (ai salvat-o). Pașii ăștia nu au fost încercați de mine pe o mașină reală; tipul '
                     '„%s” trebuie să fie corect.\n\nCa să confirmi, scrie ultimele 6 caractere ale VIN-ului (%s):' % (arch, arch, tail),
                     [('Anulează', False, 'secondary'), ('Flash + Enable', True, 'danger')], 'danger', entry='ultimele 6 caractere').run()
        if not res or not res[0] or (res[1] or '').strip().upper() != tail:
            if res and res[0]:
                self.alert('Flash', 'Anulat. Nu s-a schimbat nimic (nu ai scris corect ultimele 6 caractere din VIN).', 'info')
            return
        persist = self.v_id8_persist.get()
        self.start('Flash + Enable', lambda c: self.job_id8_tool_action(c, 'flash', arch, vin, persist))

    def id8_enable(self):
        if self.busy:
            return
        if not tool_dir():
            self.alert('Enable', 'Întâi pasul 2: unealta de root.', 'warn')
            return
        vin = self.id8_vin_ok()
        if not vin:
            return
        arch = self.v_id8_arch.get()
        err = self.id8_gate(vin, arch)
        if err:
            self.alert('Enable', err, 'warn')
            return
        if not self.ask('Enable', 'Redeschide SSH pe o unitate deja flashuită (nu reprogramează nimic). Mașina parcată, contact pus, '
                                  'cablu ENET conectat.', yes='Enable'):
            return
        persist = self.v_id8_persist.get()
        self.start('Enable SSH', lambda c: self.job_id8_tool_action(c, 'enable', arch, vin, persist))

    def id8_close(self):
        if self.busy:
            return
        if not tool_dir():
            return
        arch = self.v_id8_arch.get()
        if self.ask('Inchide SSH', 'Opreste serviciul SSH al unității. Il poți redeschide cu "Enable".', yes='Inchide'):
            self.start('Inchid SSH', lambda c: self.job_id8_tool_action(c, 'close', arch, '', False))

    def job_id8_tool_action(self, c, action, arch, vin, persist):
        td = tool_dir()
        if not td:
            raise Abort('Unealta de root nu e descărcată (pasul 2).')
        extra = ['--yes']
        if vin:
            extra += ['--vin', vin]
        if persist and action in ('flash', 'enable'):
            extra.append('--persist')
        if action == 'flash':
            self.log('Flash in curs - nu opri contactul și nu scoate cablul. După flash unitatea repornește singura.', 'warn')
        rc, lines = self.run(self.id8_tool_cmd(c, td, action, arch, extra), cwd=td)
        if rc:
            raise Abort('%s a eșuat (exit %d). Vezi Protocolul. Cauze frecvente: ENET neconectat, nu ești Administrator, contactul '
                        'oprit, VIN gresit, tip unitate gresit.' % (action, rc))
        self.log('%s terminat.' % action, 'ok')
        if action in ('flash', 'enable'):
            self.job_id8_findkey(c)

    def job_id8_findkey(self, c):
        td = tool_dir()
        found = []
        if td:
            base = os.path.join(td, 'data', 'enrollments')
            if os.path.isdir(base):
                for d in os.listdir(base):
                    p = os.path.join(base, d, 'connect.txt')
                    if os.path.isfile(p):
                        found.append((os.path.getmtime(p), p))
        if not found:
            found = [(0, p) for p in g['find_connect_files']()]
        if not found:
            raise Abort('Nu am găsit cheia SSH (connect.txt). Apare după "Flash + Enable" sau "Enable".')
        found.sort(reverse=True)
        info = parse_connect(found[0][1])
        if not info.get('key'):
            raise Abort('Langa %s nu e id_rsa.' % found[0][1])
        self.ui(self.apply_connect, info)
        self.ui(self.id8_refresh)
        self.log('Cheia SSH încărcată din %s (IP %s).' % (found[0][1], info.get('ip') or '?'), 'ok')

    # ------------------------------------------------------------------ pasul 5
    def job_id8_probe(self, c):
        need_pack()
        os.makedirs(PROBE_DIR, exist_ok=True)
        script = open(os.path.join(PACK_DIR, 'tools', 'id8-probe.sh'), encoding='utf-8').read()
        rc, _ = self.remote(c, script, label='Unitate > id8-probe.sh (doar citire)')
        if rc:
            raise Abort('Proba a eșuat (exit %d). Vezi Protocol.' % rc)
        out = os.path.join(PROBE_DIR, 'id8-probe-%s.txt' % datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        rc, _ = self.ssh_run(c, ['cat /tmp/id8-probe.txt'], out_file=out, echo=False, label='Unitate > aduc /tmp/id8-probe.txt')
        if rc or not os.path.getsize(out):
            raise Abort('Nu am putut aduce rezultatul probei.')
        self.log('Salvat: %s (%s)' % (out, fmt_size(os.path.getsize(out))), 'ok')
        self.job_id8_catalog(c)

    def job_id8_catalog(self, c):
        need_pack()
        ref = os.path.join(PACK_DIR, 'reference')
        os.makedirs(ref, exist_ok=True)
        dest = os.path.join(ref, 'someip-interfaces.json')
        rc, _ = self.ssh_run(c, ['cat /etc/oap/someip/someip-interfaces.json'], out_file=dest, echo=False,
                             label='Unitate > aduc catalogul SOME/IP')
        if rc or os.path.getsize(dest) < 1000:
            raise Abort('Nu am putut aduce catalogul SOME/IP (/etc/oap/someip/someip-interfaces.json).')
        try:
            json.load(open(dest, encoding='utf-8'))
        except ValueError:
            raise Abort('Catalogul adus nu e JSON valid.')
        shutil.copy(dest, os.path.join(PROBE_DIR, 'someip-interfaces.json'))
        self.log('Catalog SOME/IP salvat: %s (%s)' % (dest, fmt_size(os.path.getsize(dest))), 'ok')

    # ------------------------------------------------------------------ pasul 6
    def job_id8_signals(self, c, write):
        need_pack()
        py = py_or_abort(c)
        if not os.path.isfile(os.path.join(PACK_DIR, 'reference', 'someip-interfaces.json')):
            self.job_id8_catalog(c)
        args = py + [os.path.join('tools', 'gen_bclog_signals.py'), '--write' if write else '--list']
        rc, _ = self.run(args, cwd=PACK_DIR)
        if rc:
            raise Abort('Generatorul de semnale a eșuat (exit %d). Vezi Protocol.' % rc)
        if write:
            rc, _ = self.run(py + [os.path.join('tools', 'gen_tree.py')], cwd=PACK_DIR)
            if rc:
                raise Abort('Regenerarea pachetului a eșuat (exit %d).' % rc)
            self.log('Lista de semnale e scrisă. Reinstalează loggerul (pasul 8) ca să o folosească.', 'ok')



    # ------------------------------------------------------------------ backup
    def job_id8_backup(self, c):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        rc, lines = self.remote(c, 'for d in /etc /var/opt; do du -sk "$d" 2>/dev/null; done; df -k /var/opt | tail -1', echo=False)
        kb = 0
        for ln in lines:
            m = re.match(r'^(\d+)\s+/(etc|var/opt)$', ln.strip())
            if m:
                kb += int(m.group(1))
                self.log('  /%s: %s' % (m.group(2), fmt_size(int(m.group(1)) * 1024)))
        if kb > 600 * 1024:
            raise Abort('Datele de salvat sunt prea mari (%s). Nu fac backup automat; spune-mi și îl restrâng.' % fmt_size(kb * 1024))
        out = os.path.join(BACKUP_DIR, 'unitate-%s.tar.gz' % datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        cmd = ('tar czf - --exclude="/var/opt/mgu-deploy-kit-*" --exclude="*/data/bclog-*.jsonl" -C / etc var/opt 2>/dev/null; true')
        rc, _ = self.ssh_run(c, [cmd], out_file=out, echo=False, label='Unitate > backup /etc și /var/opt')
        if rc == 255:
            raise Abort('Nu m-am putut conecta prin SSH (pasul 4).')
        size = os.path.getsize(out)
        try:
            import tarfile as _tf
            with _tf.open(out, 'r:gz') as t:
                n = sum(1 for _ in t)
        except Exception as e:  # noqa: BLE001
            raise Abort('Arhiva primită nu e validă (%s). Încearcă din nou.' % e)
        if size < 1024 or n < 5:
            raise Abort('Arhiva e goală sau incompletă. Încearcă din nou.')
        self.log('✔ Backup: %s (%s, %d fișiere)' % (out, fmt_size(size), n), 'ok')
        self._notice = ('Backup făcut', 'Arhiva e în id8/backup. Păstrează-o și pe un stick sau în cloud, dacă urmează să modifici '
                        'mai mult.', 'info')

    # ------------------------------------------------------------------ pasul 7 (fara Store)
    def id8_deploy_install(self):
        if self.busy or not self.precheck('Instalez mgu-deploy', 'conn'):
            return
        if not self.ask('Instalezi mgu-deploy?',
                        'Se copiază pe unitate doar unealta de instalare (nu Store, nu actualizatorul, nu alte aplicații). '
                        'Mașina parcata, contact pus, de preferat incarcator. După instalare repornește unitatea o data.',
                        yes='Instalează'):
            return
        self.start('Instalez mgu-deploy', self.job_id8_deploy_install, 'conn')

    def job_id8_deploy_check(self, c):
        rc, lines = self.remote(c, '[ -f %s ] && sh %s status 2>&1 || echo NO_DEPLOY' % (DEPLOY_SH, DEPLOY_SH))
        if 'NO_DEPLOY' in lines:
            raise Abort('mgu-deploy NU e instalat. Folosește "Instalează doar mgu-deploy".')
        self.log('mgu-deploy este instalat.', 'ok')

    def job_id8_deploy_install(self, c):
        kitname = getattr(self, '_deploy_kit', None) or 'deploy-kit'
        kit = os.path.join(HERE, kitname)
        cj = os.path.join(kit, 'component.json')
        if not os.path.isfile(cj):
            raise Abort('Lipsește folderul "%s" de langa toolkit (%s).' % (kitname, kit))
        c0 = json.load(open(cj, encoding='utf-8'))
        files, art_entries, entries = [], [], []
        for fl in c0['files']:
            data = open(os.path.join(kit, os.path.basename(fl['from'])), 'rb').read()
            files.append({'path': fl['path'], 'mode': fl['mode'], 'sha256': hashlib.sha256(data).hexdigest()})
            art_entries.append(('files/' + fl['path'], data, int(fl['mode'], 8)))
            entries.append((fl['path'], data, int(fl['mode'], 8)))
        manifest = {'id': c0['id'], 'kind': c0['kind'], 'version': c0['version'], 'title': c0.get('title', c0['id']),
                    'description': c0.get('description', ''), 'files': files, 'units': [], 'enable': [], 'recmgr': [],
                    'restart': []}
        mj = (json.dumps(manifest, indent=2) + '\n').encode('utf-8')
        art_entries.append(('component.json', mj, 0o644))
        ab = io.BytesIO()
        with tarfile.open(fileobj=ab, mode='w:gz') as t:
            for name, data, mode in sorted(art_entries):
                ti = tarfile.TarInfo(name)
                ti.size, ti.mode, ti.mtime = len(data), mode, 0
                t.addfile(ti, io.BytesIO(data))
        blob = ab.getvalue()
        art = {'name': '%s@%s.tgz' % (c0['id'], c0['version'])}
        self.log('mgu-deploy %s pregătit local din folderul deploy-kit (%s). Nu se descarcă nimic.' % (c0['version'], fmt_size(len(blob))), 'ok')
        entries.append(('component.json', mj, 0o644))
        entries.append(('artifacts/' + art['name'], blob, 0o644))
        run_sh = ('set -e\nS=$(cd "$(dirname "$0")" && pwd)\n'
                  'sh "$S/mgu-deploy.sh" install --artifact "$S/artifacts/%s" --skip-older --yes\n' % art['name'])
        entries.append(('run.sh', run_sh.encode('utf-8'), 0o755))
        buf = io.BytesIO()
        now = int(time.time())
        with tarfile.open(fileobj=buf, mode='w:gz') as t:
            for name, data, mode in entries:
                ti = tarfile.TarInfo(name)
                ti.size, ti.mode, ti.mtime = len(data), mode, now
                t.addfile(ti, io.BytesIO(data))
        stage = '/var/opt/mgu-deploy-kit-%d' % os.getpid()
        q = g['shq'](stage)
        cmd = ('rm -rf %s && umask 077 && mkdir -p %s && tar -xzf - -C %s && { sh %s/run.sh; st=$?; rm -rf %s; exit $st; }'
               % (q, q, q, q, q))
        rc, lines = self.ssh_run(c, [cmd], stdin_bytes=buf.getvalue(), label='Unitate > instalez mgu-deploy')
        if rc == 255:
            raise Abort('Nu m-am putut conecta prin SSH (pasul 4).')
        if rc:
            raise Abort('Instalarea mgu-deploy a eșuat (exit %d). Vezi Protocol.' % rc)
        self._notice = ('mgu-deploy instalat', 'Repornește unitatea o data (un ciclu normal de contact). După aceea poți trece la pasul 8.', 'info')
        self.log('mgu-deploy instalat. Fara Store.', 'ok')

    # ------------------------------------------------------------------ pasul 8
    def _logger_upload_kit(self, c):
        """Trimite dist/id8-bench-test.tar.gz pe unitate și îl dezarhivează în KIT_REMOTE (persistent, in /var/opt)."""
        if not built_ok():
            raise Abort('Construiește mai întâi pachetul (8b).')
        tar = os.path.join(LOGGER_DIR, 'dist', 'id8-bench-test.tar.gz')
        remote_tar = '/tmp/id8-logger-kit.tar.gz'
        rc, lines = self.ssh_run(c, ['cat > %s && sync && echo UPLOAD_OK' % remote_tar], stdin_file=tar,
                                 label='Unitate > copiez kitul in /tmp (%s)' % fmt_size(os.path.getsize(tar)), echo=False)
        if rc or 'UPLOAD_OK' not in lines:
            raise Abort('Nu am putut copia pachetul pe unitate (exit %d).' % rc)
        rc, lines = self.remote(c, 'mkdir -p %s\ntar xzf %s -C %s\nrm -f %s\necho EXTRACT_OK' % (KIT_REMOTE, remote_tar, KIT_REMOTE, remote_tar),
                                echo=False, label='Unitate > dezarhivez kitul')
        if rc or 'EXTRACT_OK' not in lines:
            raise Abort('Nu am putut dezarhiva kitul pe unitate (exit %d).' % rc)

    def job_id8_extract_sdk(self, c):
        rc, lines = self.remote(c, 'ls -1 %s/oap_*/source/bundle.js 2>/dev/null | head -1' % OAP_DATA, echo=False)
        bundle_remote = next((x.strip() for x in lines if x.strip().startswith('/')), None)
        if not bundle_remote:
            raise Abort('Nu am găsit niciun "bundle.js" pe unitate (nicio aplicație instalată?). Fă pasul 5 (probă) ca să vezi ce '
                        'aplicații sunt pe unitate.')
        os.makedirs(ID8_DIR, exist_ok=True)
        local_bundle = os.path.join(ID8_DIR, 'sdk-bundle.js')
        rc, _ = self.ssh_run(c, ['cat %s' % g['shq'](bundle_remote)], out_file=local_bundle, echo=False,
                             label='Unitate > aduc %s' % bundle_remote)
        if rc or not os.path.isfile(local_bundle):
            raise Abort('Nu am putut aduce bundle-ul de pe unitate (exit %d).' % rc)
        self.log('Bundle: %s (%s)' % (bundle_remote, fmt_size(os.path.getsize(local_bundle))), 'ok')
        node = node_or_abort()
        if sdk_ok():
            shutil.rmtree(SDK_DIR, ignore_errors=True)
        rc, _ = self.run([node, os.path.join(LOGGER_DIR, 'tools', 'extract-sdk.js'), local_bundle, SDK_DIR],
                         label='node: extract-sdk.js')
        if rc or not sdk_ok():
            raise Abort('Extragerea SDK-ului a eșuat (exit %d). Vezi Protocol.' % rc)
        self.log('SDK extras in %s. Treci la 8b (Construiește pachetul).' % SDK_DIR, 'ok')

    def job_id8_build(self, c):
        need_pack()
        py = py_or_abort(c)
        if not sdk_ok():
            raise Abort('Extrage mai întâi SDK-ul (8a).')
        rc, _ = self.run(py + [os.path.join('tools', 'build.py'), '--sdk', SDK_DIR], cwd=LOGGER_DIR, label='python tools/build.py')
        if rc or not built_ok():
            raise Abort('Construirea pachetului a eșuat (exit %d). Vezi Protocol.' % rc)
        out = os.path.join(LOGGER_DIR, 'dist', 'id8-bench-test.tar.gz')
        self.log('Pachet: %s (%s). Treci la 8c (Verifică).' % (out, fmt_size(os.path.getsize(out))), 'ok')

    def job_id8_logger_check(self, c):
        self._logger_upload_kit(c)
        rc, lines = self.ssh_run(c, ['cd %s && sh mgu.sh check' % KIT_REMOTE], label='Unitate > mgu.sh check')
        if rc:
            raise Abort('Verificarea (check) a eșuat - nu s-a schimbat nimic pe unitate. Citește mesajul din Protocol.')
        self.log('Verificare reușită. Poți apăsa „Instalează” (8d).', 'ok')

    def job_id8_net_check(self, c):
        # read only: what routes the unit has and whether GitHub answers from root's shell
        cmd = ('echo "--- interfete"; ls /sys/class/net; echo "--- adrese IPv4"; ip -4 addr 2>/dev/null | grep -E "^[0-9]+:|inet " ; '
               'echo "--- rute"; ip route 2>/dev/null; echo "--- DNS"; cat /etc/resolv.conf 2>/dev/null | head -5; '
               'echo "--- GitHub"; (curl -sS -m 10 -o /dev/null -w "curl HTTP %{http_code}\\n" https://raw.githubusercontent.com/mrsradulescu/overboost-app/main/update/latest.json 2>&1 '
               '|| wget -q -T 10 -O /dev/null https://raw.githubusercontent.com/mrsradulescu/overboost-app/main/update/latest.json 2>&1 || echo "nu merge (nu are internet)"); true')
        rc, lines = self.ssh_run(c, [cmd], label='Unitate > test internet (doar citire)')
        self.log('Trimite-mi acest text din Protocol (rute + rezultatul GitHub).', 'ok')

    def id8_logger_install(self):
        if self.busy or not self.precheck('Instalez loggerul', 'conn'):
            return
        if not self.ask('Instalez aplicația OVERBOOST?',
                        'Kitul (deja construit la 8b) se copiază pe unitate și se instalează cu propriul script, care face mai '
                        'întâi backup (uid-uri, înregistrarea aplicațiilor, un fișier legat de MainApp) înainte de orice schimbare. '
                        'Pe iDrive 8 aplicația apare după repornire (se instalează cu --persist, ca hook-ul să supraviețuiască repornirii). Dacă ceva nu arată bine, „Dezinstalează” pune totul înapoi din '
                        'acel backup. Mașina parcata, contact pus, de preferat incărcător.', yes='Instalează'):
            return
        self.start('Instalez loggerul', self.job_id8_logger_install, 'conn')

    def job_id8_logger_install(self, c):
        self._logger_upload_kit(c)
        rc, lines = self.ssh_run(c, ['cd %s && sh mgu.sh install --persist' % KIT_REMOTE], label='Unitate > mgu.sh install --persist')
        if rc:
            raise Abort('Instalarea a eșuat (exit %d). Dacă s-a apucat să scrie ceva, scriptul a făcut backup înainte - vezi '
                        'Protocol pentru detalii, apoi „Dezinstalează” dacă e nevoie.' % rc)
        self._notice = ('OVERBOOST instalat', 'Repornește unitatea o data (butonul de mai sus sau un ciclu normal de contact). '
                        'Apoi "OVERBOOST" apare in Toate aplicațiile.', 'info')
        self.log('OVERBOOST instalat. Repornește unitatea ca să-l vezi in aplicații.', 'ok')

    def id8_logger_persist(self):
        if self.busy or not self.precheck('Repar loggerul', 'conn'):
            return
        self.start('Repar loggerul', self.job_id8_logger_persist, 'conn')

    def job_id8_logger_persist(self, c):
        self._logger_upload_kit(c)
        rc, lines = self.ssh_run(c, ['cd %s && sh mgu.sh persist' % KIT_REMOTE], label='Unitate > mgu.sh persist')
        if rc:
            raise Abort('Repararea a eșuat (exit %d). Vezi Protocol.' % rc)
        self.log('Hook-ul e activ și rămâne după repornire. Repornește unitatea o dată și caută "OVERBOOST".', 'ok')

    def id8_logger_remove(self):
        if self.busy or not self.precheck('Dezinstalez loggerul', 'conn'):
            return
        if self.ask('Dezinstalezi OVERBOOST / loggerul?',
                    'Aplicația (și datele ei) sunt mutate într-un folder de quarantine cu backup pe unitate, nu șterse direct. '
                    'Dacă a modificat MainApp la instalare, schimbarea e anulată acum. Nu atinge alte aplicații.',
                    yes='Dezinstalează', danger=True):
            self.start('Dezinstalez loggerul', self.job_id8_logger_remove, 'conn')

    def job_id8_logger_remove(self, c):
        rc, lines = self.remote(c, '[ -d %s ] && echo HAVE_KIT || echo NO_KIT' % KIT_REMOTE, echo=False)
        if 'HAVE_KIT' not in lines:
            raise Abort('Nu găsesc kitul instalat pe unitate (%s). Dacă a fost instalat altfel, verifică manual.' % KIT_REMOTE)
        rc, lines = self.ssh_run(c, ['cd %s && sh mgu.sh uninstall' % KIT_REMOTE], label='Unitate > mgu.sh uninstall')
        if rc:
            raise Abort('Dezinstalarea a eșuat (exit %d). Vezi Protocol.' % rc)
        self.log('Aplicația dezinstalată (pus în quarantine cu backup pe unitate).', 'ok')

    def id8_reboot(self):
        if self.busy or not self.precheck('Repornesc unitatea', 'conn'):
            return
        if self.ask('Repornești unitatea?', 'Unitatea se oprește și repornește (~1-2 minute, ecranul se stinge). Conexiunea SSH se întrerupe. Nu o folosi cât timp rulează o instalare.',
                    yes='Repornește'):
            self.start('Repornesc unitatea', self.job_id8_reboot, 'conn')

    def job_id8_reboot(self, c):
        self.ssh_run(c, ['(sleep 1; reboot) >/dev/null 2>&1 &'], label='Unitate > reboot')
        self.log('Comanda de repornire trimisa. Asteapta ~2 minute.', 'ok')

    # ------------------------------------------------------------------ pasul 9
    def job_id8_logs(self, c):
        os.makedirs(LOG_DIR, exist_ok=True)
        rc, lines = self.remote(c, 'ls -1 %s/oap_*/data/bclog-*.jsonl %s/oap_*/data/bench.log %s/oap_*/data/bclog-dids*.json 2>/dev/null; true' % (OAP_DATA, OAP_DATA, OAP_DATA),
                                echo=False)
        files = [x.strip() for x in lines if x.strip().startswith('/')]
        if not files:
            raise Abort('Nu am găsit loguri pe unitate. Ai deschis "OVERBOOST" in mașina? Log-ul aplicatiei (bench.log) apare și el '
                        'in /var/opt/public/oap/oap_*/data/.')
        got = 0
        for rf in files:
            local = os.path.join(LOG_DIR, os.path.basename(rf))
            rc, _ = self.ssh_run(c, ['cat %s' % g['shq'](rf)], out_file=local, echo=False, label='Unitate > aduc %s' % os.path.basename(rf))
            if rc == 0:
                got += 1
                self.log('  %s (%s)' % (os.path.basename(rf), fmt_size(os.path.getsize(local))))
        # logul propriu al SDK-ului (in jurnalul sistemului), pentru erori la pornire
        sdk_out = os.path.join(LOG_DIR, 'sdk-journal.txt')
        self.ssh_run(c, ['(journalctl -b --no-pager 2>/dev/null || cat /var/log/messages 2>/dev/null) | '
                         'grep -i "id8_bench\\|oap_124\\|oap_123\\|OnlineApp::" | tail -300; true'],
                     out_file=sdk_out, echo=False, label='Unitate > jurnal SDK')
        if os.path.isfile(sdk_out) and os.path.getsize(sdk_out):
            got += 1
            self.log('  sdk-journal.txt (%s)' % fmt_size(os.path.getsize(sdk_out)))
        if not got:
            raise Abort('Nu am putut aduce niciun fișier.')
        self.log('%d fișiere in %s' % (got, LOG_DIR), 'ok')

    def job_id8_analyze(self, c):
        need_pack()
        py = py_or_abort(c)
        logs = sorted(os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR) if f.startswith('bclog-') and f.endswith('.jsonl')) \
            if os.path.isdir(LOG_DIR) else []
        if not logs:
            raise Abort('Nu sunt loguri in %s. Întâi "Adu logurile".' % LOG_DIR)
        out = os.path.join(LOG_DIR, 'raport.txt')
        rc, _ = self.run(py + [os.path.join(LOGGER_DIR, 'tools', 'analyze_bclog.py')] + logs + ['--out', out], cwd=LOGGER_DIR)
        if rc:
            raise Abort('Analiza a eșuat (exit %d).' % rc)
        self.log('Raport salvat: %s' % out, 'ok')


    # ------------------------------------------------------------------ pasul 10 (motorul GPU)
    RENDER_DIR = os.path.join(HERE, 'render-kit')

    def _tool_artifact(self, kit):
        """Pachetul unei componente (component.json + files/ + units/), ca deploy/hu artifact: cu sha256 per fișier."""
        c0 = json.load(open(os.path.join(kit, 'component.json'), encoding='utf-8'))
        entries, files, units = [], [], []
        for fl in c0['files']:
            p = os.path.join(kit, fl.get('from', fl['path']).replace('/', os.sep))
            if not os.path.isfile(p):
                raise Abort('Lipsește %s. Construiește mai întâi motorul (10a).' % os.path.relpath(p, kit))
            data = open(p, 'rb').read()
            entries.append(('files/' + fl['path'], data, int(fl['mode'], 8)))
            files.append({'path': fl['path'], 'mode': fl['mode'], 'sha256': hashlib.sha256(data).hexdigest()})
        for u in c0.get('units', []):
            entries.append(('units/' + u['name'], open(os.path.join(kit, u.get('from', u['name'])), 'rb').read(), 0o644))
            units.append({'name': u['name']})
        man = {'id': c0['id'], 'kind': c0['kind'], 'version': c0['version'], 'title': c0.get('title', c0['id']),
               'description': c0.get('description', ''), 'files': files, 'units': units, 'enable': c0.get('enable', []),
               'recmgr': c0.get('recmgr', []), 'restart': c0.get('restart', [])}
        for k in ('mguHmi', 'mguProvides', 'keepRunning', 'crashNames', 'postInstall', 'check', 'activate', 'deactivate'):
            if k in c0:
                man[k] = c0[k]
        entries.append(('component.json', (json.dumps(man, indent=2) + '\n').encode('utf-8'), 0o644))
        entries.sort(key=lambda e: e[0])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as t:
            for name, data, mode in entries:
                ti = tarfile.TarInfo(name)
                ti.size, ti.mode, ti.mtime, ti.uid, ti.gid = len(data), mode, 1700000000, 0, 0
                t.addfile(ti, io.BytesIO(data))
        return '%s@%s.tgz' % (c0['id'], c0['version']), buf.getvalue()

    def _deploy_run(self, c, arts, label):
        """Pune pachetele pe unitate într-un folder temporar și rulează mgu-deploy install pentru fiecare."""
        entries = []
        run = 'set -e\nS=$(cd "$(dirname "$0")" && pwd)\n'
        for name, blob in arts:
            entries.append(('artifacts/' + name, blob, 0o644))
            run += 'sh %s install --artifact "$S/artifacts/%s" --skip-older --yes\n' % (DEPLOY_SH, name)
        entries.append(('run.sh', run.encode('utf-8'), 0o755))
        buf = io.BytesIO()
        now = int(time.time())
        with tarfile.open(fileobj=buf, mode='w:gz') as t:
            for name, data, mode in entries:
                ti = tarfile.TarInfo(name)
                ti.size, ti.mode, ti.mtime = len(data), mode, now
                t.addfile(ti, io.BytesIO(data))
        stage = '/var/opt/mgu-render-kit-%d' % os.getpid()
        q = g['shq'](stage)
        cmd = ('rm -rf %s && umask 077 && mkdir -p %s && tar -xzf - -C %s && { sh %s/run.sh; st=$?; rm -rf %s; exit $st; }' % (q, q, q, q, q))
        rc, lines = self.ssh_run(c, [cmd], stdin_bytes=buf.getvalue(), label=label)
        if rc == 255:
            raise Abort('Nu m-am putut conecta prin SSH (pasul 4).')
        if rc:
            raise Abort('%s a eșuat (exit %d). Vezi Protocol. Unitatea a făcut backup înainte; dacă ceva a rămas instalat pe jumătate, folosește "Dezinstalează motorul".' % (label, rc))
        return lines

    def job_id8_render_build(self, c=None):
        kit = RENDER_DIR
        pre = os.path.join(kit, 'build', 'target', 'mgu-render')
        if os.path.isfile(pre):
            d = open(pre, 'rb').read()
            if d[:4] != b'\x7fELF' or d[18:20] != b'\x3e\x00':
                raise Abort('Fișierul motorului nu e un program x86-64 valid. Refă toolkit-ul din arhivă.')
            self.log('Motor gata compilat (release mgu-render@0.6.8 din mgu-store, ELF x86-64, glibc 2.17): sha256 %s. '
                     'Docker nu e necesar. Continuă cu 10b.' % hashlib.sha256(d).hexdigest()[:16], 'ok')
            return
        if not os.path.isfile(os.path.join(kit, 'Makefile')):
            raise Abort('Lipsește folderul "render-kit" de langa toolkit (%s).' % kit)
        docker = shutil.which('docker')
        if not docker:
            raise Abort('Docker lipsește. Instalează Docker Desktop, pornește-l, apoi încearcă din nou.')
        rc, _ = self.run([docker, 'info'], echo=False, label='docker info')
        if rc:
            raise Abort('Docker nu rulează. Pornește Docker Desktop și așteaptă să zică "running".')
        rc, _ = self.run([docker, 'build', '-q', '--platform', 'linux/amd64', '-t', 'mgu-render-target',
                          os.path.join(kit, 'docker', 'target')], label='docker build (Ubuntu 18.04, primul build durează)')
        if rc:
            raise Abort('Imaginea Docker nu s-a construit (exit %d).' % rc)
        rc, _ = self.run([docker, 'run', '--rm', '--platform', 'linux/amd64', '-v', kit + ':/w', '-w', '/w', 'mgu-render-target',
                          'make', '-s', 'in-build', 'OUT=target', 'LDFLAGS=-Wl,--dynamic-linker=/lib/ld-linux-x86-64.so.2'],
                         label='docker run make (compilez mgu-render)')
        if rc:
            raise Abort('Compilarea a eșuat (exit %d). Vezi Protocol.' % rc)
        rc, _ = self.run([docker, 'run', '--rm', '--platform', 'linux/amd64', '-v', kit + ':/w', '-w', '/w', 'mgu-render-target',
                          'sh', 'tools/check_target.sh', 'build/target/mgu-render'], label='verific binarul pentru unitate')
        if rc:
            raise Abort('Binarul nu e compatibil cu unitatea (verificarea a eșuat). NU îl instala. Vezi Protocol.')
        self.log('Motorul GPU e construit și verificat pentru unitate.', 'ok')

    def id8_deploy070_install(self):
        if self.busy or not self.precheck('Instalez mgu-deploy 0.7', 'conn'):
            return
        if not self.ask('Instalez mgu-deploy 0.7?',
                        'Motorul GPU cere mgu-deploy 0.6.9 sau mai nou (pasul 7 pune 0.6.3). Se instalează cu backup automat. '
                        'Nu pornește nicio modificare la MainApp: asta se întâmplă doar când instalezi aplicații prin el, ceea ce nu facem.',
                        yes='Instalează'):
            return
        self.start('Instalez mgu-deploy 0.7', self.job_id8_deploy070_install, 'conn')

    def job_id8_deploy070_install(self, c):
        self._deploy_kit = 'deploy-kit-070'
        try:
            self.job_id8_deploy_install(c)
        finally:
            self._deploy_kit = None

    def id8_render_install(self):
        if self.busy or not self.precheck('Instalez motorul GPU', 'conn'):
            return
        if not self.ask('Instalez motorul GPU?',
                        'Se instalează serviciul mgu-render (root, doar mod „strat”) cu backup automat. Dacă ecranul se comportă ciudat, '
                        '„Dezinstalează motorul” îl scoate. Nu ating MainApp și nu pornesc nimic înainte de HMI.', yes='Instalează'):
            return
        self.start('Instalez motorul GPU', self.job_id8_render_install, 'conn')

    def job_id8_render_install(self, c):
        art = self._tool_artifact(RENDER_DIR)
        self._deploy_run(c, [art], 'Unitate > instalez mgu-render')
        self._notice = ('Motor instalat', 'Repornește unitatea o dată, apoi apasă „10d. Testează motorul”.', 'info')
        self.log('mgu-render instalat. Repornește unitatea, apoi testează (10d).', 'ok')

    def job_id8_render_check(self, c):
        rc, lines = self.remote(c, '[ -f /var/opt/mgu-render/current/render_check.js ] || { echo NO_RENDER; exit 3; }\n'
                                   '/usr/bin/node /var/opt/mgu-render/current/render_check.js; echo CHECK_EXIT=$?\n'
                                   'systemctl is-active mgu-render.service mgu-render.socket 2>&1 | head -3\n'
                                   'ls -l /run/mgu-render/ 2>&1 | head -5', label='Unitate > render_check')
        if 'NO_RENDER' in lines:
            raise Abort('Motorul nu e instalat (10c).')
        if 'CHECK_EXIT=0' in lines:
            self.log('Motorul GPU răspunde (HELLO/READY). Trimite-mi Protocolul.', 'ok')
        else:
            raise Abort('Testul motorului a eșuat. Copiază Protocolul și trimite-mi-l; nu repeta testul.')

    def id8_render_remove(self):
        if self.busy or not self.precheck('Dezinstalez motorul GPU', 'conn'):
            return
        if self.ask('Dezinstalezi motorul GPU?', 'Se scoate mgu-render (cu backup). OVERBOOST rămâne cu ecranul text.', yes='Dezinstalează', danger=True):
            self.start('Dezinstalez motorul GPU', self.job_id8_render_remove, 'conn')

    def job_id8_render_remove(self, c):
        rc, _ = self.remote(c, 'sh %s uninstall mgu-render --yes' % DEPLOY_SH, label='Unitate > mgu-deploy uninstall mgu-render')
        if rc:
            raise Abort('Dezinstalarea a eșuat (exit %d). Vezi Protocol.' % rc)
        self.log('Motorul GPU dezinstalat.', 'ok')

    # ------------------------------------------------------------------ inregistrare in clasa
    for fn in (id8_deploy070_install, job_id8_deploy070_install, job_id8_render_build, _tool_artifact, _deploy_run, id8_render_install, job_id8_render_install, job_id8_render_check, id8_render_remove, job_id8_render_remove, id8_gate, build_page_id8, job_id8_backup, id8_refresh, id8_chip, id8_scan, job_id8_pc, id8_fix, refresh_path, job_id8_fix, id8_relaunch_admin, id8_pick_zip, job_id8_tool, id8_tool_cmd, id8_status,
               job_id8_status, id8_vin_ok, id8_flash, id8_enable, id8_close, job_id8_tool_action, job_id8_findkey,
               job_id8_probe, job_id8_catalog, job_id8_signals, _logger_upload_kit, job_id8_extract_sdk, job_id8_build,
               job_id8_logger_check, job_id8_net_check, id8_logger_install, job_id8_logger_install,
               id8_logger_persist, job_id8_logger_persist, id8_logger_remove, job_id8_logger_remove, id8_deploy_install, job_id8_deploy_check, job_id8_deploy_install, id8_reboot, job_id8_reboot, job_id8_logs, job_id8_analyze):
        setattr(App, fn.__name__, fn)
