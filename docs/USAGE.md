# Usage

## Interface selection

```bash
prazycron          # GUI when a graphical session is available, otherwise TUI
prazycron --gui    # force GUI
prazycron --tui    # force TUI
```

## Non-interactive commands

```bash
prazycron --list
prazycron --json
prazycron --analyze 1
prazycron --next 1
prazycron --history 1
prazycron --diagnose
prazycron --systemd-list
```

## Editing safety

PrazyCron validates a proposed entry, displays a diff, and creates a backup before writing. System sources require normal Linux administrative authorization.

## Explanation modes

- **Built-in Cron analyzer** — default, offline, no key.
- **Ollama** — local optional provider, no API key.
- **OpenAI online** — optional provider; an API key is requested only after this provider is selected.

## Execution history

Enable history for a task in the editor. PrazyCron wraps the command with `prazycron-run`, stores result metadata under the user data directory, and can optionally add a non-blocking `flock` lock.
