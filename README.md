# Plex Playlist Tools

Export, import, and manage music playlists in Plex. Works with Plex running locally or anywhere on your network.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Installation & Setup](#installation--setup)
3. [Quick Start](#quick-start)
4. [Connection Options](#connection-options)
5. [Commands](#commands)
   - [list-playlists](#list-playlists)
   - [export](#export)
   - [import](#import)
   - [suggest](#suggest)
   - [generate](#generate)
   - [dedupe](#dedupe)
   - [shuffle](#shuffle)
   - [sync](#sync)
   - [rename](#rename)
   - [merge](#merge)
6. [Output Files](#output-files)
7. [Common Scenarios](#common-scenarios)

---

## Requirements

- Python 3.10+
- A running Plex Media Server (local or networked)
- Your Plex authentication token (or Plex installed locally for auto-detection)

---

## Installation & Setup

**1. Install dependencies:**

```bash
cd plex_playlist_tools
pip install -r requirements.txt
```

**2. Create your `.env` file:**

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

| Variable            | Required | Description                                                                              |
|---------------------|----------|------------------------------------------------------------------------------------------|
| `PLEX_URL`          | No       | URL of your Plex server (default: `http://localhost:32400`)                              |
| `PLEX_TOKEN`        | No       | Your Plex auth token — auto-detected from local Plex if not set                         |
| `PLEX_LIBRARY_NAME` | No       | Music library name to use — uses the first one found if blank                            |
| `OUTPUT_FILE`       | No       | Default export CSV filename (default: `plex_music_export.csv`)                           |
| `IMPORT_FILE`       | No       | Default import CSV filename (default: `plex_music_export.csv`)                           |
| `LOG_FILE`          | No       | Default log CSV filename (default: `plex_music_log.csv`)                                 |
| `IMAGES_DIR`        | No       | Path to a directory for playlist cover images — skipped entirely if blank                |

---

## Quick Start

If Plex is installed on your machine, no configuration is needed — the token is auto-detected:

```bash
# See what playlists you have
python plex_playlist_tools.py list-playlists

# Export all playlists to a CSV
python plex_playlist_tools.py export --all-playlists

# Get playlist suggestions based on your library
python plex_playlist_tools.py suggest

# Create a playlist by describing it
python plex_playlist_tools.py generate "chill lo-fi for studying"
```

If Plex is on another machine, pass the connection details:

```bash
python plex_playlist_tools.py --url http://192.168.1.50:32400 --token YOUR_TOKEN list-playlists
```

---

## Connection Options

The tool resolves the server URL and token using this priority order:

| Priority    | Source                         | How to set                              |
|-------------|--------------------------------|-----------------------------------------|
| 1 (highest) | CLI flags                      | `--url URL` and/or `--token TOKEN`      |
| 2           | `.env` file                    | `PLEX_URL=...` / `PLEX_TOKEN=...`       |
| 3 (lowest)  | Auto-detected from local Plex  | Plex installed on the same machine      |

### Token auto-detection

If no token is configured, the tool reads it automatically from the Plex Media Server `Preferences.xml` file. This means **no setup is required** if Plex is installed locally.

| OS      | Preferences.xml location                                                                  |
|---------|-------------------------------------------------------------------------------------------|
| Windows | `%LOCALAPPDATA%\Plex Media Server\Preferences.xml`                                        |
| macOS   | `~/Library/Application Support/Plex Media Server/Preferences.xml`                         |
| Linux   | `/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml`  |

### Finding your token manually

1. Open Plex Web and sign in
2. Browse to any media item, click **"..." → Get Info → View XML**
3. In the URL that opens, copy the `X-Plex-Token=` value

Full guide: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

### Passing connection details on the command line

The `--url` and `--token` flags come **before** the subcommand and override `.env` for that run:

```bash
python plex_playlist_tools.py --url http://192.168.1.50:32400 --token YOUR_TOKEN export --all-playlists
```

---

## Commands

All commands follow the same pattern:

```
python plex_playlist_tools.py [--url URL] [--token TOKEN] <command> [options]
```

---

### list-playlists

Lists all music playlists on your Plex server with their track counts.

```bash
python plex_playlist_tools.py list-playlists
```

**Example output:**

```
Found 3 music playlist(s):
  - Road Trip  (42 tracks)
  - Chill Mix  (28 tracks)
  - Workout    (55 tracks)
```

---

### export

Exports music data to a CSV file.

| Flag              | What it exports                    |
|-------------------|------------------------------------|
| _(no flag)_       | Full music library (all tracks)    |
| `--playlist NAME` | One or more named playlists        |
| `--all-playlists` | Every music playlist on the server |

**Flags:**

| Flag            | Default                   | Description                                                                                       |
|-----------------|---------------------------|---------------------------------------------------------------------------------------------------|
| `--output`      | `OUTPUT_FILE` from `.env` | Path for the output CSV file                                                                      |
| `--log`         | `LOG_FILE` from `.env`    | Path for the log CSV file                                                                         |
| `--images-dir`  | `IMAGES_DIR` from `.env`  | Path to a directory where cover images will be saved. Created automatically if it does not exist. |
| `--all-images`  | off                       | Download every available cover per playlist into `--images-dir`, not just the selected one. Requires `--images-dir`. |

**Examples:**

```bash
# Export the full music library
python plex_playlist_tools.py export

# Export a single playlist
python plex_playlist_tools.py export --playlist "Road Trip"

# Export multiple playlists into one CSV
python plex_playlist_tools.py export --playlist "Road Trip" "Chill Mix" "Workout"

# Export all playlists
python plex_playlist_tools.py export --all-playlists

# Export all playlists with the currently selected cover image saved to ./covers/
python plex_playlist_tools.py export --all-playlists --images-dir ./covers

# Export all playlists with every available cover image
python plex_playlist_tools.py export --all-playlists --images-dir ./covers --all-images
```

> **Note on images:**
> - `--images-dir` alone saves one file per playlist (the active cover): `<dir>/<playlist name>.jpg`
> - `--all-images` saves every cover into a sub-folder: `<dir>/<playlist name>/<playlist name>-1-selected.jpg`, `<playlist name>-2.jpg`, etc. The `-selected` suffix marks the active cover.
> - Library exports do not support image export.

---

### import

Reads a previously exported CSV and recreates the playlists in Plex. Tracks are matched first by **file path**, then by **artist + track title**.

**Flags:**

| Flag            | Default                   | Description                                                                                                  |
|-----------------|---------------------------|--------------------------------------------------------------------------------------------------------------|
| `--file`        | `IMPORT_FILE` from `.env` | Path to the CSV file to import                                                                               |
| `--log`         | `LOG_FILE` from `.env`    | Path for the log CSV file                                                                                    |
| `--images-dir`  | `IMAGES_DIR` from `.env`  | Path to a directory containing exported cover images to restore. Skipped if not provided.                    |
| `--mode`        | `append`                  | `append`: add only tracks not already in the playlist. `replace`: delete and recreate the playlist entirely. |

**Examples:**

```bash
# Import using default settings (appends new tracks to existing playlists)
python plex_playlist_tools.py import

# Import from a specific file
python plex_playlist_tools.py import --file my_playlists.csv

# Append new tracks to existing playlists (default)
python plex_playlist_tools.py import --file my_playlists.csv --mode append

# Replace existing playlists entirely
python plex_playlist_tools.py import --file my_playlists.csv --mode replace

# Import and restore cover images from a directory
python plex_playlist_tools.py import --file my_playlists.csv --images-dir ./covers
```

> **Tip:** Only CSVs exported with `--playlist` or `--all-playlists` can be imported — they must have a populated `Playlist` column. Library exports have a blank `Playlist` column and cannot be imported.

> **Note on `--mode`:**
> - `append` (default): only tracks not already in the playlist are added. Duplicates are skipped and logged. If the playlist does not exist yet it is created.
> - `replace`: the existing playlist is deleted and recreated. Cover images are re-uploaded if `--images-dir` is set.

---

### suggest

Scans your music library and proposes playlist ideas based on genre, decade, mood, and artist. You can then choose which ones to create.

**Flags:**

| Flag                  | Default | Description                                                              |
|-----------------------|---------|--------------------------------------------------------------------------|
| `--min-tracks`        | `10`    | Minimum tracks required for a suggestion to appear                       |
| `--min-artist-tracks` | `20`    | Minimum tracks by one artist to generate a "Best of" suggestion          |
| `--limit`             | `25`    | Maximum number of suggestions to display                                 |
| `--create-all`        | off     | Create every suggestion without prompting                                |
| `--log`               | `LOG_FILE` from `.env` | Path for the log CSV file                                   |

**Example:**

```bash
python plex_playlist_tools.py suggest
```

**Example output:**

```
Scanned 4,312 tracks across 287 artists.

47 suggestion(s) found. Showing top 25:

  [ 1]  [mood]          Chill Mix                    892 tracks — Ambient, Acoustic, Lo-Fi…
  [ 2]  [mood]          Rock Mix                     754 tracks — Rock, Classic Rock, Alternative Rock…
  [ 3]  [decade]        90s Hits                     612 tracks from 1990–1999
  [ 4]  [genre]         Rock                         521 tracks
  [ 5]  [decade + genre] 90s Rock                   318 tracks from 1990–1999
  [ 6]  [artist]        Best of Pink Floyd            67 tracks by Pink Floyd
  [ 7]  [mood]          High Energy Mix              445 tracks — Electronic, Dance, Metal…
  ...

Enter number(s) to create (e.g. 1  or  1,3,5  or  all), or 'q' to quit:
> 1,3,6
  Created 'Chill Mix' (892 tracks)
  Created '90s Hits' (612 tracks)
  Created 'Best of Pink Floyd' (67 tracks)

Created 3 playlist(s).
```

```bash
# Lower the threshold for smaller libraries
python plex_playlist_tools.py suggest --min-tracks 5

# Create all suggestions without being asked
python plex_playlist_tools.py suggest --create-all

# Show more suggestions
python plex_playlist_tools.py suggest --limit 50
```

> **Note:** Suggestions are generated from genre tags, release years, and artist metadata already in your Plex library. If your library has limited metadata, try lowering `--min-tracks`.

---

### generate

Creates a playlist by describing what you want in plain language. The tool searches your library for matching tracks using genre tags, mood keywords, decade keywords, and artist names.

**Usage:**

```bash
python plex_playlist_tools.py generate "DESCRIPTION"
```

**Flags:**

| Flag          | Default | Description                                                        |
|---------------|---------|--------------------------------------------------------------------|
| `--name`      | —       | Playlist name (defaults to your description)                       |
| `--min-tracks`| `5`     | Warn if fewer than this many tracks are found                      |
| `--yes` / `-y`| off     | Skip confirmation and create the playlist immediately              |
| `--log`       | `LOG_FILE` from `.env` | Path for the log CSV file                         |

**Examples:**

```bash
# Create a lo-fi study playlist
python plex_playlist_tools.py generate "chill lo-fi for studying"

# Create a 90s rock playlist with a custom name
python plex_playlist_tools.py generate "90s rock" --name "Throwback Rock"

# Create a workout playlist without confirmation
python plex_playlist_tools.py generate "high energy workout" --yes

# Create a playlist for a specific artist
python plex_playlist_tools.py generate "Pink Floyd"
```

**Example output:**

```
Searching for: "chill lo-fi for studying"
Matched on:
  · keyword: 'chill'
  · keyword: 'lofi'
  · keyword: 'study'

Found 143 track(s)  →  playlist name: 'Chill Lo-Fi For Studying'

Create 'Chill Lo-Fi For Studying' with 143 tracks? [y/N] y
Created playlist 'Chill Lo-Fi For Studying' with 143 tracks.
```

> **How matching works:**
> 1. Decade keywords (`90s`, `eighties`, `2000s`, etc.)
> 2. Mood / genre keywords from a built-in map (`chill`, `lofi`, `workout`, `jazz`, `rock`, etc.)
> 3. Direct word matching against your library's genre tags
> 4. Artist name matching as a final fallback
>
> Results depend on how well your library is tagged in Plex. If nothing is found, run `suggest` to see what your library can support.

---

### dedupe

Scans one or more playlists for duplicate tracks and removes them. A duplicate is any track that appears more than once in the same playlist (matched by Plex internal ID, so identical files are always caught).

**Flags:**

| Flag              | Default | Description                                              |
|-------------------|---------|----------------------------------------------------------|
| `--playlist NAME` | —       | One or more playlist names to deduplicate (required unless `--all-playlists`) |
| `--all-playlists` | off     | Deduplicate every music playlist                         |
| `--yes` / `-y`    | off     | Remove duplicates without confirmation                   |
| `--log`           | `LOG_FILE` from `.env` | Path for the log CSV file                 |

**Examples:**

```bash
# Check a single playlist for duplicates (prompts before removing)
python plex_playlist_tools.py dedupe --playlist "Road Trip"

# Check multiple playlists
python plex_playlist_tools.py dedupe --playlist "Road Trip" "Chill Mix"

# Deduplicate all playlists without prompting
python plex_playlist_tools.py dedupe --all-playlists --yes
```

**Example output:**

```
  'Road Trip' — 3 duplicate(s) in 45 tracks:
    · Tom Petty – American Girl
    · Fleetwood Mac – Go Your Own Way
    · Eagles – Hotel California
  Remove 3 duplicate(s) from 'Road Trip'? [y/N] y
  Removed 3 duplicate(s) from 'Road Trip'.

Removed 3 duplicate track(s) total.
```

---

### shuffle

Creates a new playlist that is a shuffled copy of an existing one. Useful for devices or apps that play tracks in the order they appear in the playlist.

**Usage:**

```bash
python plex_playlist_tools.py shuffle "PLAYLIST NAME"
```

**Flags:**

| Flag          | Default                    | Description                                          |
|---------------|----------------------------|------------------------------------------------------|
| `--name`      | `<name> (Shuffled)`        | Name for the new shuffled playlist                   |
| `--seed`      | —                          | Integer seed for a reproducible shuffle              |
| `--yes` / `-y`| off                        | Skip confirmation                                    |
| `--log`       | `LOG_FILE` from `.env`     | Path for the log CSV file                            |

**Examples:**

```bash
# Shuffle a playlist (creates "Road Trip (Shuffled)")
python plex_playlist_tools.py shuffle "Road Trip"

# Shuffle with a custom name
python plex_playlist_tools.py shuffle "Road Trip" --name "Road Trip Randomized"

# Reproducible shuffle — same seed always produces the same order
python plex_playlist_tools.py shuffle "Road Trip" --seed 42

# Skip confirmation
python plex_playlist_tools.py shuffle "Road Trip" --yes
```

> **Note:** The original playlist is not modified. A new playlist is created (or replaced if one with the same name already exists).

---

### sync

Copies playlist(s) from one Plex server to another. Tracks are matched on the destination by file path first, then by artist + title.

The source server is the one specified by the global `--url` / `--token` flags (or `.env`). The destination is specified with `--dest-url` and `--dest-token`.

**Flags:**

| Flag              | Default    | Description                                                              |
|-------------------|------------|--------------------------------------------------------------------------|
| `--playlist NAME` | —          | One or more playlist names to sync (required unless `--all-playlists`)   |
| `--all-playlists` | off        | Sync every music playlist from the source                                |
| `--dest-url`      | (required) | Destination Plex server URL                                              |
| `--dest-token`    | (required) | Destination Plex auth token                                              |
| `--dest-library`  | —          | Music library name on the destination (uses first found if blank)        |
| `--mode`          | `replace`  | `replace`: recreate the playlist. `append`: add only new tracks.         |
| `--log`           | `LOG_FILE` from `.env` | Path for the log CSV file                                   |

**Examples:**

```bash
# Sync all playlists from one server to another
python plex_playlist_tools.py --url http://192.168.1.10:32400 --token TOKEN_A \
  sync --all-playlists \
  --dest-url http://192.168.1.20:32400 --dest-token TOKEN_B

# Sync a specific playlist
python plex_playlist_tools.py --url http://192.168.1.10:32400 --token TOKEN_A \
  sync --playlist "Road Trip" \
  --dest-url http://192.168.1.20:32400 --dest-token TOKEN_B

# Append new tracks instead of replacing
python plex_playlist_tools.py --url http://192.168.1.10:32400 --token TOKEN_A \
  sync --all-playlists --mode append \
  --dest-url http://192.168.1.20:32400 --dest-token TOKEN_B
```

> **Note:** Tracks that cannot be matched on the destination are skipped and logged. The quality of matching depends on both libraries having the same file paths or consistent artist/title metadata.

---

### rename

Renames a playlist directly on the Plex server.

**Usage:**

```bash
python plex_playlist_tools.py rename "OLD NAME" "NEW NAME"
```

**Examples:**

```bash
# Rename a playlist
python plex_playlist_tools.py rename "Road Trip" "Summer Road Trip 2024"

# Works with remote servers too
python plex_playlist_tools.py --url http://192.168.1.50:32400 --token YOUR_TOKEN \
  rename "Chill Mix" "Evening Chill"
```

---

### merge

Combines two or more playlists into a single new playlist. Duplicate tracks are removed by default.

**Usage:**

```bash
python plex_playlist_tools.py merge "PLAYLIST 1" "PLAYLIST 2" ... --name "MERGED NAME"
```

**Flags:**

| Flag                 | Default | Description                                                              |
|----------------------|---------|--------------------------------------------------------------------------|
| `--name`             | (required) | Name for the merged playlist                                          |
| `--allow-duplicates` | off     | Keep duplicate tracks from different source playlists                    |
| `--yes` / `-y`       | off     | Skip confirmation                                                        |
| `--log`              | `LOG_FILE` from `.env` | Path for the log CSV file                                   |

**Examples:**

```bash
# Merge two playlists
python plex_playlist_tools.py merge "Morning Vibes" "Afternoon Groove" --name "Day Mix"

# Merge three playlists
python plex_playlist_tools.py merge "Rock Classics" "90s Rock" "Indie Rock" --name "All Rock"

# Merge and allow duplicate tracks
python plex_playlist_tools.py merge "Party Mix A" "Party Mix B" --name "Big Party Mix" --allow-duplicates

# Skip confirmation
python plex_playlist_tools.py merge "Morning Vibes" "Evening Chill" --name "Full Day Mix" --yes
```

**Example output:**

```
Merging 2 playlist(s)  →  'Day Mix'
  Sources : Morning Vibes, Afternoon Groove
  Tracks  : 84 (12 duplicate(s) removed)

Create 'Day Mix' with 84 tracks? [y/N] y
Created 'Day Mix' with 84 tracks.
```

> **Note:** If a playlist named `--name` already exists it will be replaced.

---

## Output Files

### Export CSV

Produced by the `export` command.

| Column         | Description                                    |
|----------------|------------------------------------------------|
| Playlist       | Playlist name (blank for library exports)      |
| Artist         | Artist name                                    |
| Album          | Album title                                    |
| Year           | Release year                                   |
| Track Number   | Track position within the album                |
| Track Title    | Name of the track                              |
| Duration (s)   | Track duration in seconds                      |
| Genre          | Genre tags (comma-separated)                   |
| File Path      | Full path to the audio file on the Plex server |

**Example:**

```
Playlist,Artist,Album,Year,Track Number,Track Title,Duration (s),Genre,File Path
Road Trip,Tom Petty,Greatest Hits,1993,1,American Girl,214,Rock,/music/Tom Petty/01 American Girl.flac
Road Trip,Fleetwood Mac,Rumours,1977,1,Second Hand News,143,Rock,/music/Fleetwood Mac/01 Second Hand News.flac
```

---

### Log CSV

Produced by all commands that modify playlists. Each run **appends** to the log so you keep a full history.

| Column      | Description                                                                                                         |
|-------------|---------------------------------------------------------------------------------------------------------------------|
| Timestamp   | Date and time (ISO 8601)                                                                                            |
| Operation   | `export`, `import`, `generate`, `dedupe`, `shuffle`, `sync`, `merge`, `export_image`, or `import_image`             |
| Playlist    | Playlist name                                                                                                       |
| Artist      | Artist name (blank for image, generate, shuffle, and merge rows)                                                    |
| Album       | Album title (blank for image, generate, shuffle, and merge rows)                                                    |
| Track Title | Track name (blank for image, generate, shuffle, and merge rows)                                                     |
| Status      | `success`, `error`, or `skipped`                                                                                    |
| Message     | Details — file path on image success, reason on error or skip                                                       |

---

## Common Scenarios

### Get up and running immediately (Plex installed locally)

```bash
python plex_playlist_tools.py list-playlists
python plex_playlist_tools.py suggest
```

### Back up all playlists and restore them

```bash
# Export everything including cover images
python plex_playlist_tools.py export --all-playlists --output backup.csv --images-dir ./covers

# Restore on any Plex server
python plex_playlist_tools.py --url http://192.168.1.50:32400 --token YOUR_TOKEN \
  import --file backup.csv --images-dir ./covers --mode replace
```

### Create playlists automatically from your library

```bash
# Browse suggestions and pick what to create
python plex_playlist_tools.py suggest

# Create everything at once (no prompts)
python plex_playlist_tools.py suggest --create-all
```

### Create a specific playlist by describing it

```bash
python plex_playlist_tools.py generate "relaxing jazz for a dinner party"
python plex_playlist_tools.py generate "80s synthwave" --name "Retro Synth"
python plex_playlist_tools.py generate "upbeat pop hits" --yes
```

### Keep a playlist up to date

```bash
# Re-import and append only new tracks (default mode)
python plex_playlist_tools.py import --file updated_export.csv
```

### Clean up duplicate tracks across all playlists

```bash
python plex_playlist_tools.py dedupe --all-playlists --yes
```

### Make a shuffled party version of a playlist

```bash
python plex_playlist_tools.py shuffle "Road Trip" --name "Road Trip (Party Mix)" --yes
```

### Combine genre playlists into one master mix

```bash
python plex_playlist_tools.py merge "Rock Classics" "90s Rock" "Indie Rock" --name "All Rock" --yes
```

### Mirror your playlists to a second Plex server

```bash
python plex_playlist_tools.py --url http://192.168.1.10:32400 --token TOKEN_A \
  sync --all-playlists \
  --dest-url http://192.168.1.20:32400 --dest-token TOKEN_B
```

### Rename a playlist

```bash
python plex_playlist_tools.py rename "Old Name" "New Name"
```

### Export a playlist with every available cover image

```bash
python plex_playlist_tools.py export --playlist "Road Trip" --images-dir ./covers --all-images
```

Saves all covers into:
```
covers/
  Road Trip/
    Road Trip-1-selected.jpg   ← currently active cover
    Road Trip-2.jpg
    Road Trip-3.jpg
```

### Connect without a .env file

```bash
python plex_playlist_tools.py --url http://192.168.1.50:32400 --token YOUR_TOKEN export --all-playlists
```
