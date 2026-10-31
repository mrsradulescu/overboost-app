
# iDrive 7 Toolkit (v0.2, untested)

A Windows GUI (Python / CustomTkinter) for the community tools for BMW iDrive 7 (MGU) head units: custom apps, root SSH access and an app store. It doesn't replace any of them. It just runs the original tools with a click and shows their log in one window.

> **Not tested on a car yet** (no time so far). Use it at your own risk and back up your head unit coding first. Not an official BMW product.

## Credits

All credit for the code this GUI runs goes to the authors of these projects:

- [idrive7-apps](https://github.com/cauliflowermossgrape/idrive7-apps): the custom apps, the kit and its tools (MIT)
- [idrive7-8-ssh-root](https://github.com/cauliflowermossgrape/idrive7-8-ssh-root): root SSH access to the head unit
- [mgu-store](https://github.com/robertnisipeanu/mgu-store): the app store for the head unit (GPL-3.0-or-later)

Only the GUI itself is this project. It was built with the help of Claude. If any of the original developers would prefer that it isn't shared, let me know and I'll take it down.

## Screenshot
<img width="1396" height="1009" alt="screen" src="https://github.com/user-attachments/assets/9e313449-5344-4ba2-ba1f-7be52a672b04" />

## What it does

- **Enable SSH:** links to the original root tool, then loads the key it creates
- **Install:** four steps (connect, get the project + SDK, choose apps and build the kit, check and install), plus "back to stock"
- **App Store:** installs mgu-store on the head unit with its own installer, always downloaded fresh from its GitHub repo (nothing is bundled)
- **Tools:** diagnostics, root shell, file browser and a PC check
- English or German interface, dark design

## Requirements

- Windows 10/11
- Python 3 from [python.org](https://www.python.org/downloads/) (tick "Add python.exe to PATH"). The .exe needs it too, for building the apps.
- Node.js (14+ for the kit, 18+ for the App Store)
- The Windows OpenSSH client
- An ENET cable

## Start

- **.py:** double-click `iDrive7-Toolkit 0.2 start.bat`. It installs `customtkinter` on the first start. You can also run `py idrive7_toolkit_v0.2.py`.
- **.exe:** download it from [Releases](../../releases). It isn't signed, so Windows SmartScreen or a virus scanner may warn about it. The .py shows the complete code.

Your settings are saved in `idrive7_gui.json` next to the program. It contains your VIN and key path, so don't share it.

## Feedback

Issues and bug reports are welcome.
