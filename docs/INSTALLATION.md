# Installation

## Debian or Ubuntu package

```bash
cd ~/Downloads
sudo apt install ./prazycron_2.1.0_all.deb
```

The package installs the application command, desktop launcher, icon sizes, AppStream metadata, documentation, and manual page.

## Source installation

```bash
git clone https://github.com/Prazynka/prazycron.git
cd prazycron
sudo ./install.sh
```

Uninstall source installation:

```bash
sudo ./uninstall.sh
```

## Required runtime components

- Python 3.10 or newer
- Tkinter
- curses
- Cron
- systemd
- util-linux (`flock`)
- PolicyKit or `pkexec`
- `xdg-utils`

## Verify installation

```bash
prazycron --version
prazycron --diagnose
```
