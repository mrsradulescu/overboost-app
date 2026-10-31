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
`Update` -> `Check for updates` -> `Update now` -> `Restart OVERBOOST`. The GitHub source (mrsradulescu/overboost-app) is built
into the app and not shown. To publish a new version:
1. Raise `version` in `app/version.json` (keep `base`), run `python tools/make_update.py`.
2. Commit and push `update/overboost-update.tar.gz` and `update/latest.json` (it holds the version and the SHA-256 checksum;
   the car refuses a package that does not match it).
An update replaces the app's JavaScript only. A change to the signal list, the screen layout or the icon needs the laptop
install again (then raise `base`). The head unit needs an internet connection for this.
