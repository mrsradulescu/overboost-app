#!/usr/bin/env python3
"""
iDrive 7 Toolkit - Auslesen & Flashen  (Version 0.2, noch ungetestet)
=====================================================================

Windows-Oberfläche für https://github.com/cauliflowermossgrape/idrive7-apps
(Custom-Apps für die BMW-MGU-Head-Unit, iDrive 7).

Credits: Der gesamte Code der Apps, des Kits und der Werkzeuge, die diese GUI
aufruft (make_kit.py, build.py, extract_sdk.js, check_signals.py, deploy-car.sh,
mgu-app-install.sh, mgu-renderd, Controller-Gate ...), stammt von
cauliflowermossgrape - https://github.com/cauliflowermossgrape/idrive7-apps
(MIT-Lizenz). Auch das Root-Tool idrive7-8-ssh-root ist von cauliflowermossgrape.
Der App Store mgu-store stammt von Robert Nisipeanu (robertnisipeanu) -
https://github.com/robertnisipeanu/mgu-store (GPL-3.0-or-later, (C) 2026 Robert
Nisipeanu and IterioDev); die GUI ruft nur dessen Installationsprogramm auf.
Ausgenommen ist nur diese GUI: ein separates, inoffizielles Zusatzprojekt.

Die GUI ruft dieselben Werkzeuge auf wie die Anleitung im Projekt
(make_kit.py, extract_sdk.js, check_signals.py, deploy-car.sh über SSH),
nur per Knopfdruck und mit Protokoll im Fenster:

  SSH aktivieren (einmalig): Link zum Original-Tool idrive7-8-ssh-root von cauliflowermossgrape,
                   danach den Schlüssel übernehmen (die GUI baut das Freischalten nicht nach)
  App Store (neu in 0.2): mgu-store von robertnisipeanu mit dessen mgu-install.js
                   ins Auto bringen (first-install mit Schlüssel/IP aus Schritt 1)
                   und einen USB-Stick für Updates ohne Internet vorbereiten (usb-repo)
  Installieren (alles, was man für die Apps im Auto braucht, in 4 Schritten):
    1 Verbinden      : SSH-Schlüssel aus idrive7-8-ssh-root laden, Verbindung testen
    2 Projekt & SDK  : idrive7-apps holen, SDK (x_call) aus der Unit extrahieren
    3 Apps & Kit     : Apps auswählen -> dist/mgu-custom-apps.tar.gz
    4 Installieren   : Kit übertragen -> deploy-car.sh check / install
    Zurück auf Serie : deploy-car.sh restore
  Tools:
    Diagnose  : Geräteinfo, Status, Backups, App-Logs, SOME/IP-Katalog, Signale, SDK aus Datei
    Shell     : interaktive root-Shell auf der Unit + Schnellbefehle (mit Strg+C)
    Dateien   : Datei-Browser (Up-/Download, Sichern/Einspielen) über ssh
    PC-Check  : Python, Node.js, OpenSSH, Git; Hinweise zum Root-Zugang

Voraussetzungen: Python 3.7+ mit Tkinter (bei python.org dabei), das Paket
CustomTkinter (pip install customtkinter - die GUI bietet die Installation beim
ersten Start selbst an), Node.js 14+, der OpenSSH-Client von Windows und der
Root-SSH-Schlüssel aus dem Tool idrive7-8-ssh-root
(data\\enrollments\\<VIN>\\id_rsa + connect.txt).

Oberfläche auf Englisch (Standard) oder Deutsch, umschaltbar links unten.
Einstellungen teilt sie sich mit der alten GUI v1.1 (idrive7_gui.json im selben Ordner).
Start: Doppelklick auf "iDrive7-Toolkit starten.bat".
"""

import codecs
import datetime
import hashlib
import json
import os
import posixpath
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import urllib.request
import webbrowser
import zipfile


class _NullWriter:
    """pythonw / PyInstaller --windowed: sys.stdout/err sind None."""
    def write(self, *_):
        pass

    def flush(self):
        pass


if sys.stdout is None:
    sys.stdout = _NullWriter()
if sys.stderr is None:
    sys.stderr = _NullWriter()

import tkinter as tk  # noqa: E402
import tkinter.font as tkfont  # noqa: E402
from tkinter import filedialog, ttk  # noqa: E402

IS_WIN = os.name == 'nt'
FROZEN = getattr(sys, 'frozen', False)
NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if IS_WIN else 0


def _ui_lang():
    """Sprache der Oberfläche aus idrive7_gui.json ("ui_lang": "en" | "de"); Standard Englisch."""
    here = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
    try:
        with open(os.path.join(here, 'idrive7_gui.json'), encoding='utf-8') as fh:
            v = json.load(fh).get('ui_lang')
    except (OSError, ValueError, AttributeError):
        v = None
    return v if v in ('en', 'de') else 'en'


UI_LANG = _ui_lang()


class TStr(str):
    """Übersetzter Text, der sich merkt, wie er entstanden ist. Beim Umschalten der Sprache erzeugt
    retr() ihn in der neuen Sprache neu - auch nach "%"-Formatierung und "+"-Verkettung. So können
    alle Beschriftungen an Ort und Stelle getauscht werden, ohne die Oberfläche neu aufzubauen."""

    def __new__(cls, render):
        o = str.__new__(cls, render())
        o._render = render
        return o

    def retr(self):
        return TStr(self._render)

    def __mod__(self, args):
        r = self._render
        return TStr(lambda: r() % _cur_args(args))

    def __add__(self, other):
        r = self._render
        return TStr(lambda: r() + _cur(other))

    def __radd__(self, other):
        r = self._render
        return TStr(lambda: _cur(other) + r())


def _cur(x):
    """Text in der aktuellen Sprache (TStr neu erzeugt, alles andere unverändert)."""
    return x._render() if isinstance(x, TStr) else x


def _cur_args(a):
    return tuple(_cur(x) for x in a) if isinstance(a, tuple) else _cur(a)


def tr(s):
    """Text in der Sprache der Oberfläche. Die deutschen Texte sind die Schlüssel, EN die Übersetzung."""
    if isinstance(s, TStr):
        return s
    return TStr(lambda: s if UI_LANG == 'de' else EN.get(s, s))


# Übersetzungstabelle Deutsch -> Englisch (Schlüssel = deutscher Originaltext)
EN = {
    'CustomTkinter fehlt in dieser .exe. Bitte mit "iDrive7-Toolkit exe bauen.bat" neu bauen.': 'CustomTkinter is missing from this .exe. Please rebuild it with "iDrive7-Toolkit exe bauen.bat".',
    'iDrive 7 Toolkit – CustomTkinter fehlt': 'iDrive 7 Toolkit – CustomTkinter missing',
    'Die neue Oberfläche braucht das Python-Paket "customtkinter".\n\nJetzt installieren?  (pip install --user customtkinter, dauert ca. 10–30 s)': 'The new interface needs the Python package "customtkinter".\n\nInstall it now?  (pip install --user customtkinter, takes about 10–30 s)',
    'Installation fehlgeschlagen:\n\n': 'Installation failed:\n\n',
    '\n\nIn einer Eingabeaufforderung:  py -m pip install customtkinter': '\n\nIn a command prompt:  py -m pip install customtkinter',
    'iDrive 7 Toolkit – Auslesen & Flashen': 'iDrive 7 Toolkit – Read & Flash',
    'Englisch': 'English',
    'Chinesisch (vereinfacht)': 'Chinese (Simplified)',
    'Französisch': 'French',
    'Italienisch': 'Italian',
    'Spanisch': 'Spanish',
    'Niederländisch': 'Dutch',
    'Wer / Uptime': 'Who / uptime',
    'Speicherplatz': 'Disk space',
    'Dienste': 'Services',
    'Registrierte Apps': 'Registered apps',
    'Kit-Status': 'Kit status',
    'Prozesse': 'Processes',
    'Kernelmeldungen': 'Kernel messages',
    'Renderer-Log': 'Renderer log',
    'Neustart App-Plattform': 'Restart app platform',
    'Neustart HMI': 'Restart HMI',
    'Das ist ein PuTTY-Schlüssel (.ppk). Bitte die Datei "id_rsa" (ohne Endung) aus demselben Ordner wählen.': 'This is a PuTTY key (.ppk). Please choose the file "id_rsa" (no extension) from the same folder.',
    '%s ist kein privater SSH-Schlüssel. Bitte "id_rsa" wählen (nicht .pub).': '%s is not a private SSH key. Please choose "id_rsa" (not .pub).',
    'Die Head Unit lehnt den Schlüssel ab. Ist SSH aktiviert (MGU-SSH-Tool → "Enable") und ist es der richtige id_rsa?': 'The head unit rejects the key. Is SSH enabled (MGU SSH tool → "Enable") and is it the right id_rsa?',
    'SSH ist auf der Head Unit nicht aktiv. Im MGU-SSH-Tool "Enable" ausführen.': 'SSH is not active on the head unit. Run "Enable" in the MGU SSH tool.',
    'Head Unit nicht erreichbar. ENET-Kabel gesteckt, Zündung an, IP-Adresse richtig? (Standard: %s)': 'Head unit not reachable. ENET cable plugged in, ignition on, IP address correct? (default: %s)',
    'Windows-OpenSSH akzeptiert die Rechte der Schlüsseldatei nicht.': "Windows OpenSSH does not accept the key file's permissions.",
    'Die Verbindung wurde unterbrochen (Head Unit neu gestartet oder eingeschlafen?).': 'The connection was interrupted (head unit restarted or asleep?).',
    'SSH-Verbindung fehlgeschlagen (Details im Protokoll).': 'SSH connection failed (details in the log).',
    'Dateien': 'Files',
    'Installieren': 'Install',
    'Wiederherstellen': 'Restore',
    'Prüfen und installieren': 'Check and install',
    'Schlüssel suchen': 'Find key',
    'Abbrechen': 'Cancel',
    'für idrive7-apps · v%s': 'for idrive7-apps · v%s',
    'Nicht geprüft': 'Not checked',
    'Katalog': 'Catalog',
    'Code der Apps & Werkzeuge:': 'Code of the apps & tools:',
    '© cauliflowermossgrape ↗': '© cauliflowermossgrape ↗',
    'Diese GUI: inoffizielles Zusatzprojekt': 'This GUI: unofficial add-on',
    'Root-Tool ↗': 'Root tool ↗',
    'Projekt auf GitHub ↗': 'Project on GitHub ↗',
    'Kein offizielles BMW-Produkt. Nutzung auf eigenes Risiko.': 'Not an official BMW product. Use at your own risk.',
    'Bereit.': 'Ready.',
    'Leeren': 'Clear',
    'Speichern…': 'Save…',
    'Protokoll ▾': 'Log ▾',
    '%s v%s – Protokoll erscheint hier.': '%s v%s – the log appears here.',
    'Credits: Apps, Kit und Werkzeuge © cauliflowermossgrape – %s (MIT). Diese GUI ist ein inoffizielles Zusatzprojekt.': 'Credits: apps, kit and tools © cauliflowermossgrape – %s (MIT). This GUI is an unofficial add-on.',
    'Protokoll ▴': 'Log ▴',
    'Verbunden': 'Connected',
    'Nicht erreichbar': 'Not reachable',
    'Root-Zugang (SSH) auf der Head Unit': 'Root access (SSH) on the head unit',
    'SSH-Tool öffnen ↗': 'Open SSH tool ↗',
    'Enrollment suchen': 'Find enrollment',
    'SSH-Schlüssel': 'SSH key',
    'Durchsuchen…': 'Browse…',
    'IP-Adresse': 'IP address',
    'connect.txt laden…': 'Load connect.txt…',
    'Automatisch suchen': 'Search automatically',
    'Verbindung testen': 'Test connection',
    'Projektordner': 'Project folder',
    'Projekt': 'Project',
    'Herunterladen / Aktualisieren': 'Download / Update',
    'Projekt herunterladen': 'Download project',
    'Ordner öffnen': 'Open folder',
    'Voraussetzungen auf diesem PC': 'Requirements on this PC',
    'python.org – beim Installieren "Add python.exe to PATH" anhaken': 'python.org – tick "Add python.exe to PATH" during setup',
    'nodejs.org – LTS-Version installieren': 'nodejs.org – install the LTS version',
    'Einstellungen › Apps › Optionale Features › "OpenSSH-Client"': 'Settings › Apps › Optional features › "OpenSSH Client"',
    'Git (optional)': 'Git (optional)',
    'ohne Git lädt die GUI das Projekt als ZIP': 'without Git the GUI downloads the project as a ZIP',
    'wird geprüft…': 'checking…',
    'Erneut prüfen': 'Check again',
    'Alles hier liest nur – an der Head Unit wird nichts verändert. Ergebnisse landen im Protokoll und im Ordner "ausgelesen" neben der GUI (SDK und Katalog direkt im Projekt).': 'Everything here only reads – nothing on the head unit is changed. Results go to the log and to the folder "ausgelesen" next to the GUI (SDK and catalog straight into the project).',
    'Geräteinfo': 'Device info',
    'Kernel, Firmware-Dateien, MainApp-Version, Dienste, Speicher, Netzwerk, VIN.': 'Kernel, firmware files, MainApp version, services, storage, network, VIN.',
    'SDK holen': 'Get SDK',
    'SDK aus Datei…': 'SDK from file…',
    'Stattdessen eine x_call@….tgz oder app.js vom PC verwenden.': 'Use an x_call@….tgz or app.js from the PC instead.',
    'SOME/IP-Katalog holen': 'Get SOME/IP catalog',
    '/etc/oap/someip/someip-interfaces.json → Projekt/reference/ (optional, für eigene Apps).': '/etc/oap/someip/someip-interfaces.json → project/reference/ (optional, for your own apps).',
    'Signale prüfen': 'Check signals',
    'Signale aller app.json gegen den Katalog prüfen (check_signals.py, offline). Optionale Schnittstellen werden nur als Hinweis gezeigt.': 'Check the signals of every app.json against the catalog (check_signals.py, offline). Optional interfaces are only shown as a note.',
    'Installierte Apps, Provisioning-Hook, GPU-Renderer, Controller-Gate, Stock-Backup. Fehlt das Kit auf der Unit, wird es nur dorthin kopiert (nicht installiert).': 'Installed apps, provisioning hook, GPU renderer, controller gate, stock backup. If the kit is not on the unit yet, it is only copied there (not installed).',
    'Backups auflisten': 'List backups',
    'App-Logs holen': 'Get app logs',
    'Logs der Custom-Apps, deploy-car.log und Journal von Renderer und App-Plattform – als Ordner auf dem PC.': 'Logs of the custom apps, deploy-car.log and the journal of the renderer and the app platform – as a folder on the PC.',
    'Starten': 'Start',
    'Ordner "ausgelesen" öffnen': 'Open folder "ausgelesen"',
    'Apps auswählen und Kit bauen': 'Select apps and build the kit',
    'Das Kit (dist/mgu-custom-apps….tar.gz) enthält die gewählten Apps, den Installer und den GPU-Renderer. Maus über eine App zeigt ihre Beschreibung.': 'The kit (dist/mgu-custom-apps….tar.gz) contains the selected apps, the installer and the GPU renderer. Hover over an app to see its description.',
    'Alle': 'All',
    'Keine': 'None',
    'Kit bauen': 'Build kit',
    'Kit-Ordner öffnen (USB-Stick)': 'Open kit folder (USB stick)',
    'Sprache der Apps': 'App language',
    'wird beim Bauen festgelegt – die Spracheinstellung im Auto ändert sie nicht': 'fixed at build time – the language setting in the car does not change it',
    'Auto geparkt, Zündung an, am besten Ladegerät dran. "Prüfen" überträgt das Kit und testet alles, ohne etwas zu ändern. "Installieren" legt vorher ein Backup an; die Kacheln erscheinen nach 1–2 Min. unter Apps.': 'Car parked, ignition on, ideally with a charger connected. "Check" copies the kit and tests everything without changing anything. "Install" makes a backup first; the tiles appear under Apps after 1–2 min.',
    'Nicht neu starten': 'Do not reboot',
    'NO_REBOOT – das Controller-Gate (für Doom) lädt dann erst beim nächsten Start der Head Unit.': 'NO_REBOOT – the controller gate (for Doom) then only loads the next time the head unit starts.',
    'Ohne Controller-Gate': 'Without controller gate',
    'NO_INPUTGATE – Doom läuft, aber das HMI reagiert mit auf den Controller. Kein Neustart nötig.': 'NO_INPUTGATE – Doom runs, but the HMI also reacts to the controller. No reboot needed.',
    'HMI gleich mit neu starten': 'Restart the HMI as well',
    'WITH_HMI – entfernt alte Kacheln sofort (Bildschirm kurz schwarz). Ohne Wirkung, wenn die Head Unit ohnehin neu startet.': 'WITH_HMI – removes old tiles right away (screen briefly black). No effect if the head unit reboots anyway.',
    'Prüfen (Trockenlauf)': 'Check (dry run)',
    'Prüfen': 'Check',
    'Zurück auf Serie': 'Back to stock',
    '"stock" = Zustand vor der allerersten Installation: entfernt alle Custom-Apps, Hook und Renderer und setzt jede berührte Datei exakt zurück.': '"stock" = the state before the very first installation: removes all custom apps, the hook and the renderer and puts every touched file back exactly.',
    'Backups laden': 'Load backups',
    'HMI nicht neu starten': 'Do not restart the HMI',
    'NO_HMI_RESTART – die entfernten Kacheln verschwinden dann erst beim nächsten Neustart.': 'NO_HMI_RESTART – the removed tiles then only disappear at the next restart.',
    'Noch kein Projekt – in Schritt 2 herunterladen oder Ordner wählen.': 'No project yet – download it in step 2 or choose a folder.',
    '✔ Projekt · %d Apps': '✔ Project · %d apps',
    'Zum Bauen fehlt noch das SDK: "SDK holen" (Head Unit muss verbunden sein).': 'The SDK is still missing for building: "Get SDK" (the head unit must be connected).',
    '✖ Kein Projekt': '✖ No project',
    'In diesem Ordner liegt kein idrive7-apps-Projekt. "Herunterladen" holt es von GitHub dorthin.': 'There is no idrive7-apps project in this folder. "Download" fetches it from GitHub into it.',
    'SDK vorhanden': 'SDK present',
    'SDK fehlt': 'SDK missing',
    'Katalog vorhanden': 'Catalog present',
    'Katalog fehlt (optional)': 'Catalog missing (optional)',
    '%d.%m.%Y %H:%M': '%Y-%m-%d %H:%M',
    'Kit %s · %s': 'Kit %s · %s',
    '%s  ·  %s  ·  gebaut %s': '%s  ·  %s  ·  built %s',
    'Enthält: ': 'Contains: ',
    '✔ Trockenlauf OK': '✔ Dry run OK',
    ' · %d App(s) ausgelassen': ' · %d app(s) left out',
    'noch nicht geprüft': 'not checked yet',
    'noch kein Kit gebaut': 'no kit built yet',
    'Noch kein Kit gebaut.': 'No kit built yet.',
    'Zum Bauen wird das SDK gebraucht (Schritt 2 › SDK holen).': 'Building needs the SDK (step 2 › Get SDK).',
    '%d von %d ausgewählt': '%d of %d selected',
    'nicht startbar': 'does not start',
    'nicht gefunden': 'not found',
    ' (zu alt)': ' (too old)',
    'nicht installiert': 'not installed',
    'Protokoll speichern': 'Save log',
    'idrive7-protokoll_%s.txt': 'idrive7-log_%s.txt',
    'Kein SSH-Schlüssel gewählt (Installieren › Schritt 1: "Durchsuchen…", "connect.txt laden…" oder "Automatisch suchen").': 'No SSH key selected (Install › step 1: "Browse…", "Load connect.txt…" or "Search automatically").',
    'Bitte eine gültige IP-Adresse der Head Unit eintragen (Standard: %s).': 'Please enter a valid IP address of the head unit (default: %s).',
    'Der Port muss eine Zahl sein (Standard: 22).': 'The port must be a number (default: 22).',
    'Der OpenSSH-Client fehlt: Windows-Einstellungen › Apps › Optionale Features › "OpenSSH-Client" hinzufügen.': 'The OpenSSH client is missing: Windows Settings › Apps › Optional features › add "OpenSSH Client".',
    'Kein idrive7-apps-Projekt gefunden. In Schritt 2 den Projektordner wählen oder "Herunterladen / Aktualisieren" klicken.': 'No idrive7-apps project found. Choose the project folder in step 2 or click "Download / Update".',
    'Es wurde noch kein Kit gebaut%s. Erst "Kit bauen" (Schritt 3).': 'No kit has been built yet%s. First "Build kit" (step 3).',
    ' (Sprache: %s)': ' (language: %s)',
    '▶ %s   (%s)': '▶ %s   (%s)',
    'Unerwarteter Fehler: %s': 'Unexpected error: %s',
    '%s – fertig%s.': '%s – done%s.',
    ' (mit Hinweisen)': ' (with notes)',
    '%s – abgebrochen.': '%s – cancelled.',
    '%s – fehlgeschlagen.': '%s – failed.',
    'Wirklich abbrechen?': 'Really cancel?',
    'Ein Abbruch mitten in der Installation/Wiederherstellung kann die Head Unit in einem halben Zustand lassen.': 'Cancelling in the middle of an installation/restore can leave the head unit half-done.',
    'Trotzdem abbrechen': 'Cancel anyway',
    'Weiterlaufen lassen': 'Keep running',
    'Beenden?': 'Quit?',
    'Es läuft noch: %s.\nTrotzdem beenden?': 'Still running: %s.\nQuit anyway?',
    'Beenden': 'Quit',
    'Ordner': 'Folder',
    'Den Ordner gibt es noch nicht.': 'This folder does not exist yet.',
    'Abgebrochen.': 'Cancelled.',
    'Konnte %s nicht starten: %s': 'Could not start %s: %s',
    'Head Unit › Skript': 'Head unit › script',
    'Kein Kit gefunden. Erst "Kit bauen".': 'No kit found. First "Build kit".',
    'Übertrage das Kit (%s, %s) nach %s …': 'Copying the kit (%s, %s) to %s …',
    'Das Kit konnte nicht übertragen/geprüft werden (Exit %d). Siehe Protokoll.': 'The kit could not be copied/verified (exit %d). See the log.',
    'Kit übertragen und per Prüfsumme bestätigt.': 'Kit copied and verified by checksum.',
    'Auf der Unit liegt noch kein Kit – übertrage es zuerst (installiert nichts).': 'There is no kit on the unit yet – copying it first (installs nothing).',
    'Auf der Head Unit liegt noch kein Kit (deploy-car.sh) und lokal wurde noch keins gebaut.\nErst in Schritt 3 ein Kit bauen.': 'There is no kit on the head unit yet (deploy-car.sh) and none has been built locally.\nFirst build a kit in step 3.',
    'Gespeichert: %s': 'Saved: %s',
    'Suche connect.txt + id_rsa des SSH-Tools (data\\enrollments\\<VIN>\\) …': "Searching for the SSH tool's connect.txt + id_rsa (data\\enrollments\\<VIN>\\) …",
    'Kein Schlüssel gefunden. Bitte "Durchsuchen…" oder "connect.txt laden…" benutzen.': 'No key found. Please use "Browse…" or "Load connect.txt…".',
    '  gefunden: ': '  found: ',
    'Neben %s liegt kein id_rsa.': 'There is no id_rsa next to %s.',
    'Übernommen: %s': 'Taken over: %s',
    'SSH-Schlüssel (id_rsa) wählen': 'Choose SSH key (id_rsa)',
    'Alle Dateien': 'All files',
    'Schlüssel gewählt: %s%s': 'Key selected: %s%s',
    ' (+ connect.txt gelesen)': ' (+ connect.txt read)',
    'connect.txt des SSH-Tools wählen': "Choose the SSH tool's connect.txt",
    'Neben dieser connect.txt liegt kein id_rsa.': 'There is no id_rsa next to this connect.txt.',
    'connect.txt gelesen: %s  →  %s, %s': 'connect.txt read: %s  →  %s, %s',
    'Projektordner idrive7-apps wählen': 'Choose the idrive7-apps project folder',
    'Unerwartete Antwort der Head Unit (Exit %d).': 'Unexpected answer from the head unit (exit %d).',
    'Verbunden, aber nicht als root (uid %s) – Installieren braucht root.': 'Connected, but not as root (uid %s) – installing needs root.',
    'Verbindung OK – root auf "%s" (%s).': 'Connection OK – root on "%s" (%s).',
    'git pull ist fehlgeschlagen (lokale Änderungen?). Siehe Protokoll.': 'git pull failed (local changes?). See the log.',
    'git clone ist fehlgeschlagen. Internetverbindung?': 'git clone failed. Internet connection?',
    'Nach dem Download fehlt kit/tools/make_kit.py in %s.': 'After the download kit/tools/make_kit.py is missing in %s.',
    'Projekt bereit: %s': 'Project ready: %s',
    'Lade %s …': 'Downloading %s …',
    'Heruntergeladen: %s – entpacke nach %s': 'Downloaded: %s – unpacking to %s',
    '%d Dateien entpackt (vorhandenes oap-sdk, reference, dist bleiben erhalten).': '%d files unpacked (existing oap-sdk, reference, dist are kept).',
    'Download fehlgeschlagen: %s': 'Download failed: %s',
    'Head Unit › Geräteinfo (nur lesen)': 'Head unit › device info (read only)',
    'Geräteinfo %s – %s  (VIN laut connect.txt: %s)\n': 'Device info %s – %s  (VIN from connect.txt: %s)\n',
    'Head Unit › x_call in der Firmware suchen': 'Head unit › look for x_call in the firmware',
    'Firmware-x_call gefunden: %s': 'Firmware x_call found: %s',
    'Head Unit › %s herunterladen': 'Head unit › download %s',
    'Konnte %s nicht lesen.': 'Could not read %s.',
    'Heruntergeladen: %s (%s)': 'Downloaded: %s (%s)',
    'Kein x_call@*.tgz in der Firmware – nehme die installierte x_call-App (neueres SDK ohne telephony/appstart/map/poi; reicht für die Kit-Apps).': 'No x_call@*.tgz in the firmware – using the installed x_call app (newer SDK without telephony/appstart/map/poi; enough for the kit apps).',
    'Keine x_call-App auf der Unit gefunden. Alternative: Tools › Diagnose › "SDK aus Datei…".': 'No x_call app found on the unit. Alternative: Tools › Diagnostics › "SDK from file…".',
    'Head Unit › package.json': 'Head unit › package.json',
    '%s ist kein gültiges .tgz: %s': '%s is not a valid .tgz: %s',
    'In %s steckt keine javascript/app.js.': 'There is no javascript/app.js in %s.',
    'Entpackt nach %s (app.js: %s)': 'Unpacked to %s (app.js: %s)',
    'Node.js fehlt (nodejs.org, LTS).': 'Node.js is missing (nodejs.org, LTS).',
    'extract_sdk.js ist fehlgeschlagen (Exit %d). Siehe Protokoll.': 'extract_sdk.js failed (exit %d). See the log.',
    'SDK bereit: %s – jetzt kann gebaut werden.': 'SDK ready: %s – you can build now.',
    'Hinweis: oap-sdk ist BMW-Code – nicht veröffentlichen (steht in .gitignore).': 'Note: oap-sdk is BMW code – do not publish it (it is in .gitignore).',
    'x_call@….tgz oder app.js wählen': 'Choose x_call@….tgz or app.js',
    'x_call-Paket oder app.js': 'x_call package or app.js',
    'SDK aus Datei': 'SDK from file',
    'Head Unit › /etc/oap/someip/someip-interfaces.json herunterladen': 'Head unit › download /etc/oap/someip/someip-interfaces.json',
    'Katalog nicht lesbar (Exit %d).': 'Catalog not readable (exit %d).',
    'Die geladene Datei ist kein gültiges JSON: %s': 'The downloaded file is not valid JSON: %s',
    'Katalog gespeichert: %s (%s, %d Interfaces)': 'Catalog saved: %s (%s, %d interfaces)',
    'Erst den SOME/IP-Katalog holen.': 'Get the SOME/IP catalog first.',
    'Python wurde nicht gefunden.': 'Python was not found.',
    '   (optional)': '   (optional)',
    '%d optionale Schnittstelle(n) fehlen auf dieser Unit – unproblematisch, die App wird ohne sie installiert:': '%d optional interface(s) missing on this unit – no problem, the app is installed without them:',
    '  %s ohne %s: %s': '  %s without %s: %s',
    'Alle Signal-Flags passen zum Katalog dieser Unit.': 'All signal flags match the catalog of this unit.',
    '%d Abweichung(en). "r" auf einem Feld ohne Getter → in app.json auf "s" ändern. Eine App, die die Unit ablehnt, lässt deploy-car.sh beim Installieren aus (die anderen werden installiert).': '%d mismatch(es). "r" on a field without a getter → change it to "s" in app.json. An app the unit refuses is left out by deploy-car.sh when installing (the others are installed).',
    'Status %s – %s\n': 'Status %s – %s\n',
    '%d Backup(s) gefunden – unter "Installieren › Zurück auf Serie" auswählbar.': '%d backup(s) found – selectable under "Install › Back to stock".',
    'Head Unit › Logs einsammeln (nur lesen)': 'Head unit › collect logs (read only)',
    'Auf der Unit wurden keine Logs von Custom-Apps gefunden (noch nichts installiert?).': 'No custom app logs were found on the unit (nothing installed yet?).',
    'Logs konnten nicht geladen werden (Exit %d).': 'Logs could not be loaded (exit %d).',
    'Log-Archiv beschädigt: %s': 'Log archive damaged: %s',
    '%d Dateien gespeichert in %s': '%d files saved in %s',
    'Zum Bauen fehlt das BMW-SDK (oap-sdk/).\nSchritt 2 › "SDK holen" (oder Tools › Diagnose › "SDK aus Datei…").': 'Building needs the BMW SDK (oap-sdk/).\nStep 2 › "Get SDK" (or Tools › Diagnostics › "SDK from file…").',
    'Keine App ausgewählt.': 'No app selected.',
    'Node.js fehlt (nodejs.org, LTS) – der Build zeichnet damit die App-Icons.': 'Node.js is missing (nodejs.org, LTS) – the build uses it to draw the app icons.',
    'Doom: der Build lädt beim ersten Mal die Freedoom-Spieldaten (~24 MB) herunter.': 'Doom: the first build downloads the Freedoom game data (~24 MB).',
    'Der Build ist fehlgeschlagen (Exit %d). Siehe Protokoll.': 'The build failed (exit %d). See the log.',
    'Kit fertig: %s (%s) – %s': 'Kit ready: %s (%s) – %s',
    'Die .mgu-Dateien enthalten BMW-Code: nur fürs eigene Auto, nicht veröffentlichen.': 'The .mgu files contain BMW code: only for your own car, do not publish them.',
    'Die Head Unit lehnt alle Apps im Kit ab (siehe Protokoll). Es wurde nichts verändert.': 'The head unit refuses every app in the kit (see the log). Nothing was changed.',
    'Der Trockenlauf meldet ein Problem (siehe Protokoll). Es wurde nichts verändert.': 'The dry run reports a problem (see the log). Nothing was changed.',
    'Trockenlauf OK – die Head Unit ist bereit für "Installieren".': 'Dry run OK – the head unit is ready for "Install".',
    'Trockenlauf OK – mit Hinweisen': 'Dry run OK – with notes',
    'Die Head Unit ist bereit. Diese Apps lehnt sie aber ab (ihre Firmware hat eine benötigte Schnittstelle nicht); "Installieren" lässt sie aus und installiert den Rest:': 'The head unit is ready. But it refuses these apps (its firmware lacks an interface they need); "Install" leaves them out and installs the rest:',
    '✖ %s  (%s) – wird nicht installiert': '✖ %s  (%s) – will not be installed',
    '◐ %s – ohne %s: %s': '◐ %s – without %s: %s',
    'Alles OK. Einzelne Apps laufen auf dieser Unit mit weniger Werten:': 'All OK. Some apps run on this unit with fewer values:',
    'Installation starten?': 'Start installation?',
    'Vorher wird automatisch ein Backup angelegt (das allererste bleibt als "stock"). Die App-Plattform startet neu, die Kacheln erscheinen nach 1–2 Minuten.': 'A backup is made automatically first (the very first one is kept as "stock"). The app platform restarts, the tiles appear after 1–2 minutes.',
    '• Auto geparkt, Zündung an, am besten Ladegerät dran\n• Nicht abbrechen, Zündung anlassen\n• Mit Doom/Controller-Gate startet die Head Unit am Ende\n  einmal neu (Bildschirm ~1 Min. schwarz)': '• Car parked, ignition on, ideally with a charger connected\n• Do not cancel, keep the ignition on\n• With Doom/controller gate the head unit reboots once\n  at the end (screen black for ~1 min)',
    'Erst prüfen?': 'Check first?',
    'Für dieses Kit gab es noch keinen erfolgreichen Trockenlauf. Empfohlen: erst prüfen und nur bei Erfolg installieren.': 'There has been no successful dry run for this kit yet. Recommended: check first and only install if it succeeds.',
    'Direkt installieren': 'Install directly',
    'Prüfen + installieren': 'Check + install',
    '▶ Installieren': '▶ Install',
    'Diese Apps lehnt die Head Unit ab, sie wurden ausgelassen; alle anderen sind installiert:': 'The head unit refuses these apps, they were left out; all others are installed:',
    'Die Head Unit startet jetzt neu (Controller-Gate). Bildschirm ~1 Min. schwarz, danach kommt alles von selbst wieder. Später mit "Status" prüfen.': 'The head unit is rebooting now (controller gate). Screen black for ~1 min, then everything comes back by itself. Check later with "Status".',
    'Installiert – mit Hinweisen': 'Installed – with notes',
    'Die Installation meldet einen Fehler (Exit %d, siehe Protokoll).\nZurück auf Serie geht mit "Wiederherstellen" (stock).': 'The installation reports an error (exit %d, see the log).\nGo back to stock with "Restore" (stock).',
    'Installation fertig. Die Kacheln erscheinen in 1–2 Minuten unter "Apps".': 'Installation done. The tiles appear under "Apps" in 1–2 minutes.',
    'Ungültiger Backup-Name.': 'Invalid backup name.',
    'Backup "%s" wiederherstellen?': 'Restore backup "%s"?',
    'Entfernt alle Custom-Apps, den Hook und den Renderer und setzt jede berührte Datei auf den Stand des Backups zurück. Danach startet %s neu.\n\nZur Bestätigung YES eingeben:': 'Removes all custom apps, the hook and the renderer and puts every touched file back to the state of the backup. Then %s restarts.\n\nType YES to confirm:',
    'die App-Plattform': 'the app platform',
    'das HMI': 'the HMI',
    'Abgebrochen – es wurde nichts geändert (YES nicht eingegeben).': 'Cancelled – nothing was changed (YES was not entered).',
    'Wiederherstellen meldet einen Fehler (Exit %d, siehe Protokoll).': 'Restore reports an error (exit %d, see the log).',
    'Wiederhergestellt (%s). Die Unit ist wieder im Stand des Backups.': 'Restored (%s). The unit is back to the state of the backup.',
    'Verbinden': 'Connect',
    'Laufenden Befehl abbrechen (z. B. journalctl -f, ping, top).': 'Stop the running command (e.g. journalctl -f, ping, top).',
    '●  nicht verbunden': '●  not connected',
    'Befehl eingeben – Enter sendet, ↑/↓ Verlauf, Strg+C bricht ab': 'Type a command – Enter sends, ↑/↓ history, Ctrl+C stops',
    'Senden': 'Send',
    'Trennen': 'Disconnect',
    '●  verbunden  ·  root@%s': '●  connected  ·  root@%s',
    'ssh konnte nicht gestartet werden: %s': 'ssh could not be started: %s',
    '*** verbinde mit root@%s:%s …\n': '*** connecting to root@%s:%s …\n',
    '\n*** Verbindung unterbrochen ***\n': '\n*** Connection interrupted ***\n',
    'Dieser Schnellbefehl startet einen Dienst auf der Head Unit neu (Bildschirm lädt neu).': 'This quick command restarts a service on the head unit (the screen reloads).',
    'Ausführen': 'Run',
    '\n*** Verbindung fehlgeschlagen – Details oben. ***\n': '\n*** Connection failed – details above. ***\n',
    '\n*** Verbindung beendet ***\n': '\n*** Connection closed ***\n',
    'Öffnen': 'Open',
    '↻  Aktualisieren': '↻  Refresh',
    '  Name': '  Name',
    'Größe': 'Size',
    'Typ': 'Type',
    'Noch nicht geladen – "Aktualisieren" klicken (Verbindung nötig).': 'Not loaded yet – click "Refresh" (connection needed).',
    'Herunterladen…': 'Download…',
    'Pfad kopieren': 'Copy path',
    'Löschen…': 'Delete…',
    '⬇  Herunterladen…': '⬇  Download…',
    '⬆  Hochladen…': '⬆  Upload…',
    '+  Neuer Ordner…': '+  New folder…',
    'Löschen': 'Delete',
    'Sicherung einspielen…': 'Restore backup…',
    'Ordner sichern (tar.gz)…': 'Back up folder (tar.gz)…',
    '%s  ·  %d Ordner, %d Dateien': '%s  ·  %d folders, %d files',
    '  ·  %d ausgewählt%s': '  ·  %d selected%s',
    '   —   Doppelklick/Enter öffnet, Rücktaste geht nach oben, Entf löscht, Rechtsklick für mehr.': '   —   Double-click/Enter opens, Backspace goes up, Del deletes, right-click for more.',
    'Head Unit › ls %s': 'Head unit › ls %s',
    'Nicht erreichbar – %s': 'Not reachable – %s',
    'Kein Verzeichnis: %s': 'Not a directory: %s',
    'Lade Verzeichnis …': 'Loading directory …',
    'Verzeichnis lesen': 'Read directory',
    'Nicht verbunden – Schlüssel unter "Installieren" (Schritt 1) laden, dann "Aktualisieren".': 'Not connected – load the key under "Install" (step 1), then "Refresh".',
    'nach oben': 'up',
    'Datei': 'File',
    'Gerät / Spezial': 'Device / special',
    ' (Link)': ' (link)',
    'Ordner ist leer.': 'Folder is empty.',
    '%s – %d Einträge': '%s – %d entries',
    'Pfad kopiert: %s': 'Path copied: %s',
    'Herunterladen': 'Download',
    'Bitte zuerst Dateien oder Ordner in der Liste auswählen.': 'Please select files or folders in the list first.',
    'Diese Einträge werden übersprungen: Geräte/Spezialdateien und Ordner in /proc, /sys, /dev lassen sich nicht sinnvoll kopieren (der Download würde hängen).': 'These entries are skipped: devices/special files and folders in /proc, /sys, /dev cannot be copied sensibly (the download would hang).',
    'Zielordner auf dem PC wählen': 'Choose the target folder on the PC',
    'Head Unit › Ordner %s laden': 'Head unit › load folder %s',
    '✖ %s konnte nicht geladen werden.': '✖ %s could not be loaded.',
    '✖ %s: Archiv-Fehler: %s': '✖ %s: archive error: %s',
    '✔ Ordner geladen: %s': '✔ Folder loaded: %s',
    'Head Unit › %s laden': 'Head unit › load %s',
    '%d von %d Einträgen heruntergeladen.': '%d of %d entries downloaded.',
    'Dateien zum Hochladen wählen': 'Choose files to upload',
    'Überschreiben?': 'Overwrite?',
    'In %s gibt es schon Einträge mit diesem Namen. Überschreiben?': 'There are already entries with this name in %s. Overwrite?',
    'Überschreiben': 'Overwrite',
    'Head Unit › %s hochladen': 'Head unit › upload %s',
    '✖ %s nicht hochgeladen (Exit %d).': '✖ %s not uploaded (exit %d).',
    '✔ hochgeladen: %s': '✔ uploaded: %s',
    '%d von %d Dateien hochgeladen.': '%d of %d files uploaded.',
    'Hochladen': 'Upload',
    'Neuer Ordner': 'New folder',
    'Name des neuen Ordners in %s:': 'Name of the new folder in %s:',
    'Anlegen': 'Create',
    'Ordnername': 'Folder name',
    'Bitte einen einfachen Namen ohne "/" angeben.': 'Please enter a simple name without "/".',
    'Head Unit › mkdir %s': 'Head unit › mkdir %s',
    'Ordner konnte nicht angelegt werden (Exit %d).': 'The folder could not be created (exit %d).',
    '✔ Ordner angelegt: %s': '✔ Folder created: %s',
    'Bitte zuerst etwas in der Liste auswählen.': 'Please select something in the list first.',
    '\n… und %d weitere': '\n… and %d more',
    'Löschen?': 'Delete?',
    'Diese Einträge auf der Head Unit löschen? Ordner werden nur entfernt, wenn sie leer sind; Links werden entfernt, nicht ihr Ziel.': 'Delete these entries on the head unit? Folders are only removed if they are empty; links are removed, not their target.',
    '✖ %s nicht gelöscht%s.': '✖ %s not deleted%s.',
    ' (Ordner nicht leer?)': ' (folder not empty?)',
    '✔ gelöscht: %s': '✔ deleted: %s',
    '%d von %d gelöscht.': '%d of %d deleted.',
    'Ordner sichern': 'Back up folder',
    'Das ganze Dateisystem bzw. /proc, /sys, /dev lässt sich nicht als Ganzes sichern. Bitte einen Unterordner öffnen oder Einträge auswählen.': 'The whole file system or /proc, /sys, /dev cannot be backed up as a whole. Please open a subfolder or select entries.',
    '/proc, /sys und /dev lassen sich nicht sichern.': '/proc, /sys and /dev cannot be backed up.',
    'Sicherung speichern': 'Save backup',
    '%d Eintrag/Einträge aus %s': '%d entry/entries from %s',
    'gesamter Ordner %s': 'whole folder %s',
    'Sichere %s …': 'Backing up %s …',
    'Head Unit › tar (%s)': 'Head unit › tar (%s)',
    'Sicherung fehlgeschlagen (Exit %d).': 'Backup failed (exit %d).',
    '✔ Sicherung: %s (%s)': '✔ Backup: %s (%s)',
    'Sichern': 'Back up',
    'Sicherung (tar.gz) wählen': 'Choose backup (tar.gz)',
    'Sicherung einspielen?': 'Restore backup?',
    'Das Archiv in %s auf der Head Unit entpacken? Vorhandene Dateien mit gleichem Namen werden überschrieben.': 'Unpack the archive into %s on the head unit? Existing files with the same name are overwritten.',
    'Einspielen': 'Restore',
    'Head Unit › tar x -C %s': 'Head unit › tar x -C %s',
    'Einspielen fehlgeschlagen (Exit %d).': 'Restore failed (exit %d).',
    '✔ Sicherung eingespielt nach %s': '✔ Backup restored to %s',
    'Sicherung einspielen': 'Restore backup',
    'PROJEKT': 'PROJECT',
    'Strg+C': 'Ctrl+C',
    'Deutsch': 'German',
    'Bitte warten, bis „%s“ fertig ist – dann die Sprache umschalten.': 'Please wait until "%s" has finished – then switch the language.',
    'Mit der Head Unit verbinden': 'Connect to the head unit',
    'Projekt & SDK': 'Project & SDK',
    'Das Projekt idrive7-apps von GitHub holen und einmalig das SDK aus der Head Unit auslesen (wird zum Bauen gebraucht).': 'Get the idrive7-apps project from GitHub and read the SDK from the head unit once (needed for building).',
    'PC-Check ›': 'PC check ›',
    'Erweiterte Optionen ▸': 'Advanced options ▸',
    'Erweiterte Optionen ▾': 'Advanced options ▾',
    'Apps installieren': 'Install apps',
    'In vier Schritten von der Verbindung bis zu den Apps im Auto.': 'Four steps from the connection to the apps in your car.',
    'Diagnose, Shell, Datei-Browser und PC-Check – für die Installation nicht nötig.': 'Diagnostics, shell, file browser and PC check – not needed for installing.',
    'Diagnose': 'Diagnostics',
    'PC-Check': 'PC check',
    'Alle Backups auf der Unit – danach unter "Installieren › Zurück auf Serie" auswählbar.': 'All backups on the unit – then selectable under "Install › Back to stock".',
    'Diese GUI meldet sich als root an – mit dem Schlüssel, den das separate Tool idrive7-8-ssh-root beim "Enroll" ablegt (data\\enrollments\\<VIN>\\id_rsa + connect.txt). Das Freischalten selbst erledigt dieses Tool; danach unter "Installieren" (Schritt 1) den Schlüssel laden und "Verbindung testen".': 'This GUI logs in as root – with the key the separate tool idrive7-8-ssh-root stores on "Enroll" (data\\enrollments\\<VIN>\\id_rsa + connect.txt). Unlocking the access itself is done by that tool; then load the key under "Install" (step 1) and click "Test connection".',
    'Auf GitHub öffnen ↗': 'Open on GitHub ↗',
    'Das Tool legt den Root-Schlüssel unter data\\enrollments\\<VIN>\\ ab (id_rsa + connect.txt). Die GUI sucht ihn automatisch, oder du wählst die connect.txt selbst – danach weiter mit "Installieren".': 'The tool stores the root key under data\\enrollments\\<VIN>\\ (id_rsa + connect.txt). The GUI finds it automatically, or you choose the connect.txt yourself – then continue with "Install".',
    'Download, Anleitung und Warnhinweise stehen im Repository von cauliflowermossgrape. Kurz: ENET-Kabel stecken, Zündung an, das Tool unter Windows als Administrator starten und einmalig "Flash + Enable" ausführen. Vorher die Codierung der Head Unit sichern (z. B. mit BimmerCode) – siehe README.': 'Download, instructions and warnings are in the repository by cauliflowermossgrape. In short: plug in the ENET cable, ignition on, start the tool as administrator on Windows and run "Flash + Enable" once. Back up the head unit coding first (e.g. with BimmerCode) – see the README.',
    'Einmalig vor der ersten Installation: Root-Zugang (SSH) auf der Head Unit freischalten. Das macht das Original-Tool idrive7-8-ssh-root von cauliflowermossgrape – diese GUI verlinkt es nur.': 'Once before the first installation: unlock root access (SSH) on the head unit. This is done by the original tool idrive7-8-ssh-root by cauliflowermossgrape – this GUI only links to it.',
    'Noch kein Schlüssel geladen.': 'No key loaded yet.',
    'Original-Tool idrive7-8-ssh-root': 'Original tool idrive7-8-ssh-root',
    'Root-Schlüssel aus dem Tool idrive7-8-ssh-root laden (data\\enrollments\\<VIN>\\id_rsa + connect.txt). SSH muss vorher einmalig aktiviert sein (Seite "SSH aktivieren"); ENET-Kabel gesteckt, Zündung an.': 'Load the root key from the tool idrive7-8-ssh-root (data\\enrollments\\<VIN>\\id_rsa + connect.txt). SSH must have been enabled once before ("Enable SSH" page); ENET cable plugged in, ignition on.',
    'SSH aktivieren': 'Enable SSH',
    'SSH aktivieren ›': 'Enable SSH ›',
    'Schlüssel übernehmen': 'Load the key',
    'Weiter zu Installieren ›': 'Continue to Install ›',
    '✔ Schlüssel geladen: %s': '✔ Key loaded: %s',
    '%s – mgu-store braucht Node.js 18 oder neuer (nodejs.org)': '%s – mgu-store needs Node.js 18 or newer (nodejs.org)',
    '--no-renderer – lässt mgu-render weg; der Store zeigt dann einfache Widget-Seiten.': '--no-renderer – leaves out mgu-render; the Store then shows simple widget pages.',
    '--no-uplink – kein Internet über USB-Netzwerkadapter, LTE-Stick oder Handy; Updates dann per USB-Stick (unten).': '--no-uplink – no internet through a USB network adapter, LTE stick or phone; updates then come by USB stick (below).',
    'App Store für die Head Unit': 'App store for the head unit',
    'App Store installieren': 'Install App Store',
    'App Store installieren?': 'Install the App Store?',
    'App Store installiert': 'App Store installed',
    'App Store installiert.': 'App Store installed.',
    'App Store mgu-store © Robert Nisipeanu (robertnisipeanu) – %s (GPL-3.0).': 'App Store mgu-store © Robert Nisipeanu (robertnisipeanu) – %s (GPL-3.0).',
    'App Store © Robert Nisipeanu ↗': 'App Store © Robert Nisipeanu ↗',
    'App Store ↗': 'App Store ↗',
    'Credits für den Code:\n\n• cauliflowermossgrape – die Custom-Apps, das Kit und die Werkzeuge, die diese GUI aufruft (make_kit.py, build.py, extract_sdk.js, check_signals.py, deploy-car.sh, mgu-app-install.sh, GPU-Renderer, Controller-Gate …), sowie das Root-Tool idrive7-8-ssh-root.\n• Robert Nisipeanu (robertnisipeanu) – der App Store mgu-store, zusammen mit IterioDev. Die GUI ruft nur sein Installationsprogramm mgu-install.js auf.\n\nAusgenommen ist nur diese GUI (iDrive 7 Toolkit v%s): ein separates, inoffizielles Zusatzprojekt. Sie ist nicht Teil dieser Projekte und ruft deren Werkzeuge nur auf.': 'Credit for the code:\n\n• cauliflowermossgrape – the custom apps, the kit and the tools this GUI runs (make_kit.py, build.py, extract_sdk.js, check_signals.py, deploy-car.sh, mgu-app-install.sh, GPU renderer, controller gate …), and the root tool idrive7-8-ssh-root.\n• Robert Nisipeanu (robertnisipeanu) – the App Store mgu-store, together with IterioDev. The GUI only runs his installer mgu-install.js.\n\nThe only exception is this GUI (iDrive 7 Toolkit v%s): a separate, unofficial add-on. It is not part of these projects and only runs their tools.',
    'Das Installationsprogramm von mgu-store hat abgelehnt: %s': 'The mgu-store installer refused: %s',
    'Das Installationsprogramm von mgu-store ist fehlgeschlagen (Exit %d): %s': 'The mgu-store installer failed (exit %d): %s',
    'Datei zu klein': 'file too small',
    'Der Store übernimmt dabei Renderer und Controller-Gate einer Kit-Installation.': 'The Store takes over the renderer and controller gate of a kit installation.',
    'Im Auto installieren': 'Install in the car',
    'Installer-Ordner öffnen': 'Open installer folder',
    'Noch kein Schlüssel – Seite "SSH aktivieren"': 'No key yet – "Enable SSH" page',
    'Node.js wurde nicht gefunden. mgu-store braucht Node.js 18 oder neuer (nodejs.org, LTS-Version).': 'Node.js was not found. mgu-store needs Node.js 18 or newer (nodejs.org, LTS version).',
    'Ohne Internet-Uplink': 'Without internet uplink',
    'Ohne Renderer': 'Without renderer',
    'Schlüssel': 'Key',
    'Schreiben': 'Write',
    'USB-Stick (Laufwerk) wählen': 'Choose the USB stick (drive)',
    'USB-Stick fertig: %s': 'USB stick ready: %s',
    'USB-Stick für Updates ohne Internet': 'USB stick for updates without internet',
    'USB-Stick vorbereiten': 'Prepare USB stick',
    'USB-Stick vorbereiten?': 'Prepare the USB stick?',
    'Unter "Installieren" "Verbindung testen"': 'Click "Test connection" under "Install"',
    'Verbindung': 'Connection',
    'Zum Abschluss die Head Unit einmal neu starten (ein normaler Zündungszyklus genügt). Danach startet der Updater und die Kachel "Store" erscheint unter Apps.': 'To finish, restart the head unit once (a normal ignition cycle does it). Then the updater starts and the "Store" tile appears among the apps.',
    'mgu-install.js geladen (%s).': 'mgu-install.js downloaded (%s).',
    'mgu-install.js konnte nicht geladen werden: %s – Internetverbindung?': 'mgu-install.js could not be downloaded: %s – internet connection?',
    'mgu-store braucht Node.js 18 oder neuer – gefunden: %s (nodejs.org, LTS-Version).': 'mgu-store needs Node.js 18 or newer – found: %s (nodejs.org, LTS version).',
    'mgu-store von Robert Nisipeanu (robertnisipeanu): ein App Store direkt im Auto – Apps und Werkzeuge aus GitHub-Repositories, Web-Adressen oder von einem USB-Stick durchsuchen, installieren, aktualisieren und entfernen. idrive7-apps ist dort schon als Quelle eingetragen. Diese GUI ruft nur sein Installationsprogramm mgu-install.js auf; Anleitung und Details stehen im Repository.': 'mgu-store by Robert Nisipeanu (robertnisipeanu): an app store right in the car – browse, install, update and remove apps and tools from GitHub repositories, web addresses or a USB stick. idrive7-apps is already set up as a source. This GUI only runs his installer mgu-install.js; instructions and details are in the repository.',
    'usb-repo ist fehlgeschlagen (Exit %d): %s': 'usb-repo failed (exit %d): %s',
    'Überträgt Store, Updater, Deploy-Tool, App-Privacy, Renderer, Python und Internet-Uplink – mit dem Root-Schlüssel, der IP und dem Port aus "Installieren" (Schritt 1). Braucht Node.js 18+ und Internet am PC. Auto geparkt, stabile Stromversorgung; danach die Head Unit einmal neu starten. Spätere Updates laufen im Store selbst.': 'Transfers the Store, updater, deploy tool, app privacy, renderer, Python and internet uplink – with the root key, IP and port from "Install" (step 1). Needs Node.js 18+ and internet on the PC. Car parked, stable power; then restart the head unit once. Later updates run in the Store itself.',
    'Bisher noch nichts geladen.': 'Nothing downloaded yet.',
    'Die GUI enthält keine Store-Dateien. Bei jedem Klick werden das Installationsprogramm mgu-install.js und die neueste veröffentlichte Store-Version direkt aus robertnisipeanu/mgu-store geladen – der PC braucht dafür Internet.': 'The GUI contains no Store files. Every click downloads the installer mgu-install.js and the newest published Store version straight from robertnisipeanu/mgu-store – the PC needs internet for this.',
    'Die neueste Store-Version wird frisch von GitHub geladen und nach %s geschrieben (Ordner mgu-repo). Andere Dateien auf dem Stick bleiben.': 'The newest Store version is downloaded fresh from GitHub and written to %s (folder mgu-repo). Other files on the stick stay.',
    'Für ein Auto ohne Internet: lädt die neueste Store-Version frisch von GitHub und schreibt sie als lokale Quelle auf einen USB-Stick (usb-repo). Den Stick ins Auto stecken und im Store die Seite "USB" öffnen.': 'For a car without internet: downloads the newest Store version fresh from GitHub and writes it to a USB stick as a local source (usb-repo). Plug the stick into the car and open the "USB" page in the Store.',
    'Immer frisch aus dem GitHub-Repo': 'Always fresh from the GitHub repo',
    'Jetzt frisch von GitHub (robertnisipeanu/mgu-store) laden: das Installationsprogramm mgu-install.js und die neueste veröffentlichte Store-Version. Danach wird alles auf der Head Unit %s installiert.\n\nAuto geparkt, Zündung an, am besten Ladegerät dran – bis das Programm fertig ist. Danach die Head Unit einmal neu starten.': 'Download fresh from GitHub (robertnisipeanu/mgu-store) now: the installer mgu-install.js and the newest published Store version. Then everything is installed on the head unit %s.\n\nCar parked, ignition on, ideally a charger connected – until the installer has finished. Then restart the head unit once.',
    'Laden und installieren': 'Download and install',
    'Quelle: %s – es wird immer die neueste Version geladen, nichts davon steckt in der GUI.': 'Source: %s – the newest version is always downloaded, none of it is built into the GUI.',
    'Von GitHub laden und auf Stick schreiben…': 'Download from GitHub and write to stick…',
    'Von GitHub laden und installieren': 'Download from GitHub and install',
    'Zuletzt geladen: %s (mgu-install.js, %s) – beim nächsten Klick wird wieder neu geladen.': 'Last downloaded: %s (mgu-install.js, %s) – the next click downloads it again.',
    'mgu-store von Robert Nisipeanu ins Auto bringen. Wird bei jedem Installieren frisch aus seinem GitHub-Repo geladen – nichts davon steckt in dieser GUI.': 'Put mgu-store by Robert Nisipeanu on the car. It is downloaded fresh from his GitHub repo on every install – none of it is built into this GUI.',
}


