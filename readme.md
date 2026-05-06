# 📺 series_checker

> Scannt deine lokale Seriensammlung und zeigt fehlende Episoden – abgeglichen gegen die [TMDB-Datenbank](https://www.themoviedb.org/).

---

## Features

- 🔍 **Intelligenter Scanner** – erkennt Videodateien, RAR-Archive und Release-Ordner mit `S##E##`-Tags
- 🗂️ **Alle Strukturen** – klassische Bibliothek (`Serienname/S01/ep.mkv`), Release-Ordner (`Serie.S01E01.GERMAN-GRP/`) und gemischte Ablagen
- 🧠 **Smart-Suche** – findet Serien auch bei Umlauten (`graeber → gräber`), Artikeln (`The Wire → Wire`) und deutschen/englischen Titeln
- 📦 **RAR-Support** – liest Episodentags aus Archiv-Dateinamen oder optional direkt aus dem Archivinhalt (`pip install rarfile`)
- 📅 **Zukunftsfilter** – blendet noch nicht ausgestrahlte Staffeln und Episoden automatisch aus
- 🎨 **Farbige Ausgabe** – übersichtliche Darstellung mit Fehlend-Ranges (`E03-E05, E08`)
- 💾 **JSON-Export** – alle Ergebnisse maschinenlesbar speichern
- 🔁 **Interaktiver Modus** – bei unklaren TMDB-Treffern manuell bestätigen oder eigene ID eingeben

---

## Installation

```bash
# Pflicht
pip install requests colorama

# Optional: Inhalt von RAR-Archiven lesen
pip install rarfile
```

**TMDB API-Key** kostenlos holen unter: https://www.themoviedb.org/settings/api

---

## Konfiguration

Öffne `series_checker.py` und trage deinen Key oben ein:

```python
# ═══════════════════════════════════════════════════
#  KONFIGURATION
# ═══════════════════════════════════════════════════
TMDB_API_KEY = "dein_key_hier"

# Zukünftige Staffeln/Episoden ausblenden?
HIDE_FUTURE_DEFAULT = True   # True = Standard: Zukunft ausgeblendet
```

Alternativ per Argument: `--api-key DEINKEY`

---

## Verwendung

```bash
python series_checker.py <ordner> [optionen]
```

### Schnellstart

```bash
# Einfacher Scan (Key in der Datei eingetragen)
python series_checker.py /mnt/serien

# API-Key per Argument
python series_checker.py /mnt/serien --api-key abc123xyz

# Tief verschachtelte Ordnerstruktur
python series_checker.py /mnt --depth 5

# Detailausgabe: was wird erkannt, welche TMDB-Query greift
python series_checker.py /mnt/serien --verbose
```

---

## Alle Optionen

```
positional arguments:
  folder                    Ordner zum Scannen (z. B. /mnt/serien)

options:
  --api-key KEY             TMDB API-Key (überschreibt den Wert in der Datei)
  --depth N                 Maximale Unterordner-Tiefe (Standard: 4)
  --verbose, -v             Detaillierte Ausgabe (Scan, TMDB-Queries, Zuordnung)
  --show-complete           Auch vollständige Serien/Staffeln anzeigen
  --skip-complete-seasons   Vollständige Staffeln in der Detailansicht ausblenden
  --interactive             Bei unklaren TMDB-Treffern interaktiv nachfragen
  --json DATEI              Ergebnisse als JSON-Datei speichern

  --hide-future             Zukünftige Staffeln + noch nicht gesendete Episoden
                            laufender Staffeln ausblenden
  --hide-future-seasons     Nur komplett zukünftige Staffeln ausblenden;
                            laufende Staffeln werden vollständig angezeigt
  --show-future             Alle TMDB-Staffeln anzeigen (auch nicht ausgestrahlte)
```

> `--hide-future`, `--hide-future-seasons` und `--show-future` schließen sich gegenseitig aus.

---

## Erkannte Dateistrukturen

### Klassische Bibliothek
```
/mnt/serien/
  Breaking Bad/
    S01/
      Titel.S01E01.mkv
    S02/
      Titel.S02E05.GERMAN.DL.1080p-XXX.mkv
```

### Release-Ordner (glFTPD-Stil)
```
/mnt/serien/
  Titel.S01E01.GERMAN.DL.1080p-XXX/
    Titel.S01E01.GERMAN.DL.1080p-XXX.mkv
    Titel.S01E01.GERMAN.DL.1080p-XXX.nfo
  Titel.S01E02.GERMAN.DL.1080p-XXX/
    Titel.S01E02.GERMAN.DL.1080p-XXX.rar
    Titel.S01E02.GERMAN.DL.1080p-XXX.r00
```

### Gemischt & verschachtelt
```
/mnt/
  hdd1/serien/Serienname/Season 1/ep.mkv
  hdd2/TV/Serienname/Staffel 2/ep.ts
  nas/downloads/Serie.S03E01.GERMAN-XXX/serie.part01.rar
```

**Erkannte Videoformate:** `mkv` `mp4` `avi` `ts` `m2ts` `mov` `wmv` `divx` `m4v` `mpg` `mpeg`

**Erkannte RAR-Muster:** `.rar` `.001` `.part01.rar` (Multipart automatisch zusammengefasst)

---

## Smart-Suche

Der Script probiert mehrere Suchstrategien bis ein TMDB-Treffer gefunden wird:

| Schritt | Beispiel |
|---------|---------|
| 1. Original (de-DE) | `zwei graeber` |
| 2. Umlaute ersetzen | `zwei gräber` ← **ae→ä, oe→ö, ue→ü, ss→ß** |
| 3. Artikel entfernen | `Wire` statt `The Wire` |
| 4. Artikel + Umlaute | kombiniert |
| 5. Englisch (en-US) | Originalname |
| 6. Englisch + Umlaute | kombiniert |

Mit `--verbose` wird angezeigt, welche Query den Treffer geliefert hat.

---

## Zukunftsfilter

TMDB enthält oft schon Staffeln und Episoden die noch nicht ausgestrahlt wurden. Der Filter verhindert Falsch-Alarme:

| Flag | Verhalten |
|------|-----------|
| `--hide-future` *(Standard)* | Zukünftige Staffeln **und** noch nicht gesendete Episoden laufender Staffeln werden ignoriert |
| `--hide-future-seasons` | Nur Staffeln ausblenden die noch gar nicht begonnen haben |
| `--show-future` | Alle TMDB-Inhalte anzeigen |

Das Standardverhalten wird über `HIDE_FUTURE_DEFAULT = True` in der Datei gesteuert.

---

## Beispielausgabe

```
╔══ Series Checker ══╗
  Ordner        : /mnt/serien
  Tiefe         : 4
  Verbose       : nein
  RAR           : rarfile verfügbar ✔
  Zukunft ausbl.: ja – Staffeln + Episoden

Gefundene Serien: 3
  Breaking Bad   –  S01(5ep)  S02(13ep)  S03(2ep)
  zwei graeber   –  S01(3ep)
  Severance      –  S01(10ep)  S02(6ep)

[1/3] Breaking Bad
  ✓ TMDB: Breaking Bad (ID 1396)
[2/3] zwei graeber
  ✓ TMDB: Zwei Gräber (ID 12345)
[3/3] Severance
  ✓ TMDB: Severance (ID 95396)

══════════════════════════════════════════════════════════════════════
  ERGEBNISÜBERSICHT
══════════════════════════════════════════════════════════════════════

✘  Breaking Bad  →  Breaking Bad (ID 1396)
   ──────────────────────────────────────────────────────────────────
   S01  5/7 (71%)  │  2 fehlend
         Fehlend : E03, E06
   S02  ✔  Vollständig (13/13)
   S03  [komplett fehlend – 13 Episoden]

✔  zwei graeber  →  Zwei Gräber (ID 12345)
   ──────────────────────────────────────────────────────────────────
   Alle Staffeln vollständig ✔

✘  Severance  →  Severance (ID 95396)
   ──────────────────────────────────────────────────────────────────
   S02  6/10 (60%)  │  4 fehlend  (+3 künftige ausgeblendet)
         Fehlend : E07-E10

══════════════════════════════════════════════════════════════════════
  Gesamt: 3  │  Vollständig: 1  │  Unvollständig: 2  │  Nicht gefunden: 0
══════════════════════════════════════════════════════════════════════
```

---

## Beispiele

```bash
# Tief verschachtelte Struktur, nur Fehler anzeigen
python series_checker.py /mnt --depth 5 --skip-complete-seasons

# Verbose: nachvollziehen was erkannt wird und wie TMDB sucht
python series_checker.py /mnt/serien --verbose

# Interaktiv + JSON-Export
python series_checker.py /mnt/serien --interactive --json /tmp/fehlend.json

# Alle Staffeln anzeigen inkl. zukünftiger
python series_checker.py /mnt/serien --show-future --show-complete

# Alles kombiniert
python series_checker.py /mnt --depth 5 --verbose \
    --interactive --skip-complete-seasons \
    --hide-future --json /tmp/bericht.json
```

---

## Abhängigkeiten

| Paket | Pflicht | Zweck |
|-------|---------|-------|
| `requests` | ✅ | TMDB API-Abfragen |
| `colorama` | ✅ | Farbige Terminalausgabe |
| `rarfile` | ⬜ optional | Inhalt von RAR-Archiven lesen |

---

## Lizenz

MIT
