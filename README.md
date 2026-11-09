# umd-sched

A command-line [Venus](https://venus.umd.edu): give it the UMD courses you need and it pulls sections
from the [Schedule of Classes](https://app.testudo.umd.edu/soc/), builds every conflict-free schedule,
and ranks them by how efficient they are for you.

Things it does that Venus doesn't:

- **Walking time.** It knows roughly where campus buildings are. A schedule that has you leaving
  Clarice at 10:50 for an 11:00 class in Iribe is thrown out, and tight connections are flagged.
- **Lunch.** It makes sure there's a lunch-sized break in your lunch window, or tells you which days
  don't have one.
- **Ranking with reasons.** Each schedule gets a cost and an explanation, e.g. `days +40, idle +9, early +8`.
- **Short course specs.** `CMSC216:01*@Herman`, `"ENGL101|ENGL102"`, `STAT400?`.
- **Optional and either-or courses.** It fits in an optional course when it can, and picks one course from a list.
- **Professor preferences**, with optional [PlanetTerp](https://planetterp.com) ratings.
- **Calendar export** (`.ics`) for Google Calendar, Apple Calendar or Outlook.

## Install

Requires Python 3.10+. Most Linux distributions (Arch, Debian/Ubuntu, Fedora) and Homebrew Python
block a plain system-wide `pip install` (the "externally-managed-environment" error), so use one of these:

```sh
# Option 1: pipx (simplest; puts `umd-sched` on your PATH)
sudo pacman -S python-pipx        # Arch; or: brew install pipx / apt install pipx
pipx install .
umd-sched --help

# Option 2: a virtual environment in the repo
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/umd-sched --help        # or `source .venv/bin/activate` first, then `umd-sched`

# Option 3: no install, using your distro's packages
sudo pacman -S python-requests python-beautifulsoup4 python-lxml
python -m umdsched --help         # run from the repo directory
```

### Windows

1. Install Python 3.10 or newer, either from [python.org](https://www.python.org/downloads/windows/)
   (tick **"Add python.exe to PATH"** in the installer) or with `winget install Python.Python.3.13`.
   Then open a **new** PowerShell or Windows Terminal window.
2. Get the code: `git clone` the repository, or download it as a ZIP from GitHub and extract it.
   Then `cd` into the folder.
3. Install with pipx:

   ```powershell
   py -m pip install --user pipx
   py -m pipx ensurepath        # then close and reopen the terminal
   pipx install .
   umd-sched show CMSC216
   ```

   Or use a virtual environment instead:

   ```powershell
   py -m venv .venv
   .venv\Scripts\pip install -e .
   .venv\Scripts\umd-sched show CMSC216
   ```

   If PowerShell refuses to run `.venv\Scripts\Activate.ps1`, you don't need it: call
   `.venv\Scripts\umd-sched` directly as shown.

Everything else in this README works the same on Windows, with a few differences:

- **Quoting:** put specs containing `|` in double quotes, as in `"ENGL101|ENGL102"`. That works in
  both PowerShell and cmd. Or use `/` (`ENGL101/ENGL102`), which needs no quotes.
- **Storage:** the cache is in `%LOCALAPPDATA%\umd-sched`, and downloaded building coordinates are
  in `%APPDATA%\umd-sched`.
- **Display:** use Windows Terminal (the default on Windows 11) for the best output. On older
  consoles, symbols such as the grid shading are replaced with plain ASCII.

## Quick start

```sh
umd-sched CMSC216 CMSC250 MATH240 "ENGL101|ENGL102"
```

To try it without network access, use the bundled (fictional) sample data:

```sh
umd-sched CMSC216 CMSC250 MATH240 "ENGL101|ENGL102" "STAT400?" --data examples/sample-data.json
```

Example output (trimmed):

```
Spring 2027 · CMSC216, CMSC250, MATH240, ENGL101|ENGL102, STAT400?
Found 7,404 possible schedules; showing the best 5.

#1  cost 32  ·  5 campus days (MTuWThF) · 11:00am–4:45pm · 3h23m idle/wk · ~17 min walking/wk · 18 cr
    why: days +50, optional -40, idle +20.33, walking +1.67
    CMSC216  0302           Avery Quill                    12/40 open
        TuTh 12:30pm-1:45pm       ESJ 0202
        F 11:00am-11:50am         IRB 2386 [Discussion]
    CMSC250  0203           Jordan Pike                    1/40 open
        TuTh 2:00pm-3:15pm        ESJ 0224
        M 12:00pm-12:50pm         EGR 2680 [Discussion]
    ENGL101  0011           Kai Brennan                    4/19 open
        async/TBA                 online
    ...
    same times, other sections (1 combination):
      - ENGL102 0004 (Hale) for ENGL101  (cost 32)

              M              Tu             W              Th             F
      11:00am MATH240       |              |              |              |CMSC216
      11:30am MTH 1764      |              |              |              |IRB 2386
      12:00pm CMSC250       |              |              |              |
      12:30pm EGR 2680      |CMSC216       |              |CMSC216       |
       1:00pm               |ESJ 0202      |              |ESJ 0202      |
      ...
```

With `--in-person` the async ENGL101 section is excluded, and the walks become visible:

```
    ! tight: M 1:50pm ENGL101 (JMZ 1710) -> 2:00pm STAT400 (PHY 1402): 9 min walk in a 10 min gap
```

## Choosing sections, professors and times

Each course is a *spec*:

| Spec | Meaning |
| --- | --- |
| `CMSC216` | any section |
| `CMSC216:0101,0102` | only these sections (`101` is fine too) |
| `CMSC216:01*` | sections matching a wildcard |
| `CMSC216:~0201` | any section except 0201 (`!` and `-` also work) |
| `CMSC216@Herman` | only sections taught by someone whose name contains "Herman" |
| `CMSC216@~Herman` | avoid that professor |
| `CMSC216:01*@Herman,Smith` | combine filters; several professors allowed |
| `"ENGL101\|ENGL102"` | exactly one of these courses (`/` works too, no quoting needed) |
| `STAT400?` | optional: added if it fits (or `--optional STAT400`, which is safer in zsh) |

Times accept `9:30`, `9:30am`, `14:00` or `2pm`. A bare hour from 1 to 7 means PM. Days accept `MWF`,
`TuTh`, `TR`, `MTuWThF`, or `mon,wed`.

```sh
# No Friday classes, nothing before 10, keep Tue/Thu afternoons free for work
umd-sched CMSC216 MATH240 ENGL101 --days-off F --earliest 10am --block "TuTh 1pm-6pm"

# Only open seats, at most 4 days on campus, Herman for 216 and anyone but Smith for 240
umd-sched CMSC216@Herman MATH240@~Smith --open-only --max-days 4

# Four required courses plus whatever extras fit, 15-17 credits total
umd-sched CMSC216 CMSC250 MATH240 ENGL101 --optional STAT400 --optional HIST200 --credits 15-17
```

## Options and defaults

**Hard constraints**: schedules that break these are never shown.

| Option | Default | |
| --- | --- | --- |
| `--earliest TIME` / `--latest TIME` | none | no class starts before / ends after |
| `--days-off DAYS` | none | e.g. `F` |
| `--max-days N` | none | days you have to be on campus |
| `--block "DAYS START-END"` | none | busy times; repeatable |
| `--open-only` | off | skip full sections |
| `--max-waitlist N` | none | skip full sections with a long waitlist |
| `--avoid-prof NAME` | none | across all courses; repeatable |
| `--credits MIN-MAX` | none | total credits (useful with optional courses) |
| `--min-gap MIN` | 0 | minimum minutes between back-to-back classes |
| `--in-person` / `--delivery f2f,blended,online` | all | delivery modes |
| `--allow-unwalkable` | off | keep schedules where a walk is longer than the gap (they're flagged) |

**Walking**

| Option | Default | |
| --- | --- | --- |
| `--walk-speed MPH` | 3.0 | typical walking pace; 2.5 is leisurely, 3.5 is brisk |
| `--path-factor X` | 1.3 | real paths are about 30% longer than a straight line |
| `--min-buffer MIN` | 3 | slack you want after arriving; less than this counts as "tight" |
| `--unknown-walk MIN` | 8 | walk assumed for a building that isn't in the table |
| `--buildings FILE` | none | JSON of extra or corrected building coordinates |

Every walk adds about a minute for getting out of one building and finding the room in the next.

**Lunch**

| Option | Default | |
| --- | --- | --- |
| `--lunch START-END` | `11:00-14:00` | window for a lunch break |
| `--lunch-length MIN` | 30 | |
| `--require-lunch` | off | drop schedules without lunch every class day |
| `--no-lunch` | | ignore lunch entirely |

**Soft preferences**: these affect the ranking, not which schedules are allowed.

| Option | Default | |
| --- | --- | --- |
| `--style` | `balanced` | presets: `balanced`, `compact`, `relaxed`, `night-owl`, `early-bird` |
| `--prefer-start TIME` | 9:00 | classes before this cost extra |
| `--prefer-end TIME` | 17:00 | classes after this cost extra |
| `--prefer-prof NAME` | none | repeatable |
| `--ratings` | off | prefer better-rated professors (PlanetTerp) |
| `--weight KEY=VALUE` | | fine-tune any weight below |

**Output**: `-n 5` shows the best five, `--format json` prints JSON, `--no-grid` hides the weekly grid,
and `--ics FILE --pick N` writes schedule N to a calendar file. Term dates in the calendar are
approximate; set `--term-start` and `--term-end` for exact ones.

### How schedules are ranked

Each schedule gets a **cost**, made of penalty points. Lower is better.

| Weight | Default | Charged for |
| --- | --- | --- |
| `day` | 10 | each day you have an in-person class |
| `idle_hour` | 6 | each hour of dead time between classes, after walking (a lunch eaten in a gap isn't dead time) |
| `early_hour` | 8 | each hour a day starts before `--prefer-start` |
| `late_hour` | 4 | each hour a day ends after `--prefer-end` |
| `no_lunch` | 8 | each class day without a lunch break |
| `tight` | 4 | each connection with less slack than `--min-buffer`; doubled if you can't make it |
| `walk_minute` | 0.1 | each minute walked per week |
| `closed` | 15 | each section with no open seats |
| `waitlist_person` | 0.5 | each person on that section's waitlist (up to 30) |
| `rating` | 4 | each rating point below 5, with `--ratings` |
| `preferred_prof` | -8 | a bonus for a preferred professor |
| `optional_course` | -40 | a bonus for each optional course that fits |

The defaults favor fewer days on campus, little dead time, no 8 a.m. classes, a lunch break, and
connections you can make comfortably. For example, `--weight day=25` cares much more about fewer
campus days, and `--weight idle_hour=0` doesn't care about gaps at all.

Sections with identical times and rooms are grouped into one choice, e.g. `0101/0102`, listed with the
open one first. Sections that put you in the same buildings at the same times but have a different
professor or room are listed under the schedule as "same times, other sections".

## Config file

For anything more than a quick query, keep your plan in a file:

```sh
umd-sched init-config            # writes a commented schedule.toml
umd-sched build -c schedule.toml
umd-sched build -c schedule.toml --style compact -n 3    # command-line options override the file
```

## Other commands

```sh
umd-sched show CMSC216 MATH240       # list sections, times, professors and seats
umd-sched show CMSC216 --ratings     # ...with PlanetTerp ratings
umd-sched walk IRB TWS               # walk estimate between two buildings
umd-sched buildings hall             # search known building codes
umd-sched buildings --refresh        # download exact coordinates from umd.io (saved and used automatically)
umd-sched fetch CMSC216 MATH240 -o spring.json   # save data to share or use offline (--data spring.json)
```

## Data and caching

- **Term**: by default, the term you're most likely registering for. In October through December that's
  next spring; in March through September it's fall. Set it with `--term fall2026`, `--term spring27`
  or `--term 202701`.
- **Source**: Testudo's Schedule of Classes, read from its HTML pages. If Testudo changes its markup,
  `--source umdio` uses the community [umd.io](https://umd.io) API instead. umd.io can lag behind on
  seat counts.
- **Cache**: responses are cached in `~/.cache/umd-sched` (Windows: `%LOCALAPPDATA%\umd-sched`) for 30 minutes, since seat counts change
  constantly during registration. `--refresh` forces a fresh download. `--offline` uses the cache at
  any age, and `--cache-minutes` changes the lifetime. If the network fails, stale cached data is used
  as a fallback.
- **Buildings**: the built-in coordinates are approximate, good to about a block. Run
  `umd-sched buildings --refresh` once for exact coordinates from umd.io.

## Limitations

- Sections that meet for only part of the term (for example, the first eight weeks) are treated as
  meeting all term, so they may show conflicts that don't really exist.
- Walking estimates are straight-line distance times a path factor. They don't know about stairs,
  shuttles or construction.
- `examples/sample-data.json` is made up: its courses, sections and professors are for demos and
  tests only.

## Development

```sh
python -m unittest discover -s tests
```

The tests need `requests` and `beautifulsoup4` in whichever Python runs them. pipx installs those
into its own isolated environment, so either install them for your system Python
(`sudo pacman -S python-requests python-beautifulsoup4`) or run the tests with pipx's interpreter:
`~/.local/share/pipx/venvs/umd-sched/bin/python -m unittest discover -s tests`.

The tests use HTML fixtures shaped like Testudo's pages, so they need no network access.

How the search works: it backtracks over courses, using precomputed bitmasks of which sections are
compatible with each other (no overlap, and enough time to walk between them). Branch-and-bound skips
any partial schedule whose cost can only end up worse than the current top results. Counts stay
exact, because skipped branches are still counted.
