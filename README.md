# Plex Playlist Tools

Export your Plex music library and playlists to CSV, and import them back again. Works with Plex running **locally or anywhere on your network**.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Connection](#connection)
   - [Token auto-detection](#token-auto-detection)
   - [Passing connection details on the command line](#passing-connection-details-on-the-command-line)
5. [Commands](#commands)
   - [list-playlists](#list-playlists)
   - [export](#export)
   - [import](#import)
6. [Output Files](#output-files)
7. [Common Scenarios](#common-scenarios)

---

## Requirements

- Python 3.10+
- A running Plex Media Server (local or networked)
- Your Plex authentication token

---

## Installation

```bash
cd plex_playlist_tools
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
| `PLEX_URL`          | No       | URL of your Plex server (default: `http://localhost:32400`)                  |
| `PLEX_TOKEN`        | No       | Your Plex auth token — auto-detected from local Plex if not set              |
| `PLEX_LIBRARY_NAME` | No       | Music library name to use — uses the first found if blank                    |
| `OUTPUT_FILE`       | No       | Default export CSV filename (default: `plex_music_export.csv`)               |
| `IMPORT_FILE`       | No       | Default import CSV filename (default: `plex_music_export.csv`)               |
| `LOG_FILE`          | No       | Default log CSV filename (default: `plex_music_log.csv`)                     |
| `IMAGES_DIR`        | No       | Directory for playlist cover images — skipped entirely if blank              |

All settings can also be passed directly as CLI flags and will take precedence over `.env` values.

### Finding your Plex token manually

1. Open Plex Web and sign in
2. Browse to any media item, click **"..." → Get Info → View XML**
3. In the URL that opens, copy the `X-Plex-Token=` value

Full guide: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

---

## Connection

The tool resolves the Plex URL and token using the following priority order:

| Priority | Source                          | How to set                          |
|----------|---------------------------------|-------------------------------------|
| 1 (highest) | CLI flag                    | `--url URL` / `--token TOKEN`       |
| 2        | `.env` file                     | `PLEX_URL=...` / `PLEX_TOKEN=...`   |
| 3 (lowest)  | Auto-detected from local Plex | Plex installed on the same machine  |

### Token auto-detection

If `PLEX_TOKEN` is not set in `.env` and `--token` is not passed, the tool automatically reads the token from the Plex Media Server `Preferences.xml` file on the local machine. This means **no configuration is needed** if Plex is installed on the machine you're running the tool from.

Supported locations:

| OS      | Preferences.xml path                                                                                     |
|---------|----------------------------------------------------------------------------------------------------------|
| Windows | `%LOCALAPPDATA%\Plex Media Server\Preferences.xml`                                                       |
| macOS   | `~/Library/Application Support/Plex Media Server/Preferences.xml`                                        |
| Linux   | `/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml`                 |

### Passing connection details on the command line

Use `--url` and `--token` to connect without a `.env` file, or to override it for a single run. These flags come **before** the subcommand:

```bash
# Connect to a remote server with an explicit token
python plex_playlist_tools.py --url http://192.168.1.50:32400 --token YOUR_TOKEN list-playlists

# Export using a different server than the one in .env
python plex_playlist_tools.py --url http://192.168.1.100:32400 export --all-playlists

# Use HTTPS
python plex_playlist_tools.py --url https://plex.yourdomain.com:32400 --token YOUR_TOKEN export --all-playlists
```

---

## Commands

All commands share the same entry point:

```
python plex_playlist_tools.py [--url URL] [--token TOKEN] <command> [options]
```

The `--url` and `--token` flags are optional and come **before** the subcommand. They override any values set in `.env`.

---

### list-playlists

Lists all music playlists on your Plex server with their track counts.

```bash
# Using .env or auto-detected token
python plex_playlist_tools.py list-playlists

# Connecting to a specific server with an explicit token
python plex_playlist_tools.py --url http://192.168.1.50:32400 --token YOUR_TOKEN list-playlists
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

| Flag            | Default                    | Description                                                                    |
|-----------------|----------------------------|--------------------------------------------------------------------------------|
| `--output`      | `OUTPUT_FILE` from `.env`  | Path for the output CSV                                                        |
| `--log`         | `LOG_FILE` from `.env`     | Path for the log CSV                                                           |
| `--images-dir`  | `IMAGES_DIR` from `.env`   | Directory to save cover images (skipped if not provided)                       |
| `--all-images`  | off                        | Download every available cover per playlist, not just the selected one. Requires `--images-dir` |

**Examples:**

```bash
# Export the full music library
python plex_playlist_tools.py export

# Export a single playlist
python plex_playlist_tools.py export --playlist "Road Trip"

# Export multiple playlists in one file
python plex_playlist_tools.py export --playlist "Road Trip" "Chill Mix" "Workout"

# Export all playlists
python plex_playlist_tools.py export --all-playlists

# Export all playlists with the currently selected cover image
python plex_playlist_tools.py export --all-playlists --images-dir playlist_images

# Export all playlists with every available cover image (custom + auto-generated)
python plex_playlist_tools.py export --all-playlists --images-dir playlist_images --all-images

# Export a single playlist with all its covers
python plex_playlist_tools.py export --playlist "Road Trip" --images-dir playlist_images --all-images

# Export with custom output filenames
python plex_playlist_tools.py export --all-playlists --output my_playlists.csv --log my_log.csv
```

> **Note on image saving:**
> - `--images-dir` alone saves **one file** per playlist — the currently selected cover:
>   `{images-dir}/{playlist name}.jpg`
> - `--all-images` saves **every available cover** into a sub-folder per playlist:
>   `{images-dir}/{playlist name}/{playlist name}-{index}[-selected].jpg`
>   The file with `-selected` in its name is the cover currently active in Plex.
> - Library exports (`--library`) do not support image export since they have no associated playlist artwork.

---

### import

Reads a previously exported CSV and recreates the playlists in Plex. Tracks are matched first by **file path**, then by **artist + track title** as a fallback.

> **Note:** If a playlist with the same name already exists in Plex, it will be replaced.

**Flags:**

| Flag            | Default                    | Description                                                   |
|-----------------|----------------------------|---------------------------------------------------------------|
| `--file`        | `IMPORT_FILE` from `.env`  | CSV file to import from                                       |
| `--log`         | `LOG_FILE` from `.env`     | Path for the log CSV                                          |
| `--images-dir`  | `IMAGES_DIR` from `.env`   | Directory to read cover images from (skipped if not provided) |

**Examples:**

```bash
# Import using the default file (plex_music_export.csv)
python plex_playlist_tools.py import

# Import from a specific file
python plex_playlist_tools.py import --file my_playlists.csv

# Import with cover images restored
python plex_playlist_tools.py import --file my_playlists.csv --images-dir playlist_images

# Import with a custom log file
python plex_playlist_tools.py import --file my_playlists.csv --log import_log.csv
```

> **Tip:** Only CSVs that were exported with `--playlist` or `--all-playlists` can be imported (they must have a populated `Playlist` column). Library exports have a blank `Playlist` column and are not importable.

> **Note:** On import, the tool looks for `{images-dir}/{playlist name}.jpg` (falling back to `.png`). If the file exists, it is uploaded as the playlist's cover. If a playlist already exists in Plex, it is replaced before the image is uploaded.

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

| Column      | Description                                                                        |
|-------------|------------------------------------------------------------------------------------|
| Timestamp   | Date and time of the operation (ISO 8601)                                          |
| Operation   | `export`, `import`, `export_image`, or `import_image`                             |
| Playlist    | Playlist name (blank for library exports)                                          |
| Artist      | Artist name (blank for image rows)                                                 |
| Album       | Album title (blank for image rows)                                                 |
| Track Title | Name of the track (blank for image rows)                                           |
| Status      | `success`, `error`, or `skipped`                                                   |
| Message     | Details — file path on image success, reason on error/skip                         |

**Example:**

```
Timestamp,Operation,Playlist,Artist,Album,Track Title,Status,Message
2026-04-04T14:32:01,export,Road Trip,Tom Petty,Greatest Hits,American Girl,success,
2026-04-04T14:32:05,export_image,Road Trip,,,,success,Saved to playlist_images/Road Trip.jpg
2026-04-04T14:35:10,import,Road Trip,Tom Petty,Greatest Hits,American Girl,success,Matched and added to playlist
2026-04-04T14:35:10,import,Road Trip,Unknown Band,Unknown Album,Mystery Track,error,Track not found in Plex library
2026-04-04T14:35:12,import_image,Road Trip,,,,success,Playlist image uploaded
```

---

## Common Scenarios

### Back up all playlists and restore them

```bash
# Step 1 — export tracks and cover images
python plex_playlist_tools.py export --all-playlists --output backup.csv --images-dir playlist_images

# Step 2 — restore (e.g. after a server migration)
python plex_playlist_tools.py import --file backup.csv --images-dir playlist_images
```

### Export all playlists with every available cover image

```bash
python plex_playlist_tools.py export --all-playlists --images-dir playlist_images --all-images
```

This creates a sub-folder for each playlist inside `playlist_images/`:

```
playlist_images/
  Road Trip/
    Road Trip-1-selected.jpg   ← currently active cover
    Road Trip-2.jpg
    Road Trip-3.jpg
  Chill Mix/
    Chill Mix-1-selected.jpg
    Chill Mix-2.jpg
```

### Export one playlist and check the log for issues

```bash
python plex_playlist_tools.py export --playlist "Chill Mix" --output chill.csv --log chill_log.csv
```

### Export playlists without images

```bash
python plex_playlist_tools.py export --all-playlists --output backup.csv
```

### See what playlists are available before exporting

```bash
python plex_playlist_tools.py list-playlists
```

### Connect without a .env file (one-off or CI use)

```bash
python plex_playlist_tools.py --url http://192.168.1.50:32400 --token YOUR_TOKEN export --all-playlists
```

### Plex is installed locally — no configuration needed

If Plex is installed on the same machine, the token is auto-detected and `PLEX_URL` defaults to `localhost:32400`, so no `.env` is required:

```bash
python plex_playlist_tools.py list-playlists
python plex_playlist_tools.py export --all-playlists
```