def _ensure_customtkinter():
    """CustomTkinter laden; fehlt es, die Installation per pip anbieten und neu starten."""
    try:
        import customtkinter  # noqa: F401
        try:
            import PIL  # noqa: F401 - für Logo und Icons
        except ImportError:
            try:
                subprocess.run([sys.executable, '-m', 'pip', 'install', '--user', 'pillow'], capture_output=True, timeout=120,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                import importlib
                importlib.invalidate_caches()
            except Exception:  # noqa: BLE001
                pass
        return customtkinter
    except ImportError:
        pass
    from tkinter import messagebox
    r = tk.Tk()
    r.withdraw()
    if FROZEN:
        messagebox.showerror('iDrive 7 Toolkit', tr('CustomTkinter fehlt in dieser .exe. Bitte mit '
                                              '"iDrive7-Toolkit exe bauen.bat" neu bauen.'), parent=r)
        sys.exit(1)
    if not messagebox.askyesno(
            tr('iDrive 7 Toolkit – CustomTkinter fehlt'),
            tr('Die neue Oberfläche braucht das Python-Paket "customtkinter".\n\n'
            'Jetzt installieren?  (pip install --user customtkinter, dauert ca. 10–30 s)'), parent=r):
        sys.exit(1)
    exe = sys.executable
    base = os.path.basename(exe)
    if base.lower().startswith('pythonw'):                    # pip lieber mit python.exe
        alt = os.path.join(os.path.dirname(exe), 'python' + base[7:])
        if os.path.isfile(alt):
            exe = alt
    r.config(cursor='watch')
    r.update()
    try:
        p = subprocess.run([exe, '-m', 'pip', 'install', '--user', '--upgrade', 'customtkinter', 'pillow'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                           creationflags=NOWIN, timeout=600)
        out, ok = p.stdout.decode('utf-8', 'replace'), p.returncode == 0
    except Exception as e:  # noqa: BLE001
        out, ok = str(e), False
    if not ok:
        messagebox.showerror('iDrive 7 Toolkit', tr('Installation fehlgeschlagen:\n\n') + out[-1500:] +
                             tr('\n\nIn einer Eingabeaufforderung:  py -m pip install customtkinter'), parent=r)
        sys.exit(1)
    r.destroy()
    subprocess.Popen([sys.executable] + sys.argv, creationflags=0)   # frisch starten, damit der Pfad stimmt
    sys.exit(0)


ctk = _ensure_customtkinter()


_WHEEL_WIN = sys.platform.startswith('win')
_WHEEL_PX = 60          # Pixel je Rastung des Mausrads (Windows-Standard von customtkinter: 20)


def _fast_wheel():
    """Mausrad in scrollbaren Seiten: ~3x schneller und weich (animiert statt in Sprüngen)."""
    try:
        from customtkinter.windows.widgets import ctk_scrollable_frame as _sf
        orig = _sf.CTkScrollableFrame._mouse_wheel_all

        def tick(self):
            c = self._parent_canvas
            rem = self._sm_t
            if abs(rem) < 1:
                self._sm_t, self._sm_run = 0.0, False
                return
            step = int(rem * 0.3) or (1 if rem > 0 else -1)
            before = c.yview()
            c.yview_scroll(step, 'units')
            if c.yview() == before:                 # Anfang/Ende erreicht
                self._sm_t, self._sm_run = 0.0, False
                return
            self._sm_t -= step
            self.after(10, lambda: tick(self))

        def fast(self, event):
            if not _WHEEL_WIN or getattr(self, '_shift_pressed', False):
                return orig(self, event)
            if not self._check_if_valid_scroll(event.widget) or self._parent_canvas.yview() == (0.0, 1.0):
                return None
            self._sm_t = getattr(self, '_sm_t', 0.0) - event.delta / 120.0 * _WHEEL_PX
            if not getattr(self, '_sm_run', False):
                self._sm_run = True
                tick(self)
            return None
        _sf.CTkScrollableFrame._mouse_wheel_all = fast
    except Exception:  # noqa: BLE001 - andere customtkinter-Version: Standard-Tempo
        pass


_fast_wheel()

APP_TITLE = 'iDrive 7&8 Toolkit'   # tr() erst bei Gebrauch
VERSION = '0.2'
REPO_URL = 'https://github.com/cauliflowermossgrape/idrive7-apps'
ZIP_URL = REPO_URL + '/archive/refs/heads/main.zip'
SSH_TOOL_URL = 'https://github.com/cauliflowermossgrape/idrive7-8-ssh-root'
STORE_URL = 'https://github.com/robertnisipeanu/mgu-store'          # App Store von Robert Nisipeanu
STORE_INSTALLER_URL = 'https://raw.githubusercontent.com/robertnisipeanu/mgu-store/main/deploy/mgu-install.js'
DEFAULT_IP = '169.254.36.47'
DEST = '/var/opt/mgu-custom-apps'          # dort legt push-to-car das Kit ab
# Englisch (Standard) = dist/mgu-custom-apps(.tar.gz); andere Sprachen (make_kit.py --language)
# bekommen ein Suffix, z. B. dist/mgu-custom-apps-zh-CN(.tar.gz). Im Archiv heißt der Ordner immer
# mgu-custom-apps, landet auf der Unit also am selben Ort.
LANG_NAMES = {'en': 'Englisch', 'de': 'Deutsch', 'de-DE': 'Deutsch', 'zh-CN': 'Chinesisch (vereinfacht)',
              'fr': 'Französisch', 'it': 'Italienisch', 'es': 'Spanisch', 'nl': 'Niederländisch'}

# Schnellbefehle im Bereich "Shell" (Bezeichnung, Kommando, verändert etwas?)
SHELL_PRESETS = [
    ('Wer / Uptime', 'id; uname -a; uptime', False),
    ('Speicherplatz', 'df -h / /var/opt /var/opt/hdshare 2>/dev/null', False),
    ('Dienste', 'for s in oapcontroller hmi mgu-renderd; do echo "$s: $(systemctl is-active $s.service 2>/dev/null)"; done', False),
    ('Registrierte Apps', 'cat /var/opt/public/oap/oap_12201/security/uids.txt 2>/dev/null', False),
    ('Kit-Status', 'cd %s 2>/dev/null && sh deploy-car.sh status || echo "noch kein Kit auf der Unit"' % DEST, False),
    ('Prozesse', 'top -b -n 1 2>/dev/null | head -n 20 || ps aux 2>/dev/null | head -n 20', False),
    ('Kernelmeldungen', 'dmesg 2>/dev/null | tail -n 40', False),
    ('Renderer-Log', 'journalctl -u mgu-renderd.service -n 40 --no-pager 2>/dev/null', False),
    ('Neustart App-Plattform', 'systemctl restart oapcontroller.service', True),
    ('Neustart HMI', 'systemctl restart hmi.service', True),
]

# Verzeichnislisting für den Datei-Browser. @DIR@ wird durch den shell-escapten Pfad ersetzt.
# Ausgabe je Eintrag: typ/groesse/link/name  ('/' kommt in keinem Dateinamen vor).
# typ: d = Ordner, f = normale Datei, o = Gerät/FIFO/Socket/kaputter Link (nicht herunterladbar)
LIST_TEMPLATE = '''cd @DIR@ 2>/dev/null || { echo __NODIR__; exit 2; }
pwd
for n in * .*; do
  [ "$n" = "." ] && continue
  [ "$n" = ".." ] && continue
  [ -e "$n" ] || [ -L "$n" ] || continue
  l=0; [ -L "$n" ] && l=1
  if [ -d "$n" ]; then t=d; sz=0
  elif [ -f "$n" ]; then t=f; sz=$(stat -L -c %s "$n" 2>/dev/null || wc -c < "$n" 2>/dev/null || echo 0)
  else t=o; sz=0; fi
  echo "$t/$sz/$l/$n"
done
'''

# Pseudo-Dateisysteme: als Ordner per tar sichern/laden hängt oder liefert Unsinn
VIRTUAL_FS = ('/proc', '/sys', '/dev')

HERE = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
CONFIG_FILE = os.path.join(HERE, 'idrive7_gui.json')     # gemeinsam mit v1
OUT_DIR = os.path.join(HERE, 'ausgelesen')
STORE_DIR = os.path.join(HERE, 'mgu-store')          # dort landet mgu-install.js


class C:
    """Farbpalette (dunkel)."""
    BG = '#0e1014'
    SIDEBAR = '#13161b'
    CARD = '#181c22'
    CARD_HI = '#1e232b'
    BORDER = '#262c35'
    INPUT = '#0f1217'
    TEXT = '#e7eaf0'
    MUTED = '#8a93a3'
    FAINT = '#5d6573'
    ACCENT = '#1c69d4'          # BMW-Blau
    ACCENT_HOVER = '#2c7cf0'
    ACCENT_SOFT = '#16263d'
    ACCENT_TEXT = '#7fb0ff'
    BTN = '#252b34'
    BTN_HOVER = '#2f3743'
    GREEN = '#22c55e'
    GREEN_SOFT = '#12291c'
    GREEN_TEXT = '#4ade80'
    RED = '#d93a3a'
    RED_HOVER = '#ef4f4f'
    RED_SOFT = '#33181b'
    RED_TEXT = '#f87171'
    AMBER = '#f59e0b'
    AMBER_SOFT = '#33270f'
    AMBER_TEXT = '#fbbf24'
    CONSOLE = '#0a0c0f'
    CONSOLE_TEXT = '#d4d9e2'


class Abort(Exception):
    """Fehler, der dem Nutzer als verständliche Meldung gezeigt wird."""


# --------------------------------------------------------------------------- Shell/Pfad-Helfer

_ESC = chr(27)
_ANSI_RE = re.compile(
    _ESC + r'\[[0-9;?=]*[ -/]*[@-~]'                   # CSI (Farben, Cursor)
    + '|' + _ESC + r'\][^' + chr(7) + r']*' + chr(7)   # OSC … BEL
    + '|' + _ESC + r'[=>()][0-9A-Za-z]?'               # einfache Escapes / Zeichensatz
)


def strip_ansi(s):
    """Terminal-Steuersequenzen entfernen, damit die Shell-Ausgabe lesbar bleibt."""
    s = _ANSI_RE.sub('', s)
    s = s.replace('\r\n', '\n').replace('\r', '')
    for ch in (chr(0), chr(7), chr(8)):
        s = s.replace(ch, '')
    return s


def shq(s):
    """POSIX-Shell-Quoting: beliebigen Pfad als genau ein Argument absichern."""
    return "'" + str(s).replace("'", "'" + chr(92) + "''") + "'"


def rjoin(base, name):
    return posixpath.normpath(posixpath.join(base or '/', name))


def rparent(p):
    return posixpath.normpath(posixpath.join(p or '/', '..'))


def is_virtual(path):
    p = posixpath.normpath(path or '/')
    return any(p == v or p.startswith(v + '/') for v in VIRTUAL_FS)


# --------------------------------------------------------------------------- Werkzeuge

def find_ssh():
    """Windows' eigener OpenSSH-Client zuerst (auch aus 32-bit-Python), dann PATH."""
    cands = []
    if IS_WIN:
        root = os.environ.get('SystemRoot', r'C:\Windows')
        cands += [os.path.join(root, 'System32', 'OpenSSH', 'ssh.exe'),
                  os.path.join(root, 'Sysnative', 'OpenSSH', 'ssh.exe')]
    w = shutil.which('ssh')
    if w:
        cands.append(w)
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def null_dev(ssh):
    if IS_WIN and ssh and '\\git\\' not in ssh.lower() and '\\msys' not in ssh.lower():
        return 'NUL'
    return '/dev/null'


def find_python():
    """Der Python-Interpreter für make_kit.py & Co. (nicht pythonw, nicht die .exe)."""
    if not FROZEN:
        exe = sys.executable
        base = os.path.basename(exe)
        if base.lower().startswith('pythonw'):
            alt = os.path.join(os.path.dirname(exe), 'python' + base[7:])
            if os.path.isfile(alt):
                return [alt]
        return [exe]
    for c in (['py', '-3'], ['python'], ['python3']):
        w = shutil.which(c[0])
        if w:
            return [w] + c[1:]
    return None


def tool_version(args):
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=20, creationflags=NOWIN)
        out = r.stdout.decode('utf-8', 'replace').strip()
        return r.returncode == 0, (out.splitlines() or [''])[0]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def fmt_size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0
    return str(n)


def stamp():
    return datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')


def open_path(path):
    try:
        if IS_WIN:
            os.startfile(path)  # noqa: S606
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def repo_ok(path):
    return bool(path) and os.path.isfile(os.path.join(path, 'kit', 'tools', 'make_kit.py'))


def list_apps(repo):
    apps = []
    d = os.path.join(repo, 'apps')
    if not os.path.isdir(d):
        return apps
    for n in sorted(os.listdir(d)):
        f = os.path.join(d, n, 'app.json')
        if not os.path.isfile(f):
            continue
        try:
            with open(f, encoding='utf-8') as fh:
                a = json.load(fh)
            # make_kit.py/build.py erwarten den ORDNERNAMEN unter apps/
            apps.append({'id': n, 'title': a.get('title', n),
                         'version': a.get('version', '?'), 'desc': a.get('description', '')})
        except (OSError, ValueError):
            apps.append({'id': n, 'title': n, 'version': '?', 'desc': ''})
    return sorted(apps, key=lambda a: a['title'].lower())


def kit_name(lang='en'):
    return 'mgu-custom-apps' + ('' if not lang or lang == 'en' else '-' + lang)


def kit_dir(repo, lang='en'):
    return os.path.join(repo, 'dist', kit_name(lang))


def kit_info(repo, lang='en'):
    tar = kit_dir(repo, lang) + '.tar.gz'
    if not os.path.isfile(tar):
        return None
    st = os.stat(tar)
    apps_dir = os.path.join(kit_dir(repo, lang), 'apps')
    apps = sorted(n[:-4] for n in os.listdir(apps_dir) if n.endswith('.mgu')) if os.path.isdir(apps_dir) else []
    return {'path': tar, 'rel': 'dist/%s.tar.gz' % kit_name(lang), 'size': st.st_size, 'mtime': st.st_mtime,
            'apps': apps}


def repo_languages(repo):
    """Sprachen, in denen das Projekt bauen kann (make_kit.py --language). Ältere Stände: nur Englisch."""
    if not os.path.isfile(os.path.join(repo, 'kit', 'tools', 'localize.py')):
        return ['en']
    d = os.path.join(repo, 'kit', 'runtime', 'locales')
    try:
        extra = sorted(n[:-5] for n in os.listdir(d) if n.endswith('.json') and n[:-5] != 'en')
    except OSError:
        extra = []
    return ['en'] + extra


def lang_label(code):
    return tr(LANG_NAMES.get(code, code))


def optional_ifaces(repo, app_id):
    """app.json: Schnittstellen mit "_optional" -> {interface: was die App ohne sie verliert}"""
    try:
        with open(os.path.join(repo, 'apps', app_id, 'app.json'), encoding='utf-8') as fh:
            sig = json.load(fh).get('signals', {})
    except (OSError, ValueError):
        return {}
    return {i: f['_optional'] for i, f in sig.items() if isinstance(f, dict) and f.get('_optional')}


_SKIP_RES = (re.compile(r'left out \(refused by this unit\):\s*(.+)$'),
             re.compile(r'WARNING: (\S+\.mgu) is refused by this unit'),
             re.compile(r'not installing (\S+\.mgu) \(refused by this unit\)'))
_OPT_RE = re.compile(r'WARNING: ([\w-]+): this unit has no (\S+) - installed without it: (.*)$')


def unit_notes(lines):
    """Hinweise von deploy-car.sh / mgu-app-install.sh (neuere Projektstände):
    -> (ausgelassene .mgu-Dateien, [(app, schnittstelle, was fehlt)])"""
    skipped, optional = [], []
    for ln in lines:
        for rx in _SKIP_RES:
            m = rx.search(ln)
            if m:
                for n in m.group(1).split():
                    if n.endswith('.mgu') and n not in skipped:
                        skipped.append(n)
        m = _OPT_RE.search(ln)
        if m and m.groups() not in optional:
            optional.append(m.groups())
    return skipped, optional


def parse_connect(path):
    """connect.txt / metadata.json aus idrive7-8-ssh-root lesen -> {key, ip, port, vin}."""
    info = {}
    d = os.path.dirname(path)
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            txt = fh.read()
    except OSError:
        txt = ''
    for k, rx in (('vin', r'^VIN:\s*(\S+)'), ('ip', r'^Host \(IP\):\s*(\S+)'),
                  ('port', r'^Port:\s*(\d+)'), ('key', r'^Key:\s*(.+?)\s*$')):
        m = re.search(rx, txt, re.M)
        if m:
            info[k] = m.group(1)
    meta = os.path.join(d, 'metadata.json')
    if os.path.isfile(meta):
        try:
            with open(meta, encoding='utf-8') as fh:
                mj = json.load(fh)
            if mj.get('mgu_ip') and not info.get('ip', '').replace('.', '').isdigit():
                info['ip'] = mj['mgu_ip']
            if mj.get('vin') and not info.get('vin'):
                info['vin'] = mj['vin']
        except (OSError, ValueError):
            pass
    if info.get('ip', '').startswith('<'):
        info.pop('ip')
    if info.get('vin', '').lower() == 'unknown':
        info.pop('vin')
    key = info.get('key')
    if not key or not os.path.isfile(key):
        cand = os.path.join(d, 'id_rsa')
        info['key'] = cand if os.path.isfile(cand) else None
    return info


def find_connect_files(max_depth=6, budget=40000):
    """Sucht data/enrollments/<VIN>/connect.txt des SSH-Tools in naheliegenden Ordnern."""
    home = os.path.expanduser('~')
    bases = [HERE, os.path.dirname(HERE)]
    for sub in ('Downloads', 'Desktop', 'Documents', 'Dokumente',
                os.path.join('OneDrive', 'Desktop'), os.path.join('OneDrive', 'Documents'),
                os.path.join('OneDrive', 'Dokumente')):
        bases.append(os.path.join(home, sub))
    skip = {'node_modules', 'appdata', 'windows', 'program files', 'program files (x86)',
            '$recycle.bin', 'system volume information', 'idrive7-apps', '.git', '__pycache__'}
    found, seen = [], set()
    for base in bases:
        if not os.path.isdir(base):
            continue
        base = os.path.abspath(base)
        bdepth = base.rstrip(os.sep).count(os.sep)
        for root, dirs, files in os.walk(base):
            budget -= 1
            if budget <= 0:
                return found
            if root in seen:
                dirs[:] = []
                continue
            seen.add(root)
            if 'connect.txt' in files and 'id_rsa' in files:
                found.append(os.path.join(root, 'connect.txt'))
            depth = root.count(os.sep) - bdepth
            if depth >= max_depth:
                dirs[:] = []
            else:
                dirs[:] = [x for x in dirs if x.lower() not in skip and not x.startswith('.')]
    found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return found


def prepare_key(key):
    """Kopie des Schlüssels, die OpenSSH akzeptiert (Windows: nur der eigene Benutzer darf lesen).

    Windows' ssh verweigert Schlüssel, die andere Benutzer lesen dürfen ("UNPROTECTED PRIVATE
    KEY FILE") - auf Laufwerken wie G:\\ ist das oft so. Die Kopie liegt in %LOCALAPPDATA%.
    """
    with open(key, 'rb') as fh:
        data = fh.read()
    if b'PuTTY-User-Key-File' in data:
        raise Abort(tr('Das ist ein PuTTY-Schlüssel (.ppk). Bitte die Datei "id_rsa" (ohne Endung) '
                    'aus demselben Ordner wählen.'))
    if b'PRIVATE KEY' not in data:
        raise Abort(tr('%s ist kein privater SSH-Schlüssel. Bitte "id_rsa" wählen (nicht .pub).') % key)
    if IS_WIN:
        base = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
        d = os.path.join(base, 'iDrive7-Apps-GUI', 'keys')
    else:
        d = os.path.join(os.path.expanduser('~'), '.cache', 'idrive7-apps-gui')
    dst = os.path.join(d, 'id_rsa_' + hashlib.sha256(data).hexdigest()[:16])
    if not os.path.isfile(dst):
        os.makedirs(d, exist_ok=True)
        with open(dst, 'wb') as fh:
            fh.write(data.replace(b'\r\n', b'\n'))
        if IS_WIN:
            user = os.environ.get('USERNAME', '')
            dom = os.environ.get('USERDOMAIN', '')
            for who in ([dom + '\\' + user] if dom else []) + [user]:
                r = subprocess.run(['icacls', dst, '/inheritance:r', '/grant:r', who + ':F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, creationflags=NOWIN)
                if r.returncode == 0:
                    break
        else:
            os.chmod(dst, 0o600)
    return dst


_ERR_RE = re.compile(r'\b(error|fehler|failed|fehlgeschlagen|refused|denied|fatal)\b|nicht gefunden', re.I)
_WARN_RE = re.compile(r'\b(warning|warnung|warn)\b|^!!', re.I)


def classify(line):
    if _ERR_RE.search(line):
        return 'err'
    if _WARN_RE.search(line):
        return 'warn'
    return None


def ssh_hint(lines):
    t = '\n'.join(lines).lower()
    if 'permission denied' in t:
        return (tr('Die Head Unit lehnt den Schlüssel ab. Ist SSH aktiviert (MGU-SSH-Tool → "Enable") '
                'und ist es der richtige id_rsa?'))
    if 'connection refused' in t:
        return tr('SSH ist auf der Head Unit nicht aktiv. Im MGU-SSH-Tool "Enable" ausführen.')
    if 'timed out' in t or 'no route' in t or 'unreachable' in t or 'could not resolve' in t:
        return (tr('Head Unit nicht erreichbar. ENET-Kabel gesteckt, Zündung an, IP-Adresse richtig? '
                '(Standard: %s)') % DEFAULT_IP)
    if 'unprotected private key' in t or 'bad permissions' in t:
        return tr('Windows-OpenSSH akzeptiert die Rechte der Schlüsseldatei nicht.')
    if 'closed by remote host' in t or 'connection reset' in t or 'broken pipe' in t:
        return tr('Die Verbindung wurde unterbrochen (Head Unit neu gestartet oder eingeschlafen?).')
    return tr('SSH-Verbindung fehlgeschlagen (Details im Protokoll).')


def on_screen(root, x, y):
    """Liegt die gespeicherte Fensterposition auf irgendeinem Monitor? (auch 2./3. Monitor)"""
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes
            f = ctypes.windll.user32.MonitorFromPoint
            f.argtypes = [wintypes.POINT, wintypes.DWORD]
            f.restype = ctypes.c_void_p
            return bool(f(wintypes.POINT(int(x) + 80, int(y) + 20), 0))   # 0 = MONITOR_DEFAULTTONULL
        except Exception:  # noqa: BLE001
            pass
    vw = max(root.winfo_vrootwidth(), root.winfo_screenwidth())
    vh = max(root.winfo_vrootheight(), root.winfo_screenheight())
    return -50 < x < vw - 100 and -10 < y < vh - 100


# --------------------------------------------------------------------------- Skripte für die Unit

INFO_SCRIPT = r'''
echo "== System =="
uname -a
for f in /etc/*version* /etc/*release* /etc/*build*; do
  [ -f "$f" ] || continue
  echo "-- $f"
  head -n 12 "$f"
done 2>/dev/null
V=$(grep -o -i 'vin[^A-Za-z0-9]\{1,4\}[A-HJ-NPR-Z0-9]\{17\}' /var/opt/public/tm/tm_12000/data/prov/ProvSigned.xml 2>/dev/null | grep -o '[A-HJ-NPR-Z0-9]\{17\}$' | head -n 1)
[ -n "$V" ] && echo "VIN (aus Provisioning): $V"
echo
echo "== Laufzeit =="
uptime
echo
echo "== App-Plattform =="
M=/usr/lib/oap-mainapp/package.json
[ -f "$M" ] && sed -n 's/.*"version" *: *"\([^"]*\)".*/MainApp-Version: \1/p' "$M" | head -n 1
[ -x /usr/bin/node ] && echo "Node.js auf der Unit: $(/usr/bin/node --version 2>/dev/null)"
for f in /usr/lib/oap-mainapp/preinstalled_apps/*; do [ -e "$f" ] && echo "  vorinstalliert: ${f##*/}"; done
U=/var/opt/public/oap/oap_12201/security/uids.txt
[ -f "$U" ] && echo "Registrierte OAP-Apps: $(grep -c '=' "$U")"
echo
echo "== Dienste =="
for s in oapcontroller hmi mgu-renderd; do printf '  %-15s %s\n' "$s" "$(systemctl is-active "$s.service" 2>/dev/null)"; done
echo
echo "== Speicher =="
df -h / /var/opt /var/opt/hdshare 2>/dev/null
echo
free 2>/dev/null
echo
echo "== Netzwerk =="
ip -4 -o addr show 2>/dev/null | awk '{print "  " $2 ": " $4}'
echo
echo "== Custom-Apps-Kit (/var/opt/mgu-custom-apps) =="
if [ -d /var/opt/mgu-custom-apps ]; then ls -la /var/opt/mgu-custom-apps; else echo "  noch nicht auf der Unit"; fi
true
'''

LOGS_SCRIPT = r'''
cd / || exit 1
T=/tmp/mgu-gui-logs
rm -rf "$T"; mkdir -p "$T"
L=""
for d in var/opt/public/oap/oap_*; do
  [ -f "$d/source/javascript/kit/app-spec.json" ] || continue
  for f in "$d"/data/*.log "$d"/data/*.log.[0-9]; do [ -f "$f" ] && L="$L $f"; done
done
for f in var/opt/mgu-custom-apps/*.log; do [ -f "$f" ] && L="$L $f"; done
if command -v journalctl >/dev/null 2>&1; then
  for s in mgu-renderd oapcontroller; do
    journalctl -u "$s.service" -n 400 --no-pager > "$T/journal-$s.log" 2>&1 && L="$L ${T#/}/journal-$s.log"
  done
fi
cp var/opt/public/oap/oap_12201/security/uids.txt "$T/uids.txt" 2>/dev/null && L="$L ${T#/}/uids.txt"
[ -n "$L" ] || { echo "NO_LOGS" >&2; exit 3; }
tar czf - $L
rc=$?
rm -rf "$T"
exit $rc
'''


# --------------------------------------------------------------------------- kleine Grafiken (ohne Pillow)

def _rect(img, color, x1, y1, x2, y2):
    if x2 > x1 and y2 > y1:
        img.put(color, to=(int(x1), int(y1), int(x2), int(y2)))


def make_icon(kind, size):
    """16er-Raster-Icons für den Datei-Browser, auf `size` Pixel skaliert."""
    img = tk.PhotoImage(width=size, height=size)
    s = size / 16.0

    def r(color, x1, y1, x2, y2):
        _rect(img, color, round(x1 * s), round(y1 * s), round(x2 * s), round(y2 * s))
    if kind == 'up':
        r('#8a93a3', 7, 3, 9, 13)
        for i in range(4):
            r('#8a93a3', 7 - i - 1, 4 + i, 9 + i + 1, 5 + i)
    elif kind == 'dir':
        r('#c9952e', 1, 3, 7, 5)
        r('#e3ac3f', 1, 5, 15, 14)
        r('#f0c260', 1, 6, 15, 7)
    elif kind == 'other':
        r('#5d6573', 3, 2, 13, 14)
        r('#f87171', 6, 6, 10, 10)
    else:   # file / link
        r('#9aa4b2', 3, 1, 10, 15)
        r('#9aa4b2', 3, 4, 13, 15)
        r('#cdd3dc', 10, 1, 13, 4)
        r('#6b7484', 5, 7, 11, 8)
        r('#6b7484', 5, 10, 11, 11)
        if kind == 'link':
            r('#1c69d4', 8, 9, 16, 16)
            r('#ffffff', 10, 12, 14, 13)
            r('#ffffff', 13, 11, 14, 14)
    return img


def make_app_icon(size=64):
    """Fenster-Icon: blaues abgerundetes Quadrat mit einer weißen 7."""
    img = tk.PhotoImage(width=size, height=size)
    s = size / 64.0
    rad = int(12 * s)
    for y in range(size):
        inset = 0
        if y < rad:
            inset = rad - int((rad ** 2 - (rad - y) ** 2) ** 0.5)
        elif y >= size - rad:
            inset = rad - int((rad ** 2 - (y - (size - rad - 1)) ** 2) ** 0.5)
        _rect(img, C.ACCENT, inset, y, size - inset, y + 1)
    _rect(img, '#ffffff', 18 * s, 15 * s, 46 * s, 23 * s)          # Querbalken der 7
    for yy in range(int(23 * s), int(50 * s)):
        cx = 46 * s - (yy - 23 * s) * (16.0 / 27.0)
        _rect(img, '#ffffff', cx - 8 * s, yy, cx, yy + 1)
    return img


# --------------------------------------------------------------------------- Tooltip

class Tooltip:
    def __init__(self, app, widget, text):
        self.app, self.widget, self.text = app, widget, text
        self.tip = None
        self.job = None
        for w in [widget] + list(widget.winfo_children()):
            tk.Misc.bind(w, '<Enter>', self._enter, '+')
            tk.Misc.bind(w, '<Leave>', self._leave, '+')

    def _enter(self, _e=None):
        if self.job is None and self.tip is None and self.text:
            self.job = self.widget.after(450, self._show)

    def _leave(self, _e=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def _show(self):
        self.job = None
        try:
            x = self.widget.winfo_pointerx() + 14
            y = self.widget.winfo_pointery() + 18
        except tk.TclError:
            return
        self.tip = t = tk.Toplevel(self.widget)
        t.wm_overrideredirect(True)
        t.configure(bg=C.BORDER)
        tk.Label(t, text=_cur(self.text), bg=C.CARD_HI, fg=C.TEXT, justify='left', wraplength=self.app.px(360),
                 font=self.app.tkf_small, padx=10, pady=6).pack(padx=1, pady=1)
        t.wm_geometry('+%d+%d' % (x, y))


# --------------------------------------------------------------------------- Dialog

class Dialog(ctk.CTkToplevel):
    """Dunkler, modaler Dialog statt der grauen Windows-Messageboxen.

    buttons: Liste (Text, Rückgabewert, Art) – Art: primary / secondary / danger.
    entry:   Platzhaltertext -> Dialog hat ein Eingabefeld; Rückgabe ist dann (Wert, Eingabe).
    """
    ICONS = {'info': ('i', C.ACCENT_SOFT, C.ACCENT_TEXT), 'question': ('?', C.ACCENT_SOFT, C.ACCENT_TEXT),
             'warn': ('!', C.AMBER_SOFT, C.AMBER_TEXT), 'error': ('✕', C.RED_SOFT, C.RED_TEXT),
             'danger': ('!', C.RED_SOFT, C.RED_TEXT), 'ok': ('✓', C.GREEN_SOFT, C.GREEN_TEXT)}

    def __init__(self, app, title, message, buttons, kind='info', entry=None, detail=None):
        super().__init__(app.root, fg_color=C.CARD)
        self.app = app
        self.result = None
        self._entry = None
        self._iconbitmap_method_called = True       # Icon des Hauptfensters behalten
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(app.root)
        glyph, bg, fg = self.ICONS.get(kind, self.ICONS['info'])
        body = ctk.CTkFrame(self, fg_color='transparent')
        body.pack(fill='both', expand=True, padx=22, pady=(20, 10))
        app.badge(body, glyph, bg, fg, size=40, bg_parent=C.CARD, font_px=17).grid(
            row=0, column=0, rowspan=3, sticky='n', padx=(0, 16))
        ctk.CTkLabel(body, text=title, font=app.f_h2, text_color=C.TEXT, anchor='w'
                     ).grid(row=0, column=1, sticky='w')
        ctk.CTkLabel(body, text=message, font=app.f_body, text_color=C.MUTED, anchor='w', justify='left',
                     wraplength=440).grid(row=1, column=1, sticky='w', pady=(6, 0))
        if detail:
            box = ctk.CTkFrame(body, fg_color=C.INPUT, corner_radius=8)
            box.grid(row=2, column=1, sticky='ew', pady=(10, 0))
            ctk.CTkLabel(box, text=detail, font=app.f_mono, text_color=C.TEXT, anchor='w', justify='left',
                         wraplength=420).pack(fill='x', padx=10, pady=8)
        if entry is not None:
            self._entry = ctk.CTkEntry(body, placeholder_text=entry, width=260, height=34, font=app.f_mono,
                                       fg_color=C.INPUT, border_color=C.BORDER, text_color=C.TEXT)
            self._entry.grid(row=3, column=1, sticky='w', pady=(12, 0))
        bar = ctk.CTkFrame(self, fg_color=C.CARD_HI, corner_radius=0)
        bar.pack(fill='x', side='bottom')
        inner = ctk.CTkFrame(bar, fg_color='transparent')
        inner.pack(side='right', padx=16, pady=12)
        default = cancel = None
        for text, value, style in buttons:
            b = app.button(inner, text, lambda v=value: self._close(v), style, action=False)
            b.pack(side='left', padx=(8, 0))
            if style in ('primary', 'danger'):
                default = value
            if value in (None, False) and cancel is None:
                cancel = value                       # Esc / Fenster schließen = "Abbrechen"
        if default is None and buttons:
            default = buttons[-1][1]
        self.bind('<Return>', lambda e: self._close(default))
        self.bind('<Escape>', lambda e: self._close(cancel))
        self.protocol('WM_DELETE_WINDOW', lambda: self._close(cancel))
        self._place()

    def _place(self):
        self.update_idletasks()
        root = self.app.root
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - w) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - h) // 3)
        tk.Wm.wm_geometry(self, '+%d+%d' % (x, y))
        self.deiconify()
        self.lift()
        try:
            self.attributes('-topmost', True)
            self.after(150, lambda: self.attributes('-topmost', False))
        except tk.TclError:
            pass
        self.after(30, self._grab)

    def _grab(self):
        try:
            self.grab_set()
            (self._entry or self).focus_force()
        except tk.TclError:
            self.after(50, self._grab)

    def _close(self, value):
        self.result = value
        if self._entry is not None:
            self.result = (value, self._entry.get())
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def run(self):
        self.app.root.wait_window(self)
        return self.result


# =========================================================================== Hauptfenster

NAV = (('id8', 'iDrive 8 – Ghid', 'id8'), ('ssh', 'SSH aktivieren', 'ssh'), ('install', 'Installieren', 'install'),
       ('tools', 'Tools', 'tools'))
PAGES_TOP = ('id8', 'ssh', 'install', 'store')            # Seiten ohne Reiter
TOOL_TABS = (('diag', 'Diagnose'), ('shell', 'Shell'), ('files', 'Dateien'), ('system', 'PC-Check'))
OLD_PAGES = {'conn': 'install', 'flash': 'install', 'read': 'diag'}   # Seitennamen älterer Versionen


class App:
    ACTION_BUSY_CRITICAL = ('Installieren', 'Wiederherstellen', 'Prüfen und installieren', 'Flash + Enable', 'Instalez mgu-deploy',
                            'Instalez loggerul bclog8', 'Dezinstalez loggerul', 'Repornesc unitatea')   # tr() beim Vergleich

    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self.cancel_flag = False
        self.proc = None
        self.job_title = ''
        self.action_widgets = []
        self.app_vars = {}
        self._app_sig = None
        self.checked_kit = None          # (kit-mtime, ip) des letzten erfolgreichen Trockenlaufs
        self.checked_notes = ([], [])    # (ausgelassene Apps, fehlende optionale Schnittstellen)
        self._notice = None              # Hinweis, der nach einem erfolgreichen Job erscheint
        self._titles = {}
        self._langs = ['en']
        self._ui_lang = UI_LANG
        self.cfg = self.load_config()
        self.shell_proc = None           # laufende interaktive Shell (ssh -tt)
        self.shell_connected = False
        self.sh_hist = []
        self.sh_hist_i = 0
        self.cur_path = self.cfg.get('rpath', '/') or '/'   # Datei-Browser
        self._files_inited = False
        self._entries = {}               # Datei-Browser: Name -> (typ, groesse, link)
        self._tree_meta = {}
        self._wrap = []
        self.page = None
        self._log_h = dict(self.cfg.get('v2_log') or {})
        self._log_open = True
        self._conn = ('unknown', None)
        self._files_last = None
        self._installed = None           # (kit-mtime, ip) der letzten erfolgreichen Installation
        self._tool_tab = None

        self.setup_fonts()
        root.title(APP_TITLE)
        root.configure(fg_color=C.BG)
        try:
            root._iconbitmap_method_called = True        # eigenes Icon statt des CTk-Logos
            self._icons_app = [make_app_icon(64), make_app_icon(32)]
            root.iconphoto(True, *self._icons_app)
        except Exception:  # noqa: BLE001
            pass
        self.place_window()
        root.protocol('WM_DELETE_WINDOW', self.on_close)

        self.build_ui()
        self.load_vars()
        self.refresh_project()
        self.show_page(self.cfg.get('v2_page') or 'id8')
        self.root.after(60, self.poll)
        self.root.after(200, self.check_tools)
        if not self.v_key.get():
            self.root.after(400, lambda: self.start(tr('Schlüssel suchen'), self.job_find_key, quiet_fail=True))

    # ------------------------------------------------------------------ Grundlagen Darstellung
    def setup_fonts(self):
        fams = set(tkfont.families(self.root))
        if IS_WIN:
            fam = 'Segoe UI Variable Text' if 'Segoe UI Variable Text' in fams else 'Segoe UI'
            disp = 'Segoe UI Variable Display' if 'Segoe UI Variable Display' in fams else 'Segoe UI Semibold'
            mono = next((m for m in ('Cascadia Mono', 'Cascadia Code', 'Consolas') if m in fams), 'Courier New')
        else:
            fam = next((f for f in ('Inter', 'Segoe UI', 'Roboto', 'DejaVu Sans') if f in fams), 'TkDefaultFont')
            disp = fam
            mono = next((m for m in ('JetBrains Mono', 'DejaVu Sans Mono', 'Liberation Mono') if m in fams), 'TkFixedFont')
        self.fam, self.fam_mono = fam, mono
        self.f_h1 = ctk.CTkFont(disp, 24, 'bold')
        self.f_h2 = ctk.CTkFont(fam, 15, 'bold')
        self.f_body = ctk.CTkFont(fam, 13)
        self.f_bold = ctk.CTkFont(fam, 13, 'bold')
        self.f_small = ctk.CTkFont(fam, 12)
        self.f_tiny = ctk.CTkFont(fam, 11)
        self.f_nav = ctk.CTkFont(fam, 14)
        self.f_mono = ctk.CTkFont(mono, 12)
        self.f_glyph = ctk.CTkFont(fam, 16)
        self.tkf_small = tkfont.Font(self.root, family=fam, size=9)
        self.tkf_tree = tkfont.Font(self.root, family=fam, size=10)
        self.tkf_tree_b = tkfont.Font(self.root, family=fam, size=10, weight='bold')

    def scale(self):
        try:
            return ctk.ScalingTracker.get_widget_scaling(self.root)
        except Exception:  # noqa: BLE001
            return 1.0

    def px(self, v):
        return int(v * self.scale())

    def place_window(self):
        root = self.root
        ws = 1.0
        try:
            ws = ctk.ScalingTracker.get_window_scaling(root)
        except Exception:  # noqa: BLE001
            pass
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        lw, lh = int(sw / ws), int(sh / ws)
        w, h = min(1240, lw - 40), min(880, lh - 80)
        m = re.match(r'^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$', self.cfg.get('v2_geometry') or '')
        if m and on_screen(root, int(m.group(3)), int(m.group(4))):
            w, h = min(int(m.group(1)), lw), min(int(m.group(2)), lh - 40)
            x, y = int(m.group(3)), int(m.group(4))
        else:
            x, y = max(0, (sw - int(w * ws)) // 2), max(0, (sh - int(h * ws)) // 3)
        root.geometry('%dx%d+%d+%d' % (w, h, x, y))
        root.minsize(min(940, lw - 40), min(620, lh - 80))
        if IS_WIN and self.cfg.get('v2_zoomed'):
            root.after(80, lambda: root.state('zoomed'))

    # ------------------------------------------------------------------ Widget-Bausteine
    def button(self, parent, text, cmd, style='secondary', width=None, action=True, height=34, **kw):
        colors = {
            'primary': dict(fg_color=C.ACCENT, hover_color=C.ACCENT_HOVER, text_color='#ffffff',
                            text_color_disabled='#9fb6d8'),
            'secondary': dict(fg_color=C.BTN, hover_color=C.BTN_HOVER, text_color=C.TEXT,
                              text_color_disabled=C.FAINT),
            'danger': dict(fg_color=C.RED, hover_color=C.RED_HOVER, text_color='#ffffff',
                           text_color_disabled='#e3a4a4'),
            'ghost': dict(fg_color='transparent', hover_color=C.BTN, text_color=C.MUTED,
                          text_color_disabled=C.FAINT),
            'chip': dict(fg_color=C.CARD_HI, hover_color=C.BTN_HOVER, text_color=C.TEXT,
                         text_color_disabled=C.FAINT, border_width=1, border_color=C.BORDER),
        }[style]
        font = self.f_bold if style in ('primary', 'danger') else self.f_body
        b = ctk.CTkButton(parent, text=text, command=cmd, height=height, corner_radius=8, font=font,
                          **colors, **({'width': width} if width else {}), **kw)
        if action:
            self.action_widgets.append(b)
        return b

    def label(self, parent, text='', kind='body', wrap=None, **kw):
        font, color = {
            'body': (self.f_body, C.TEXT), 'muted': (self.f_body, C.MUTED), 'small': (self.f_small, C.MUTED),
            'bold': (self.f_bold, C.TEXT), 'h2': (self.f_h2, C.TEXT), 'h1': (self.f_h1, C.TEXT),
            'mono': (self.f_mono, C.TEXT), 'tiny': (self.f_tiny, C.FAINT),
        }[kind]
        lb = ctk.CTkLabel(parent, text=text, font=font, text_color=kw.pop('text_color', color), anchor='w',
                          justify='left', **kw)
        if wrap:
            self.autowrap(lb, parent, wrap)
        return lb

    def autowrap(self, label, container=None, margin=0):
        """Text an die tatsächliche Breite des Labels anpassen (Label muss mit sticky='ew' liegen)."""
        label.configure(wraplength=560)

        def upd(e, lb=label):
            w = int(e.width / max(self.scale(), 0.1)) - 6
            if w > 80 and abs((getattr(lb, '_wl', 0) or 0) - w) > 4:
                lb._wl = w
                lb.configure(wraplength=w)
        tk.Misc.bind(label, '<Configure>', upd, '+')

    def chip(self, parent, text='', state='neutral'):
        lb = ctk.CTkLabel(parent, text=text, font=self.f_small, corner_radius=10, height=24)
        self.set_chip(lb, text, state)
        return lb

    def set_chip(self, lb, text, state):
        bg, fg = {'ok': (C.GREEN_SOFT, C.GREEN_TEXT), 'bad': (C.RED_SOFT, C.RED_TEXT),
                  'warn': (C.AMBER_SOFT, C.AMBER_TEXT), 'info': (C.ACCENT_SOFT, C.ACCENT_TEXT),
                  'neutral': (C.CARD_HI, C.MUTED)}[state]
        lb.configure(text='  %s  ' % text, fg_color=bg, text_color=fg)

    def badge(self, parent, text, bg, fg, size=28, bg_parent=C.CARD, font_px=13):
        """Echter Kreis mit Text (CTkLabel wird bei kleinem Radius zur Pille)."""
        s = self.px(size)
        cv = tk.Canvas(parent, width=s, height=s, bg=bg_parent, highlightthickness=0, bd=0)
        cv._oval = cv.create_oval(0, 0, s - 1, s - 1, fill=bg, outline=bg)
        f = tkfont.Font(self.root, family=self.fam, size=-self.px(font_px), weight='bold')
        cv._txt = cv.create_text(s / 2, s / 2, text=text, fill=fg, font=f)
        cv._font = f
        return cv

    def card(self, parent, title=None, subtitle=None, badge=None, tone=None):
        """Abgerundete Karte. -> body (Frame für den Inhalt)."""
        border = {'danger': '#3d2226', 'accent': '#1f3554'}.get(tone, C.BORDER)
        fr = ctk.CTkFrame(parent, fg_color=C.CARD, corner_radius=14, border_width=1, border_color=border)
        fr.columnconfigure(0, weight=1)
        r = 0
        if title:
            head = ctk.CTkFrame(fr, fg_color='transparent')
            head.grid(row=0, column=0, sticky='ew', padx=20, pady=(16, 0))
            head.columnconfigure(1, weight=1)
            if badge:
                bg, fg = (C.RED_SOFT, C.RED_TEXT) if tone == 'danger' else (C.ACCENT_SOFT, C.ACCENT_TEXT)
                fr.badge = self.badge(head, badge, bg, fg)
                fr.badge.grid(row=0, column=0, rowspan=2, padx=(0, 12), sticky='n')
            self.label(head, title, 'h2').grid(row=0, column=1, sticky='w')
            if subtitle:
                self.label(head, subtitle, 'small', wrap=True).grid(row=1, column=1, sticky='ew', pady=(2, 0))
            r = 1
        body = ctk.CTkFrame(fr, fg_color='transparent')
        body.grid(row=r, column=0, sticky='nsew', padx=20, pady=(12 if title else 16, 18))
        body.columnconfigure(0, weight=1)
        fr.body = body
        return fr

    def entry(self, parent, var, width=None, mono=False, **kw):
        return ctk.CTkEntry(parent, textvariable=var, height=34, corner_radius=8, fg_color=C.INPUT,
                            border_color=C.BORDER, border_width=1, text_color=C.TEXT,
                            font=self.f_mono if mono else self.f_body, **({'width': width} if width else {}), **kw)

    def switch(self, parent, var, title, desc):
        fr = ctk.CTkFrame(parent, fg_color='transparent')
        fr.columnconfigure(1, weight=1)
        ctk.CTkSwitch(fr, text='', variable=var, width=46, switch_width=40, switch_height=20,
                      progress_color=C.ACCENT, button_color='#e7eaf0', button_hover_color='#ffffff',
                      fg_color=C.BTN, command=self.on_option).grid(row=0, column=0, rowspan=2, sticky='n', pady=(2, 0))
        self.label(fr, title, 'bold').grid(row=0, column=1, sticky='w')
        self.label(fr, desc, 'small', wrap=True).grid(row=1, column=1, sticky='ew')
        return fr

    # ------------------------------------------------------------------ Dialoge
    def alert(self, title, msg, kind='info', detail=None):
        Dialog(self, title, msg, [('OK', True, 'primary')], kind, detail=detail).run()

    def ask(self, title, msg, yes='OK', no=None, kind='question', danger=False, detail=None):
        no = no or tr('Abbrechen')
        return bool(Dialog(self, title, msg, [(no, False, 'secondary'), (yes, True, 'danger' if danger else 'primary')],
                           kind, detail=detail).run())

    # ------------------------------------------------------------------ Aufbau
    def build_ui(self):
        r = self.root
        r.columnconfigure(1, weight=1)
        r.rowconfigure(0, weight=1)
        self.build_sidebar()

        main = ctk.CTkFrame(r, fg_color=C.BG, corner_radius=0)
        main.grid(row=0, column=1, sticky='nsew')
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        self.pw = tk.PanedWindow(main, orient='vertical', bg=C.BG, sashwidth=self.px(7), bd=0,
                                 sashrelief='flat', opaqueresize=True, showhandle=False, sashcursor='sb_v_double_arrow')
        self.pw.grid(row=0, column=0, sticky='nsew')
        self.pages_host = ctk.CTkFrame(self.pw, fg_color=C.BG, corner_radius=0)
        self.pages_host.columnconfigure(0, weight=1)
        self.pages_host.rowconfigure(0, weight=1)
        self.log_panel = ctk.CTkFrame(self.pw, fg_color=C.BG, corner_radius=0)
        self.pw.add(self.pages_host, stretch='always', minsize=self.px(240))
        self.pw.add(self.log_panel, stretch='never', minsize=self.px(48))
        tk.Misc.bind(self.pw, '<ButtonRelease-1>', self._sash_moved, '+')

        self.pages = {}
        self.page_content = {}
        self.tool_frames = {}
        # Seite "Installieren": nur, was man zum Installieren der Apps im Auto braucht
        pg, content = self._page_frame(tr('Apps installieren'),
                                       tr('In vier Schritten von der Verbindung bis zu den Apps im Auto.'))
        content = ctk.CTkScrollableFrame(pg, fg_color=C.BG, corner_radius=0, scrollbar_button_color=C.BTN,
                                         scrollbar_button_hover_color=C.BTN_HOVER)
        content.grid(row=2, column=0, sticky='nsew', padx=(16, 6), pady=(0, 6))
        content.columnconfigure(0, weight=1)
        self.build_page_install(content)
        self.pages['install'] = pg
        self.page_content['install'] = content
        # Seite "SSH aktivieren": einmalig, deshalb über "Installieren" - verweist nur auf das Original-Tool
        pg, _head = self._page_frame(tr('SSH aktivieren'),
                                     tr('Einmalig vor der ersten Installation: Root-Zugang (SSH) auf der Head Unit '
                                        'freischalten. Das macht das Original-Tool idrive7-8-ssh-root von '
                                        'cauliflowermossgrape – diese GUI verlinkt es nur.'))
        content = ctk.CTkScrollableFrame(pg, fg_color=C.BG, corner_radius=0, scrollbar_button_color=C.BTN,
                                         scrollbar_button_hover_color=C.BTN_HOVER)
        content.grid(row=2, column=0, sticky='nsew', padx=(16, 6), pady=(0, 6))
        content.columnconfigure(0, weight=1)
        self.build_page_ssh(content)
        self.pages['ssh'] = pg
        self.page_content['ssh'] = content
        # Seite "iDrive 8 – Ghid" (id8_wizard.py)
        pg, _head = self._page_frame('iDrive 8 – Ghid',
                                     'Pas cu pas pentru iDrive 8: laptopul rulează comenzile în locul tău. Ai nevoie doar de cablul ENET și de ce scrie la fiecare pas.')
        content = ctk.CTkScrollableFrame(pg, fg_color=C.BG, corner_radius=0, scrollbar_button_color=C.BTN,
                                         scrollbar_button_hover_color=C.BTN_HOVER)
        content.grid(row=2, column=0, sticky='nsew', padx=(16, 6), pady=(0, 6))
        content.columnconfigure(0, weight=1)
        self.build_page_id8(content)
        self.pages['id8'] = pg
        self.page_content['id8'] = content
        # Seite "App Store": mgu-store von robertnisipeanu
        pg, _head = self._page_frame('App Store',
                                     tr('mgu-store von Robert Nisipeanu ins Auto bringen. Wird bei jedem Installieren '
                                        'frisch aus seinem GitHub-Repo geladen – nichts davon steckt in dieser GUI.'))
        content = ctk.CTkScrollableFrame(pg, fg_color=C.BG, corner_radius=0, scrollbar_button_color=C.BTN,
                                         scrollbar_button_hover_color=C.BTN_HOVER)
        content.grid(row=2, column=0, sticky='nsew', padx=(16, 6), pady=(0, 6))
        content.columnconfigure(0, weight=1)
        self.build_page_store(content)
        self.pages['store'] = pg
        self.page_content['store'] = content
        # Seite "Tools": Diagnose, Shell, Dateien, PC-Check
        pg, head = self._page_frame('Tools', tr('Diagnose, Shell, Datei-Browser und PC-Check – für die Installation '
                                                'nicht nötig.'))
        self.seg_tools = ctk.CTkSegmentedButton(pg, values=[tr(l) for _k, l in TOOL_TABS], height=34, font=self.f_body,
                                                width=480, dynamic_resizing=False,
                                                fg_color=C.CARD, selected_color=C.ACCENT,
                                                selected_hover_color=C.ACCENT_HOVER, unselected_color=C.CARD,
                                                unselected_hover_color=C.BTN_HOVER, text_color=C.TEXT,
                                                command=self._on_tool_tab)
        self.seg_tools.grid(row=1, column=0, sticky='w', padx=28, pady=(0, 12))
        host = ctk.CTkFrame(pg, fg_color='transparent')
        host.grid(row=2, column=0, sticky='nsew')
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        builders = {'diag': self.build_tool_diag, 'shell': self.build_page_shell, 'files': self.build_page_files,
                    'system': self.build_tool_system}
        for key, _label in TOOL_TABS:
            fr = ctk.CTkFrame(host, fg_color='transparent')
            fr.columnconfigure(0, weight=1)
            fr.rowconfigure(0, weight=1)
            if key in ('shell', 'files'):
                content = ctk.CTkFrame(fr, fg_color='transparent')
                content.grid(row=0, column=0, sticky='nsew', padx=28, pady=(0, 12))
            else:
                content = ctk.CTkScrollableFrame(fr, fg_color=C.BG, corner_radius=0, scrollbar_button_color=C.BTN,
                                                 scrollbar_button_hover_color=C.BTN_HOVER)
                content.grid(row=0, column=0, sticky='nsew', padx=(16, 6), pady=(0, 6))
            content.columnconfigure(0, weight=1)
            builders[key](content)
            self.tool_frames[key] = fr
            self.page_content[key] = content
        self.pages['tools'] = pg
        self.build_log(self.log_panel)
        for i, k in enumerate(list(PAGES_TOP) + [t[0] for t in TOOL_TABS], start=1):
            r.bind('<Control-Key-%d>' % i, lambda e, k=k: self.show_page(k))

    def _page_frame(self, title, sub):
        """Seite mit Überschrift. -> (Seite, Kopf); Inhalt kommt in Zeile 1/2 der Seite."""
        pg = ctk.CTkFrame(self.pages_host, fg_color=C.BG, corner_radius=0)
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)
        head = ctk.CTkFrame(pg, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew', padx=28, pady=(22, 10))
        head.columnconfigure(0, weight=1)
        self.label(head, title, 'h1').grid(row=0, column=0, sticky='w')
        self.label(head, sub, 'muted', wrap=True).grid(row=1, column=0, sticky='ew', pady=(2, 0))
        return pg, head

    def _on_tool_tab(self, label):
        key = next((k for k, l in TOOL_TABS if tr(l) == label), 'diag')
        self.show_page(key)

    def nav_icon(self, kind, color='#9aa4b5', px=22):
        """Einheitliche Linien-Icons (gleiche Größe, Strichstärke, Farbe) statt Unicode-Zeichen."""
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return None
        S = 176
        im = Image.new('RGBA', (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        w, c = 14, color
        if kind == 'id8':           # zwei Ringe = die Ziffer 8
            d.ellipse((46, 14, 130, 82), outline=c, width=w)
            d.ellipse((38, 82, 138, 162), outline=c, width=w)
        elif kind == 'ssh':         # Schlüssel
            d.ellipse((14, 56, 84, 126), outline=c, width=w)
            d.line((84, 91, 162, 91), fill=c, width=w)
            d.line((130, 91, 130, 130), fill=c, width=w)
            d.line((152, 91, 152, 120), fill=c, width=w)
        elif kind == 'install':     # Pfeil nach unten in eine Ablage
            d.line((88, 14, 88, 108), fill=c, width=w)
            d.line((46, 72, 88, 114), fill=c, width=w)
            d.line((130, 72, 88, 114), fill=c, width=w)
            d.line((22, 136, 22, 162), fill=c, width=w)
            d.line((22, 162, 154, 162), fill=c, width=w)
            d.line((154, 136, 154, 162), fill=c, width=w)
        else:                       # Zahnrad
            import math
            cx = cy = S // 2
            d.ellipse((cx - 50, cy - 50, cx + 50, cy + 50), outline=c, width=w)
            d.ellipse((cx - 18, cy - 18, cx + 18, cy + 18), outline=c, width=w)
            for k in range(8):
                a = k * math.pi / 4
                d.line((cx + 58 * math.cos(a), cy + 58 * math.sin(a), cx + 78 * math.cos(a), cy + 78 * math.sin(a)),
                       fill=c, width=w + 4)
        img = ctk.CTkImage(light_image=im, dark_image=im, size=(px, px))
        self._nav_imgs = getattr(self, '_nav_imgs', [])
        self._nav_imgs.append(img)
        return img

    def build_sidebar(self):
        sb = ctk.CTkFrame(self.root, fg_color=C.SIDEBAR, corner_radius=0, width=236)
        sb.grid(row=0, column=0, sticky='nsw')
        sb.grid_propagate(False)
        sb.columnconfigure(0, weight=1)
        sb.rowconfigure(2, weight=1)
        brand = ctk.CTkFrame(sb, fg_color='transparent')
        brand.grid(row=0, column=0, sticky='ew', padx=18, pady=(22, 18))
        try:
            from PIL import Image
            lg = Image.open(os.path.join(HERE, 'overboost_logo.png')).convert('RGBA')
            w_ = 188
            self._logo_img = ctk.CTkImage(light_image=lg, dark_image=lg, size=(w_, int(w_ * lg.height / lg.width)))
            ctk.CTkLabel(brand, text='', image=self._logo_img).grid(row=0, column=0, sticky='w')
        except Exception:  # noqa: BLE001 - Logo fehlt: nur der Name
            self.label(brand, 'iDrive 7&8 Toolkit', 'h2').grid(row=0, column=0, sticky='w')

        nav = ctk.CTkFrame(sb, fg_color='transparent')
        nav.grid(row=1, column=0, sticky='ew', padx=12)
        nav.columnconfigure(1, weight=1)
        self.nav = {}
        self._nav_text = {key: (glyph, text) for key, text, glyph in NAV}
        for i, (key, text, glyph) in enumerate(NAV):
            ind = ctk.CTkFrame(nav, width=4, height=26, corner_radius=2, fg_color='transparent')
            ind.grid(row=i, column=0, padx=(0, 6))
            b = ctk.CTkButton(nav, text='  ' + tr(text), image=self.nav_icon(glyph), compound='left', anchor='w',
                              height=40, corner_radius=8, font=self.f_nav, fg_color='transparent', hover_color=C.CARD_HI, text_color=C.MUTED,
                              command=lambda k=key: self.show_page(k))
            b.grid(row=i, column=1, sticky='ew', pady=2)
            self.nav[key] = (b, ind)

        st = ctk.CTkFrame(sb, fg_color=C.CARD, corner_radius=12, border_width=1, border_color=C.BORDER)
        st.grid(row=3, column=0, sticky='ew', padx=14, pady=(0, 10))
        st.columnconfigure(0, weight=1)
        self.label(st, 'HEAD UNIT', 'tiny').grid(row=0, column=0, sticky='w', padx=14, pady=(12, 2))
        row = ctk.CTkFrame(st, fg_color='transparent')
        row.grid(row=1, column=0, sticky='ew', padx=14)
        self.conn_dot = ctk.CTkLabel(row, text='●', font=self.f_body, text_color=C.FAINT, width=14)
        self.conn_dot.pack(side='left')
        self.conn_txt = self.label(row, tr('Nicht geprüft'), 'bold')
        self.conn_txt.pack(side='left', padx=(6, 0))
        self.conn_sub = self.label(st, '', 'small')
        self.conn_sub.grid(row=2, column=0, sticky='w', padx=14, pady=(2, 0))
        self.lbl_sb_proj = self.label(st, tr('PROJEKT'), 'tiny')
        self.lbl_sb_proj.grid(row=3, column=0, sticky='w', padx=14, pady=(12, 4))
        chips = ctk.CTkFrame(st, fg_color='transparent')
        chips.grid(row=4, column=0, sticky='w', padx=12)
        self.sb_chip_sdk = self.chip(chips, 'SDK')
        self.sb_chip_sdk.pack(side='left', padx=2)
        self.sb_chip_kit = self.chip(chips, 'Kit')
        self.sb_chip_kit.pack(side='left', padx=2)
        self.sb_chip_cat = self.chip(chips, tr('Katalog'))
        self.sb_chip_cat.pack(side='left', padx=2)
        self.sb_kit = self.label(st, '', 'small')
        self.sb_kit.grid(row=5, column=0, sticky='w', padx=14, pady=(6, 12))
        for w_ in (self.lbl_sb_proj, chips, self.sb_kit):
            w_.grid_remove()
        self.conn_sub.grid_configure(pady=(2, 12))

        bot = ctk.CTkFrame(sb, fg_color='transparent')
        bot.grid(row=5, column=0, sticky='ew', padx=12, pady=(4, 14))
        bot.columnconfigure(0, weight=1)
        self.seg_lang = ctk.CTkSegmentedButton(bot, values=['English', 'Deutsch'], height=28, font=self.f_small,
                                               fg_color=C.CARD, selected_color=C.ACCENT,
                                               selected_hover_color=C.ACCENT_HOVER, unselected_color=C.CARD,
                                               unselected_hover_color=C.BTN_HOVER, text_color=C.TEXT,
                                               command=self.set_ui_lang)
        self.seg_lang.set('Deutsch' if UI_LANG == 'de' else 'English')
        self.seg_lang.grid(row=0, column=0, sticky='ew')

    def show_about(self):
        v = Dialog(self, 'Credits', tr('Credits für den Code:\n\n• cauliflowermossgrape – die Custom-Apps, das Kit und die '
                   'Werkzeuge, die diese GUI aufruft (make_kit.py, build.py, extract_sdk.js, check_signals.py, '
                   'deploy-car.sh, mgu-app-install.sh, GPU-Renderer, Controller-Gate …), sowie das Root-Tool '
                   'idrive7-8-ssh-root.\n• Robert Nisipeanu (robertnisipeanu) – der App Store mgu-store, zusammen mit '
                   'IterioDev. Die GUI ruft nur sein Installationsprogramm mgu-install.js auf.\n\nAusgenommen ist nur '
                   'diese GUI (iDrive 7 Toolkit v%s): ein separates, inoffizielles Zusatzprojekt. Sie ist nicht Teil '
                   'dieser Projekte und ruft deren Werkzeuge nur auf.') % VERSION,
                   [(tr('Root-Tool ↗'), 'ssh', 'secondary'), (tr('App Store ↗'), 'store', 'secondary'),
                    (tr('Projekt auf GitHub ↗'), 'repo', 'secondary'), ('OK', None, 'primary')], 'info',
                   detail='idrive7-apps – MIT License\nCopyright (c) 2026 cauliflowermossgrape\n%s\n%s\n\n'
                          'mgu-store – GPL-3.0-or-later\nCopyright (C) 2026 Robert Nisipeanu and IterioDev\n%s\n\n%s' % (
                              REPO_URL, SSH_TOOL_URL, STORE_URL,
                              tr('Kein offizielles BMW-Produkt. Nutzung auf eigenes Risiko.'))).run()
        if v == 'repo':
            webbrowser.open(REPO_URL)
        elif v == 'ssh':
            webbrowser.open(SSH_TOOL_URL)
        elif v == 'store':
            webbrowser.open(STORE_URL)

    def set_ui_lang(self, label):
        """Sprache der Oberfläche sofort umschalten - Beschriftungen werden an Ort und Stelle getauscht."""
        global UI_LANG
        code = 'de' if label == 'Deutsch' else 'en'
        if code == UI_LANG:
            return
        if self.busy:
            self.seg_lang.set('Deutsch' if UI_LANG == 'de' else 'English')
            self.alert('English / Deutsch', tr('Bitte warten, bis „%s“ fertig ist – dann die Sprache umschalten.')
                       % self.job_title, 'info')
            return
        UI_LANG = self._ui_lang = code
        if self.seg_lang.get() != label:
            self.seg_lang.set(label)
        self.retranslate()
        self.save_config()

    def retranslate(self):
        """Alle Beschriftungen an Ort und Stelle in die neue Sprache tauschen - kein Neuaufbau, kein Flackern."""
        self.root.title(APP_TITLE)

        def walk(w):
            for c in w.winfo_children():
                for opt in ('text', 'placeholder_text'):
                    try:
                        v = c.cget(opt)
                    except Exception:  # noqa: BLE001 - Widget ohne diese Option
                        continue
                    if isinstance(v, TStr):
                        try:
                            c.configure(**{opt: v.retr()})
                        except Exception:  # noqa: BLE001
                            pass
                walk(c)
        walk(self.root)
        # Texte, die aus Daten zusammengesetzt werden, neu erzeugen
        self._tree_headings()
        self._build_fmenu()
        self.seg_tools.configure(values=[tr(l) for _k, l in TOOL_TABS])
        self.seg_tools.set(tr(dict(TOOL_TABS)[self._tool_tab or 'diag']))
        self._langs = None                      # Sprachliste der Apps neu beschriften
        self.refresh_project()
        self.on_app_toggle(save=False)
        if getattr(self, '_tool_res', None):
            self.show_tools(self._tool_res)
        self._shell_ui()
        if self._files_last:
            self._files_show(*self._files_last, quiet=True)
        elif self.lbl_files.cget('text'):
            self._files_footer()
        self.set_conn(*self._conn)

    def show_page(self, key):
        """key: 'ssh', 'install', 'store' oder ein Tools-Bereich ('diag', 'shell', 'files', 'system');
        'tools' = zuletzt gewählter."""
        key = OLD_PAGES.get(key, key)
        tabs = [k for k, _l in TOOL_TABS]
        if key == 'tools':
            key = self._tool_tab or 'diag'
        if key not in PAGES_TOP and key not in tabs:
            key = 'install'
        if self.page == key:
            return
        top = key if key in PAGES_TOP else 'tools'
        for k, pg in self.pages.items():
            if k == top:
                pg.grid(row=0, column=0, sticky='nsew')
            else:
                pg.grid_remove()
        if top == 'tools':
            for k, fr in self.tool_frames.items():
                if k == key:
                    fr.grid(row=0, column=0, sticky='nsew')
                else:
                    fr.grid_remove()
            self._tool_tab = key
            self.seg_tools.set(tr(dict(TOOL_TABS)[key]))
        for k, (b, ind) in self.nav.items():
            on = k == top
            b.configure(fg_color=C.CARD_HI if on else 'transparent', text_color=C.TEXT if on else C.MUTED)
            ind.configure(fg_color=C.ACCENT if on else 'transparent')
        self.page = key
        self.root.after(30, self.apply_log_height)
        if key == 'files' and not self._files_inited and not self.busy and self.v_key.get().strip():
            self._files_inited = True
            self.root.after(150, lambda: self._list(self.cur_path, quiet=True))
        if key == 'shell' and self.shell_proc:
            self.sh_entry.focus_set()

    # ------------------------------------------------------------------ Protokoll-Bereich
    def build_log(self, f):
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(f, fg_color=C.SIDEBAR, corner_radius=0, height=46)
        bar.grid(row=0, column=0, sticky='ew')
        bar.columnconfigure(2, weight=1)
        self.st_dot = ctk.CTkLabel(bar, text='●', text_color=C.FAINT, font=self.f_body, width=14)
        self.st_dot.grid(row=0, column=0, padx=(18, 6), pady=8)
        self.lbl_status = self.label(bar, tr('Bereit.'), 'bold')
        self.lbl_status.grid(row=0, column=1, sticky='w')
        self.pb = ctk.CTkProgressBar(bar, mode='indeterminate', width=150, height=6, progress_color=C.ACCENT,
                                     fg_color=C.BTN, indeterminate_speed=1.2)
        self.pb.grid(row=0, column=2, sticky='w', padx=16)
        self.pb.grid_remove()
        right = ctk.CTkFrame(bar, fg_color='transparent')
        right.grid(row=0, column=3, sticky='e', padx=(0, 12))
        self.btn_cancel = self.button(right, tr('Abbrechen'), self.cancel, 'danger', action=False, height=30, width=100)
        self.btn_cancel.pack(side='left', padx=4)
        self.btn_cancel.configure(state='disabled', fg_color=C.BTN, text_color_disabled=C.FAINT)
        self.button(right, tr('Leeren'), self.clear_log, 'ghost', action=False, height=30, width=70).pack(side='left', padx=2)
        self.button(right, tr('Speichern…'), self.save_log, 'ghost', action=False, height=30, width=90).pack(side='left', padx=2)
        self.btn_logtoggle = self.button(right, tr('Protokoll ▾'), self.toggle_log, 'ghost', action=False, height=30, width=110)
        self.btn_logtoggle.pack(side='left', padx=2)
        self.txt = ctk.CTkTextbox(f, fg_color=C.CONSOLE, text_color=C.CONSOLE_TEXT, font=self.f_mono, wrap='word',
                                  corner_radius=0, border_width=0, scrollbar_button_color=C.BTN,
                                  scrollbar_button_hover_color=C.BTN_HOVER)
        self.txt.grid(row=1, column=0, sticky='nsew')
        self.txt.configure(state='disabled')
        for tag, col in (('cmd', '#6b7484'), ('head', C.ACCENT_TEXT), ('ok', C.GREEN_TEXT), ('err', C.RED_TEXT),
                         ('warn', C.AMBER_TEXT)):
            self.txt.tag_config(tag, foreground=col)

    def _cat(self):
        return 'term' if self.page in ('shell', 'files') else 'main'

    def apply_log_height(self):
        self.root.update_idletasks()
        total = self.pw.winfo_height()
        if total < 100:
            self.root.after(100, self.apply_log_height)
            return
        if not self._log_open:
            h = self.px(46)
        else:
            h = self._log_h.get(self._cat())
            h = self.px(h) if h else int(total * (0.22 if self._cat() == 'term' else 0.24))
            h = max(self.px(110), min(h, total - self.px(260)))
        try:
            self.pw.sash_place(0, 0, total - h)
        except tk.TclError:
            pass

    def _sash_moved(self, _e=None):
        try:
            y = self.pw.sash_coord(0)[1]
        except tk.TclError:
            return
        h = self.pw.winfo_height() - y
        if h > self.px(70):
            self._log_open = True
            self._log_h[self._cat()] = int(h / max(self.scale(), 0.1))
            self.btn_logtoggle.configure(text=tr('Protokoll ▾'))

    def toggle_log(self):
        self._log_open = not self._log_open
        self.btn_logtoggle.configure(text=tr('Protokoll ▾') if self._log_open else tr('Protokoll ▴'))
        self.apply_log_height()

    def set_status(self, text, state='idle'):
        col = {'idle': C.FAINT, 'busy': C.ACCENT_TEXT, 'ok': C.GREEN, 'bad': C.RED_TEXT}[state]
        self.st_dot.configure(text_color=col)
        self.lbl_status.configure(text=text, text_color=C.TEXT if state in ('idle', 'busy') else col)

    def set_conn(self, state, detail=None):
        """Verbindungsanzeige links unten: on / off / unknown."""
        txt, col = {'on': (tr('Verbunden'), C.GREEN), 'off': (tr('Nicht erreichbar'), C.RED_TEXT),
                    'unknown': (tr('Nicht geprüft'), C.FAINT)}[state]
        self.conn_dot.configure(text_color=col)
        self.conn_txt.configure(text=txt)
        if detail is not None:
            self.conn_sub.configure(text=detail)
        self._conn = (state, detail if detail is not None else self._conn[1])
        self.update_steps()

    # ================================================================= Seite: Installieren (4 Schritte)
    def build_page_install(self, f):
        pad = dict(sticky='ew', padx=12, pady=(0, 14))
        self.step_cards = {}

        # --- Schritt 1: Verbindung
        c1 = self.card(f, tr('Mit der Head Unit verbinden'), badge='1',
                       subtitle=tr('Root-Schlüssel aus dem Tool idrive7-8-ssh-root laden (data\\enrollments\\<VIN>\\id_rsa '
                                   '+ connect.txt). SSH muss vorher einmalig aktiviert sein (Seite "SSH aktivieren"); '
                                   'ENET-Kabel gesteckt, Zündung an.'))
        c1.grid(row=0, column=0, **pad)
        self.step_cards[1] = c1
        b = c1.body
        b.columnconfigure(0, weight=0)
        b.columnconfigure(1, weight=1)
        self.v_key = tk.StringVar()
        self.v_ip = tk.StringVar()
        self.v_port = tk.StringVar()
        self.v_vin = tk.StringVar()
        self.label(b, tr('SSH-Schlüssel'), 'muted').grid(row=0, column=0, sticky='w', padx=(0, 14))
        self.entry(b, self.v_key, mono=True).grid(row=0, column=1, sticky='ew')
        self.button(b, tr('Durchsuchen…'), self.browse_key, width=120).grid(row=0, column=2, padx=(8, 0))
        self.label(b, tr('IP-Adresse'), 'muted').grid(row=1, column=0, sticky='w', padx=(0, 14), pady=(10, 0))
        ipr = ctk.CTkFrame(b, fg_color='transparent')
        ipr.grid(row=1, column=1, columnspan=2, sticky='w', pady=(10, 0))
        self.entry(ipr, self.v_ip, width=170, mono=True).pack(side='left')
        self.label(ipr, 'Port', 'muted').pack(side='left', padx=(18, 8))
        self.entry(ipr, self.v_port, width=70, mono=True).pack(side='left')
        self.label(ipr, 'VIN', 'muted').pack(side='left', padx=(18, 8))
        self.lbl_vin = ctk.CTkLabel(ipr, textvariable=self.v_vin, font=self.f_mono, text_color=C.TEXT,
                                    fg_color=C.CARD_HI, corner_radius=8, height=30, padx=10)
        self.lbl_vin.pack(side='left')
        btns = ctk.CTkFrame(b, fg_color='transparent')
        btns.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(16, 0))
        self.button(btns, tr('connect.txt laden…'), self.browse_connect).pack(side='left')
        self.button(btns, tr('Automatisch suchen'), lambda: self.start(tr('Schlüssel suchen'), self.job_find_key)
                    ).pack(side='left', padx=8)
        self.button(btns, tr('SSH aktivieren ›'), lambda: self.show_page('ssh'), 'ghost', action=False
                    ).pack(side='left')
        self.button(btns, tr('Verbindung testen'), lambda: self.start(tr('Verbindung testen'), self.job_test, 'conn'),
                    'primary', width=170).pack(side='right')

        # --- Schritt 2: Projekt & SDK
        c2 = self.card(f, tr('Projekt & SDK'), badge='2',
                       subtitle=tr('Das Projekt idrive7-apps von GitHub holen und einmalig das SDK aus der Head Unit '
                                   'auslesen (wird zum Bauen gebraucht).'))
        c2.grid(row=1, column=0, **pad)
        self.step_cards[2] = c2
        b = c2.body
        b.columnconfigure(0, weight=0)
        b.columnconfigure(1, weight=1)
        self.v_repo = tk.StringVar()
        self.label(b, tr('Projektordner'), 'muted').grid(row=0, column=0, sticky='w', padx=(0, 14))
        e = self.entry(b, self.v_repo, mono=True)
        e.grid(row=0, column=1, sticky='ew')
        e.bind('<FocusOut>', lambda ev: self.refresh_project())
        e.bind('<Return>', lambda ev: self.refresh_project())
        self.button(b, tr('Durchsuchen…'), self.browse_repo, width=120).grid(row=0, column=2, padx=(8, 0))
        chips = ctk.CTkFrame(b, fg_color='transparent')
        chips.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(14, 0))
        self.chip_proj = self.chip(chips, tr('Projekt'))
        self.chip_proj.pack(side='left', padx=(0, 6))
        self.chip_sdk = self.chip(chips, 'SDK')
        self.chip_sdk.pack(side='left', padx=6)
        self.pc_chips = {}
        for k, name in (('python', 'Python'), ('node', 'Node.js'), ('ssh', 'OpenSSH')):
            ch = self.chip(chips, name)
            ch.pack(side='left', padx=(12 if k == 'python' else 6, 0))
            self.pc_chips[k] = (ch, name, Tooltip(self, ch, ''))
        self.button(chips, tr('PC-Check ›'), lambda: self.show_page('system'), 'ghost', action=False, height=26,
                    width=90).pack(side='left', padx=(6, 0))
        self.lbl_proj = self.label(b, '', 'small', wrap=True)
        self.lbl_proj.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        btns = ctk.CTkFrame(b, fg_color='transparent')
        btns.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(14, 0))
        self.button(btns, tr('Herunterladen / Aktualisieren'),
                    lambda: self.start(tr('Projekt herunterladen'), self.job_repo)).pack(side='left')
        self.button(btns, tr('Ordner öffnen'), lambda: self.open_dir(self.v_repo.get()), 'ghost', action=False
                    ).pack(side='left', padx=8)
        self.btn_sdk = self.button(btns, tr('SDK holen'), lambda: self.start(tr('SDK holen'), self.job_sdk, 'conn', 'repo'),
                                   'primary', width=150)
        self.btn_sdk.pack(side='right')

        # --- Schritt 3: Apps & Kit
        c3 = self.card(f, tr('Apps auswählen und Kit bauen'), badge='3',
                       subtitle=tr('Das Kit (dist/mgu-custom-apps….tar.gz) enthält die gewählten Apps, den Installer '
                                   'und den GPU-Renderer. Maus über eine App zeigt ihre Beschreibung.'))
        c3.grid(row=2, column=0, **pad)
        self.step_cards[3] = c3
        b = c3.body
        self.apps_frame = ctk.CTkFrame(b, fg_color='transparent')
        self.apps_frame.grid(row=0, column=0, sticky='ew')
        row = ctk.CTkFrame(b, fg_color='transparent')
        row.grid(row=1, column=0, sticky='ew', pady=(14, 0))
        self.button(row, tr('Alle'), lambda: self.select_apps(True), 'chip', width=64, action=False, height=30
                    ).pack(side='left')
        self.button(row, tr('Keine'), lambda: self.select_apps(False), 'chip', width=64, action=False, height=30
                    ).pack(side='left', padx=6)
        self.lbl_sel = self.label(row, '', 'small')
        self.lbl_sel.pack(side='left', padx=10)
        self.button(row, tr('Kit bauen'), lambda: self.start(tr('Kit bauen'), self.job_build, 'repo'), 'primary',
                    width=140).pack(side='right')
        self.button(row, tr('Kit-Ordner öffnen (USB-Stick)'),
                    lambda: self.open_dir(kit_dir(self.v_repo.get().strip(), self.lang())), 'ghost', action=False
                    ).pack(side='right', padx=8)
        # Sprache der Apps (make_kit.py --language) - nur sichtbar, wenn das Projekt mehr als Englisch kann
        self.v_lang = tk.StringVar(value='en')
        self._langs = ['en']
        self.lang_row = ctk.CTkFrame(b, fg_color='transparent')
        self.lang_row.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        self.lang_row.columnconfigure(2, weight=1)
        self.label(self.lang_row, tr('Sprache der Apps'), 'muted').grid(row=0, column=0, sticky='w', padx=(0, 10))
        self.om_lang = ctk.CTkOptionMenu(self.lang_row, values=[lang_label('en')], width=220, height=30,
                                         corner_radius=8, fg_color=C.BTN, button_color=C.BTN,
                                         button_hover_color=C.BTN_HOVER, text_color=C.TEXT, font=self.f_body,
                                         dropdown_font=self.f_body, dropdown_fg_color=C.CARD_HI,
                                         dropdown_hover_color=C.BTN_HOVER, dropdown_text_color=C.TEXT,
                                         command=self.on_lang)
        self.om_lang.grid(row=0, column=1, sticky='w')
        self.label(self.lang_row, tr('wird beim Bauen festgelegt – die Spracheinstellung im Auto ändert sie nicht'),
                   'small', wrap=True).grid(row=0, column=2, sticky='ew', padx=(12, 0))
        self.lang_row.grid_remove()
        kit = ctk.CTkFrame(b, fg_color=C.INPUT, corner_radius=10)
        kit.grid(row=3, column=0, sticky='ew', pady=(14, 0))
        kit.columnconfigure(0, weight=1)
        top = ctk.CTkFrame(kit, fg_color='transparent')
        top.grid(row=0, column=0, sticky='ew', padx=14, pady=(12, 0))
        top.columnconfigure(0, weight=1)
        self.lbl_kit = self.label(top, '', 'bold')
        self.lbl_kit.grid(row=0, column=0, sticky='w')
        self.chip_checked = self.chip(top, '')
        self.chip_checked.grid(row=0, column=1, sticky='e')
        self.lbl_kit_apps = self.label(kit, '', 'small', wrap=True)
        self.lbl_kit_apps.grid(row=1, column=0, sticky='ew', padx=14, pady=(4, 12))

        # --- Schritt 4: Prüfen & Installieren
        c4 = self.card(f, tr('Prüfen und installieren'), badge='4',
                       subtitle=tr('Auto geparkt, Zündung an, am besten Ladegerät dran. "Prüfen" überträgt das Kit und '
                                   'testet alles, ohne etwas zu ändern. "Installieren" legt vorher ein Backup an; die '
                                   'Kacheln erscheinen nach 1–2 Min. unter Apps.'))
        c4.grid(row=3, column=0, **pad)
        self.step_cards[4] = c4
        b = c4.body
        self.v_noreboot = tk.BooleanVar()
        self.v_nogate = tk.BooleanVar()
        self.v_withhmi = tk.BooleanVar()
        row = ctk.CTkFrame(b, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.btn_opts = self.button(row, tr('Erweiterte Optionen ▸'), self.toggle_options, 'ghost', action=False,
                                    height=30)
        self.btn_opts.pack(side='left')
        self.button(row, tr('Installieren'), self.install, 'primary', width=150).pack(side='right')
        self.button(row, tr('Prüfen (Trockenlauf)'), lambda: self.start(tr('Prüfen'), self.job_check, 'conn', 'kit'),
                    width=170).pack(side='right', padx=8)
        self.opts_frame = opts = ctk.CTkFrame(b, fg_color='transparent')
        opts.grid(row=1, column=0, sticky='ew', pady=(12, 0))
        opts.columnconfigure(0, weight=1)
        self.switch(opts, self.v_noreboot, tr('Nicht neu starten'),
                    tr('NO_REBOOT – das Controller-Gate (für Doom) lädt dann erst beim nächsten Start der Head Unit.')
                    ).grid(row=0, column=0, sticky='ew', pady=4)
        self.switch(opts, self.v_nogate, tr('Ohne Controller-Gate'),
                    tr('NO_INPUTGATE – Doom läuft, aber das HMI reagiert mit auf den Controller. Kein Neustart nötig.')
                    ).grid(row=1, column=0, sticky='ew', pady=4)
        self.switch(opts, self.v_withhmi, tr('HMI gleich mit neu starten'),
                    tr('WITH_HMI – entfernt alte Kacheln sofort (Bildschirm kurz schwarz). Ohne Wirkung, wenn die '
                       'Head Unit ohnehin neu startet.')).grid(row=2, column=0, sticky='ew', pady=4)
        opts.grid_remove()
        self._opts_open = False

        # --- Zurück auf Serie (wenn etwas schiefgeht)
        c5 = self.card(f, tr('Zurück auf Serie'), badge='↺', tone='danger',
                       subtitle=tr('"stock" = Zustand vor der allerersten Installation: entfernt alle Custom-Apps, Hook '
                                   'und Renderer und setzt jede berührte Datei exakt zurück.'))
        c5.grid(row=4, column=0, **pad)
        b = c5.body
        row = ctk.CTkFrame(b, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.label(row, 'Backup', 'muted').pack(side='left', padx=(0, 10))
        self.v_backup = tk.StringVar(value='stock')
        self.cb_backup = ctk.CTkComboBox(row, variable=self.v_backup, values=['stock', 'latest'], width=230, height=34,
                                         corner_radius=8, fg_color=C.INPUT, border_color=C.BORDER,
                                         button_color=C.BTN, button_hover_color=C.BTN_HOVER, text_color=C.TEXT,
                                         font=self.f_mono, dropdown_font=self.f_mono, dropdown_fg_color=C.CARD_HI,
                                         dropdown_hover_color=C.BTN_HOVER, dropdown_text_color=C.TEXT)
        self.cb_backup.pack(side='left')
        self.button(row, tr('Backups laden'), lambda: self.start(tr('Backups auflisten'), self.job_backups, 'conn')
                    ).pack(side='left', padx=8)
        self.v_nohmi = tk.BooleanVar()
        sw = self.switch(b, self.v_nohmi, tr('HMI nicht neu starten'),
                         tr('NO_HMI_RESTART – die entfernten Kacheln verschwinden dann erst beim nächsten Neustart.'))
        sw.grid(row=1, column=0, sticky='ew', pady=(14, 0))
        row = ctk.CTkFrame(b, fg_color='transparent')
        row.grid(row=2, column=0, sticky='ew', pady=(14, 0))
        self.button(row, tr('Wiederherstellen'), self.restore, 'danger', width=170).pack(side='right')

    def toggle_options(self, show=None):
        self._opts_open = (not self._opts_open) if show is None else show
        if self._opts_open:
            self.opts_frame.grid()
        else:
            self.opts_frame.grid_remove()
        self.btn_opts.configure(text=tr('Erweiterte Optionen ▾') if self._opts_open else tr('Erweiterte Optionen ▸'))

    def update_steps(self):
        """Schritt-Nummern: ✓ für erledigte Schritte, der nächste offene Schritt ist blau hervorgehoben."""
        if not getattr(self, 'step_cards', None):
            return
        repo = self.v_repo.get().strip()
        ok = repo_ok(repo)
        ki = kit_info(repo, self.lang()) if ok else None
        done = {1: self._conn[0] == 'on',
                2: ok and os.path.isfile(os.path.join(repo, 'oap-sdk', 'index.js')),
                3: bool(ki),
                4: bool(ki) and self._installed == (ki['mtime'], self.v_ip.get().strip())}
        nxt = next((n for n in (1, 2, 3, 4) if not done[n]), None)
        for n, card in self.step_cards.items():
            cv = card.badge
            if done[n]:
                cv.itemconfigure(cv._oval, fill=C.GREEN_SOFT, outline=C.GREEN_SOFT)
                cv.itemconfigure(cv._txt, text='✓', fill=C.GREEN_TEXT)
            elif n == nxt:
                cv.itemconfigure(cv._oval, fill=C.ACCENT, outline=C.ACCENT)
                cv.itemconfigure(cv._txt, text=str(n), fill='#ffffff')
            else:
                cv.itemconfigure(cv._oval, fill=C.ACCENT_SOFT, outline=C.ACCENT_SOFT)
                cv.itemconfigure(cv._txt, text=str(n), fill=C.ACCENT_TEXT)
            card.configure(border_color='#2a4f86' if n == nxt else C.BORDER)
        self.update_ssh_page()
        self.update_store_page()

    # ================================================================= Seite: SSH aktivieren (einmalig, Original-Tool)
    def build_page_ssh(self, f):
        """Nur Wegweiser: das Freischalten macht das Original-Tool idrive7-8-ssh-root, die GUI verlinkt es."""
        pad = dict(sticky='ew', padx=12, pady=(0, 14))
        c1 = self.card(f, tr('Original-Tool idrive7-8-ssh-root'), badge='1',
                       subtitle=tr('Download, Anleitung und Warnhinweise stehen im Repository von cauliflowermossgrape. '
                                   'Kurz: ENET-Kabel stecken, Zündung an, das Tool unter Windows als Administrator '
                                   'starten und einmalig "Flash + Enable" ausführen. Vorher die Codierung der Head Unit '
                                   'sichern (z. B. mit BimmerCode) – siehe README.'))
        c1.grid(row=0, column=0, **pad)
        row = ctk.CTkFrame(c1.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        lk = ctk.CTkLabel(row, text=SSH_TOOL_URL, font=self.f_mono, text_color=C.ACCENT_TEXT, anchor='w', cursor='hand2')
        lk.pack(side='left')
        lk.bind('<Button-1>', lambda e: webbrowser.open(SSH_TOOL_URL))
        self.button(row, tr('Auf GitHub öffnen ↗'), lambda: webbrowser.open(SSH_TOOL_URL), 'primary', action=False,
                    width=180).pack(side='right')

        c2 = self.card(f, tr('Schlüssel übernehmen'), badge='2',
                       subtitle=tr('Das Tool legt den Root-Schlüssel unter data\\enrollments\\<VIN>\\ ab (id_rsa + '
                                   'connect.txt). Die GUI sucht ihn automatisch, oder du wählst die connect.txt selbst – '
                                   'danach weiter mit "Installieren".'))
        c2.grid(row=1, column=0, **pad)
        b = c2.body
        self.lbl_ssh_key = self.label(b, '', 'small', wrap=True)
        self.lbl_ssh_key.grid(row=0, column=0, sticky='ew')
        row = ctk.CTkFrame(b, fg_color='transparent')
        row.grid(row=1, column=0, sticky='ew', pady=(14, 0))
        self.button(row, tr('Automatisch suchen'), lambda: self.start(tr('Schlüssel suchen'), self.job_find_key)
                    ).pack(side='left')
        self.button(row, tr('connect.txt laden…'), self.browse_connect).pack(side='left', padx=8)
        self.button(row, tr('Weiter zu Installieren ›'), lambda: self.show_page('install'), 'primary', action=False,
                    width=190).pack(side='right')

    def update_ssh_page(self):
        """Seite "SSH aktivieren": gefundener Schlüssel + ✓ in der Navigation."""
        if not hasattr(self, 'lbl_ssh_key'):
            return
        key = self.v_key.get().strip().strip('"')
        have = bool(key) and os.path.isfile(key)
        self.lbl_ssh_key.configure(text=(tr('✔ Schlüssel geladen: %s') % key) if have else
                                   tr('Noch kein Schlüssel geladen.'),
                                   text_color=C.GREEN_TEXT if have else C.MUTED)
        done = have or self._conn[0] == 'on'
        b, _ind = self.nav['ssh']
        if getattr(b, '_done', None) is not done:
            b._done = done
            glyph, text = self._nav_text['ssh']
            b.configure(text='  ' + tr(text) + ('   ✓' if done else ''))

    # ================================================================= Seite: App Store (mgu-store)
    def build_page_store(self, f):
        """App Store von robertnisipeanu/mgu-store: die GUI ruft nur dessen Installationsprogramm auf."""
        pad = dict(sticky='ew', padx=12, pady=(0, 14))
        c1 = self.card(f, tr('App Store für die Head Unit'), tone='accent',
                       subtitle=tr('mgu-store von Robert Nisipeanu (robertnisipeanu): ein App Store direkt im Auto – Apps '
                                   'und Werkzeuge aus GitHub-Repositories, Web-Adressen oder von einem USB-Stick '
                                   'durchsuchen, installieren, aktualisieren und entfernen. idrive7-apps ist dort schon '
                                   'als Quelle eingetragen. Diese GUI ruft nur sein Installationsprogramm mgu-install.js '
                                   'auf; Anleitung und Details stehen im Repository.'))
        c1.grid(row=0, column=0, **pad)
        row = ctk.CTkFrame(c1.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        lk = ctk.CTkLabel(row, text=STORE_URL, font=self.f_mono, text_color=C.ACCENT_TEXT, anchor='w', cursor='hand2')
        lk.pack(side='left')
        lk.bind('<Button-1>', lambda e: webbrowser.open(STORE_URL))
        self.button(row, tr('Auf GitHub öffnen ↗'), lambda: webbrowser.open(STORE_URL), action=False,
                    width=180).pack(side='right')

        c2 = self.card(f, tr('Im Auto installieren'), badge='1',
                       subtitle=tr('Überträgt Store, Updater, Deploy-Tool, App-Privacy, Renderer, Python und '
                                   'Internet-Uplink – mit dem Root-Schlüssel, der IP und dem Port aus "Installieren" '
                                   '(Schritt 1). Braucht Node.js 18+ und Internet am PC. Auto geparkt, stabile '
                                   'Stromversorgung; danach die Head Unit einmal neu starten. Spätere Updates laufen im '
                                   'Store selbst.'))
        c2.grid(row=1, column=0, **pad)
        b = c2.body
        chips = ctk.CTkFrame(b, fg_color='transparent')
        chips.grid(row=0, column=0, sticky='ew')
        self.store_chips = {}
        for i, (k, name) in enumerate((('node', 'Node.js 18+'), ('ssh', 'OpenSSH'), ('key', tr('Schlüssel')),
                                       ('conn', tr('Verbindung')))):
            ch = self.chip(chips, name)
            ch.pack(side='left', padx=(0 if i == 0 else 6, 0))
            self.store_chips[k] = (ch, name, Tooltip(self, ch, ''))
        self.v_store_norender = tk.BooleanVar()
        self.v_store_nouplink = tk.BooleanVar()
        self.switch(b, self.v_store_norender, tr('Ohne Renderer'),
                    tr('--no-renderer – lässt mgu-render weg; der Store zeigt dann einfache Widget-Seiten.')
                    ).grid(row=1, column=0, sticky='ew', pady=(14, 4))
        self.switch(b, self.v_store_nouplink, tr('Ohne Internet-Uplink'),
                    tr('--no-uplink – kein Internet über USB-Netzwerkadapter, LTE-Stick oder Handy; Updates dann per '
                       'USB-Stick (unten).')).grid(row=2, column=0, sticky='ew', pady=4)
        # Quelle deutlich zeigen: es wird immer frisch aus dem Repo geladen
        src = ctk.CTkFrame(b, fg_color=C.ACCENT_SOFT, corner_radius=10)
        src.grid(row=3, column=0, sticky='ew', pady=(14, 0))
        src.columnconfigure(1, weight=1)
        self.badge(src, '⇣', C.ACCENT, '#ffffff', size=26, bg_parent=C.ACCENT_SOFT, font_px=15).grid(
            row=0, column=0, rowspan=3, padx=(14, 12), pady=12, sticky='n')
        self.label(src, tr('Immer frisch aus dem GitHub-Repo'), 'bold', text_color=C.ACCENT_TEXT).grid(
            row=0, column=1, sticky='w', pady=(12, 0))
        self.label(src, tr('Die GUI enthält keine Store-Dateien. Bei jedem Klick werden das Installationsprogramm '
                           'mgu-install.js und die neueste veröffentlichte Store-Version direkt aus '
                           'robertnisipeanu/mgu-store geladen – der PC braucht dafür Internet.'),
                   'small', wrap=True).grid(row=1, column=1, sticky='ew', padx=(0, 12))
        self.lbl_store_last = self.label(src, '', 'small', wrap=True, text_color=C.ACCENT_TEXT)
        self.lbl_store_last.grid(row=2, column=1, sticky='ew', padx=(0, 12), pady=(2, 12))
        self.button(src, tr('Von GitHub laden und installieren'), self.store_install, 'primary', width=260
                    ).grid(row=0, column=2, rowspan=3, padx=14, pady=12)
        self.label(b, tr('Der Store übernimmt dabei Renderer und Controller-Gate einer Kit-Installation.'),
                   'small').grid(row=4, column=0, sticky='w', pady=(10, 0))

        c3 = self.card(f, tr('USB-Stick für Updates ohne Internet'), badge='2',
                       subtitle=tr('Für ein Auto ohne Internet: lädt die neueste Store-Version frisch von GitHub und '
                                   'schreibt sie als lokale Quelle auf einen USB-Stick (usb-repo). Den Stick ins Auto '
                                   'stecken und im Store die Seite "USB" öffnen.'))
        c3.grid(row=2, column=0, **pad)
        row = ctk.CTkFrame(c3.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='ew')
        self.button(row, tr('Installer-Ordner öffnen'), lambda: self.open_dir(STORE_DIR), 'ghost', action=False
                    ).pack(side='left')
        self.button(row, tr('Von GitHub laden und auf Stick schreiben…'), self.store_usb, width=290).pack(side='right')

    def update_store_page(self):
        """Chips auf der Seite "App Store" (Node.js 18+, OpenSSH, Schlüssel, Verbindung)."""
        if not getattr(self, 'store_chips', None):
            return
        res = getattr(self, '_tool_res', None) or {}
        key = self.v_key.get().strip().strip('"')
        node = res.get('node')
        if node:
            m = re.search(r'v(\d+)', _cur(node[1]))
            node = (bool(m) and int(m.group(1)) >= 18, _cur(node[1]) if m and int(m.group(1)) >= 18 else
                    tr('%s – mgu-store braucht Node.js 18 oder neuer (nodejs.org)') % _cur(node[1]))
        st = {'node': node, 'ssh': res.get('ssh'),
              'key': (bool(key) and os.path.isfile(key), key or tr('Noch kein Schlüssel – Seite "SSH aktivieren"')),
              'conn': {'on': (True, tr('Verbunden')), 'off': (False, tr('Nicht erreichbar'))}.get(
                  self._conn[0], (None, tr('Unter "Installieren" "Verbindung testen"')))}
        for k, (ch, name, tip) in self.store_chips.items():
            ok, text = st[k] or (None, tr('wird geprüft…'))
            self.set_chip(ch, '%s %s' % (_cur(name), '✓' if ok else ('✖' if ok is False else '…')),
                          'ok' if ok else ('bad' if ok is False else 'neutral'))
            tip.text = _cur(text)
        js = os.path.join(STORE_DIR, 'mgu-install.js')
        if os.path.isfile(js):
            t = datetime.datetime.fromtimestamp(os.path.getmtime(js)).strftime(tr('%d.%m.%Y %H:%M'))
            self.lbl_store_last.configure(text=tr('Zuletzt geladen: %s (mgu-install.js, %s) – beim nächsten Klick wird '
                                                  'wieder neu geladen.') % (t, fmt_size(os.path.getsize(js))))
        else:
            self.lbl_store_last.configure(text=tr('Bisher noch nichts geladen.'))

    def store_install(self):
        title = tr('App Store installieren')
        if self.busy or not self.precheck(title, 'conn'):
            return
        if not self.ask(tr('App Store installieren?'),
                        tr('Jetzt frisch von GitHub (robertnisipeanu/mgu-store) laden: das Installationsprogramm '
                           'mgu-install.js und die neueste veröffentlichte Store-Version. Danach wird alles auf der '
                           'Head Unit %s installiert.\n\nAuto geparkt, Zündung an, am besten Ladegerät dran – bis das '
                           'Programm fertig ist. Danach die Head Unit einmal neu starten.') % self.v_ip.get().strip(),
                        yes=tr('Laden und installieren')):
            return
        self.start(title, self.job_store_install, 'conn')

    def store_usb(self):
        if self.busy:
            return
        d = filedialog.askdirectory(title=tr('USB-Stick (Laufwerk) wählen'), parent=self.root)
        if not d:
            return
        d = os.path.normpath(d)
        if not self.ask(tr('USB-Stick vorbereiten?'), tr('Die neueste Store-Version wird frisch von GitHub geladen und '
                                                         'nach %s geschrieben (Ordner mgu-repo). Andere Dateien auf dem '
                                                         'Stick bleiben.') %
                        os.path.join(d, 'mgu-repo'), yes=tr('Schreiben')):
            return
        self.start(tr('USB-Stick vorbereiten'), lambda c, d=d: self.job_store_usb(c, d))

    # ------------------------------------------------------------------ Jobs: App Store
    def store_node(self):
        """node.exe mit Version 18+ -> Pfad"""
        node = shutil.which('node')
        if not node:
            raise Abort(tr('Node.js wurde nicht gefunden. mgu-store braucht Node.js 18 oder neuer (nodejs.org, '
                           'LTS-Version).'))
        ok, v = tool_version([node, '--version'])
        m = re.match(r'v(\d+)', v)
        if not (ok and m and int(m.group(1)) >= 18):
            raise Abort(tr('mgu-store braucht Node.js 18 oder neuer – gefunden: %s (nodejs.org, LTS-Version).') % (v or '?'))
        return node

    def fetch_store_installer(self):
        """mgu-install.js frisch von GitHub holen (wie in der Anleitung von mgu-store) -> Pfad"""
        os.makedirs(STORE_DIR, exist_ok=True)
        dest = os.path.join(STORE_DIR, 'mgu-install.js')
        self.log(tr('Quelle: %s – es wird immer die neueste Version geladen, nichts davon steckt in der GUI.') % STORE_URL)
        self.log(tr('Lade %s …') % STORE_INSTALLER_URL)
        tmp = dest + '.part'
        try:
            req = urllib.request.Request(STORE_INSTALLER_URL, headers={'User-Agent': 'idrive7-toolkit'})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, 'wb') as fh:
                shutil.copyfileobj(r, fh)
            if os.path.getsize(tmp) < 1000:
                raise ValueError(tr('Datei zu klein'))
            os.replace(tmp, dest)
        except Exception as e:  # noqa: BLE001
            self._rm(tmp)
            raise Abort(tr('mgu-install.js konnte nicht geladen werden: %s – Internetverbindung?') % e)
        self.log(tr('mgu-install.js geladen (%s).') % fmt_size(os.path.getsize(dest)), 'ok')
        return dest

    def store_env(self, c):
        """Das Installationsprogramm ruft "ssh" auf: den OpenSSH-Client der GUI vorne in den PATH."""
        ssh = c.get('ssh') or find_ssh()
        if not ssh:
            return None
        return {'PATH': os.path.dirname(ssh) + os.pathsep + os.environ.get('PATH', '')}

    def job_store_install(self, c):
        node = self.store_node()
        js = self.fetch_store_installer()
        nul = null_dev(c['ssh'])
        args = [node, js, 'first-install', '--identity', c['keyfile'], '--host', c['ip'], '--port', c['port']]
        for opt in ('BatchMode=yes', 'StrictHostKeyChecking=no', 'UserKnownHostsFile=' + nul,
                    'GlobalKnownHostsFile=' + nul, 'ConnectTimeout=15', 'LogLevel=ERROR'):
            args += ['--ssh-opt', opt]
        if c['store_norender']:
            args.append('--no-renderer')
        if c['store_nouplink']:
            args.append('--no-uplink')
        rc, lines = self.run(args, cwd=STORE_DIR, env=self.store_env(c))
        last = next((x.strip() for x in reversed(lines) if x.strip()), '')
        if rc == 2:
            raise Abort(tr('Das Installationsprogramm von mgu-store hat abgelehnt: %s') % last)
        if rc:
            raise Abort(tr('Das Installationsprogramm von mgu-store ist fehlgeschlagen (Exit %d): %s') % (rc, last))
        if any('Restart the head unit' in x for x in lines):
            self._notice = (tr('App Store installiert'),
                            tr('Zum Abschluss die Head Unit einmal neu starten (ein normaler Zündungszyklus genügt). '
                               'Danach startet der Updater und die Kachel "Store" erscheint unter Apps.'), 'info')
        self.log(tr('App Store installiert.'), 'ok')

    def job_store_usb(self, c, folder):
        node = self.store_node()
        js = self.fetch_store_installer()
        rc, lines = self.run([node, js, 'usb-repo', folder], cwd=STORE_DIR)
        if rc:
            last = next((x.strip() for x in reversed(lines) if x.strip()), '')
            raise Abort(tr('usb-repo ist fehlgeschlagen (Exit %d): %s') % (rc, last))
        self.log(tr('USB-Stick fertig: %s') % os.path.join(folder, 'mgu-repo'), 'ok')

    # ================================================================= Seite: Tools
    def build_tool_diag(self, f):
        ban = ctk.CTkFrame(f, fg_color=C.ACCENT_SOFT, corner_radius=12)
        ban.grid(row=0, column=0, sticky='ew', padx=12, pady=(0, 14))
        ban.columnconfigure(1, weight=1)
        self.badge(ban, 'i', C.ACCENT, '#ffffff', size=26, bg_parent=C.ACCENT_SOFT).grid(
            row=0, column=0, padx=(16, 12), pady=12, sticky='n')
        self.label(ban, tr('Alles hier liest nur – an der Head Unit wird nichts verändert. Ergebnisse landen im '
                           'Protokoll und im Ordner "ausgelesen" neben der GUI (SDK und Katalog direkt im Projekt).'),
                   'body', wrap=True, text_color=C.ACCENT_TEXT).grid(row=0, column=1, sticky='ew', pady=12, padx=(0, 16))

        grid = ctk.CTkFrame(f, fg_color='transparent')
        grid.grid(row=1, column=0, sticky='ew', padx=12)
        grid.columnconfigure((0, 1), weight=1, uniform='tiles')
        items = (
            ('ℹ', tr('Geräteinfo'), tr('Kernel, Firmware-Dateien, MainApp-Version, Dienste, Speicher, Netzwerk, VIN.'),
             lambda: self.start(tr('Geräteinfo'), self.job_info, 'conn')),
            ('◉', 'Status', tr('Installierte Apps, Provisioning-Hook, GPU-Renderer, Controller-Gate, Stock-Backup. '
                               'Fehlt das Kit auf der Unit, wird es nur dorthin kopiert (nicht installiert).'),
             lambda: self.start('Status', self.job_status, 'conn')),
            ('☰', tr('Backups auflisten'), tr('Alle Backups auf der Unit – danach unter "Installieren › Zurück auf '
                                              'Serie" auswählbar.'),
             lambda: self.start(tr('Backups auflisten'), self.job_backups, 'conn')),
            ('≡', tr('App-Logs holen'), tr('Logs der Custom-Apps, deploy-car.log und Journal von Renderer und '
                                           'App-Plattform – als Ordner auf dem PC.'),
             lambda: self.start(tr('App-Logs holen'), self.job_logs, 'conn')),
            ('⇵', tr('SOME/IP-Katalog holen'), tr('/etc/oap/someip/someip-interfaces.json → Projekt/reference/ '
                                                  '(optional, für eigene Apps).'),
             lambda: self.start(tr('SOME/IP-Katalog holen'), self.job_catalog, 'conn', 'repo')),
            ('✓', tr('Signale prüfen'), tr('Signale aller app.json gegen den Katalog prüfen (check_signals.py, offline). '
                                           'Optionale Schnittstellen werden nur als Hinweis gezeigt.'),
             lambda: self.start(tr('Signale prüfen'), self.job_check_signals, 'repo')),
            ('❐', tr('SDK aus Datei…'), tr('Stattdessen eine x_call@….tgz oder app.js vom PC verwenden.'),
             self.sdk_from_file),
        )
        for i, (glyph, title, desc, cmd) in enumerate(items):
            t = ctk.CTkFrame(grid, fg_color=C.CARD, corner_radius=14, border_width=1, border_color=C.BORDER)
            t.grid(row=i // 2, column=i % 2, sticky='nsew', padx=(0 if i % 2 == 0 else 7, 7 if i % 2 == 0 else 0),
                   pady=7)
            t.columnconfigure(1, weight=1)
            t.rowconfigure(1, weight=1)
            ctk.CTkLabel(t, text=glyph, width=36, height=36, corner_radius=10, fg_color=C.CARD_HI,
                         text_color=C.ACCENT_TEXT, font=self.f_glyph).grid(row=0, column=0, rowspan=2, padx=(16, 12),
                                                                          pady=16, sticky='n')
            self.label(t, title, 'h2').grid(row=0, column=1, sticky='w', pady=(16, 0), padx=(0, 16))
            self.label(t, desc, 'small', wrap=True).grid(row=1, column=1, sticky='new', padx=(0, 16), pady=(4, 0))
            self.button(t, tr('Starten'), cmd, width=110).grid(row=2, column=1, sticky='e', padx=16, pady=(10, 16))

        foot = ctk.CTkFrame(f, fg_color='transparent')
        foot.grid(row=2, column=0, sticky='ew', padx=12, pady=(10, 14))
        self.chip_read_sdk = self.chip(foot, 'SDK')
        self.chip_read_sdk.pack(side='left')
        self.chip_read_cat = self.chip(foot, tr('Katalog'))
        self.chip_read_cat.pack(side='left', padx=8)
        self.button(foot, tr('Ordner "ausgelesen" öffnen'), lambda: self.open_dir(OUT_DIR, create=True),
                    action=False).pack(side='right')

    def build_tool_system(self, f):
        pad = dict(sticky='ew', padx=12, pady=(0, 14))
        c = self.card(f, tr('Voraussetzungen auf diesem PC'))
        c.grid(row=0, column=0, **pad)
        b = c.body
        grid = ctk.CTkFrame(b, fg_color='transparent')
        grid.grid(row=0, column=0, sticky='ew')
        grid.columnconfigure((0, 1), weight=1, uniform='tools')
        self.tool_labels = {}
        rows = (('python', 'Python 3', tr('python.org – beim Installieren "Add python.exe to PATH" anhaken')),
                ('node', 'Node.js 14+', tr('nodejs.org – LTS-Version installieren')),
                ('ssh', 'OpenSSH-Client', tr('Einstellungen › Apps › Optionale Features › "OpenSSH-Client"')),
                ('git', tr('Git (optional)'), tr('ohne Git lädt die GUI das Projekt als ZIP')))
        for i, (k, name, hint) in enumerate(rows):
            t = ctk.CTkFrame(grid, fg_color=C.CARD_HI, corner_radius=10)
            t.grid(row=i // 2, column=i % 2, sticky='nsew', padx=(0 if i % 2 == 0 else 6, 6 if i % 2 == 0 else 0),
                   pady=4)
            t.columnconfigure(1, weight=1)
            dot = ctk.CTkLabel(t, text='●', text_color=C.FAINT, font=self.f_body, width=14)
            dot.grid(row=0, column=0, rowspan=2, padx=(14, 10), pady=10, sticky='n')
            self.label(t, name, 'bold').grid(row=0, column=1, sticky='w', pady=(10, 0))
            lb = self.label(t, tr('wird geprüft…'), 'small', wrap=True)
            lb.grid(row=1, column=1, sticky='ew', pady=(0, 10), padx=(0, 10))
            self.tool_labels[k] = (dot, lb, hint)
        self.button(b, tr('Erneut prüfen'), self.check_tools, action=False).grid(row=1, column=0, sticky='w', pady=(10, 0))

        c = self.card(f, tr('Root-Zugang (SSH) auf der Head Unit'),
                      subtitle=tr('Diese GUI meldet sich als root an – mit dem Schlüssel, den das separate Tool '
                                  'idrive7-8-ssh-root beim "Enroll" ablegt (data\\enrollments\\<VIN>\\id_rsa + '
                                  'connect.txt). Das Freischalten selbst erledigt dieses Tool; danach unter '
                                  '"Installieren" (Schritt 1) den Schlüssel laden und "Verbindung testen".'))
        c.grid(row=1, column=0, **pad)
        row = ctk.CTkFrame(c.body, fg_color='transparent')
        row.grid(row=0, column=0, sticky='w')
        self.button(row, tr('SSH-Tool öffnen ↗'), lambda: webbrowser.open(SSH_TOOL_URL), action=False).pack(side='left')
        self.button(row, tr('Enrollment suchen'), lambda: self.start(tr('Schlüssel suchen'), self.job_find_key)
                    ).pack(side='left', padx=8)

    # ------------------------------------------------------------------ Konfiguration
    def load_config(self):
        try:
            with open(CONFIG_FILE, encoding='utf-8') as fh:
                c = json.load(fh)
                return c if isinstance(c, dict) else {}
        except (OSError, ValueError):
            return {}

    def load_vars(self):
        c = self.cfg
        self.v_key.set(c.get('key', ''))
        self.v_ip.set(c.get('ip', DEFAULT_IP) or DEFAULT_IP)
        self.v_port.set(str(c.get('port', 22) or 22))
        self.v_vin.set(c.get('vin', '–') or '–')
        repo = c.get('repo')
        if not repo:
            for cand in (HERE, os.path.dirname(HERE)):
                if repo_ok(cand):
                    repo = cand
                    break
            else:
                repo = os.path.join(HERE, 'idrive7-apps')
        self.v_repo.set(repo)
        self.v_noreboot.set(bool(c.get('no_reboot', False)))
        self.v_nogate.set(bool(c.get('no_inputgate', False)))
        self.v_withhmi.set(bool(c.get('with_hmi', False)))
        self.v_nohmi.set(bool(c.get('no_hmi_restart', False)))
        self.v_lang.set(c.get('language') or 'en')
        self.v_store_norender.set(bool(c.get('store_no_renderer', False)))
        self.v_store_nouplink.set(bool(c.get('store_no_uplink', False)))
        self.toggle_options(show=bool(self.v_noreboot.get() or self.v_nogate.get() or self.v_withhmi.get()))
        self.set_conn('unknown', '%s\nVIN %s' % (self.v_ip.get(), self.v_vin.get()))

    def save_config(self):
        c = dict(self.cfg)                    # unbekannte Schlüssel (z. B. von v1) behalten
        c.update({
            'key': self.v_key.get().strip(), 'ip': self.v_ip.get().strip(), 'port': self.v_port.get().strip(),
            'vin': self.v_vin.get(), 'repo': self.v_repo.get().strip(),
            'apps': [k for k, v in self.app_vars.items() if v.get()] if self.app_vars else self.cfg.get('apps'),
            'all_apps': bool(self.app_vars) and all(v.get() for v in self.app_vars.values()),
            'no_reboot': self.v_noreboot.get(), 'no_inputgate': self.v_nogate.get(),
            'with_hmi': self.v_withhmi.get(), 'no_hmi_restart': self.v_nohmi.get(),
            'rpath': self.cur_path or '/', 'v2_page': self.page or 'conn', 'v2_log': self._log_h,
            'language': self.lang(), 'ui_lang': self._ui_lang,
            'store_no_renderer': self.v_store_norender.get(), 'store_no_uplink': self.v_store_nouplink.get(),
        })
        try:
            zoomed = IS_WIN and self.root.state() == 'zoomed'
            c['v2_zoomed'] = bool(zoomed)
            if not zoomed and self.root.state() == 'normal':
                c['v2_geometry'] = self.root.geometry()
        except tk.TclError:
            pass
        self.cfg = c
        try:
            tmp = CONFIG_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(c, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_FILE)
        except OSError:
            pass

    def on_option(self):
        self.save_config()

    def lang(self):
        """gewählte App-Sprache (nur Sprachen, die das Projekt kennt; sonst Englisch)"""
        v = self.v_lang.get() or 'en'
        return v if v in self._langs else 'en'

    def on_lang(self, label):
        code = next((c for c in self._langs if lang_label(c) == label), 'en')
        self.v_lang.set(code)
        self.save_config()
        self.refresh_project()

    # ------------------------------------------------------------------ Anzeige aktualisieren
    def refresh_project(self):
        repo = self.v_repo.get().strip()
        ok = repo_ok(repo)
        apps = list_apps(repo) if ok else []
        sig = tuple((a['id'], a['version']) for a in apps)
        if sig != self._app_sig:                  # Liste nur neu aufbauen, wenn sich die Apps geändert haben
            self._app_sig = sig
            prev = {k: v.get() for k, v in self.app_vars.items()}
            saved = self.cfg.get('apps')
            all_saved = self.cfg.get('all_apps', True) if saved is not None else True
            for w in self.apps_frame.winfo_children():
                w.destroy()
            self.app_vars = {}
            if not apps:
                self.label(self.apps_frame, tr('Noch kein Projekt – in Schritt 2 herunterladen oder Ordner wählen.'),
                           'muted').grid(row=0, column=0, sticky='w')
            cols = 3
            for c in range(cols):
                self.apps_frame.columnconfigure(c, weight=1, uniform='apps')
            for i, a in enumerate(apps):
                if a['id'] in prev:
                    val = prev[a['id']]
                elif saved is None or all_saved:
                    val = True
                else:
                    val = a['id'] in saved
                v = tk.BooleanVar(value=val)
                self.app_vars[a['id']] = v
                cell = ctk.CTkFrame(self.apps_frame, fg_color=C.CARD_HI, corner_radius=10)
                cell.grid(row=i // cols, column=i % cols, sticky='ew', padx=4, pady=4)
                cell.columnconfigure(0, weight=1)
                cb = ctk.CTkCheckBox(cell, text=a['title'], variable=v, font=self.f_bold, text_color=C.TEXT,
                                     fg_color=C.ACCENT, hover_color=C.ACCENT_HOVER, border_color='#4a5361',
                                     checkbox_width=20, checkbox_height=20, corner_radius=6, border_width=2,
                                     command=self.on_app_toggle)
                cb.grid(row=0, column=0, sticky='w', padx=(12, 4), pady=10)
                self.label(cell, 'v' + a['version'], 'tiny').grid(row=0, column=1, sticky='e', padx=(0, 12))
                if a['desc']:
                    Tooltip(self, cell, a['desc'])
                    Tooltip(self, cb, a['desc'])
            self.on_app_toggle(save=False)

        self._titles = {a['id']: a['title'] for a in apps}
        langs = repo_languages(repo) if ok else ['en']
        if langs != self._langs:
            self._langs = langs
            self.om_lang.configure(values=[lang_label(x) for x in langs])
        if len(langs) > 1:
            self.lang_row.grid()
        else:
            self.lang_row.grid_remove()
        self.om_lang.set(lang_label(self.lang()))
        sdk = ok and os.path.isfile(os.path.join(repo, 'oap-sdk', 'index.js'))
        cat = ok and os.path.isfile(os.path.join(repo, 'reference', 'someip-interfaces.json'))
        ki = kit_info(repo, self.lang()) if ok else None
        if ok:
            self.set_chip(self.chip_proj, tr('✔ Projekt · %d Apps') % len(apps), 'ok')
            self.lbl_proj.configure(text='' if sdk else tr('Zum Bauen fehlt noch das SDK: "SDK holen" (Head Unit muss verbunden sein).'))
        else:
            self.set_chip(self.chip_proj, tr('✖ Kein Projekt'), 'bad')
            self.lbl_proj.configure(text=tr('In diesem Ordner liegt kein idrive7-apps-Projekt. "Herunterladen" holt es '
                                         'von GitHub dorthin.'))
        if self.lbl_proj.cget('text'):
            self.lbl_proj.grid()
        else:
            self.lbl_proj.grid_remove()
        for chip, short in ((self.chip_sdk, False), (self.chip_read_sdk, False), (self.sb_chip_sdk, True)):
            self.set_chip(chip, ('SDK' if short else tr('SDK vorhanden')) if sdk else ('SDK' if short else tr('SDK fehlt')),
                          'ok' if sdk else ('bad' if ok else 'neutral'))
        for chip, short in ((self.chip_read_cat, False), (self.sb_chip_cat, True)):
            self.set_chip(chip, (tr('Katalog') if short else tr('Katalog vorhanden')) if cat else
                          (tr('Katalog') if short else tr('Katalog fehlt (optional)')), 'ok' if cat else 'neutral')
        if ki:
            t = datetime.datetime.fromtimestamp(ki['mtime']).strftime(tr('%d.%m.%Y %H:%M'))
            self.set_chip(self.sb_chip_kit, 'Kit', 'ok')
            self.sb_kit.configure(text=tr('Kit %s · %s') % (fmt_size(ki['size']), t))
            self.lbl_kit.configure(text=tr('%s  ·  %s  ·  gebaut %s') % (ki['rel'], fmt_size(ki['size']), t))
            self.lbl_kit_apps.configure(text=tr('Enthält: ') + (', '.join(ki['apps']) or '–'))
            checked = self.checked_kit and self.checked_kit[0] == ki['mtime']
            skipped = len(self.checked_notes[0]) if checked else 0
            self.set_chip(self.chip_checked, (tr('✔ Trockenlauf OK') + (tr(' · %d App(s) ausgelassen') % skipped if skipped else ''))
                          if checked else tr('noch nicht geprüft'), ('warn' if skipped else 'ok') if checked else 'neutral')
        else:
            self.set_chip(self.sb_chip_kit, 'Kit', 'neutral')
            self.sb_kit.configure(text=tr('noch kein Kit gebaut'))
            self.lbl_kit.configure(text=tr('Noch kein Kit gebaut.'))
            self.lbl_kit_apps.configure(text='' if sdk or not ok else
                                        tr('Zum Bauen wird das SDK gebraucht (Schritt 2 › SDK holen).'))
            self.set_chip(self.chip_checked, '', 'neutral')
            self.chip_checked.configure(text='', fg_color='transparent')
        self.btn_sdk.configure(fg_color=C.BTN if sdk else C.ACCENT, hover_color=C.BTN_HOVER if sdk else C.ACCENT_HOVER,
                               text_color=C.TEXT if sdk else '#ffffff')
        self.update_steps()

    def on_app_toggle(self, save=True):
        n = sum(1 for v in self.app_vars.values() if v.get())
        self.lbl_sel.configure(text=tr('%d von %d ausgewählt') % (n, len(self.app_vars)) if self.app_vars else '')
        if save:
            self.save_config()

    def select_apps(self, on):
        for v in self.app_vars.values():
            v.set(on)
        self.on_app_toggle()

    def check_tools(self):
        for dot, lb, _h in self.tool_labels.values():
            dot.configure(text_color=C.FAINT)
            lb.configure(text=tr('wird geprüft…'), text_color=C.MUTED)

        def work():
            res = {}
            py = find_python()
            if py:
                ok, v = tool_version(py + ['--version'])
                res['python'] = (ok and v.startswith('Python 3'), v or tr('nicht startbar'))
            else:
                res['python'] = (False, tr('nicht gefunden'))
            node = shutil.which('node')
            if node:
                ok, v = tool_version([node, '--version'])
                m = re.match(r'v(\d+)', v)
                res['node'] = (ok and bool(m) and int(m.group(1)) >= 14, v + ('' if m and int(m.group(1)) >= 14 else tr(' (zu alt)')))
            else:
                res['node'] = (False, tr('nicht gefunden'))
            ssh = find_ssh()
            if ssh:
                ok, v = tool_version([ssh, '-V'])
                res['ssh'] = (True, v or ssh)
            else:
                res['ssh'] = (False, tr('nicht gefunden'))
            git = shutil.which('git')
            res['git'] = (True, tool_version([git, '--version'])[1]) if git else (None, tr('nicht installiert'))
            self.ui(self.show_tools, res)
        threading.Thread(target=work, daemon=True).start()

    def show_tools(self, res):
        self._tool_res = res
        for k, (ch, name, tip) in self.pc_chips.items():
            ok, text = res.get(k, (False, '?'))
            self.set_chip(ch, '%s %s' % (name, '✓' if ok else '✖'), 'ok' if ok else 'bad')
            tip.text = _cur(text) if ok else '%s – %s' % (_cur(text), _cur(self.tool_labels[k][2]))
        for k, (dot, lb, hint) in self.tool_labels.items():
            ok, text = res.get(k, (False, '?'))
            if ok:
                dot.configure(text_color=C.GREEN)
                lb.configure(text=_cur(text), text_color=C.MUTED)
            elif ok is None:
                dot.configure(text_color=C.FAINT)
                lb.configure(text='%s – %s' % (_cur(text), _cur(hint)), text_color=C.MUTED)
            else:
                dot.configure(text_color=C.RED_TEXT)
                lb.configure(text='%s – %s' % (_cur(text), _cur(hint)), text_color=C.RED_TEXT)
        self.update_store_page()

    # ------------------------------------------------------------------ Protokoll / Threads
    def ui(self, fn, *args):
        self.q.put(('call', fn, args))

    def log(self, text, tag=None):
        if getattr(self, '_quiet_job', False) and tag != 'err':
            return
        self.q.put(('log', text, tag))

    def poll(self):
        n = 0
        try:
            while n < 400:
                item = self.q.get_nowait()
                n += 1
                if item[0] == 'log':
                    self._append(item[1], item[2])
                elif item[0] == 'call':
                    try:
                        item[1](*item[2])
                    except Exception:  # noqa: BLE001
                        self._append(traceback.format_exc(), 'err')
                elif item[0] == 'done':
                    self._done(*item[1:])
        except queue.Empty:
            pass
        self.root.after(40 if n else 80, self.poll)

    def _append(self, text, tag):
        t = self.txt
        t.configure(state='normal')
        t.insert('end', text + '\n', tag or None)
        lines = int(t.index('end-1c').split('.')[0])
        if lines > 30000:
            t.delete('1.0', '%d.0' % (lines - 25000))
        t.configure(state='disabled')
        t.see('end')

    def clear_log(self):
        self.txt.configure(state='normal')
        self.txt.delete('1.0', 'end')
        self.txt.configure(state='disabled')

    def save_log(self):
        p = filedialog.asksaveasfilename(title=tr('Protokoll speichern'), defaultextension='.txt', parent=self.root,
                                         initialfile=tr('idrive7-protokoll_%s.txt') % stamp(),
                                         filetypes=[('Text', '*.txt')])
        if p:
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write(self.txt.get('1.0', 'end'))

    def snapshot(self, needs):
        """Alle Eingaben im UI-Thread einsammeln und prüfen (Worker lesen keine Tk-Variablen)."""
        c = {
            'key': self.v_key.get().strip().strip('"'), 'ip': self.v_ip.get().strip(),
            'port': self.v_port.get().strip() or '22', 'repo': self.v_repo.get().strip().strip('"'),
            'apps': [k for k, v in self.app_vars.items() if v.get()],
            'all_apps': bool(self.app_vars) and all(v.get() for v in self.app_vars.values()),
            'no_reboot': self.v_noreboot.get(), 'no_inputgate': self.v_nogate.get(),
            'with_hmi': self.v_withhmi.get(), 'no_hmi_restart': self.v_nohmi.get(),
            'backup': self.v_backup.get().strip() or 'stock', 'vin': self.v_vin.get(),
            'lang': self.lang(),
            'store_norender': self.v_store_norender.get(), 'store_nouplink': self.v_store_nouplink.get(),
        }
        if 'conn' in needs:
            if not c['key'] or not os.path.isfile(c['key']):
                try:        # config nou (folder nou după dezarhivare): încearcă să găsești singur cheia
                    found = find_connect_files()
                    info = parse_connect(found[0]) if found else {}
                    if info.get('key') and os.path.isfile(info['key']):
                        self.apply_connect(info)
                        c['key'] = self.v_key.get().strip().strip('"')
                        c['ip'] = self.v_ip.get().strip()
                        c['port'] = self.v_port.get().strip() or '22'
                except Exception:
                    pass
            if not c['key'] or not os.path.isfile(c['key']):
                raise Abort(tr('Kein SSH-Schlüssel gewählt (Installieren › Schritt 1: "Durchsuchen…", "connect.txt '
                               'laden…" oder "Automatisch suchen").'))
            if not re.match(r'^[A-Za-z0-9.\-:\[\]]+$', c['ip']):
                raise Abort(tr('Bitte eine gültige IP-Adresse der Head Unit eintragen (Standard: %s).') % DEFAULT_IP)
            if not c['port'].isdigit():
                raise Abort(tr('Der Port muss eine Zahl sein (Standard: 22).'))
            c['ssh'] = find_ssh()
            if not c['ssh']:
                raise Abort(tr('Der OpenSSH-Client fehlt: Windows-Einstellungen › Apps › Optionale Features › '
                            '"OpenSSH-Client" hinzufügen.'))
            c['keyfile'] = prepare_key(c['key'])
        if 'repo' in needs or 'kit' in needs:
            if not repo_ok(c['repo']):
                raise Abort(tr('Kein idrive7-apps-Projekt gefunden. In Schritt 2 den Projektordner wählen oder '
                               '"Herunterladen / Aktualisieren" klicken.'))
        if 'kit' in needs:
            if not kit_info(c['repo'], c['lang']):
                raise Abort(tr('Es wurde noch kein Kit gebaut%s. Erst "Kit bauen" (Schritt 3).') % (
                    '' if c['lang'] == 'en' else tr(' (Sprache: %s)') % lang_label(c['lang'])))
        c['python'] = find_python()
        return c

    def precheck(self, title, *needs):
        """Voraussetzungen VOR Bestätigungsdialogen prüfen. -> True, wenn alles passt."""
        try:
            self.snapshot(needs)
            return True
        except Abort as e:
            self.alert(title, str(e), 'warn')
        except OSError as e:
            self.alert(title, str(e), 'error')
        return False

    def start(self, title, fn, *needs, quiet_fail=False, then=None):
        if self.busy:
            return
        self.save_config()
        try:
            c = self.snapshot(needs)
        except Abort as e:
            if not quiet_fail:
                self.alert(title, str(e), 'warn')
            return
        except OSError as e:
            if not quiet_fail:
                self.alert(title, str(e), 'error')
            return
        self.busy = True
        self.cancel_flag = False
        self._notice = None
        self.job_title = title
        for w in self.action_widgets:
            w.configure(state='disabled')
        self.btn_cancel.configure(state='normal', fg_color=C.RED)
        self.pb.grid()
        self.pb.start()
        self.set_status(title + ' …', 'busy')
        self._quiet_job = bool(quiet_fail)
        self.log('')
        self.log(tr('▶ %s   (%s)') % (title, datetime.datetime.now().strftime('%H:%M:%S')), 'head')

        def work():
            ok, msg = False, ''
            try:
                fn(c)
                ok = True
            except Abort as e:
                msg = str(e)
            except Exception as e:  # noqa: BLE001
                msg = tr('Unerwarteter Fehler: %s') % e
                self.log(traceback.format_exc(), 'err')
            self.q.put(('done', title, ok, msg, quiet_fail, then))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, title, ok, msg, quiet_fail, then):
        self.busy = False
        self._quiet_job = False
        self.proc = None
        for w in self.action_widgets:
            w.configure(state='normal')
        self.btn_cancel.configure(state='disabled', fg_color=C.BTN)
        self.pb.stop()
        self.pb.grid_remove()
        self.refresh_project()
        self.save_config()
        if ok:
            notice, self._notice = self._notice, None
            self.set_status(tr('%s – fertig%s.') % (title, tr(' (mit Hinweisen)') if notice else ''), 'ok')
            if then:
                then()
            if notice:
                self.alert(*notice)
            return
        self._notice = None
        cancelled = self.cancel_flag
        if quiet_fail:      # automatische Aktionen: kein Alarm, nur ein Hinweis
            self.set_status(tr('Bereit.'), 'idle')
            return
        self.set_status((tr('%s – abgebrochen.') if cancelled else tr('%s – fehlgeschlagen.')) % title, 'bad')
        if msg:
            self._append('✖ ' + msg, 'err')
        if msg and not cancelled:
            self.alert(title, msg, 'error')

    def cancel(self):
        if not self.busy:
            return
        if self.job_title in [tr(x) for x in self.ACTION_BUSY_CRITICAL]:
            if not self.ask(tr('Wirklich abbrechen?'), tr('Ein Abbruch mitten in der Installation/Wiederherstellung kann '
                            'die Head Unit in einem halben Zustand lassen.'), yes=tr('Trotzdem abbrechen'),
                            no=tr('Weiterlaufen lassen'), kind='danger', danger=True):
                return
        self.cancel_flag = True
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def on_close(self):
        if self.busy:
            if not self.ask(tr('Beenden?'), tr('Es läuft noch: %s.\nTrotzdem beenden?') % self.job_title, yes=tr('Beenden'),
                            no=tr('Weiterlaufen lassen'), kind='warn', danger=True):
                return False
            self.cancel_flag = True
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.kill()
                except OSError:
                    pass
        try:
            self.shell_disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.save_config()
        self.root.destroy()
        return True

    def open_dir(self, path, create=False):
        if create:
            os.makedirs(path, exist_ok=True)
        if path and os.path.isdir(path):
            open_path(path)
        else:
            self.alert(tr('Ordner'), tr('Den Ordner gibt es noch nicht.'), 'info', detail=path)

    # ------------------------------------------------------------------ Prozesse
    def run(self, args, cwd=None, stdin_file=None, stdin_bytes=None, out_file=None, env=None,
            label=None, echo=True):
        """Startet einen Prozess, streamt seine Ausgabe ins Protokoll. -> (returncode, zeilen)"""
        if self.cancel_flag:
            raise Abort(tr('Abgebrochen.'))
        self.log('$ ' + (label or ' '.join(args)), 'cmd')
        e = os.environ.copy()
        e.update({'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8', 'GIT_TERMINAL_PROMPT': '0'})
        if FROZEN:      # .exe: Variablen des PyInstaller-Starters nicht an Python/Node/git weiterreichen
            for k in list(e):
                if k.startswith('_PYI') or k in ('_MEIPASS2', 'TCL_LIBRARY', 'TK_LIBRARY'):
                    del e[k]
        if env:
            e.update(env)
        feed = stdin_file is not None or stdin_bytes is not None
        out = open(out_file, 'wb') if out_file else None
        try:
            p = subprocess.Popen(args, cwd=cwd, env=e,
                                 stdin=subprocess.PIPE if feed else subprocess.DEVNULL,
                                 stdout=out if out else subprocess.PIPE,
                                 stderr=subprocess.PIPE if out else subprocess.STDOUT,
                                 creationflags=NOWIN)
        except OSError as ex:
            if out:
                out.close()
            raise Abort(tr('Konnte %s nicht starten: %s') % (os.path.basename(args[0]), ex))
        self.proc = p
        writer = None
        if feed:
            def pump():
                try:
                    if stdin_file is not None:
                        with open(stdin_file, 'rb') as fh:
                            shutil.copyfileobj(fh, p.stdin, 1 << 16)
                    else:
                        p.stdin.write(stdin_bytes)
                except OSError:
                    pass
                finally:
                    try:
                        p.stdin.close()
                    except OSError:
                        pass
            writer = threading.Thread(target=pump, daemon=True)
            writer.start()
        lines = []
        stream = p.stderr if out else p.stdout
        for raw in iter(stream.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip('\r\n')
            lines.append(line)
            if echo:
                self.log(line, classify(line))
        rc = p.wait()
        if writer:
            writer.join(10)
        if out:
            out.close()
        self.proc = None
        if self.cancel_flag:
            raise Abort(tr('Abgebrochen.'))
        return rc, lines

    def ssh_cmd(self, c):
        nul = null_dev(c['ssh'])
        return [c['ssh'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=' + nul, '-o', 'GlobalKnownHostsFile=' + nul,
                '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=15', '-o', 'LogLevel=ERROR',
                '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4',
                '-p', c['port'], '-i', c['keyfile'], 'root@' + c['ip']]

    def ssh_run(self, c, args, **kw):
        """ssh-Aufruf; aktualisiert die Verbindungsanzeige. rc 255 = ssh selbst ist gescheitert."""
        rc, lines = self.run(self.ssh_cmd(c) + args, **kw)
        detail = '%s\nVIN %s' % (c['ip'], c.get('vin') or '–')
        self.ui(self.set_conn, 'off' if rc == 255 else 'on', detail)
        return rc, lines

    def remote(self, c, script, out_file=None, label=None, echo=True):
        """Shell-Skript auf der Unit ausführen (per stdin an 'sh -s'; kein Quoting-Ärger unter Windows)."""
        body = '{\n' + script.strip('\n') + '\n} </dev/null\n'
        rc, lines = self.ssh_run(c, ['sh -s'], stdin_bytes=body.encode('utf-8'),
                                 out_file=out_file, label=label or tr('Head Unit › Skript'), echo=echo)
        if rc == 255:
            raise Abort(ssh_hint(lines))
        return rc, lines

    def deploy(self, c, sub, envs=()):
        pre = ''.join('%s=1 ' % e for e in envs)
        cmd = 'cd %s && %ssh deploy-car.sh %s' % (DEST, pre, sub)
        return self.ssh_run(c, [cmd], label='Head Unit › ' + cmd)

    def push_kit(self, c):
        ki = kit_info(c['repo'], c['lang'])
        if not ki:
            raise Abort(tr('Kein Kit gefunden. Erst "Kit bauen".'))
        self.log(tr('Übertrage das Kit (%s, %s) nach %s …') % (os.path.basename(ki['path']), fmt_size(ki['size']), DEST))
        # wie push-to-car.ps1: tar.gz byteweise über ssh-stdin, apps/ wird ersetzt, dann md5-Prüfung
        cmd = ('mkdir -p %s && rm -rf %s/apps && tar xzf - -C /var/opt && cd %s && '
               'md5sum -c KIT_SUMS >/dev/null && echo KIT_OK' % (DEST, DEST, DEST))
        rc, lines = self.ssh_run(c, [cmd], stdin_file=ki['path'], label='Head Unit › ' + cmd, echo=False)
        for ln in lines:
            if ln != 'KIT_OK':
                self.log(ln, classify(ln))
        if rc == 255:
            raise Abort(ssh_hint(lines))
        if rc != 0 or 'KIT_OK' not in lines:
            raise Abort(tr('Das Kit konnte nicht übertragen/geprüft werden (Exit %d). Siehe Protokoll.') % rc)
        self.log(tr('Kit übertragen und per Prüfsumme bestätigt.'), 'ok')
        return ki

    def ensure_remote_kit(self, c):
        rc, lines = self.remote(c, '[ -f %s/deploy-car.sh ] && echo HAVE_KIT || echo NO_KIT' % DEST, echo=False)
        if 'HAVE_KIT' in lines:
            return
        if repo_ok(c['repo']) and kit_info(c['repo'], c['lang']):
            self.log(tr('Auf der Unit liegt noch kein Kit – übertrage es zuerst (installiert nichts).'), 'warn')
            self.push_kit(c)
            return
        raise Abort(tr('Auf der Head Unit liegt noch kein Kit (deploy-car.sh) und lokal wurde noch keins '
                       'gebaut.\nErst in Schritt 3 ein Kit bauen.'))

    def save_output(self, name, lines, header=''):
        os.makedirs(OUT_DIR, exist_ok=True)
        p = os.path.join(OUT_DIR, '%s_%s.txt' % (name, stamp()))
        with open(p, 'w', encoding='utf-8') as fh:
            if header:
                fh.write(header + '\n')
            fh.write('\n'.join(lines) + '\n')
        self.log(tr('Gespeichert: %s') % p, 'ok')
        return p

    # ------------------------------------------------------------------ Jobs: Verbindung/Projekt
    def job_find_key(self, c):
        self.log(tr('Suche connect.txt + id_rsa des SSH-Tools (data\\enrollments\\<VIN>\\) …'))
        found = find_connect_files()
        if not found:
            raise Abort(tr('Kein Schlüssel gefunden. Bitte "Durchsuchen…" oder "connect.txt laden…" benutzen.'))
        for p in found:
            self.log(tr('  gefunden: ') + p)
        info = parse_connect(found[0])
        if not info.get('key'):
            raise Abort(tr('Neben %s liegt kein id_rsa.') % found[0])
        self.ui(self.apply_connect, info)
        self.log(tr('Übernommen: %s') % found[0], 'ok')

    def apply_connect(self, info):
        if info.get('key'):
            self.v_key.set(info['key'])
        if info.get('ip'):
            self.v_ip.set(info['ip'])
        if info.get('port'):
            self.v_port.set(info['port'])
        if info.get('vin'):
            self.v_vin.set(info['vin'].upper())
        self.set_conn('unknown', '%s\nVIN %s' % (self.v_ip.get(), self.v_vin.get()))
        self.save_config()

    def browse_key(self):
        p = filedialog.askopenfilename(title=tr('SSH-Schlüssel (id_rsa) wählen'), parent=self.root,
                                       filetypes=[(tr('SSH-Schlüssel'), 'id_rsa *.pem *.key'), (tr('Alle Dateien'), '*.*')])
        if not p:
            return
        p = os.path.normpath(p)
        ct = os.path.join(os.path.dirname(p), 'connect.txt')
        info = parse_connect(ct) if os.path.isfile(ct) else {}
        info['key'] = p
        self.apply_connect(info)
        self.log(tr('Schlüssel gewählt: %s%s') % (p, tr(' (+ connect.txt gelesen)') if os.path.isfile(ct) else ''))

    def browse_connect(self):
        p = filedialog.askopenfilename(title=tr('connect.txt des SSH-Tools wählen'), parent=self.root,
                                       filetypes=[('connect.txt', 'connect.txt'), ('Text', '*.txt')])
        if not p:
            return
        info = parse_connect(os.path.normpath(p))
        if not info.get('key'):
            self.alert('connect.txt', tr('Neben dieser connect.txt liegt kein id_rsa.'), 'warn', detail=p)
            return
        self.apply_connect(info)
        self.log(tr('connect.txt gelesen: %s  →  %s, %s') % (p, info.get('ip', '?'), info.get('vin', '')))

    def browse_repo(self):
        p = filedialog.askdirectory(title=tr('Projektordner idrive7-apps wählen'), parent=self.root,
                                    initialdir=self.v_repo.get() or HERE)
        if p:
            self.v_repo.set(os.path.normpath(p))
            self.refresh_project()
            self.save_config()

    def job_test(self, c):
        rc, lines = self.remote(c, 'echo MGU_OK; id -u; uname -n; uname -r; uptime')
        if 'MGU_OK' not in lines:
            raise Abort(tr('Unerwartete Antwort der Head Unit (Exit %d).') % rc)
        i = lines.index('MGU_OK')
        uid = lines[i + 1] if len(lines) > i + 1 else '?'
        host = lines[i + 2] if len(lines) > i + 2 else '?'
        if uid != '0':
            self.log(tr('Verbunden, aber nicht als root (uid %s) – Installieren braucht root.') % uid, 'warn')
        else:
            self.log(tr('Verbindung OK – root auf "%s" (%s).') % (host, c['ip']), 'ok')

    def job_repo(self, c):
        repo = c['repo'] or os.path.join(HERE, 'idrive7-apps')
        git = shutil.which('git')
        if git and os.path.isdir(os.path.join(repo, '.git')):
            rc, _ = self.run([git, '-C', repo, 'pull', '--ff-only'])
            if rc:
                raise Abort(tr('git pull ist fehlgeschlagen (lokale Änderungen?). Siehe Protokoll.'))
        elif git and (not os.path.exists(repo) or not os.listdir(repo)):
            rc, _ = self.run([git, 'clone', '--depth', '1', REPO_URL + '.git', repo])
            if rc:
                raise Abort(tr('git clone ist fehlgeschlagen. Internetverbindung?'))
        else:
            self.download_zip(repo)
        if not repo_ok(repo):
            raise Abort(tr('Nach dem Download fehlt kit/tools/make_kit.py in %s.') % repo)
        self.ui(self.v_repo.set, repo)
        self.log(tr('Projekt bereit: %s') % repo, 'ok')

    def download_zip(self, repo):
        self.log(tr('Lade %s …') % ZIP_URL)
        fd, tmp = tempfile.mkstemp(suffix='.zip')
        os.close(fd)
        try:
            req = urllib.request.Request(ZIP_URL, headers={'User-Agent': 'idrive7-apps-gui'})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, 'wb') as fh:
                got, nxt = 0, 4 << 20
                while True:
                    if self.cancel_flag:
                        raise Abort(tr('Abgebrochen.'))
                    b = r.read(1 << 18)
                    if not b:
                        break
                    fh.write(b)
                    got += len(b)
                    if got >= nxt:
                        self.log('  … %s' % fmt_size(got), 'cmd')
                        nxt += 4 << 20
            self.log(tr('Heruntergeladen: %s – entpacke nach %s') % (fmt_size(os.path.getsize(tmp)), repo))
            with zipfile.ZipFile(tmp) as z:
                names = z.namelist()
                prefix = names[0].split('/')[0] + '/' if names else ''
                n = 0
                for info in z.infolist():
                    rel = info.filename[len(prefix):] if info.filename.startswith(prefix) else info.filename
                    parts = [x for x in rel.split('/') if x]
                    if not parts or '..' in parts or ':' in rel:
                        continue
                    target = os.path.join(repo, *parts)
                    if info.is_dir():
                        os.makedirs(target, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(info) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    n += 1
            self.log(tr('%d Dateien entpackt (vorhandenes oap-sdk, reference, dist bleiben erhalten).') % n, 'ok')
        except Abort:
            raise
        except Exception as e:  # noqa: BLE001
            raise Abort(tr('Download fehlgeschlagen: %s') % e)
        finally:
            self._rm(tmp)

    # ------------------------------------------------------------------ Jobs: Auslesen
    def job_info(self, c):
        rc, lines = self.remote(c, INFO_SCRIPT, label=tr('Head Unit › Geräteinfo (nur lesen)'))
        hdr = tr('Geräteinfo %s – %s  (VIN laut connect.txt: %s)\n') % (
            datetime.datetime.now().strftime(tr('%d.%m.%Y %H:%M')), c['ip'], c.get('vin') or '–')
        self.save_output('geraeteinfo', lines, hdr)

    def job_sdk(self, c):
        sdkdir = os.path.join(OUT_DIR, 'sdk')
        os.makedirs(sdkdir, exist_ok=True)
        rc, lines = self.remote(c, 'ls -1 /usr/lib/oap-mainapp/preinstalled_apps/x_call@*.tgz 2>/dev/null | head -n 1',
                                label=tr('Head Unit › x_call in der Firmware suchen'), echo=False)
        src = next((ln.strip() for ln in lines if ln.strip().startswith('/')), None)
        if src:
            self.log(tr('Firmware-x_call gefunden: %s') % src)
            local = os.path.join(sdkdir, os.path.basename(src))
            rc, _ = self.remote(c, 'cat %s' % shq(src), out_file=local,
                                label=tr('Head Unit › %s herunterladen') % os.path.basename(src))
            if rc or os.path.getsize(local) == 0:
                raise Abort(tr('Konnte %s nicht lesen.') % src)
            self.log(tr('Heruntergeladen: %s (%s)') % (local, fmt_size(os.path.getsize(local))))
            appjs = self.appjs_from_tgz(local, sdkdir)
        else:
            self.log(tr('Kein x_call@*.tgz in der Firmware – nehme die installierte x_call-App '
                     '(neueres SDK ohne telephony/appstart/map/poi; reicht für die Kit-Apps).'), 'warn')
            rc, lines = self.remote(c, r'''
U=$(tr -d '\r' < /var/opt/public/oap/oap_12201/security/uids.txt 2>/dev/null | sed -n 's/^\([0-9]*\)=x_call$/\1/p' | head -n 1)
F=/var/opt/public/oap/oap_${U:-12204}/source/javascript/app.js
[ -f "$F" ] && echo "$F"
true''', echo=False)
            src = next((ln.strip() for ln in lines if ln.strip().startswith('/')), None)
            if not src:
                raise Abort(tr('Keine x_call-App auf der Unit gefunden. Alternative: Tools › Diagnose › "SDK aus '
                               'Datei…".'))
            base = os.path.join(sdkdir, 'x_call_installiert')
            os.makedirs(os.path.join(base, 'javascript'), exist_ok=True)
            appjs = os.path.join(base, 'javascript', 'app.js')
            rc, _ = self.remote(c, 'cat %s' % shq(src), out_file=appjs, label=tr('Head Unit › %s herunterladen') % src)
            if rc or os.path.getsize(appjs) == 0:
                raise Abort(tr('Konnte %s nicht lesen.') % src)
            pkg = src[:-len('javascript/app.js')] + 'package.json'      # für die SDK-Versionsnummer
            pj = os.path.join(base, 'package.json')
            rc, _ = self.remote(c, 'cat %s 2>/dev/null' % shq(pkg), out_file=pj, label=tr('Head Unit › package.json'),
                                echo=False)
            if rc or not os.path.getsize(pj):
                self._rm(pj)
        self.extract_sdk(c, appjs)

    def appjs_from_tgz(self, tgz, dest):
        """Entpackt das x_call-Paket nach dest/x_call/ (wie 'tar xzf … -C x_call' in der Anleitung).

        extract_sdk.js liest die SDK-Version aus der package.json neben javascript/, deshalb wird das
        ganze Paket entpackt und nicht nur app.js."""
        out = os.path.join(dest, 'x_call')
        shutil.rmtree(out, ignore_errors=True)
        appjs = None
        try:
            with tarfile.open(tgz, 'r:*') as tf:
                for m in tf.getmembers():
                    parts = [x for x in m.name.replace('\\', '/').split('/') if x not in ('', '.')]
                    if not m.isfile() or not parts or '..' in parts or ':' in m.name:
                        continue
                    target = os.path.join(out, *parts)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with tf.extractfile(m) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    if '/'.join(parts).endswith('javascript/app.js') and (appjs is None or len(target) < len(appjs)):
                        appjs = target
        except (tarfile.TarError, OSError) as e:
            raise Abort(tr('%s ist kein gültiges .tgz: %s') % (os.path.basename(tgz), e))
        if not appjs:
            raise Abort(tr('In %s steckt keine javascript/app.js.') % os.path.basename(tgz))
        self.log(tr('Entpackt nach %s (app.js: %s)') % (out, fmt_size(os.path.getsize(appjs))))
        return appjs

    def extract_sdk(self, c, appjs):
        node = shutil.which('node')
        if not node:
            raise Abort(tr('Node.js fehlt (nodejs.org, LTS).'))
        out = os.path.join(c['repo'], 'oap-sdk')
        rc, _ = self.run([node, os.path.join(c['repo'], 'kit', 'tools', 'extract_sdk.js'), appjs, out], cwd=c['repo'])
        if rc or not os.path.isfile(os.path.join(out, 'index.js')):
            raise Abort(tr('extract_sdk.js ist fehlgeschlagen (Exit %d). Siehe Protokoll.') % rc)
        self.log(tr('SDK bereit: %s – jetzt kann gebaut werden.') % out, 'ok')
        self.log(tr('Hinweis: oap-sdk ist BMW-Code – nicht veröffentlichen (steht in .gitignore).'), 'cmd')

    def sdk_from_file(self):
        if self.busy:
            return
        p = filedialog.askopenfilename(title=tr('x_call@….tgz oder app.js wählen'), parent=self.root,
                                       filetypes=[(tr('x_call-Paket oder app.js'), '*.tgz *.tar.gz *.js'),
                                                  (tr('Alle Dateien'), '*.*')])
        if not p:
            return
        p = os.path.normpath(p)

        def job(c):
            if p.lower().endswith('.js'):
                appjs = p
            else:
                d = os.path.join(OUT_DIR, 'sdk')
                os.makedirs(d, exist_ok=True)
                appjs = self.appjs_from_tgz(p, d)
            self.extract_sdk(c, appjs)
        self.start(tr('SDK aus Datei'), job, 'repo')

    def job_catalog(self, c):
        dst = os.path.join(c['repo'], 'reference', 'someip-interfaces.json')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + '.part'
        try:
            rc, lines = self.remote(c, 'cat /etc/oap/someip/someip-interfaces.json', out_file=tmp,
                                    label=tr('Head Unit › /etc/oap/someip/someip-interfaces.json herunterladen'))
            if rc or not os.path.getsize(tmp):
                raise Abort(tr('Katalog nicht lesbar (Exit %d).') % rc)
            try:
                with open(tmp, encoding='utf-8') as fh:
                    db = json.load(fh)
                n = len(db.get('interfaces', {}))
            except (OSError, ValueError) as e:
                raise Abort(tr('Die geladene Datei ist kein gültiges JSON: %s') % e)
            os.replace(tmp, dst)
        finally:
            self._rm(tmp)
        self.log(tr('Katalog gespeichert: %s (%s, %d Interfaces)') % (dst, fmt_size(os.path.getsize(dst)), n), 'ok')

    def job_check_signals(self, c):
        cat = os.path.join(c['repo'], 'reference', 'someip-interfaces.json')
        if not os.path.isfile(cat):
            raise Abort(tr('Erst den SOME/IP-Katalog holen.'))
        if not c['python']:
            raise Abort(tr('Python wurde nicht gefunden.'))
        rc, lines = self.run(c['python'] + [os.path.join('kit', 'tools', 'check_signals.py')], cwd=c['repo'],
                             echo=False)
        real, opt = [], []
        for ln in lines:
            m = re.match(r'^(\S+) MISSING IFACE (\S+)\s*$', ln)
            loss = optional_ifaces(c['repo'], m.group(1)).get(m.group(2)) if m else None
            if loss:
                opt.append((m.group(1), m.group(2), loss))
                self.log(ln + tr('   (optional)'), 'cmd')
            elif ln.startswith('problems:'):
                continue
            else:
                real.append(ln)
                self.log(ln, 'warn')
        if opt:
            self.log(tr('%d optionale Schnittstelle(n) fehlen auf dieser Unit – unproblematisch, die App wird ohne sie '
                     'installiert:') % len(opt), 'cmd')
            for a, i, loss in opt:
                self.log(tr('  %s ohne %s: %s') % (self._titles.get(a, a), i.rsplit('.', 1)[-1], loss), 'cmd')
        if not real:
            self.log(tr('Alle Signal-Flags passen zum Katalog dieser Unit.'), 'ok')
        else:
            self.log(tr('%d Abweichung(en). "r" auf einem Feld ohne Getter → in app.json auf "s" ändern. Eine App, die '
                     'die Unit ablehnt, lässt deploy-car.sh beim Installieren aus (die anderen werden installiert).')
                     % len(real), 'warn')

    def job_status(self, c):
        self.ensure_remote_kit(c)
        rc, lines = self.deploy(c, 'status')
        if rc == 255:
            raise Abort(ssh_hint(lines))
        self.save_output('status', lines, tr('Status %s – %s\n') % (datetime.datetime.now().strftime(tr('%d.%m.%Y %H:%M')), c['ip']))

    def job_backups(self, c):
        self.ensure_remote_kit(c)
        rc, lines = self.deploy(c, 'backups')
        if rc == 255:
            raise Abort(ssh_hint(lines))
        names = []
        for ln in lines:
            m = re.match(r'^ {4}(\S+)\s*$', ln)
            if m and m.group(1) not in names:
                names.append(m.group(1))
        names.sort(reverse=True)
        self.ui(lambda: self.cb_backup.configure(values=['stock', 'latest'] + names))
        self.log(tr('%d Backup(s) gefunden – unter "Installieren › Zurück auf Serie" auswählbar.') % len(names),
                 'ok' if names else 'warn')

    def job_logs(self, c):
        dest = os.path.join(OUT_DIR, 'logs_%s' % stamp())
        os.makedirs(dest, exist_ok=True)
        tgz = os.path.join(dest, '_logs.tar.gz')
        try:
            rc, lines = self.remote(c, LOGS_SCRIPT, out_file=tgz, label=tr('Head Unit › Logs einsammeln (nur lesen)'))
        except Abort:
            self._rm(tgz)
            self._rmdir(dest)
            raise
        if rc == 3 or 'NO_LOGS' in lines:
            self._rm(tgz)
            self._rmdir(dest)
            raise Abort(tr('Auf der Unit wurden keine Logs von Custom-Apps gefunden (noch nichts installiert?).'))
        if rc:
            raise Abort(tr('Logs konnten nicht geladen werden (Exit %d).') % rc)
        names = []
        try:
            with tarfile.open(tgz, 'r:gz') as tf:
                for m in tf.getmembers():
                    if not m.isfile():
                        continue
                    base = os.path.basename(m.name.replace('\\', '/'))
                    um = re.search(r'oap_(\d+)/', m.name)
                    target = os.path.join(dest, base)
                    if os.path.exists(target) and um:
                        target = os.path.join(dest, '%s_%s' % (um.group(1), base))
                    with tf.extractfile(m) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    names.append((os.path.basename(target), m.size))
        except tarfile.TarError as e:
            raise Abort(tr('Log-Archiv beschädigt: %s') % e)
        self._rm(tgz)
        for n, s in sorted(names):
            self.log('  %-40s %s' % (n, fmt_size(s)))
        self.log(tr('%d Dateien gespeichert in %s') % (len(names), dest), 'ok')
        open_path(dest)

    # ------------------------------------------------------------------ Jobs: Bauen & Flashen
    def job_build(self, c):
        if not os.path.isfile(os.path.join(c['repo'], 'oap-sdk', 'index.js')):
            raise Abort(tr('Zum Bauen fehlt das BMW-SDK (oap-sdk/).\nSchritt 2 › "SDK holen" (oder Tools › Diagnose '
                           '› "SDK aus Datei…").'))
        if not c['apps']:
            raise Abort(tr('Keine App ausgewählt.'))
        if not shutil.which('node'):
            raise Abort(tr('Node.js fehlt (nodejs.org, LTS) – der Build zeichnet damit die App-Icons.'))
        if not c['python']:
            raise Abort(tr('Python wurde nicht gefunden.'))
        if 'doom' in c['apps'] and not os.path.isfile(os.path.join(c['repo'], 'apps', 'doom', 'freedoom1.wad')):
            self.log(tr('Doom: der Build lädt beim ersten Mal die Freedoom-Spieldaten (~24 MB) herunter.'), 'warn')
        t0 = time.time()
        args = c['python'] + [os.path.join('kit', 'tools', 'make_kit.py')]
        if c['lang'] != 'en':
            args += ['--language', c['lang']]
        args += [] if c['all_apps'] else c['apps']
        rc, _ = self.run(args, cwd=c['repo'])
        ki = kit_info(c['repo'], c['lang'])
        if rc or not ki or ki['mtime'] < t0 - 2:
            raise Abort(tr('Der Build ist fehlgeschlagen (Exit %d). Siehe Protokoll.') % rc)
        self.checked_kit = None
        self.checked_notes = ([], [])
        self.log(tr('Kit fertig: %s (%s) – %s') % (ki['path'], fmt_size(ki['size']), ', '.join(ki['apps'])), 'ok')
        self.log(tr('Die .mgu-Dateien enthalten BMW-Code: nur fürs eigene Auto, nicht veröffentlichen.'), 'cmd')

    def job_check(self, c):
        ki = self.push_kit(c)
        rc, lines = self.deploy(c, 'check')
        if rc == 255:
            raise Abort(ssh_hint(lines))
        if rc != 0:
            self.checked_kit = None
            if 'refuses every app' in '\n'.join(lines):
                raise Abort(tr('Die Head Unit lehnt alle Apps im Kit ab (siehe Protokoll). Es wurde nichts verändert.'))
            raise Abort(tr('Der Trockenlauf meldet ein Problem (siehe Protokoll). Es wurde nichts verändert.'))
        notes = unit_notes(lines)
        self.checked_notes = notes
        self.checked_kit = (ki['mtime'], c['ip'])
        self.log(tr('Trockenlauf OK – die Head Unit ist bereit für "Installieren".'), 'ok')
        self.report_notes(notes, tr('Trockenlauf OK – mit Hinweisen'),
                          tr('Die Head Unit ist bereit. Diese Apps lehnt sie aber ab (ihre Firmware hat eine benötigte '
                          'Schnittstelle nicht); "Installieren" lässt sie aus und installiert den Rest:'))

    def report_notes(self, notes, title, intro):
        """ausgelassene Apps / fehlende optionale Schnittstellen ins Protokoll und als Hinweis nach dem Job"""
        skipped, optional = notes
        if not skipped and not optional:
            return
        rows = []
        for n in skipped:
            a = n.split('@', 1)[0]
            rows.append(tr('✖ %s  (%s) – wird nicht installiert') % (self._titles.get(a, a), n))
        for a, iface, loss in optional:
            rows.append(tr('◐ %s – ohne %s: %s') % (self._titles.get(a, a), iface.rsplit('.', 1)[-1], loss))
        for r in rows:
            self.log(r, 'warn')
        msg = intro if skipped else tr('Alles OK. Einzelne Apps laufen auf dieser Unit mit weniger Werten:')
        self._notice = (title, msg, 'warn', '\n'.join(rows))

    def install(self):
        if self.busy or not self.precheck(tr('Installieren'), 'conn', 'kit'):
            return
        if not self.ask(tr('Installation starten?'),
                        tr('Vorher wird automatisch ein Backup angelegt (das allererste bleibt als "stock"). '
                        'Die App-Plattform startet neu, die Kacheln erscheinen nach 1–2 Minuten.'),
                        yes=tr('Installieren'), kind='warn',
                        detail=tr('• Auto geparkt, Zündung an, am besten Ladegerät dran\n'
                               '• Nicht abbrechen, Zündung anlassen\n'
                               '• Mit Doom/Controller-Gate startet die Head Unit am Ende\n'
                               '  einmal neu (Bildschirm ~1 Min. schwarz)')):
            return
        ki = kit_info(self.v_repo.get().strip(), self.lang())
        do_check = False
        if not ki or self.checked_kit != (ki['mtime'], self.v_ip.get().strip()):
            a = Dialog(self, tr('Erst prüfen?'), tr('Für dieses Kit gab es noch keinen erfolgreichen Trockenlauf. '
                                             'Empfohlen: erst prüfen und nur bei Erfolg installieren.'),
                       [(tr('Abbrechen'), None, 'secondary'), (tr('Direkt installieren'), False, 'secondary'),
                        (tr('Prüfen + installieren'), True, 'primary')], 'question').run()
            if a is None:
                return
            do_check = a

        def job(c):
            if do_check:
                self.job_check(c)
                self.log('')
                self.log(tr('▶ Installieren'), 'head')
            else:
                self.push_kit(c)
            envs = [k for k, on in (('NO_REBOOT', c['no_reboot']), ('NO_INPUTGATE', c['no_inputgate']),
                                    ('WITH_HMI', c['with_hmi'])) if on]
            rc, lines = self.deploy(c, 'install', envs)
            text = '\n'.join(lines)
            notes = unit_notes(lines)
            intro = tr('Diese Apps lehnt die Head Unit ab, sie wurden ausgelassen; alle anderen sind installiert:')
            if 'rebooting the head unit' in text and rc != 0:
                self.log(tr('Die Head Unit startet jetzt neu (Controller-Gate). Bildschirm ~1 Min. schwarz, danach '
                         'kommt alles von selbst wieder. Später mit "Status" prüfen.'), 'ok')
                self.ui(self.set_conn, 'unknown')
                ki = kit_info(c['repo'], c['lang'])
                self._installed = (ki['mtime'], c['ip']) if ki else None
                self.report_notes(notes, tr('Installiert – mit Hinweisen'), intro)
                return
            if rc == 255:
                raise Abort(ssh_hint(lines))
            if rc != 0:
                raise Abort(tr('Die Installation meldet einen Fehler (Exit %d, siehe Protokoll).\n'
                            'Zurück auf Serie geht mit "Wiederherstellen" (stock).') % rc)
            self.log(tr('Installation fertig. Die Kacheln erscheinen in 1–2 Minuten unter "Apps".'), 'ok')
            ki = kit_info(c['repo'], c['lang'])
            self._installed = (ki['mtime'], c['ip']) if ki else None
            self.report_notes(notes, tr('Installiert – mit Hinweisen'), intro)
        self.start(tr('Prüfen und installieren') if do_check else tr('Installieren'), job, 'conn', 'kit')

    def restore(self):
        if self.busy:
            return
        name = self.v_backup.get().strip() or 'stock'
        if not re.match(r'^[A-Za-z0-9._-]+$', name):
            self.alert(tr('Wiederherstellen'), tr('Ungültiger Backup-Name.'), 'warn', detail=name)
            return
        if not self.precheck(tr('Wiederherstellen'), 'conn'):
            return
        res = Dialog(self, tr('Backup "%s" wiederherstellen?') % name,
                     tr('Entfernt alle Custom-Apps, den Hook und den Renderer und setzt jede berührte Datei auf den '
                     'Stand des Backups zurück. Danach startet %s neu.\n\nZur Bestätigung YES eingeben:')
                     % (tr('die App-Plattform') if self.v_nohmi.get() else tr('das HMI')),
                     [(tr('Abbrechen'), False, 'secondary'), (tr('Wiederherstellen'), True, 'danger')], 'danger',
                     entry='YES').run()
        if not res or not res[0]:
            return
        if (res[1] or '').strip() != 'YES':
            self.alert(tr('Wiederherstellen'), tr('Abgebrochen – es wurde nichts geändert (YES nicht eingegeben).'), 'info')
            return

        def job(c):
            self.ensure_remote_kit(c)
            envs = ['YES'] + (['NO_HMI_RESTART'] if c['no_hmi_restart'] else [])
            rc, lines = self.deploy(c, 'restore' + ('' if name == 'stock' else ' ' + name), envs)
            if rc == 255:
                raise Abort(ssh_hint(lines))
            if rc != 0:
                raise Abort(tr('Wiederherstellen meldet einen Fehler (Exit %d, siehe Protokoll).') % rc)
            self.log(tr('Wiederhergestellt (%s). Die Unit ist wieder im Stand des Backups.') % name, 'ok')
        self.start(tr('Wiederherstellen'), job, 'conn')

    def _rm(self, path):
        try:
            os.remove(path)
        except OSError:
            pass

    def _rmdir(self, path):
        try:
            os.rmdir(path)
        except OSError:
            pass

    # ================================================================= Seite: Shell
    def build_page_shell(self, f):
        f.rowconfigure(2, weight=1)
        top = ctk.CTkFrame(f, fg_color='transparent')
        top.grid(row=0, column=0, sticky='ew')
        self.btn_shell = self.button(top, tr('Verbinden'), self.shell_toggle, 'primary', width=130, action=False)
        self.btn_shell.pack(side='left')
        self.btn_intr = self.button(top, tr('Strg+C'), self.shell_interrupt, width=90, action=False)
        self.btn_intr.pack(side='left', padx=(8, 0))
        Tooltip(self, self.btn_intr, tr('Laufenden Befehl abbrechen (z. B. journalctl -f, ping, top).'))
        self.button(top, tr('Leeren'), self.shell_clear, 'ghost', width=80, action=False).pack(side='left', padx=4)
        self.sh_state = ctk.CTkLabel(top, text=tr('●  nicht verbunden'), font=self.f_small, text_color=C.FAINT)
        self.sh_state.pack(side='right')

        pf = ctk.CTkFrame(f, fg_color='transparent')
        pf.grid(row=1, column=0, sticky='ew', pady=(12, 0))
        cols = 5
        for c in range(cols):
            pf.columnconfigure(c, weight=1, uniform='presets')
        for i, (label, cmd, changes) in enumerate(SHELL_PRESETS):
            b = ctk.CTkButton(pf, text=('↻ ' if changes else '') + tr(label), height=30, corner_radius=15,
                              font=self.f_small, fg_color=C.AMBER_SOFT if changes else C.CARD_HI,
                              hover_color=C.BTN_HOVER, text_color=C.AMBER_TEXT if changes else C.TEXT,
                              border_width=1, border_color=C.BORDER,
                              command=lambda cmd=cmd, ch=changes, lb=tr(label): self.shell_preset(cmd, ch, lb))
            b.grid(row=i // cols, column=i % cols, sticky='ew', padx=3, pady=3)
            Tooltip(self, b, cmd)

        con = ctk.CTkFrame(f, fg_color=C.CONSOLE, corner_radius=12, border_width=1, border_color=C.BORDER)
        con.grid(row=2, column=0, sticky='nsew', pady=(12, 0))
        con.rowconfigure(0, weight=1)
        con.columnconfigure(0, weight=1)
        self.sh_txt = ctk.CTkTextbox(con, fg_color=C.CONSOLE, text_color=C.CONSOLE_TEXT, font=self.f_mono,
                                     wrap='char', corner_radius=10, border_width=0, scrollbar_button_color=C.BTN,
                                     scrollbar_button_hover_color=C.BTN_HOVER)
        self.sh_txt.grid(row=0, column=0, sticky='nsew', padx=4, pady=(4, 0))
        self.sh_txt.configure(state='disabled')
        self.sh_txt.tag_config('sys', foreground=C.ACCENT_TEXT)
        inp = ctk.CTkFrame(con, fg_color=C.INPUT, corner_radius=10)
        inp.grid(row=1, column=0, sticky='ew', padx=8, pady=8)
        inp.columnconfigure(1, weight=1)
        ctk.CTkLabel(inp, text='#', font=self.f_mono, text_color=C.GREEN_TEXT, width=20).grid(row=0, column=0, padx=(10, 0))
        self.sh_in = tk.StringVar()
        self.sh_entry = ctk.CTkEntry(inp, textvariable=self.sh_in, font=self.f_mono, height=34, border_width=0,
                                     fg_color=C.INPUT, text_color=C.TEXT,
                                     placeholder_text=tr('Befehl eingeben – Enter sendet, ↑/↓ Verlauf, Strg+C bricht ab'))
        self.sh_entry.grid(row=0, column=1, sticky='ew', padx=4)
        self.sh_entry.configure(state='disabled')
        self.sh_entry.bind('<Return>', self.shell_send)
        self.sh_entry.bind('<Up>', lambda e: self.shell_history(-1))
        self.sh_entry.bind('<Down>', lambda e: self.shell_history(1))
        self.sh_entry.bind('<Control-c>', self._shell_ctrl_c)
        self.btn_send = self.button(inp, tr('Senden'), self.shell_send, 'primary', width=90, height=30, action=False)
        self.btn_send.grid(row=0, column=2, padx=(4, 6), pady=4)
        self._shell_ui()

    def _shell_ui(self):
        on = bool(self.shell_proc)
        self.btn_shell.configure(text=tr('Trennen') if on else tr('Verbinden'),
                                 fg_color=C.RED if on else C.ACCENT, hover_color=C.RED_HOVER if on else C.ACCENT_HOVER)
        self.btn_intr.configure(state='normal' if on else 'disabled')
        self.btn_send.configure(state='normal' if on else 'disabled')
        self.sh_entry.configure(state='normal' if on else 'disabled')
        self.sh_state.configure(text=(tr('●  verbunden  ·  root@%s') % self.v_ip.get()) if on else tr('●  nicht verbunden'),
                                text_color=C.GREEN_TEXT if on else C.FAINT)

    def shell_clear(self):
        self.sh_txt.configure(state='normal')
        self.sh_txt.delete('1.0', 'end')
        self.sh_txt.configure(state='disabled')

    def _shell_write(self, text, tag=None):
        t = self.sh_txt
        t.configure(state='normal')
        t.insert('end', text, tag)
        n = int(t.index('end-1c').split('.')[0])
        if n > 6000:
            t.delete('1.0', '%d.0' % (n - 5000))
        t.configure(state='disabled')
        t.see('end')

    def shell_toggle(self):
        if self.shell_proc:
            self.shell_disconnect()
        else:
            self.shell_connect()

    def shell_connect(self):
        if self.shell_proc:
            return True
        self.save_config()
        try:
            c = self.snapshot(['conn'])
        except Abort as e:
            self.alert('Shell', str(e), 'warn')
            return False
        except OSError as e:
            self.alert('Shell', str(e), 'error')
            return False
        # breites Pseudo-Terminal: sonst bricht die Unit lange Befehlszeilen bei 80 Zeichen um und das
        # Echo enthält Steuerzeichen-Reste (doppelte Buchstaben)
        cmd = [c['ssh'], '-tt'] + self.ssh_cmd(c)[1:] + [
            'stty cols 250 rows 60 2>/dev/null; exec "${SHELL:-/bin/sh}" -l']
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, creationflags=NOWIN, bufsize=0)
        except OSError as e:
            self.alert('Shell', tr('ssh konnte nicht gestartet werden: %s') % e, 'error')
            return False
        self.shell_proc = p
        self.shell_connected = True
        self._shell_ui()
        self.sh_entry.focus_set()
        self._shell_write(tr('*** verbinde mit root@%s:%s …\n') % (c['ip'], c['port']), 'sys')
        dec = codecs.getincrementaldecoder('utf-8')('replace')
        threading.Thread(target=self._shell_reader, args=(p, dec, c), daemon=True).start()
        return True

    def _shell_reader(self, p, dec, c):
        try:
            fd = p.stdout.fileno()
        except (OSError, ValueError, AttributeError):
            fd = None
        got = False
        while fd is not None:
            try:
                chunk = os.read(fd, 4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            text = strip_ansi(dec.decode(chunk))
            if text:
                if not got:
                    got = True
                    self.ui(self.set_conn, 'on', '%s\nVIN %s' % (c['ip'], c.get('vin') or '–'))
                self.ui(self._shell_write, text)
        try:
            rc = p.wait(5)
        except Exception:  # noqa: BLE001
            rc = None
        self.ui(self._shell_ended, p, rc)

    def shell_send(self, _e=None):
        p = self.shell_proc
        if not p:
            return 'break'
        line = self.sh_in.get()
        self.sh_in.set('')
        if line.strip() and (not self.sh_hist or self.sh_hist[-1] != line):
            self.sh_hist.append(line)
        self.sh_hist_i = len(self.sh_hist)
        self._shell_raw((line + '\n').encode('utf-8'))
        return 'break'

    def _shell_raw(self, data):
        p = self.shell_proc
        if not p:
            return
        try:
            p.stdin.write(data)
            p.stdin.flush()
        except (OSError, ValueError):
            self._shell_write(tr('\n*** Verbindung unterbrochen ***\n'), 'sys')

    def shell_interrupt(self):
        """Strg+C an die Shell auf der Unit (über das Pseudo-Terminal von ssh -tt)."""
        self._shell_raw(b'\x03')

    def _shell_ctrl_c(self, _e=None):
        try:
            if self.sh_entry.select_present():
                return None                  # markierten Text normal kopieren
        except (tk.TclError, AttributeError):
            pass
        self.shell_interrupt()
        return 'break'

    def shell_history(self, d):
        if not self.sh_hist:
            return 'break'
        self.sh_hist_i = max(0, min(len(self.sh_hist), self.sh_hist_i + d))
        self.sh_in.set(self.sh_hist[self.sh_hist_i] if self.sh_hist_i < len(self.sh_hist) else '')
        self.sh_entry.icursor('end')
        return 'break'

    def shell_preset(self, cmd, changes=False, label=''):
        if changes and not self.ask(label, tr('Dieser Schnellbefehl startet einen Dienst auf der Head Unit neu '
                                    '(Bildschirm lädt neu).'), yes=tr('Ausführen'), kind='warn', detail=cmd):
            return
        if not self.shell_proc:
            if not self.shell_connect():
                return
            self.root.after(900, lambda: self._shell_send_text(cmd))
            return
        self._shell_send_text(cmd)

    def _shell_send_text(self, cmd):
        if not self.shell_proc:
            return
        self.sh_in.set(cmd)
        self.shell_send()

    def shell_disconnect(self):
        p = self.shell_proc
        self.shell_proc = None
        self.shell_connected = False
        try:
            self._shell_ui()
        except tk.TclError:
            pass
        if p and p.poll() is None:
            try:
                p.stdin.write(b'exit\n')
                p.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                p.terminate()
            except OSError:
                pass

    def _shell_ended(self, p, rc=None):
        if self.shell_proc is not None and self.shell_proc is not p:
            return                           # alte Sitzung – eine neue läuft schon
        was = self.shell_connected
        self.shell_proc = None
        self.shell_connected = False
        try:
            self._shell_ui()
        except tk.TclError:
            pass
        if was:
            if rc == 255:
                self.set_conn('off')
                self._shell_write(tr('\n*** Verbindung fehlgeschlagen – Details oben. ***\n'), 'sys')
            else:
                self._shell_write(tr('\n*** Verbindung beendet ***\n'), 'sys')

    # ================================================================= Seite: Dateien
    def build_page_files(self, f):
        f.rowconfigure(1, weight=1)
        top = ctk.CTkFrame(f, fg_color='transparent')
        top.grid(row=0, column=0, sticky='ew')
        top.columnconfigure(1, weight=1)
        self.button(top, '↑', self.files_up, width=40).grid(row=0, column=0, padx=(0, 8))
        self.v_rpath = tk.StringVar(value=self.cur_path)
        e = self.entry(top, self.v_rpath, mono=True)
        e.grid(row=0, column=1, sticky='ew')
        e.bind('<Return>', lambda ev: self.files_go())
        self.button(top, tr('Öffnen'), self.files_go, width=90).grid(row=0, column=2, padx=(8, 0))
        self.button(top, tr('↻  Aktualisieren'), self.files_refresh, width=130).grid(row=0, column=3, padx=(8, 0))

        box = ctk.CTkFrame(f, fg_color=C.CARD, corner_radius=12, border_width=1, border_color=C.BORDER)
        box.grid(row=1, column=0, sticky='nsew', pady=(12, 0))
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        st = ttk.Style(self.root)
        try:
            st.theme_use('clam')
        except tk.TclError:
            pass
        rh = self.px(30)
        st.configure('Dark.Treeview', background=C.CARD, fieldbackground=C.CARD, foreground=C.TEXT,
                     bordercolor=C.CARD, lightcolor=C.CARD, darkcolor=C.CARD, borderwidth=0, rowheight=rh,
                     font=self.tkf_tree)
        st.map('Dark.Treeview', background=[('selected', C.ACCENT)], foreground=[('selected', '#ffffff')])
        st.configure('Dark.Treeview.Heading', background=C.CARD_HI, foreground=C.MUTED, relief='flat',
                     bordercolor=C.BORDER, lightcolor=C.CARD_HI, darkcolor=C.CARD_HI, font=self.tkf_tree_b,
                     padding=(8, 6))
        st.map('Dark.Treeview.Heading', background=[('active', C.BTN_HOVER)])
        st.layout('Dark.Treeview', [('Dark.Treeview.treearea', {'sticky': 'nswe'})])
        self.tree = ttk.Treeview(box, columns=('size', 'type'), selectmode='extended', style='Dark.Treeview')
        self._tree_headings()
        self.tree.column('#0', width=self.px(460), anchor='w', stretch=True)
        self.tree.column('size', width=self.px(110), anchor='e', stretch=False)
        self.tree.column('type', width=self.px(175), anchor='w', stretch=False)
        self.tree.tag_configure('dim', foreground=C.MUTED)
        sb = ctk.CTkScrollbar(box, command=self.tree.yview, button_color=C.BTN, button_hover_color=C.BTN_HOVER)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky='nsew', padx=(8, 0), pady=8)
        sb.grid(row=0, column=1, sticky='ns', padx=(2, 6), pady=8)
        isz = self.px(16)
        self._ficons = {k: make_icon(k, isz) for k in ('dir', 'file', 'link', 'other', 'up')}
        self.tree.bind('<Double-1>', self.files_open_sel)
        self.tree.bind('<Return>', self.files_open_sel)
        self.tree.bind('<BackSpace>', lambda e: self.files_up())
        self.tree.bind('<Delete>', lambda e: self.files_delete())
        self.tree.bind('<Button-3>', self._files_menu)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._files_footer())
        self.files_hint = ctk.CTkLabel(box, text='', font=self.f_body, text_color=C.MUTED, fg_color=C.CARD)
        self.files_hint.place(relx=0.5, rely=0.45, anchor='center')
        self._files_hint(tr('Noch nicht geladen – "Aktualisieren" klicken (Verbindung nötig).'))

        self._build_fmenu()

        bf = ctk.CTkFrame(f, fg_color='transparent')
        bf.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        self.button(bf, tr('⬇  Herunterladen…'), self.files_download).pack(side='left')
        self.button(bf, tr('⬆  Hochladen…'), self.files_upload).pack(side='left', padx=6)
        self.button(bf, tr('+  Neuer Ordner…'), self.files_mkdir).pack(side='left')
        self.button(bf, tr('Löschen'), self.files_delete, 'danger', width=90).pack(side='left', padx=6)
        self.button(bf, tr('Sicherung einspielen…'), self.files_restore).pack(side='right')
        self.button(bf, tr('Ordner sichern (tar.gz)…'), self.files_backup).pack(side='right', padx=6)
        self.lbl_files = self.label(f, '', 'small', wrap=True)
        self.lbl_files.grid(row=3, column=0, sticky='ew', pady=(8, 0))

    def _tree_headings(self):
        self.tree.heading('#0', text=tr('  Name'), anchor='w')
        self.tree.heading('size', text=tr('Größe'), anchor='e')
        self.tree.heading('type', text=tr('Typ'), anchor='w')

    def _build_fmenu(self):
        if getattr(self, 'fmenu', None) is not None:
            self.fmenu.destroy()
        self.fmenu = tk.Menu(self.root, tearoff=0, bg=C.CARD_HI, fg=C.TEXT, activebackground=C.ACCENT,
                             activeforeground='#ffffff', bd=0, relief='flat', font=self.tkf_tree)
        self.fmenu.add_command(label=tr('Öffnen'), command=self.files_open_sel)
        self.fmenu.add_command(label=tr('Herunterladen…'), command=self.files_download)
        self.fmenu.add_command(label=tr('Pfad kopieren'), command=self.files_copy_path)
        self.fmenu.add_separator()
        self.fmenu.add_command(label=tr('Löschen…'), command=self.files_delete)

    def _files_hint(self, text):
        if text:
            self.files_hint.configure(text=text)
            self.files_hint.place(relx=0.5, rely=0.45, anchor='center')
            self.files_hint.lift()
        else:
            self.files_hint.place_forget()

    def _files_footer(self):
        n_d = sum(1 for t, _s, _l in self._entries.values() if t == 'd')
        n_f = len(self._entries) - n_d
        sel = self._selected()
        txt = tr('%s  ·  %d Ordner, %d Dateien') % (self.cur_path, n_d, n_f)
        if sel:
            size = sum(s for t, _n, s, _l in sel if t == 'f')
            txt += tr('  ·  %d ausgewählt%s') % (len(sel), (' (%s)' % fmt_size(size)) if size else '')
        txt += tr('   —   Doppelklick/Enter öffnet, Rücktaste geht nach oben, Entf löscht, Rechtsklick für mehr.')
        self.lbl_files.configure(text=txt)

    def files_go(self):
        self._list(self.v_rpath.get().strip() or '/')

    def files_up(self):
        self._list(rparent(self.cur_path))

    def files_refresh(self):
        self._list(self.cur_path)

    def _list(self, path, quiet=False):
        def job(c, p=path):
            script = LIST_TEMPLATE.replace('@DIR@', shq(p))
            try:
                rc, lines = self.remote(c, script, label=tr('Head Unit › ls %s') % p, echo=False)
            except Abort as e:
                if quiet or not self._entries:
                    self.ui(self._files_hint, tr('Nicht erreichbar – %s') % e)
                raise
            if any(ln.strip() == '__NODIR__' for ln in lines):
                raise Abort(tr('Kein Verzeichnis: %s') % p)
            real, entries, started = p, [], False
            for ln in lines:
                if not started:
                    if ln.strip():
                        real, started = ln.strip(), True
                    continue
                parts = ln.split('/', 3)
                if len(parts) == 4 and parts[0] in ('d', 'f', 'o') and parts[3]:
                    t, sz, lk, name = parts
                    entries.append((t, int(sz) if sz.isdigit() else 0, lk == '1', name))
            entries.sort(key=lambda x: (x[0] != 'd', x[3].lower()))
            self.ui(self._files_show, real, entries)
        if quiet:
            self._files_hint(tr('Lade Verzeichnis …'))
        self.start(tr('Verzeichnis lesen'), job, 'conn', quiet_fail=quiet)
        if quiet and not self.busy:
            self._files_hint(tr('Nicht verbunden – Schlüssel unter "Installieren" (Schritt 1) laden, dann '
                                '"Aktualisieren".'))

    def _files_show(self, real, entries, quiet=False):
        self._files_last = (real, entries)
        self.cur_path = real
        self.v_rpath.set(real)
        self.tree.delete(*self.tree.get_children())
        self._tree_meta = {}
        self._entries = {}
        if real not in ('/', ''):
            iid = self.tree.insert('', 'end', text='  ..', image=self._ficons['up'], values=('', tr('nach oben')),
                                   tags=('dim',))
            self._tree_meta[iid] = ('up', '..', 0, False)
        for t, sz, lk, name in entries:
            kind = {'d': 'dir', 'o': 'other'}.get(t, 'link' if lk else 'file')
            typ = {'d': tr('Ordner'), 'f': tr('Datei'), 'o': tr('Gerät / Spezial')}[t] + (tr(' (Link)') if lk else '')
            iid = self.tree.insert('', 'end', text='  ' + name, image=self._ficons[kind],
                                   values=('' if t != 'f' else fmt_size(sz), typ),
                                   tags=('dim',) if t == 'o' else ())
            self._tree_meta[iid] = (t, name, sz, lk)
            self._entries[name] = (t, sz, lk)
        self._files_hint('' if entries else tr('Ordner ist leer.'))
        self._files_footer()
        if not quiet:
            self.log(tr('%s – %d Einträge') % (real, len(entries)), 'ok')

    def files_open_sel(self, _e=None):
        meta = self._tree_meta.get(self.tree.focus())
        if not meta:
            return
        t, name = meta[0], meta[1]
        if t == 'up':
            self.files_up()
        elif t == 'd':
            self._list(rjoin(self.cur_path, name))
        elif t == 'f':
            self.files_download()

    def _files_menu(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.tree.focus(iid)
        try:
            self.fmenu.tk_popup(e.x_root, e.y_root)
        finally:
            self.fmenu.grab_release()

    def files_copy_path(self):
        sel = self._selected()
        paths = [rjoin(self.cur_path, n) for _t, n, _s, _l in sel] or [self.cur_path]
        self.root.clipboard_clear()
        self.root.clipboard_append('\n'.join(paths))
        self.set_status(tr('Pfad kopiert: %s') % ', '.join(paths)[:120], 'ok')

    def _selected(self):
        out = []
        for iid in self.tree.selection():
            meta = self._tree_meta.get(iid)
            if meta and meta[0] in ('d', 'f', 'o'):
                out.append(meta)
        return out

    def files_download(self):
        if self.busy:
            return
        items = self._selected()
        if not items:
            self.alert(tr('Herunterladen'), tr('Bitte zuerst Dateien oder Ordner in der Liste auswählen.'))
            return
        cur = self.cur_path
        skipped = [n for t, n, _s, _l in items if t == 'o' or (t == 'd' and is_virtual(rjoin(cur, n)))]
        items = [m for m in items if m[1] not in skipped]
        if skipped:
            self.alert(tr('Herunterladen'), tr('Diese Einträge werden übersprungen: Geräte/Spezialdateien und Ordner in '
                                        '/proc, /sys, /dev lassen sich nicht sinnvoll kopieren (der Download '
                                        'würde hängen).'), 'warn', detail='\n'.join(skipped[:15]))
            if not items:
                return
        dest = filedialog.askdirectory(title=tr('Zielordner auf dem PC wählen'), parent=self.root)
        if not dest:
            return

        def job(c, items=items, dest=dest, cur=cur):
            ok = 0
            for t, name, _sz, _lk in items:
                if t == 'd':
                    tgz = os.path.join(dest, name + '.download.tar.gz')
                    rc, _ = self.remote(c, 'cd %s && tar czf - -- %s' % (shq(cur), shq(name)),
                                        out_file=tgz, label=tr('Head Unit › Ordner %s laden') % name, echo=False)
                    if rc or not os.path.getsize(tgz):
                        self.log(tr('✖ %s konnte nicht geladen werden.') % name, 'err')
                        self._rm(tgz)
                        continue
                    try:
                        with tarfile.open(tgz, 'r:gz') as tar:
                            self._safe_extract(tar, dest)
                        os.remove(tgz)
                    except (tarfile.TarError, OSError) as ex:
                        self.log(tr('✖ %s: Archiv-Fehler: %s') % (name, ex), 'err')
                        continue
                    self.log(tr('✔ Ordner geladen: %s') % os.path.join(dest, name), 'ok')
                    ok += 1
                else:
                    local = os.path.join(dest, name)
                    rc, _ = self.remote(c, 'cat %s' % shq(rjoin(cur, name)), out_file=local,
                                        label=tr('Head Unit › %s laden') % name, echo=False)
                    if rc:
                        self.log(tr('✖ %s konnte nicht geladen werden.') % name, 'err')
                        self._rm(local)
                        continue
                    self.log('✔ %s (%s)' % (local, fmt_size(os.path.getsize(local))), 'ok')
                    ok += 1
            self.log(tr('%d von %d Einträgen heruntergeladen.') % (ok, len(items)), 'ok' if ok else 'warn')
        self.start(tr('Herunterladen'), job, 'conn', then=lambda: open_path(dest))

    def _safe_extract(self, tar, dest):
        base = os.path.abspath(dest)
        for m in tar.getmembers():
            parts = [x for x in m.name.replace('\\', '/').split('/') if x not in ('', '.')]
            if not parts or '..' in parts or ':' in m.name:
                continue
            target = os.path.join(dest, *parts)
            if not os.path.abspath(target).startswith(base):
                continue
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.isfile():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tar.extractfile(m) as src, open(target, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

    def files_upload(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(title=tr('Dateien zum Hochladen wählen'), parent=self.root)
        if not paths:
            return
        cur = self.cur_path
        exists = [os.path.basename(p) for p in paths if os.path.basename(p) in self._entries]
        if exists and not self.ask(tr('Überschreiben?'), tr('In %s gibt es schon Einträge mit diesem Namen. '
                                   'Überschreiben?') % cur, yes=tr('Überschreiben'), kind='warn', danger=True,
                                   detail='\n'.join(exists[:15])):
            return

        def job(c, paths=list(paths), cur=cur):
            ok = 0
            for p in paths:
                p = os.path.normpath(p)
                dst = rjoin(cur, os.path.basename(p))
                rc, lines = self.ssh_run(c, ['cat > %s' % shq(dst)], stdin_file=p,
                                         label=tr('Head Unit › %s hochladen') % os.path.basename(p), echo=False)
                if rc == 255:
                    raise Abort(ssh_hint(lines))
                if rc:
                    self.log(tr('✖ %s nicht hochgeladen (Exit %d).') % (os.path.basename(p), rc), 'err')
                    for ln in lines:
                        self.log('  ' + ln, 'err')
                    continue
                self.log(tr('✔ hochgeladen: %s') % dst, 'ok')
                ok += 1
            self.log(tr('%d von %d Dateien hochgeladen.') % (ok, len(paths)), 'ok' if ok else 'warn')
        self.start(tr('Hochladen'), job, 'conn', then=self.files_refresh)

    def files_mkdir(self):
        if self.busy:
            return
        res = Dialog(self, tr('Neuer Ordner'), tr('Name des neuen Ordners in %s:') % self.cur_path,
                     [(tr('Abbrechen'), False, 'secondary'), (tr('Anlegen'), True, 'primary')], 'question',
                     entry=tr('Ordnername')).run()
        if not res or not res[0]:
            return
        name = (res[1] or '').strip().strip('/')
        if not name or '/' in name or name in ('.', '..'):
            self.alert(tr('Neuer Ordner'), tr('Bitte einen einfachen Namen ohne "/" angeben.'), 'warn')
            return

        def job(c, path=rjoin(self.cur_path, name)):
            rc, _ = self.remote(c, 'mkdir -p %s' % shq(path), label=tr('Head Unit › mkdir %s') % path)
            if rc:
                raise Abort(tr('Ordner konnte nicht angelegt werden (Exit %d).') % rc)
            self.log(tr('✔ Ordner angelegt: %s') % path, 'ok')
        self.start(tr('Neuer Ordner'), job, 'conn', then=self.files_refresh)

    def files_delete(self):
        if self.busy:
            return
        items = self._selected()
        if not items:
            self.alert(tr('Löschen'), tr('Bitte zuerst etwas in der Liste auswählen.'))
            return
        names = '\n'.join(('%s/' % n) if t == 'd' and not lk else n for t, n, _s, lk in items[:20])
        if len(items) > 20:
            names += tr('\n… und %d weitere') % (len(items) - 20)
        if not self.ask(tr('Löschen?'), tr('Diese Einträge auf der Head Unit löschen? Ordner werden nur entfernt, wenn sie '
                        'leer sind; Links werden entfernt, nicht ihr Ziel.'), yes=tr('Löschen'), kind='danger', danger=True,
                        detail=names):
            return
        cur = self.cur_path

        def job(c, items=items, cur=cur):
            ok = 0
            for t, name, _sz, lk in items:
                path = rjoin(cur, name)
                cmd = ('rmdir %s' if t == 'd' and not lk else 'rm -f %s') % shq(path)
                rc, _ = self.remote(c, cmd, label='Head Unit › ' + cmd)
                if rc:
                    self.log(tr('✖ %s nicht gelöscht%s.') % (name, tr(' (Ordner nicht leer?)') if t == 'd' else ''), 'err')
                    continue
                self.log(tr('✔ gelöscht: %s') % name, 'ok')
                ok += 1
            self.log(tr('%d von %d gelöscht.') % (ok, len(items)), 'ok' if ok else 'warn')
        self.start(tr('Löschen'), job, 'conn', then=self.files_refresh)

    def files_backup(self):
        if self.busy:
            return
        items = [m for m in self._selected() if m[0] != 'o']
        cur = self.cur_path
        if not items and (cur in ('/', '') or is_virtual(cur)):
            self.alert(tr('Ordner sichern'), tr('Das ganze Dateisystem bzw. /proc, /sys, /dev lässt sich nicht als Ganzes '
                                         'sichern. Bitte einen Unterordner öffnen oder Einträge auswählen.'), 'warn')
            return
        bad = [n for t, n, _s, _l in items if is_virtual(rjoin(cur, n))]
        if bad:
            self.alert(tr('Ordner sichern'), tr('/proc, /sys und /dev lassen sich nicht sichern.'), 'warn',
                       detail='\n'.join(bad[:15]))
            return
        default = (os.path.basename(cur.rstrip('/')) or 'root') + '_%s.tar.gz' % stamp()
        save = filedialog.asksaveasfilename(title=tr('Sicherung speichern'), defaultextension='.tar.gz',
                                            initialfile=default, filetypes=[('tar.gz', '*.tar.gz *.tgz')],
                                            parent=self.root)
        if not save:
            return

        def job(c, items=items, cur=cur, save=save):
            if items:
                sel = ' '.join(shq(n) for _t, n, _s, _l in items)
                cmd = 'cd %s && tar czf - -- %s' % (shq(cur), sel)
                what = tr('%d Eintrag/Einträge aus %s') % (len(items), cur)
            else:
                cmd = 'cd %s && tar czf - .' % shq(cur)
                what = tr('gesamter Ordner %s') % cur
            self.log(tr('Sichere %s …') % what)
            rc, _ = self.remote(c, cmd, out_file=save, label=tr('Head Unit › tar (%s)') % what, echo=False)
            if rc or not os.path.getsize(save):
                raise Abort(tr('Sicherung fehlgeschlagen (Exit %d).') % rc)
            self.log(tr('✔ Sicherung: %s (%s)') % (save, fmt_size(os.path.getsize(save))), 'ok')
        self.start(tr('Sichern'), job, 'conn')

    def files_restore(self):
        if self.busy:
            return
        p = filedialog.askopenfilename(title=tr('Sicherung (tar.gz) wählen'), parent=self.root,
                                       filetypes=[('tar.gz', '*.tar.gz *.tgz'), (tr('Alle Dateien'), '*.*')])
        if not p:
            return
        cur = self.cur_path
        if not self.ask(tr('Sicherung einspielen?'), tr('Das Archiv in %s auf der Head Unit entpacken? Vorhandene Dateien '
                        'mit gleichem Namen werden überschrieben.') % cur, yes=tr('Einspielen'), kind='warn', danger=True,
                        detail=p):
            return

        def job(c, p=os.path.normpath(p), cur=cur):
            rc, lines = self.ssh_run(c, ['mkdir -p %s && tar xzf - -C %s' % (shq(cur), shq(cur))],
                                     stdin_file=p, label=tr('Head Unit › tar x -C %s') % cur, echo=False)
            if rc == 255:
                raise Abort(ssh_hint(lines))
            if rc:
                for ln in lines:
                    self.log('  ' + ln, 'err')
                raise Abort(tr('Einspielen fehlgeschlagen (Exit %d).') % rc)
            self.log(tr('✔ Sicherung eingespielt nach %s') % cur, 'ok')
        self.start(tr('Sicherung einspielen'), job, 'conn', then=self.files_refresh)


import id8_wizard  # noqa: E402
id8_wizard.install(App, globals())


def main():
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
