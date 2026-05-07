#!/usr/bin/env python3
"""
series_checker.py – Scannt einen Ordner nach Serien-Episoden und zeigt
fehlende Episoden anhand der TMDB-Datenbank an.

Benötigt:  pip install requests colorama
Optional:  pip install rarfile   (zum Lesen von RAR-Inhalten)
TMDB API-Key: https://www.themoviedb.org/settings/api
"""

# ══════════════════════════════════════════════════════════════════════════════
#  KONFIGURATION – hier deinen TMDB API-Key eintragen
# ══════════════════════════════════════════════════════════════════════════════
TMDB_API_KEY = ""

# Zukünftige Staffeln/Episoden standardmäßig ausblenden?
# True  = nur bereits ausgestrahlte Inhalte werden verglichen
# False = alle TMDB-Staffeln werden angezeigt (auch Zukünftige)
# Kann per --hide-future / --show-future überschrieben werden.
HIDE_FUTURE_DEFAULT = True
# ══════════════════════════════════════════════════════════════════════════════

import os
import re
import sys
import json
import time
import argparse
from datetime import date
from pathlib import Path
from collections import defaultdict

try:
    import requests
except ImportError:
    sys.exit("Fehler: 'requests' nicht installiert. → pip install requests")

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
except ImportError:
    class Fore:
        RED = GREEN = YELLOW = CYAN = MAGENTA = BLUE = WHITE = ""
    class Style:
        BRIGHT = RESET_ALL = DIM = ""

# Optional: rarfile für RAR-Inhalts-Scan
try:
    import rarfile
    HAS_RARFILE = True
except ImportError:
    HAS_RARFILE = False

# ── Regex ─────────────────────────────────────────────────────────────────────

# S01E01, S01E01E02, S01E01-E02, s1e1 usw.
EP_REGEX = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})(?:[-]?[Ee](\d{1,2}))?")

# Staffel-Ordner: S01, S1, Season 1, Staffel 2 usw.
SEASON_DIR_RE = re.compile(
    r"^(?:[Ss](?:taffel|eason)?\s*\d{1,2}|[Ss]\d{2})$"
)

# Erstes RAR-Archiv einer Multipart-Sammlung
RAR_FIRST_RE = re.compile(r"\.rar$|\.001$|\.part0*1\.rar$", re.I)
# Alle RAR-Teilarchive (zum Überspringen von Teil 2+)
RAR_PART_RE = re.compile(r"\.(r\d{2}|\d{3}|part0*[2-9]\d*\.rar)$", re.I)

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts",
    ".wmv", ".flv", ".divx", ".m4v", ".mpg", ".mpeg",
}

# ── TMDB ──────────────────────────────────────────────────────────────────────
TMDB_BASE = "https://api.themoviedb.org/3"

# Umlaut-Ersetzungen: ae→ä, oe→ö, ue→ü, ss→ß
UMLAUT_MAP = [("ae", "ä"), ("oe", "ö"), ("ue", "ü"), ("ss", "ß")]


def apply_umlauts(name: str) -> str:
    """'zwei graeber' → 'Zwei Gräber'"""
    result = name.lower()
    for old, new in UMLAUT_MAP:
        result = result.replace(old, new)
    if result == name.lower():
        return name  # keine Änderung
    return result.title()


def strip_articles(name: str) -> str:
    """Entfernt häufige Artikel am Wortanfang."""
    for art in ("Der ", "Die ", "Das ", "The ", "Ein ", "Eine ", "Les ", "Le ", "La "):
        if name.startswith(art):
            return name[len(art):]
    return name


