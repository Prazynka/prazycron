from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import CronEntry

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
WEEKDAYS = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}
ALIASES = {
    "@reboot": "at system startup",
    "@yearly": "once a year at midnight on January 1",
    "@annually": "once a year at midnight on January 1",
    "@monthly": "once a month at midnight on the first day",
    "@weekly": "once a week at midnight on Sunday",
    "@daily": "once a day at midnight",
    "@midnight": "once a day at midnight",
    "@hourly": "once an hour at minute 0",
}
ALIASES_PL = {
    "@reboot": "przy uruchomieniu systemu",
    "@yearly": "raz w roku o północy 1 stycznia",
    "@annually": "raz w roku o północy 1 stycznia",
    "@monthly": "raz w miesiącu, pierwszego dnia o 00:00",
    "@weekly": "co tydzień w niedzielę o 00:00",
    "@daily": "raz dziennie o 00:00",
    "@midnight": "raz dziennie o 00:00",
    "@hourly": "raz na godzinę, w minucie 00",
}


@dataclass(slots=True)
class Analysis:
    summary: str
    details: list[str]
    warnings: list[str]
    sections: dict[str, str] = field(default_factory=dict)

    def as_text(self) -> str:
        if self.sections:
            parts: list[str] = []
            for title, body in self.sections.items():
                parts.extend([title, body.strip(), ""])
            return "\n".join(parts).rstrip()
        out = [self.summary]
        if self.details:
            out.extend(["", "Details:", *[f"• {x}" for x in self.details]])
        if self.warnings:
            out.extend(["", "Warnings:", *[f"• {x}" for x in self.warnings]])
        return "\n".join(out)


def _normalize_named(value: str, names: dict[str, int]) -> str:
    upper = value.upper()
    for name, number in names.items():
        upper = re.sub(rf"\b{name}\b", str(number), upper)
    return upper


def _validate_atom(atom: str, low: int, high: int, names: dict[str, int] | None = None) -> bool:
    atom = _normalize_named(atom, names or {})
    if atom == "*":
        return True
    if "/" in atom:
        base, step = atom.split("/", 1)
        if not step.isdigit() or int(step) <= 0:
            return False
        return _validate_atom(base, low, high, names)
    if "," in atom:
        return all(_validate_atom(part, low, high, names) for part in atom.split(","))
    if "-" in atom:
        left, right = atom.split("-", 1)
        return left.isdigit() and right.isdigit() and low <= int(left) <= int(right) <= high
    return atom.isdigit() and low <= int(atom) <= high


def validate_schedule(schedule: str) -> list[str]:
    if schedule in ALIASES:
        return []
    fields = schedule.split()
    if len(fields) != 5:
        return ["A standard cron expression must contain exactly five time fields."]
    specs = [(0, 59, {}), (0, 23, {}), (1, 31, {}), (1, 12, MONTHS), (0, 7, WEEKDAYS)]
    labels = ["minute", "hour", "day of month", "month", "day of week"]
    errors: list[str] = []
    for value, spec, label in zip(fields, specs, labels):
        if not _validate_atom(value, *spec):
            errors.append(f"Invalid {label} field: {value}")
    return errors


def _join_readable(items: list[str], conjunction: str) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conjunction} {items[1]}"
    return f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


def _parse_numbers(value: str, low: int, high: int, *, sunday_7: bool = False) -> list[int] | None:
    """Parse a simple comma-separated numeric cron field.

    Ranges and steps intentionally return None and are handled by dedicated branches or
    the readable fallback. This keeps descriptions accurate instead of over-promising.
    """
    if not re.fullmatch(r"\d+(?:,\d+)*", value):
        return None
    numbers: list[int] = []
    for raw in value.split(","):
        number = int(raw)
        if sunday_7 and number == 7:
            number = 0
        if not low <= number <= high:
            return None
        if number not in numbers:
            numbers.append(number)
    return numbers


