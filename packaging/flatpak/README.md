# Experimental Flatpak packaging

PrazyCron manages host Cron files and systemd units, which conflicts with the
normal Flatpak sandbox model. The included manifest is a development template,
not a guarantee of Flathub acceptance. The DEB package and source installation
are the supported publication formats for the first public release.

Build for local evaluation:

```bash
flatpak-builder --force-clean build-dir packaging/flatpak/io.github.Prazynka.PrazyCron.yml
```
