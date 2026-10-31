# OVERBOOST for iDrive 8 (MGU21)

One app with a main menu: **M Vitals**, **Drag Timer**, **Fault Scanner**, **Engine Config**, **Update**.
The logic comes from the open-source idrive7-apps project; the screens are plain HMI rows (proven on iDrive 8).

## Install (replaces "BC / ECU Logger" in place)
1. Unzip the toolkit, copy your old `id8` key folder next to it if you use a new folder.
2. Run the wizard, step 3 (connect), then step 8: **8a** (SDK), **8b** (build), **8c** (check), **8d** (install), reboot the unit.
3. If the app disappears after a reboot: **8e**.

## Uninstall
* Remove OVERBOOST *or* the old ID8 logger (they are the same installed app): step 8, **Dezinstalează**
  (moves the app to a quarantine folder with a backup and undoes the MainApp hook change).
* Installing OVERBOOST with 8d already replaces the logger, so uninstalling the logger first is optional.

## In-car update
`Update` -> `Check for updates` -> `Update now` -> `Restart OVERBOOST`. The GitHub source is built into the app
(not shown). Put the updater package in a GitHub release:
1. Raise `version` in `overboost/app/version.json` (keep `base`), run `python tools/make_update.py`.
2. Create a release whose **tag equals the version** (`1.0.1` or `v1.0.1`) and attach `dist/overboost-update.tar.gz`.
An update replaces the app's JavaScript only. A change to the signal list, the screen layout or the icon needs the laptop
install again (then raise `base`). The head unit needs an internet connection for this.