def _clock(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _describe_field(value: str, unit: str) -> str:
    if value == "*":
        return f"every {unit}"
    if value.startswith("*/") and value[2:].isdigit():
        return f"every {value[2:]} {unit}s"
    if "," in value:
        return f"{unit}s {value}"
    if "-" in value:
        return f"{unit}s {value}"
    return f"{unit} {value}"


def _weekday_text_en(value: str) -> str:
    names = {0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday"}
    numbers = _parse_numbers(value, 0, 7, sunday_7=True)
    if numbers is not None:
        return _join_readable([names[n] for n in numbers], "and")
    match = re.fullmatch(r"(\d)-(\d)", value)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        start = 0 if start == 7 else start
        end = 0 if end == 7 else end
        if start in names and end in names:
            return f"{names[start]} through {names[end]}"
    return f"weekday(s) {value}"


def _weekday_text_pl(value: str) -> str:
    singular = {0: "niedzielę", 1: "poniedziałek", 2: "wtorek", 3: "środę", 4: "czwartek", 5: "piątek", 6: "sobotę"}
    plural = {0: "niedziele", 1: "poniedziałki", 2: "wtorki", 3: "środy", 4: "czwartki", 5: "piątki", 6: "soboty"}
    range_names = {0: "niedzieli", 1: "poniedziałku", 2: "wtorku", 3: "środy", 4: "czwartku", 5: "piątku", 6: "soboty"}
    numbers = _parse_numbers(value, 0, 7, sunday_7=True)
    if numbers is not None:
        if len(numbers) == 1:
            return singular[numbers[0]]
        return _join_readable([plural[n] for n in numbers], "i")
    match = re.fullmatch(r"(\d)-(\d)", value)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        start = 0 if start == 7 else start
        end = 0 if end == 7 else end
        if start in range_names and end in range_names:
            return f"od {range_names[start]} do {range_names[end]}"
    return f"dni tygodnia {value}"


def describe_schedule(schedule: str) -> str:
    if schedule in ALIASES:
        return ALIASES[schedule]
    fields = schedule.split()
    if len(fields) != 5:
        return "uses an invalid cron schedule"
    minute, hour, dom, month, dow = fields
    if fields == ["*", "*", "*", "*", "*"]:
        return "every minute"
    if minute.startswith("*/") and minute[2:].isdigit() and all(v == "*" for v in (hour, dom, month, dow)):
        return f"every {minute[2:]} minutes"

    minutes = _parse_numbers(minute, 0, 59)
    hours = _parse_numbers(hour, 0, 23)
    if minute.isdigit() and hour == "*" and all(v == "*" for v in (dom, month, dow)):
        return f"every hour at minute {int(minute):02d}"
    if minutes and len(minutes) > 1 and hour == "*" and all(v == "*" for v in (dom, month, dow)):
        minute_text = _join_readable([f"{m:02d}" for m in minutes], "and")
        return f"every hour: minutes {minute_text}"
    if minute.isdigit() and re.fullmatch(r"\d+-\d+", hour) and dom == month == dow == "*":
        start, end = map(int, hour.split("-"))
        return f"every hour from {_clock(start, int(minute))} to {_clock(end, int(minute))}, daily"
    if minute.isdigit() and hours and len(hours) > 1 and dom == month == dow == "*":
        times = _join_readable([_clock(h, int(minute)) for h in hours], "and")
        return f"daily at {times}"
    if minutes and len(minutes) > 1 and hour.isdigit() and dom == month == dow == "*":
        times = _join_readable([_clock(int(hour), m) for m in minutes], "and")
        return f"daily at {times}"
    if minute.isdigit() and hour.isdigit() and dom == month == dow == "*":
        return f"once a day at {_clock(int(hour), int(minute))}"
    if minute.isdigit() and hour.isdigit() and dom == month == "*" and dow != "*":
        return f"every week on {_weekday_text_en(dow)} at {_clock(int(hour), int(minute))}"
    if minute.isdigit() and hour.isdigit() and dom.isdigit() and month == "*" and dow == "*":
        return f"once a month on day {int(dom)} at {_clock(int(hour), int(minute))}"
    if minute.isdigit() and hour.isdigit() and dom.isdigit() and month.isdigit() and dow == "*":
        return f"once a year on {int(month)}/{int(dom)} at {_clock(int(hour), int(minute))}"
    return ", ".join([
        _describe_field(minute, "minute"), _describe_field(hour, "hour"),
        _describe_field(dom, "day"), _describe_field(month, "month"), _describe_field(dow, "weekday")
    ])


def _pl_field(value: str, name: str) -> str:
    if value == "*":
        return f"każdy {name}"
    if value.startswith("*/") and value[2:].isdigit():
        return f"co {value[2:]} {name}"
    if "," in value:
        return f"{name}: {value}"
    if "-" in value:
        return f"{name} w zakresie {value}"
    return f"{name}: {value}"


def describe_schedule_pl(schedule: str) -> str:
    if schedule in ALIASES_PL:
        return ALIASES_PL[schedule]
    fields = schedule.split()
    if len(fields) != 5:
        return "używa nieprawidłowego harmonogramu Cron"
    minute, hour, dom, month, dow = fields
    if fields == ["*", "*", "*", "*", "*"]:
        return "co minutę"
    if minute.startswith("*/") and minute[2:].isdigit() and all(v == "*" for v in (hour, dom, month, dow)):
        return f"co {minute[2:]} minut"

    minutes = _parse_numbers(minute, 0, 59)
    hours = _parse_numbers(hour, 0, 23)
    if minute.isdigit() and hour == "*" and all(v == "*" for v in (dom, month, dow)):
        return f"co godzinę, o minucie {int(minute):02d}"
    if minutes and len(minutes) > 1 and hour == "*" and all(v == "*" for v in (dom, month, dow)):
        minute_text = _join_readable([f"{m:02d}" for m in minutes], "i")
        return f"co godzinę: minuty {minute_text}"
    if minute.isdigit() and re.fullmatch(r"\d+-\d+", hour) and dom == month == dow == "*":
        start, end = map(int, hour.split("-"))
        return f"co godzinę od {_clock(start, int(minute))} do {_clock(end, int(minute))}, codziennie"
    if minute.isdigit() and hours and len(hours) > 1 and dom == month == dow == "*":
        times = _join_readable([_clock(h, int(minute)) for h in hours], "i")
        return f"codziennie o {times}"
    if minutes and len(minutes) > 1 and hour.isdigit() and dom == month == dow == "*":
        times = _join_readable([_clock(int(hour), m) for m in minutes], "i")
        return f"codziennie o {times}"
    if minute.isdigit() and hour.isdigit() and dom == month == dow == "*":
        return f"raz dziennie o {_clock(int(hour), int(minute))}"
    if minute.isdigit() and hour.isdigit() and dom == month == "*" and dow != "*":
        return f"co tydzień w {_weekday_text_pl(dow)} o {_clock(int(hour), int(minute))}"
    if minute.isdigit() and hour.isdigit() and dom.isdigit() and month == "*" and dow == "*":
        return f"raz w miesiącu, {int(dom)}. dnia o {_clock(int(hour), int(minute))}"
    if minute.isdigit() and hour.isdigit() and dom.isdigit() and month.isdigit() and dow == "*":
        return f"raz w roku, {int(dom):02d}.{int(month):02d} o {_clock(int(hour), int(minute))}"
    return ", ".join([
        _pl_field(minute, "minuta"), _pl_field(hour, "godzina"), _pl_field(dom, "dzień miesiąca"),
        _pl_field(month, "miesiąc"), _pl_field(dow, "dzień tygodnia")
    ])


def humanize_schedule(schedule: str, language: str = "en") -> str:
    """Return a concise, user-facing schedule label for GUI and TUI tables."""
    text = describe_schedule_pl(schedule) if language == "pl" else describe_schedule(schedule)
    return text[:1].upper() + text[1:] if text else schedule

def _estimate_frequency(schedule: str, language: str) -> str:
    pl = language == "pl"
    aliases = {
        "@reboot": "przy każdym uruchomieniu systemu" if pl else "once per system start",
        "@hourly": "24 razy na dobę" if pl else "24 times per day",
        "@daily": "1 raz na dobę" if pl else "once per day",
        "@midnight": "1 raz na dobę" if pl else "once per day",
        "@weekly": "1 raz w tygodniu" if pl else "once per week",
        "@monthly": "1 raz w miesiącu" if pl else "once per month",
        "@yearly": "1 raz w roku" if pl else "once per year",
        "@annually": "1 raz w roku" if pl else "once per year",
    }
    if schedule in aliases:
        return aliases[schedule]
    fields = schedule.split()
    if len(fields) != 5:
        return "Nie można obliczyć z powodu błędnego harmonogramu." if pl else "Cannot be calculated because the schedule is invalid."
    minute, hour, dom, month, dow = fields
    if minute == hour == dom == month == dow == "*":
        return "1440 razy na dobę" if pl else "1440 times per day"
    if minute.startswith("*/") and minute[2:].isdigit() and all(v == "*" for v in (hour, dom, month, dow)):
        step = int(minute[2:])
        count = 1440 // step
        return f"około {count} razy na dobę (co {step} minut)" if pl else f"about {count} times per day (every {step} minutes)"
    if minute.isdigit() and hour == "*" and dom == month == dow == "*":
        return "24 razy na dobę" if pl else "24 times per day"
    if re.fullmatch(r"\d+(?:,\d+)+", minute) and hour == "*" and dom == month == dow == "*":
        count = len(minute.split(",")) * 24
        return f"{count} razy na dobę ({len(minute.split(','))} uruchomienia w każdej godzinie)" if pl else f"{count} times per day ({len(minute.split(','))} runs each hour)"
    if minute.isdigit() and hour.isdigit() and dom == month == dow == "*":
        return "1 raz na dobę" if pl else "once per day"
    if minute.isdigit() and re.fullmatch(r"\d+-\d+", hour) and dom == month == dow == "*":
        start, end = map(int, hour.split("-"))
        count = max(0, end - start + 1)
        return f"{count} razy na dobę (od {start:02d}:{int(minute):02d} do {end:02d}:{int(minute):02d})" if pl else f"{count} times per day (from {start:02d}:{int(minute):02d} to {end:02d}:{int(minute):02d})"
    return "Częstotliwość zależy od wszystkich pięciu pól harmonogramu." if pl else "Frequency depends on all five schedule fields."


def _command_explanation(entry: CronEntry, language: str) -> str:
    cmd = entry.command.strip()
    lower = cmd.lower()
    pl = language == "pl"
    pieces: list[str] = []

    if "run-parts" in lower:
        match = re.search(r"run-parts(?:\s+--report)?\s+([^;&|]+)", cmd)
        directory = match.group(1).strip() if match else "wskazanego katalogu"
        pieces.append((f"Polecenie run-parts uruchamia po kolei wykonywalne skrypty z katalogu {directory}." if pl else f"The run-parts command runs executable scripts from {directory} in sequence."))
        if "--report" in lower:
            pieces.append("Opcja --report powoduje raportowanie wyjścia tylko tych skryptów, które coś wypiszą." if pl else "The --report option reports output only for scripts that produce output.")
    if "anacron" in lower:
        pieces.append("Sprawdza obecność lub stan programu anacron, który nadrabia zadania pominięte podczas wyłączenia komputera." if pl else "Checks or invokes anacron, which catches up jobs missed while the computer was powered off.")
    if re.search(r"\btest\s+-x\b|\[\s+-x\s+", lower):
        pieces.append("Najpierw sprawdza, czy wskazany plik istnieje i ma prawo wykonywania." if pl else "First checks whether the referenced file exists and is executable.")
    if re.search(r"\btest\s+-e\b|\[\s+-e\s+", lower):
        pieces.append("Najpierw sprawdza, czy wskazany plik lub katalog istnieje." if pl else "First checks whether the referenced file or directory exists.")
    if "systemctl" in lower:
        pieces.append("Steruje usługą systemd lub odczytuje jej stan." if pl else "Controls or inspects a systemd service.")
    if "apt" in lower or "unattended-upgrade" in lower:
        pieces.append("Dotyczy obsługi pakietów lub automatycznych aktualizacji systemu." if pl else "Handles packages or automatic system updates.")
    if "logrotate" in lower:
        pieces.append("Rotuje i porządkuje pliki dziennika, aby ograniczyć ich rozmiar." if pl else "Rotates and manages log files to limit their size.")
    if "e2scrub" in lower:
        pieces.append("Sprawdza metadane systemów plików ext4 pod kątem błędów, zwykle bez ich odmontowywania." if pl else "Checks ext4 filesystem metadata for errors, usually without unmounting it.")
    if "rsync" in lower:
        pieces.append("Synchronizuje pliki lub katalogi; ostateczny efekt zależy od parametrów źródła i celu." if pl else "Synchronizes files or directories; the final effect depends on source and destination options.")
    if "curl" in lower or "wget" in lower:
        pieces.append("Pobiera dane z sieci. Należy zweryfikować adres oraz sposób użycia pobranej zawartości." if pl else "Downloads data from the network. Verify the URL and how the downloaded content is used.")
    if "&&" in cmd:
        pieces.append("Element po operatorze && wykona się tylko wtedy, gdy poprzedni element zakończy się powodzeniem." if pl else "The part after && runs only if the previous part succeeds.")
    if "||" in cmd:
        pieces.append("Element po operatorze || wykona się tylko wtedy, gdy poprzedni element zakończy się błędem." if pl else "The part after || runs only if the previous part fails.")
    if "cd /" in cmd:
        pieces.append("Zmienia katalog roboczy na katalog główny /, aby zadania nie zależały od bieżącego katalogu Crona." if pl else "Changes the working directory to / so the job does not depend on cron's current directory.")
    if not pieces:
        executable = cmd.split(maxsplit=1)[0] if cmd else ""
        name = Path(executable).name if executable else ""
        pieces.append((f"Uruchamia program lub skrypt „{name or executable}”. Dokładnego działania nie da się potwierdzić bez odczytania jego zawartości i parametrów." if pl else f"Runs the program or script “{name or executable}”. Its exact behavior cannot be confirmed without inspecting its contents and arguments."))
    return " ".join(pieces)


def _collect_warnings(entry: CronEntry, errors: list[str], language: str) -> list[str]:
    pl = language == "pl"
    warnings: list[str] = []
    for error in errors:
        warnings.append(f"Błąd harmonogramu: {error}" if pl else error)
    cmd = entry.command.lower()
    risky_patterns = [
        (r"\brm\s+-rf\s+/(?:\s|$)", "Polecenie może usunąć główny system plików." if pl else "The command appears capable of deleting the root filesystem."),
        (r"curl\b.*\|\s*(?:sh|bash)", "Pobrana z sieci zawartość jest przekazywana bezpośrednio do powłoki — to poważne ryzyko bezpieczeństwa." if pl else "The command pipes downloaded content directly into a shell."),
        (r"wget\b.*\|\s*(?:sh|bash)", "Pobrana z sieci zawartość jest przekazywana bezpośrednio do powłoki — to poważne ryzyko bezpieczeństwa." if pl else "The command pipes downloaded content directly into a shell."),
        (r"\bsudo\b", "Cron działa bez interakcji; sudo może oczekiwać hasła i zakończyć się niepowodzeniem." if pl else "Cron jobs normally run non-interactively; sudo may wait for a password or fail."),
        (r">\s*/dev/null\s+2>&1", "Całe wyjście jest odrzucane, przez co błędy mogą pozostać niewidoczne." if pl else "All output is discarded, which can hide failures."),
        (r"\bchmod\s+777\b", "Uprawnienia 777 zwykle są zbyt szerokie i mogą obniżyć bezpieczeństwo." if pl else "World-writable permissions are usually unsafe."),
    ]
    for pattern, message in risky_patterns:
        if re.search(pattern, cmd):
            warnings.append(message)
    first = entry.command.split(maxsplit=1)[0] if entry.command.strip() else ""
    if first and not first.startswith("/") and first not in {"cd", "export", "source", "test", "[", "if"}:
        warnings.append("Program nie ma ścieżki bezwzględnej. Cron używa ograniczonej zmiennej PATH, więc zadanie może działać inaczej niż w terminalu." if pl else "The executable is not an absolute path; cron has a limited PATH environment.")
    if "%" in entry.command and "\\%" not in entry.command:
        warnings.append("Niezabezpieczony znak % ma specjalne znaczenie w crontab i może podzielić polecenie." if pl else "Unescaped % characters have special meaning in crontab commands.")
    if entry.schedule == "* * * * *":
        warnings.append("Zadanie uruchamia się co minutę. Jeżeli trwa dłużej niż minutę, kolejne procesy mogą się nakładać." if pl else "Running every minute may create overlapping processes if the command takes longer than one minute.")
    if entry.user == "root":
        warnings.append("Zadanie działa jako root, więc błąd w skrypcie może mieć wpływ na cały system." if pl else "The job runs as root, so a script error can affect the entire system.")
    return warnings


def analyze(entry: CronEntry, language: str = "en") -> Analysis:
    language = "pl" if language.lower().startswith("pl") else "en"
    pl = language == "pl"
    errors = validate_schedule(entry.schedule)
    schedule_text = describe_schedule_pl(entry.schedule) if pl else describe_schedule(entry.schedule)
    warnings = _collect_warnings(entry, errors, language)
    command_text = _command_explanation(entry, language)
    frequency = _estimate_frequency(entry.schedule, language)

    state_text = "włączone" if entry.enabled else "wyłączone"
    if not pl:
        state_text = "enabled" if entry.enabled else "disabled"
    summary = (f"Zadanie jest {state_text} i uruchamia się {schedule_text}." if pl else f"This task is {state_text} and runs {schedule_text}.")
    details = [
        (f"Użytkownik: {entry.user or 'bieżący użytkownik'}" if pl else f"User: {entry.user or 'current user'}"),
        (f"Źródło: {entry.source}" if pl else f"Source: {entry.source}"),
        (f"Polecenie: {entry.command}" if pl else f"Command: {entry.command}"),
    ]

    impact = (
        "Zadanie działa z uprawnieniami roota i może wpływać na cały system. " if entry.user == "root" else
        "Zadanie działa z uprawnieniami wskazanego użytkownika. "
    ) if pl else (
        "The job runs as root and can affect the whole system. " if entry.user == "root" else
        "The job runs with the selected user's permissions. "
    )
    if any(token in entry.command.lower() for token in ("run-parts", "anacron", "logrotate", "e2scrub", "apt")):
        impact += "Wygląda na zadanie konserwacyjne; może używać dysku, procesora lub sieci przez krótki czas." if pl else "It appears to be a maintenance task and may briefly use disk, CPU, or network resources."
    else:
        impact += "Rzeczywisty wpływ zależy od działania uruchamianego programu lub skryptu." if pl else "The actual impact depends on the invoked program or script."

    risk_text = "\n".join(f"• {item}" for item in warnings) if warnings else (
        "Nie wykryto oczywistych problemów składniowych ani typowych ryzyk. Nadal warto sprawdzić zawartość wywoływanych skryptów." if pl else
        "No obvious syntax problems or common risks were detected. You should still inspect any invoked scripts."
    )

    suggestions: list[str] = []
    if not entry.enabled:
        suggestions.append("Zadanie jest wyłączone — nie zostanie uruchomione, dopóki go nie włączysz." if pl else "The task is disabled and will not run until enabled.")
    if entry.user == "root":
        suggestions.append("Pozostaw uprawnienia root tylko wtedy, gdy są rzeczywiście wymagane." if pl else "Keep root privileges only when they are genuinely required.")
    if not re.search(r"\bflock\b", entry.command) and (entry.schedule == "* * * * *" or re.search(r"\*/[1-9]", entry.schedule)):
        suggestions.append("Przy częstym uruchamianiu rozważ użycie flock, aby zapobiec nakładaniu się kolejnych instancji." if pl else "For frequent runs, consider flock to prevent overlapping instances.")
    if not re.search(r"(?:>>?|2>)", entry.command):
        suggestions.append("Rozważ zapis standardowego wyjścia i błędów do dziennika albo systemd-journald, aby łatwiej diagnozować awarie." if pl else "Consider logging standard output and errors to a file or systemd-journald for easier troubleshooting.")
    suggestions.append("Sprawdź harmonogram względem strefy czasowej systemu; Cron używa czasu skonfigurowanego na komputerze." if pl else "Verify the schedule against the system timezone; cron uses the computer's configured time.")

    sections = {
        ("Znaczenie harmonogramu" if pl else "Schedule meaning"): (f"{schedule_text.capitalize()}. Harmonogram źródłowy: {entry.schedule}." if pl else f"{schedule_text.capitalize()}. Raw schedule: {entry.schedule}."),
        ("Co robi zadanie" if pl else "What the task does"): command_text,
        ("Częstotliwość" if pl else "Frequency"): frequency,
        ("Wpływ na system" if pl else "System impact"): impact,
        ("Ryzyka / uwagi" if pl else "Risks / notes"): risk_text,
        ("Sugestia" if pl else "Recommendation"): "\n".join(f"• {item}" for item in suggestions),
    }
    return Analysis(summary=summary, details=details, warnings=warnings, sections=sections)
