# Contributing to PrazyCron

Thank you for improving PrazyCron.

## Before starting

1. Search existing issues and pull requests.
2. Open an issue before large interface, storage, privilege, or compatibility changes.
3. Keep privileged operations narrow and explicit.
4. Never commit passwords, API keys, private crontabs, logs containing secrets, or personal paths.

## Development setup

```bash
git clone https://github.com/Prazynka/prazycron.git
cd prazycron
python3 -m unittest discover -s tests -v
```

On Ubuntu, install runtime and test dependencies with:

```bash
sudo apt update
sudo apt install python3 python3-tk cron util-linux systemd policykit-1 xvfb desktop-file-utils appstream
```

## Quality checks

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q prazycron
xvfb-run -a python3 -m prazycron.main --gui --smoke-test
./scripts/check-release.sh
```

## Pull requests

- Keep each pull request focused.
- Add or update tests for behavior changes.
- Update `CHANGELOG.md` under `Unreleased`.
- Update screenshots when the main interface changes.
- Use clear commit messages written in the imperative mood.
- Confirm that the application still starts in both GUI and TUI modes.

## Translation changes

English is the source catalog and fallback language. The Polish catalog must contain every English key; other catalogs may fall back to English for missing technical phrases.

## License

By contributing, you agree that your contribution is licensed under the MIT License.
