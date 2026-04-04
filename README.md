# Plex Music Tool

Export your Plex music library and playlists to CSV, and import them back again. Works with Plex running **locally or anywhere on your network**.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Commands](#commands)
   - [list-playlists](#list-playlists)
   - [export](#export)
   - [import](#import)
5. [Output Files](#output-files)
6. [Common Scenarios](#common-scenarios)

---

## Requirements

- Python 3.10+
- A running Plex Media Server (local or networked)
- Your Plex authentication token

---

## Installation

```bash
cd plex_music_exporter
pip install -r requirements.txt
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable            | Required | Description                                                                  |
|---------------------|----------|------------------------------------------------------------------------------|
| `PLEX_URL`          | Yes      | URL of your Plex server (e.g. `http://192.168.1.50:32400`)                   |
| `PLEX_TOKEN`        | Yes      | Your Plex authentication token                                               |
| `PLEX_LIBRARY_NAME` | No       | Music library name to use — uses the first found if blank                    |
| `OUTPUT_FILE`       | No       | Default export CSV filename (default: `plex_music_export.csv`)               |
| `IMPORT_FILE`       | No       | Default import CSV filename (default: `plex_music_export.csv`)               |
| `LOG_FILE`          | No       | Default log CSV filename (default: `plex_music_log.csv`)                     |

All file path settings can also be overridden per-run with CLI flags (`--output`, `--file`, `--log`).

### Finding your Plex token

1. Open Plex Web and sign in
2. Browse to any media item, click **"..." → Get Info → View XML**
3. In the URL that opens, copy the `X-Plex-Token=` value

Full guide: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

### Connecting to a networked Plex server

If Plex is running on another machine, replace `localhost` with its IP address:

```ini
PLEX_URL=http://192.168.1.50:32400
```

HTTPS and custom domains are also supported:

```ini
PLEX_URL=https://plex.yourdomain.com:32400
```

---

## Commands

All commands share the same entry point:

```
python plex_music.py <command> [options]
```

---

### list-playlists

Lists all music playlists on your Plex server with their track counts.

```bash
python plex_music.py list-playlists
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

Exports music data to a CSV file. Three modes are available:

| Flag              | What it exports                        |
|-------------------|----------------------------------------|
| _(no flag)_       | Full music library (all tracks)        |
| `--playlist NAME` | One or more named playlists            |
| `--all-playlists` | Every music playlist on the server     |

**Flags:**

| Flag          | Default                   | Description                    |
|---------------|---------------------------|--------------------------------|
| `--output`    | `OUTPUT_FILE` from `.env` | Path for the output CSV        |
| `--log`       | `LOG_FILE` from `.env`    | Path for the log CSV           |

**Examples:**

```bash
# Export the full music library
python plex_music.py export

# Export a single playlist
python plex_music.py export --playlist "Road Trip"

# Export multiple playlists in one file
python plex_music.py export --playlist "Road Trip" "Chill Mix" "Workout"

# Export all playlists
python plex_music.py export --all-playlists

# Export with custom output filenames
python plex_music.py export --all-playlists --output my_playlists.csv --log my_log.csv
```

---

### import

Reads a previously exported CSV and recreates the playlists in Plex. Tracks are matched first by **file path**, then by **artist + track title** as a fallback.

> **Note:** If a playlist with the same name already exists in Plex, it will be replaced.

**Flags:**

| Flag     | Default                   | Description                      |
|----------|---------------------------|----------------------------------|
| `--file` | `IMPORT_FILE` from `.env` | CSV file to import from          |
| `--log`  | `LOG_FILE` from `.env`    | Path for the log CSV             |

**Examples:**

```bash
# Import using the default file (plex_music_export.csv)
python plex_music.py import

# Import from a specific file
python plex_music.py import --file my_playlists.csv

# Import with a custom log file
python plex_music.py import --file my_playlists.csv --log import_log.csv
```

> **Tip:** Only CSVs that were exported with `--playlist` or `--all-playlists` can be imported (they must have a populated `Playlist` column). Library exports have a blank `Playlist` column and are not importable.

---

## Output Files

### Export CSV

Produced by the `export` command.

| Column         | Description                                   |
|----------------|-----------------------------------------------|
| Playlist       | Playlist name (blank for library exports)     |
| Artist         | Artist name                                   |
| Album          | Album title                                   |
| Year           | Release year                                  |
| Track Number   | Track position within the album               |
| Track Title    | Name of the track                             |
| Duration (s)   | Track duration in seconds                     |
| Genre          | Genre tags (comma-separated)                  |
| File Path      | Full path to the audio file on the Plex server|

**Example:**

```
Playlist,Artist,Album,Year,Track Number,Track Title,Duration (s),Genre,File Path
Road Trip,Tom Petty,Greatest Hits,1993,1,American Girl,214,Rock,/music/Tom Petty/Greatest Hits/01 American Girl.flac
Road Trip,Fleetwood Mac,Rumours,1977,1,Second Hand News,143,Rock,/music/Fleetwood Mac/Rumours/01 Second Hand News.flac
```

---

### Log CSV

Produced by both `export` and `import` commands. Each run **appends** to the log file so you have a full history.

| Column      | Description                                        |
|-------------|----------------------------------------------------|
| Timestamp   | Date and time of the operation (ISO 8601)          |
| Operation   | `export` or `import`                               |
| Playlist    | Playlist name (blank for library exports)          |
| Artist      | Artist name                                        |
| Album       | Album title                                        |
| Track Title | Name of the track                                  |
| Status      | `success` or `error`                               |
| Message     | Details — match method on success, reason on error |

**Example:**

```
Timestamp,Operation,Playlist,Artist,Album,Track Title,Status,Message
2026-04-04T14:32:01,export,Road Trip,Tom Petty,Greatest Hits,American Girl,success,
2026-04-04T14:35:10,import,Road Trip,Tom Petty,Greatest Hits,American Girl,success,Matched and added to playlist
2026-04-04T14:35:10,import,Road Trip,Unknown Band,Unknown Album,Mystery Track,error,Track not found in Plex library
```

---

## Common Scenarios

### Back up all playlists and restore them

```bash
# Step 1 — export
python plex_music.py export --all-playlists --output backup.csv

# Step 2 — restore (e.g. after a server migration)
python plex_music.py import --file backup.csv
```

### Export one playlist and check the log for issues

```bash
python plex_music.py export --playlist "Chill Mix" --output chill.csv --log chill_log.csv
```

### See what playlists are available before exporting

```bash
python plex_music.py list-playlists
```