class TMDB:
    def __init__(self, api_key: str, verbose: bool = False):
        self.api_key = api_key
        self.verbose = verbose
        self.session = requests.Session()
        self._cache: dict = {}

    def _get(self, path: str, lang: str = "de-DE", **params) -> dict:
        url = f"{TMDB_BASE}{path}"
        p = {"api_key": self.api_key, "language": lang, **params}
        r = self.session.get(url, params=p, timeout=10)
        r.raise_for_status()
        return r.json()

    def search_smart(self, name: str) -> tuple[list[dict], str]:
        """
        Sucht mit mehreren Strategien bis ein Treffer gefunden wird:
          1. Original (de-DE)
          2. Mit Umlauten ae→ä / oe→ö / ue→ü (de-DE)
          3. Ohne Artikel (de-DE)
          4. Ohne Artikel + Umlaute (de-DE)
          5. Original (en-US)
          6. Mit Umlauten (en-US)
        Gibt (Ergebnisliste, genutzte Query) zurück.
        """
        import re as _re2, unicodedata as _ud
        name = _ud.normalize("NFC", name)
        name = _re2.sub(r" \(\d{4}\)$", "", name).strip()
        with_umlauts = apply_umlauts(name)
        stripped = strip_articles(name)
        stripped_umlauts = apply_umlauts(stripped)

        strategies: list[tuple[str, str]] = [
            ("de-DE", name),
            ("de-DE", with_umlauts),
            ("de-DE", stripped),
            ("de-DE", stripped_umlauts),
            ("en-US", name),
            ("en-US", with_umlauts),
        ]

        # Duplikate entfernen
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for lang, q in strategies:
            key = f"{lang}:{q.lower()}"
            if key not in seen:
                seen.add(key)
                unique.append((lang, q))

        for lang, query in unique:
            self._vlog(f"[TMDB] Suche ({lang}): '{query}'")
            try:
                data = self._get("/search/tv", lang=lang, query=query)
                results = data.get("results", [])
                if results:
                    names = [r.get("name", "?") for r in results[:3]]
                    self._vlog(
                        f"[TMDB] {len(results)} Treffer: {', '.join(names)}"
                    )
                    return results, query
                else:
                    self._vlog("[TMDB] Keine Treffer.")
            except requests.RequestException as e:
                self._vlog(f"[TMDB] Fehler: {e}")
            time.sleep(0.05)

        return [], name

    def get_series_details(self, series_id: int) -> dict:
        key = f"details:{series_id}"
        if key not in self._cache:
            self._cache[key] = self._get(f"/tv/{series_id}")
        return self._cache[key]

    def get_season_episodes(self, series_id: int, season: int) -> list[dict]:
        """Gibt rohe Episode-Dicts inkl. air_date zurück."""
        key = f"season:{series_id}:{season}"
        if key not in self._cache:
            data = self._get(f"/tv/{series_id}/season/{season}")
            self._cache[key] = data.get("episodes", [])
        return self._cache[key]

    def get_season_air_date(self, series_id: int, season: int) -> date | None:
        """Gibt das Ausstrahlungsdatum der Staffel zurück (aus Series-Details)."""
        details = self.get_series_details(series_id)
        for s in details.get("seasons", []):
            if s["season_number"] == season:
                raw = s.get("air_date") or ""
                return _parse_date(raw)
        return None

    def _vlog(self, msg: str):
        if self.verbose:
            print(f"    {Style.DIM}{msg}{Style.RESET_ALL}")


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _parse_date(raw: str) -> date | None:
    """Parst 'YYYY-MM-DD' → date, oder None bei leerem/ungültigem Wert."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _episode_aired(ep: dict, today: date) -> bool:
    """True wenn die Episode ein Datum hat und dieses ≤ heute ist."""
    d = _parse_date(ep.get("air_date", ""))
    return d is not None and d <= today

def vlog(verbose: bool, msg: str):
    if verbose:
        print(f"  {Style.DIM}{msg}{Style.RESET_ALL}")


def normalize_name(raw: str) -> str:
    """
    Bereinigt Ordner-/Dateinamen zu einem lesbaren Serientitel.
    Alles vor dem ersten S##E## gilt als Serienname.
    """
    import unicodedata as _ud
    raw = _ud.normalize("NFC", raw)  # macOS NFD-Umlaute -> NFC
    m = EP_REGEX.search(raw)
    if m:
        raw = raw[: m.start()]
    name = re.sub(r"[._\-]", " ", raw).strip()
    name = re.sub(r" {2,}", " ", name).strip(" -_.")
    return name


def is_season_dir(name: str) -> bool:
    return bool(SEASON_DIR_RE.match(name.strip()))


def extract_episodes(name: str) -> list[tuple[int, int]]:
    """Gibt Liste von (season, episode) aus einem Namen zurück."""
    result = []
    for m in EP_REGEX.finditer(name):
        s, e1 = int(m.group(1)), int(m.group(2))
        result.append((s, e1))
        if m.group(3):
            result.append((s, int(m.group(3))))
    return result


def series_name_from_path(file_path: Path, root: Path, verbose: bool) -> str | None:
    """
    Leitet den Seriennamen aus der Ordnerstruktur ab.

    Prioritäten:
      1. Erster Nicht-Staffel-Ordner unterhalb von root
         → Ordner enthält S##E##: Serienname ist alles davor
         → Ordner ohne S##E##: der Ordnername selbst ist der Serienname
      2. Fallback: Datei-/Archivname selbst
    """
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        return None

    # Ordnerteile ohne den Dateinamen selbst
    parts = list(rel.parts[:-1])

    for part in parts:
        if is_season_dir(part):
            continue  # S01, Season 1, Staffel 2 → überspringen
        if EP_REGEX.search(part):
            # Release-Ordner: "My.Series.S01E01.GERMAN-XXX"
            name = normalize_name(part)
            vlog(verbose, f"Name aus Release-Ordner: '{part}' → '{name}'")
            return name or None
        # Normaler Serienordner: "My Series"
        name = normalize_name(part)
        vlog(verbose, f"Name aus Serienordner: '{part}' → '{name}'")
        return name or None

    # Fallback: aus Dateiname / RAR-Name
    name = normalize_name(file_path.stem)
    vlog(verbose, f"Name aus Dateiname: '{file_path.name}' → '{name}'")
    return name or None


# ── Scanner ───────────────────────────────────────────────────────────────────

def scan_folder(
    root: str, depth: int, verbose: bool
) -> dict[str, dict[int, set[int]]]:
    """
    Scannt den Ordner rekursiv. Erkennt:
      · Videodateien (mkv, mp4, avi …)
      · RAR-Archive  (Dateiname + optionaler Blick ins Archiv via rarfile)
      · Ordnernamen  mit S##E##-Tag (glFTPD Release-Ordner Stil)
    """
    found: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    root_path = Path(root).resolve()
    stats = {"videos": 0, "rars": 0, "dirs": 0, "skipped": 0}

    def add(series: str, season: int, ep: int):
        found[series][season].add(ep)

    def recurse(path: Path, depth_left: int):
        if depth_left < 0:
            return
        try:
            entries = sorted(path.iterdir())
        except PermissionError:
            vlog(verbose, f"Kein Zugriff: {path}")
            return

        for entry in entries:
            if entry.is_dir():
                # Ordnername selbst auf S##E## prüfen (Release-Ordner-Stil)
                eps = extract_episodes(entry.name)
                if eps:
                    stats["dirs"] += 1
                    # Dummy-Pfad: der Ordner selbst ist "die Datei"
                    dummy = entry / "_"
                    series = series_name_from_path(dummy, root_path, verbose)
                    if series:
                        for s, e in eps:
                            vlog(verbose, f"  [ORDNER] {entry.name} → {series} S{s:02d}E{e:02d}")
                            add(series, s, e)
                recurse(entry, depth_left - 1)

            elif entry.is_file():
                ext = entry.suffix.lower()

                # Videodatei
                if ext in VIDEO_EXTENSIONS:
                    eps = extract_episodes(entry.name)
                    if eps:
                        stats["videos"] += 1
                        series = series_name_from_path(entry, root_path, verbose)
                        if series:
                            for s, e in eps:
                                vlog(verbose, f"  [VIDEO] {entry.name} → {series} S{s:02d}E{e:02d}")
                                add(series, s, e)
                    else:
                        stats["skipped"] += 1
                        vlog(verbose, f"  [SKIP]  Kein S##E##: {entry.name}")

                # RAR-Archiv (nur erstes Teilarchiv verarbeiten)
                elif entry.name.lower().endswith(".rar") or entry.name.endswith(".001"):
                    if RAR_PART_RE.search(entry.name):
                        continue  # .r00, .r01, part02.rar usw. überspringen
                    process_rar(entry)

    def process_rar(rar_path: Path):
        stats["rars"] += 1
        eps: list[tuple[int, int]] = []
        source = ""

        # 1) RAR-Inhalt lesen (wenn rarfile installiert)
        if HAS_RARFILE:
            try:
                with rarfile.RarFile(str(rar_path)) as rf:
                    for info in rf.infolist():
                        inner = Path(info.filename).name
                        if Path(inner).suffix.lower() in VIDEO_EXTENSIONS:
                            inner_eps = extract_episodes(inner)
                            if inner_eps:
                                vlog(verbose, f"  [RAR-INHALT] {rar_path.name} → {inner}")
                                eps.extend(inner_eps)
                                source = "RAR-Inhalt"
            except Exception as ex:
                vlog(verbose, f"  [RAR-FEHLER] {rar_path.name}: {ex}")

        # 2) Fallback: RAR-Dateiname selbst
        if not eps:
            eps = extract_episodes(rar_path.stem)
            if eps:
                source = "RAR-Name"

        # 3) Letzter Fallback: Elternordner-Name
        if not eps:
            eps = extract_episodes(rar_path.parent.name)
            if eps:
                source = "Ordner-Name"

        if not eps:
            stats["skipped"] += 1
            vlog(verbose, f"  [RAR-SKIP] Kein S##E## gefunden: {rar_path.name}")
            return

        series = series_name_from_path(rar_path, root_path, verbose)
        if series:
            for s, e in eps:
                vlog(verbose, f"  [{source}] {rar_path.name} → {series} S{s:02d}E{e:02d}")
                add(series, s, e)

    recurse(root_path, depth)

    if verbose:
        print(
            f"\n  {Style.DIM}Scan fertig: "
            f"{stats['videos']} Videos  |  "
            f"{stats['rars']} RAR-Archive  |  "
            f"{stats['dirs']} Release-Ordner  |  "
            f"{stats['skipped']} übersprungen{Style.RESET_ALL}\n"
        )

    return dict(found)


# ── TMDB-Abgleich ─────────────────────────────────────────────────────────────

def pick_best_match(results: list[dict], name: str) -> dict | None:
    if not results:
        return None
    name_lower = name.lower()
    name_uml = apply_umlauts(name).lower()
    for r in results:
        for field in ("name", "original_name"):
            val = r.get(field, "").lower()
            if val in (name_lower, name_uml):
                return r
    return results[0]


def check_series(
    tmdb: TMDB,
    local_data: dict[str, dict[int, set[int]]],
    interactive: bool,
    skip_complete_seasons: bool,
    verbose: bool,
    hide_future: bool,
    hide_future_seasons_only: bool,
) -> list[dict]:
    results = []
    total = len(local_data)
    today = date.today()
    filter_mode = (
        "alle (inkl. Zukunft)"
        if not hide_future and not hide_future_seasons_only
        else ("nur Staffeln" if hide_future_seasons_only else "Staffeln + Episoden")
    )

    for idx, (series_name, seasons) in enumerate(sorted(local_data.items()), 1):
        print(
            f"\n{Fore.CYAN}{Style.BRIGHT}[{idx}/{total}] {series_name}{Style.RESET_ALL}"
        )

        try:
            import unicodedata, re as _re
            # Fix 1: NFD-Umlaute (macOS) → NFC normalisieren (ä statt a+combining)
            search_name = unicodedata.normalize("NFC", series_name)
            # Fix 2: Doppeltes Jahr entfernen z.B. "Magnum (2018) (2018)" → "Magnum (2018)"
            search_name = _re.sub(r"(\s*\(\d{4}\))\s*\(\d{4}\)$", r"\1", search_name)
            # Fix 3: {tmdb XXXX} direkt als TMDB-ID nutzen
            tmdb_id_match = _re.search(r"\{tmdb[- ](\d+)\}", search_name, _re.IGNORECASE)
            if tmdb_id_match:
                forced_id = int(tmdb_id_match.group(1))
                det = tmdb.get_series_details(forced_id)
                forced_name = det.get("name") or det.get("original_name", series_name)
                search_results = [{"id": forced_id, "name": forced_name}]
                used_query = f"{{tmdb {forced_id}}}"
            else:
                search_results, used_query = tmdb.search_smart(search_name)
        except requests.RequestException as e:
            print(f"  {Fore.RED}API-Fehler: {e}")
            continue

        if not search_results:
            print(f"  {Fore.RED}✘ Kein Treffer (alle Suchstrategien erschöpft).")
            print(
                f"  {Fore.YELLOW}  → TMDB manuell prüfen: "
                f"https://www.themoviedb.org/search?query="
                f"{requests.utils.quote(series_name)}"
            )
            results.append({
                "local_name": series_name,
                "tmdb_name": None,
                "tmdb_id": None,
                "seasons": [],
                "complete": False,
                "not_found": True,
            })
            continue

        match = pick_best_match(search_results, series_name)
        tmdb_name = match.get("name") or match.get("original_name", "?")
        series_id = match["id"]

        if verbose and used_query.lower() != series_name.lower():
            print(
                f"  {Style.DIM}Treffer via: '{series_name}' → '{used_query}'"
                f"{Style.RESET_ALL}"
            )

        # Interaktiv nachfragen wenn Name stark abweicht
        if interactive and tmdb_name.lower() not in (
            series_name.lower(), apply_umlauts(series_name).lower()
        ):
            print(
                f"  {Fore.YELLOW}? Gefunden: '{tmdb_name}' (ID {series_id})\n"
                f"    Korrekt? [j / n / TMDB-ID]: ",
                end="",
            )
            ans = input().strip().lower()
            if ans in ("n", ""):
                print(f"  {Fore.YELLOW}  Übersprungen.")
                continue
            elif ans not in ("j", "y") and ans.isdigit():
                series_id = int(ans)
                det = tmdb.get_series_details(series_id)
                tmdb_name = det.get("name", tmdb_name)
        else:
            print(f"  {Fore.GREEN}✓ TMDB: {tmdb_name} (ID {series_id})")

        try:
            details = tmdb.get_series_details(series_id)
        except requests.RequestException as e:
            print(f"  {Fore.RED}Details-Fehler: {e}")
            continue

        local_seasons = set(seasons.keys())
        tmdb_seasons = {
            s["season_number"]
            for s in details.get("seasons", [])
            if s["season_number"] > 0  # Staffel 0 = Specials
        }

        series_result: dict = {
            "local_name": series_name,
            "tmdb_name": tmdb_name,
            "tmdb_id": series_id,
            "seasons": [],
            "complete": False,
            "not_found": False,
        }
        all_complete = True

        for sn in sorted(tmdb_seasons):
            try:
                eps = tmdb.get_season_episodes(series_id, sn)
                time.sleep(0.07)
            except requests.RequestException:
                continue

            # ── Zukunftsfilter ──────────────────────────────────────────────
            any_filter = hide_future or hide_future_seasons_only

            if any_filter:
                # Staffel-Startdatum aus Series-Details
                season_air = tmdb.get_season_air_date(series_id, sn)
                season_future = (season_air is None) or (season_air > today)

                # Hat mindestens eine Episode ein Datum ≤ heute?
                any_aired = any(_episode_aired(e, today) for e in eps)

                # Staffel komplett in der Zukunft → überspringen
                if season_future and not any_aired:
                    if verbose:
                        air_str = str(season_air) if season_air else "kein Datum"
                        print(
                            f"    {Style.DIM}S{sn:02d}: Zukunft ({air_str}) – "
                            f"übersprungen{Style.RESET_ALL}"
                        )
                    continue  # Staffel komplett ausblenden

            # Bei --hide-future: auch innerhalb laufender Staffeln nur
            # bereits ausgestrahlte Episoden als "erwartet" zählen
            if hide_future and not hide_future_seasons_only:
                aired_eps = [e for e in eps if _episode_aired(e, today)]
                future_count = len(eps) - len(aired_eps)
                eps_for_expected = aired_eps
            else:
                eps_for_expected = eps
                future_count = 0
            # ────────────────────────────────────────────────────────────────

            expected = {
                e["episode_number"] for e in eps_for_expected
                if e.get("episode_number")
            }
            local_eps = seasons.get(sn, set())
            missing = sorted(expected - local_eps)
            extra = sorted(local_eps - expected)
            in_local = sn in local_seasons

            if verbose and in_local:
                future_note = f"  [{future_count} künftige ausgeblendet]" if future_count else ""
                print(
                    f"    {Style.DIM}S{sn:02d}: "
                    f"erwartet {sorted(expected)}  "
                    f"lokal {sorted(local_eps)}{future_note}{Style.RESET_ALL}"
                )

            sdata = {
                "season": sn,
                "expected": sorted(expected),
                "local": sorted(local_eps),
                "missing": missing,
                "extra": extra,
                "in_local": in_local,
                "future_episodes_hidden": future_count,
            }

            # Vollständige Staffeln optional weglassen
            if skip_complete_seasons and not missing and in_local:
                pass
            else:
                series_result["seasons"].append(sdata)

            if missing or not in_local:
                all_complete = False

        series_result["complete"] = all_complete
        results.append(series_result)

    return results


# ── Ausgabe ───────────────────────────────────────────────────────────────────

def episodes_to_ranges(eps: list[int]) -> str:
    if not eps:
        return ""
    ranges: list[tuple[int, int]] = []
    start = end = eps[0]
    for e in eps[1:]:
        if e == end + 1:
            end = e
        else:
            ranges.append((start, end))
            start = end = e
    ranges.append((start, end))
    parts = [f"E{s:02d}" if s == e else f"E{s:02d}-E{e:02d}" for s, e in ranges]
    return ", ".join(parts)


def render_results(results: list[dict], show_complete: bool, json_out: str | None):
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n{Fore.GREEN}JSON gespeichert: {json_out}{Style.RESET_ALL}")

    W = 70
    sep = "─" * (W - 3)

    print(f"\n\n{Fore.WHITE}{Style.BRIGHT}{'═' * W}")
    print(f"  ERGEBNISÜBERSICHT")
    print(f"{'═' * W}{Style.RESET_ALL}")

    found_series = [r for r in results if not r.get("not_found")]
    not_found = [r for r in results if r.get("not_found")]

    for r in found_series:
        is_complete = r.get("complete", False)
        if is_complete and not show_complete:
            continue

        icon = f"{Fore.GREEN}✔" if is_complete else f"{Fore.RED}✘"
        print(
            f"\n{icon}  {Fore.WHITE}{Style.BRIGHT}{r['local_name']}{Style.RESET_ALL}"
            f"  {Style.DIM}→  {r['tmdb_name']} (ID {r['tmdb_id']}){Style.RESET_ALL}"
        )
        print(f"   {sep}")

        if not r["seasons"]:
            print(f"   {Fore.GREEN}  Alle Staffeln vollständig ✔")
            continue

        for s in r["seasons"]:
            sn       = s["season"]
            local    = s["local"]
            missing  = s["missing"]
            extra    = s["extra"]
            in_local = s["in_local"]
            total_ex = len(s["expected"])

            if not in_local:
                print(
                    f"   {Fore.MAGENTA}  S{sn:02d}  "
                    f"[komplett fehlend – {total_ex} Episoden]"
                )
                continue

            have = len(local)
            miss = len(missing)

            if miss == 0:
                if show_complete:
                    fut = s.get("future_episodes_hidden", 0)
                    fut_note = f"  {Style.DIM}+{fut} künftige ausgeblendet{Style.RESET_ALL}" if fut else ""
                    print(
                        f"   {Fore.GREEN}  S{sn:02d}  ✔  Vollständig ({have}/{total_ex}){fut_note}"
                    )
            else:
                pct = 100 * have / total_ex if total_ex else 0
                fut = s.get("future_episodes_hidden", 0)
                fut_note = f"  {Style.DIM}(+{fut} künftige ausgeblendet){Style.RESET_ALL}" if fut else ""
                print(
                    f"   {Fore.YELLOW}  S{sn:02d}  "
                    f"{have}/{total_ex} ({pct:.0f}%)  │  "
                    f"{Fore.RED}{miss} fehlend{fut_note}"
                )
                print(f"         {Fore.RED}Fehlend : {episodes_to_ranges(missing)}")

            if extra:
                print(
                    f"         {Fore.BLUE}Extras  : "
                    f"{', '.join(f'E{e:02d}' for e in extra)}"
                    f"  {Style.DIM}(nicht in TMDB){Style.RESET_ALL}"
                )

    # Nicht gefundene Serien
    if not_found:
        print(f"\n{Fore.RED}{Style.BRIGHT}Nicht in TMDB gefunden:{Style.RESET_ALL}")
        print(f"{'─' * W}")
        for r in not_found:
            print(f"  {Fore.RED}✘  {r['local_name']}")

    # Zusammenfassung
    total    = len(results)
    complete = sum(1 for r in found_series if r.get("complete"))
    incompl  = len(found_series) - complete
    nf       = len(not_found)

    print(f"\n{'═' * W}")
    print(
        f"  Gesamt: {total}  │  "
        f"{Fore.GREEN}Vollständig: {complete}{Style.RESET_ALL}  │  "
        f"{Fore.YELLOW}Unvollständig: {incompl}{Style.RESET_ALL}  │  "
        f"{Fore.RED}Nicht gefunden: {nf}{Style.RESET_ALL}"
    )
    print(f"{'═' * W}\n")


# ── Help-Text ─────────────────────────────────────────────────────────────────

HELP_EPILOG = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  OPTIONEN IM DETAIL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  folder                Pfad zum Scannen, z. B. /mnt oder /mnt/serien.
                        Unterordner werden automatisch mitgescannt.

  --api-key KEY         TMDB API-Key. Falls nicht angegeben, wird der
                        Wert von TMDB_API_KEY oben in der Datei genutzt.
                        → Key holen: themoviedb.org/settings/api

  --depth N             Maximale Unterordner-Tiefe (Standard: 4).
                        Tiefe 2:  /serien/My Series/S01/ep.mkv
                        Tiefe 3:  /mnt/hdd/serien/Name/S01/ep.mkv
                        Tiefe 4:  /mnt/A/B/Serienname/S01/ep.mkv

  --verbose, -v         Detaillierte Ausgabe während des Scans:
                          · Welche Datei/Ordner auf welche Serie gemappt wurde
                          · Welche TMDB-Suchanfragen gestellt wurden
                          · Woher der Episodentag stammt (Ordner/Datei/RAR)
                          · Scan-Statistik: Videos / RARs / Ordner / Skipped

  --show-complete       Zeigt auch vollständig vorhandene Serien/Staffeln.
                        Ohne Flag erscheinen nur Serien mit Lücken.

  --skip-complete-seasons
                        Staffeln ohne Fehlende werden in der Detailansicht
                        ausgeblendet – nur Staffeln mit Lücken erscheinen.

  --interactive         Bei unklaren TMDB-Treffern interaktiv nachfragen.
                        Eingabemöglichkeiten nach dem Prompt:
                          j / y    → Treffer akzeptieren
                          n        → überspringen
                          12345    → eigene TMDB-ID eingeben

  --json DATEI          Ergebnisse als JSON-Datei speichern.
                        Enthält alle Staffeln, erwartete/lokale/fehlende
                        Episodennummern und TMDB-Metadaten.

  --hide-future         Noch nicht ausgestrahlte Inhalte komplett ausblenden.
                        · Staffeln deren Startdatum in der Zukunft liegt
                          werden nicht angezeigt.
                        · Innerhalb laufender Staffeln werden nur bereits
                          gesendete Episoden als "erwartet" gezählt.
                        Standard: abhängig von HIDE_FUTURE_DEFAULT in der Datei
                                  (aktuell True = ausgeblendet).

  --hide-future-seasons Nur komplett zukünftige Staffeln ausblenden.
                        Episoden innerhalb laufender Staffeln (auch künftige)
                        werden weiterhin als fehlend angezeigt.

  --show-future         Alle TMDB-Staffeln anzeigen, auch noch nicht
                        ausgestrahlte. Überschreibt HIDE_FUTURE_DEFAULT.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ERKANNTE STRUKTUREN & DATEITYPEN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Videodateien (mkv, mp4, avi, ts, m2ts, mov, wmv, divx, m4v, mpg):
    My.Series.S02E05.GERMAN.DL.1080p.BluRay-GRP.mkv
    The.Wire.S03E01E02.720p.x265-XXX.mkv        ← Doppelepisode
    Severance - S01E04 - The You You Are.mp4

  RAR-Archive (Dateiname oder Archivinhalt via rarfile):
    serie.S01E01.GERMAN.DL.1080p-GRP.rar
    serie.s01e02.part01.rar  /  serie.s01e02.001
    → pip install rarfile erlaubt Blick in den Archiv-Inhalt

  Release-Ordner (Ordnername enthält S##E##, glFTPD-Stil):
    /mnt/TV/My.Series.S01E01.GERMAN.1080p-GRP/
      My.Series.S01E01.GERMAN.1080p-GRP.rar
      My.Series.S01E01.GERMAN.1080p-GRP.sfv

  Klassische Bibliotheks-Struktur:
    /serien/My Series/S01/E01.mkv
    /serien/My Series/Staffel 1/episode.mkv
    /serien/My Series/Season 2/ep.mkv

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SMART-SUCHE – WIE WIRD DER SERIENNAME GEFUNDEN?
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Suchstrategien (erste mit Treffer gewinnt):
    1. Original auf Deutsch          "zwei graeber"
    2. Mit Umlauten (ae→ä usw.)     "zwei gräber"   ← löst das Problem!
    3. Ohne Artikel                  "Wire" statt "The Wire"
    4. Ohne Artikel + Umlaute
    5. Englisch + Original
    6. Englisch + Umlaute

  Mit --verbose sieht man genau welche Query den Treffer geliefert hat.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BEISPIELE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  # Einfacher Scan (API-Key oben in der Datei eingetragen)
  python series_checker.py /mnt/serien

  # API-Key per Argument
  python series_checker.py /mnt/serien --api-key abc123xyz

  # Tief verschachtelte Ordnerstruktur
  python series_checker.py /mnt --depth 5

  # Details anzeigen: was wird erkannt, welche TMDB-Query greift
  python series_checker.py /mnt/serien --verbose

  # Vollständige Serien ebenfalls anzeigen
  python series_checker.py /mnt/serien --show-complete

  # Nur fehlerhafte Staffeln anzeigen (vollständige ausblenden)
  python series_checker.py /mnt/serien --skip-complete-seasons

  # Zukünftige Staffeln + Episoden komplett ausblenden
  python series_checker.py /mnt/serien --hide-future

  # Nur komplett zukünftige Staffeln ausblenden (laufende Staffeln vollständig)
  python series_checker.py /mnt/serien --hide-future-seasons

  # Alle TMDB-Staffeln anzeigen (auch nicht ausgestrahlte)
  python series_checker.py /mnt/serien --show-future

  # Interaktiver Modus bei Namensproblemen
  python series_checker.py /mnt/serien --interactive

  # JSON-Export
  python series_checker.py /mnt/serien --json /tmp/fehlend.json

  # Alles kombiniert
  python series_checker.py /mnt --depth 5 --verbose \\
      --interactive --skip-complete-seasons --json /tmp/bericht.json

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BEISPIELAUSGABE (ohne --verbose)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ╔══ Series Checker ══╗
    Ordner : /mnt/serien
    Tiefe  : 4
    Verbose: nein
    RAR    : rarfile verfügbar ✔

  Gefundene Serien: 3
    My Series   –  S01(5ep) S02(13ep) S03(2ep)
    zwei graeber   –  S01(3ep)
    Some Show      –  S01(6ep)

  [1/3] My Series
    ✓ TMDB: My Series (ID 1396)
  [2/3] zwei graeber
    ✓ TMDB: Zwei Gräber (ID 12345)    ← via Umlaut-Strategie
  [3/3] Some Show
    ✘ Kein Treffer (alle Strategien erschöpft)
      → TMDB manuell prüfen: https://www.themoviedb.org/search?query=...

  ══════════════════════════════════════════════════════════════════════
    ERGEBNISÜBERSICHT
  ══════════════════════════════════════════════════════════════════════

  ✘  My Series  →  My Series (ID 1396)
     ─────────────────────────────────────────────────────────────────
     S01  5/7 (71%)  │  2 fehlend
           Fehlend : E03, E06
     S02  ✔  Vollständig (13/13)
     S03  [komplett fehlend – 13 Episoden]

  ✔  zwei graeber  →  Zwei Gräber (ID 12345)
     ─────────────────────────────────────────────────────────────────
     Alle Staffeln vollständig ✔

  Nicht in TMDB gefunden:
  ──────────────────────────────────────────────────────────────────────
    ✘  Some Show

  ══════════════════════════════════════════════════════════════════════
    Gesamt: 3  │  Vollständig: 1  │  Unvollständig: 1  │  Nicht gefunden: 1
  ══════════════════════════════════════════════════════════════════════
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="series_checker.py",
        description=(
            "Scannt einen Ordner nach Serien-Episoden (Videos, RARs,\n"
            "Release-Ordner) und zeigt fehlende Episoden via TMDB an.\n\n"
            "API-Key oben in der Datei unter TMDB_API_KEY eintragen\n"
            "oder per --api-key übergeben."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG,
    )
    parser.add_argument(
        "folder",
        help="Ordner zum Scannen (z. B. /mnt/serien)",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        default=None,
        help="TMDB API-Key (überschreibt TMDB_API_KEY oben in der Datei)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=4,
        metavar="N",
        help="Maximale Suchtiefe in Unterordnern (Standard: 4)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Detaillierte Ausgabe: Scan-Details, TMDB-Queries, Episodenzuordnung",
    )
    parser.add_argument(
        "--show-complete",
        action="store_true",
        help="Auch vollständige Serien/Staffeln anzeigen",
    )
    parser.add_argument(
        "--skip-complete-seasons",
        action="store_true",
        help="Vollständige Staffeln in der Detailansicht ausblenden",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Bei unklaren TMDB-Treffern interaktiv nachfragen",
    )
    parser.add_argument(
        "--json",
        metavar="DATEI",
        help="Ergebnisse als JSON-Datei speichern",
    )

    # Zukunfts-Filter
    future_group = parser.add_mutually_exclusive_group()
    future_group.add_argument(
        "--hide-future",
        action="store_true",
        default=None,
        help=(
            "Noch nicht ausgestrahlte Staffeln UND Episoden ausblenden "
            "(Standard wenn HIDE_FUTURE_DEFAULT=True)"
        ),
    )
    future_group.add_argument(
        "--hide-future-seasons",
        action="store_true",
        help=(
            "Nur komplett zukünftige Staffeln ausblenden; "
            "einzelne künftige Episoden laufender Staffeln bleiben sichtbar"
        ),
    )
    future_group.add_argument(
        "--show-future",
        action="store_true",
        help="Alle TMDB-Staffeln anzeigen, auch noch nicht ausgestrahlte",
    )

    args = parser.parse_args()

    # API-Key: CLI hat Vorrang, sonst Datei-Konstante
    api_key = args.api_key or TMDB_API_KEY
    if not api_key or api_key == "DEIN_API_KEY_HIER":
        sys.exit(
            "Fehler: Kein TMDB API-Key angegeben.\n"
            "  → Trage ihn oben in der Datei unter TMDB_API_KEY ein, oder\n"
            "  → übergib ihn per --api-key DEINKEY\n"
            "  → Key holen: https://www.themoviedb.org/settings/api"
        )

    if not os.path.isdir(args.folder):
        sys.exit(f"Fehler: Ordner nicht gefunden: {args.folder}")

    # Zukunfts-Filter auflösen
    if args.show_future:
        hide_future = False
        hide_future_seasons_only = False
    elif args.hide_future_seasons:
        hide_future = False
        hide_future_seasons_only = True
    elif args.hide_future:
        hide_future = True
        hide_future_seasons_only = False
    else:
        # Standard aus Datei-Konstante
        hide_future = HIDE_FUTURE_DEFAULT
        hide_future_seasons_only = False

    if hide_future:
        future_label = f"{Fore.GREEN}ja – Staffeln + Episoden{Style.RESET_ALL}"
    elif hide_future_seasons_only:
        future_label = f"{Fore.YELLOW}nur komplett zukünftige Staffeln{Style.RESET_ALL}"
    else:
        future_label = f"{Fore.RED}nein – alle TMDB-Staffeln sichtbar{Style.RESET_ALL}"

    rar_status = (
        f"{Fore.GREEN}rarfile verfügbar ✔{Style.RESET_ALL}"
        if HAS_RARFILE
        else f"nur Dateiname  {Style.DIM}(pip install rarfile für Archiv-Scan){Style.RESET_ALL}"
    )

    print(f"{Fore.CYAN}{Style.BRIGHT}╔══ Series Checker ══╗{Style.RESET_ALL}")
    print(f"  Ordner        : {args.folder}")
    print(f"  Tiefe         : {args.depth}")
    print(f"  Verbose       : {'ja' if args.verbose else 'nein'}")
    print(f"  RAR           : {rar_status}")
    print(f"  Zukunft ausbl.: {future_label}")
    print()

    # 1. Scan
    if args.verbose:
        print(f"{Fore.CYAN}── Scan ────────────────────────────────────{Style.RESET_ALL}")

    local_data = scan_folder(args.folder, args.depth, args.verbose)

    if not local_data:
        sys.exit(
            "Keine Episodendateien gefunden.\n"
            "Tipps:\n"
            "  · Prüfe den Pfad\n"
            "  · Erhöhe die Tiefe mit --depth N\n"
            "  · Nutze --verbose für Details"
        )

    print(f"{Fore.GREEN}Gefundene Serien: {len(local_data)}{Style.RESET_ALL}")
    for name, seasons in sorted(local_data.items()):
        staffeln = "  ".join(f"S{s:02d}({len(e)}ep)" for s, e in sorted(seasons.items()))
        print(f"  {Fore.WHITE}{name}{Style.RESET_ALL}  –  {staffeln}")

    # 2. TMDB
    print()
    if args.verbose:
        print(f"{Fore.CYAN}── TMDB ────────────────────────────────────{Style.RESET_ALL}")

    tmdb = TMDB(api_key, verbose=args.verbose)
    results = check_series(
        tmdb,
        local_data,
        interactive=args.interactive,
        skip_complete_seasons=args.skip_complete_seasons,
        verbose=args.verbose,
        hide_future=hide_future,
        hide_future_seasons_only=hide_future_seasons_only,
    )

    # 3. Ausgabe
    render_results(results, show_complete=args.show_complete, json_out=args.json)


if __name__ == "__main__":
    main()
