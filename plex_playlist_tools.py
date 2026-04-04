"""
Plex Music Tool
Export and import Plex music playlists and libraries.

Usage:
  python plex_music.py export                         # export full library
  python plex_music.py export --playlist "My Mix"     # export specific playlist(s)
  python plex_music.py export --all-playlists         # export all playlists
  python plex_music.py import --file export.csv       # import playlists from CSV
  python plex_music.py list-playlists                 # list all music playlists
"""

import argparse
import csv
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from plexapi.exceptions import Unauthorized
from plexapi.server import PlexServer


# ─── Connection ──────────────────────────────────────────────────────────────

def get_plex_server(base_url: str, token: str) -> PlexServer:
    try:
        return PlexServer(base_url, token)
    except Unauthorized:
        print("Error: Invalid Plex token. Check your PLEX_TOKEN in .env")
        sys.exit(1)
    except Exception as e:
        print(f"Error connecting to Plex at {base_url}: {e}")
        sys.exit(1)


def find_music_library(plex: PlexServer, library_name: str | None):
    sections = [s for s in plex.library.sections() if s.type == "artist"]
    if not sections:
        print("No music libraries found on this Plex server.")
        sys.exit(1)
    if library_name:
        for s in sections:
            if s.title.lower() == library_name.lower():
                return s
        print(f"Library '{library_name}' not found. Available music libraries:")
        for s in sections:
            print(f"  - {s.title}")
        sys.exit(1)
    if len(sections) > 1:
        print("Multiple music libraries found — using the first one.")
        print("Set PLEX_LIBRARY_NAME in .env to specify one:")
        for s in sections:
            print(f"  - {s.title}")
    return sections[0]


# ─── CSV helpers ─────────────────────────────────────────────────────────────

EXPORT_FIELDS = [
    "Playlist", "Artist", "Album", "Year", "Track Number",
    "Track Title", "Duration (s)", "Genre", "File Path",
]

LOG_FIELDS = [
    "Timestamp", "Operation", "Playlist", "Artist", "Album",
    "Track Title", "Status", "Message",
]


def write_csv(path: str, fields: list, rows: list):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def append_log(log_file: str, rows: list):
    if not rows:
        return
    file_exists = os.path.exists(log_file)
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def log_row(operation: str, playlist: str, artist: str, album: str,
            title: str, status: str, message: str = "") -> dict:
    return {
        "Timestamp": datetime.now().isoformat(timespec="seconds"),
        "Operation": operation,
        "Playlist":  playlist,
        "Artist":    artist,
        "Album":     album,
        "Track Title": title,
        "Status":    status,
        "Message":   message,
    }


# ─── Export ──────────────────────────────────────────────────────────────────

def export_library(plex: PlexServer, library_name: str | None,
                   output_file: str, log_file: str):
    library = find_music_library(plex, library_name)
    print(f"Exporting library: '{library.title}'")

    rows, log_rows = [], []
    artists = library.all()
    total = len(artists)

    for i, artist in enumerate(artists, 1):
        print(f"  [{i}/{total}] {artist.title}                    ", end="\r")
        genres = ", ".join(g.tag for g in (artist.genres or []))
        for album in artist.albums():
            for track in album.tracks():
                file_path = track.media[0].parts[0].file if track.media else ""
                rows.append({
                    "Playlist":     "",
                    "Artist":       artist.title,
                    "Album":        album.title,
                    "Year":         album.year or "",
                    "Track Number": track.trackNumber or "",
                    "Track Title":  track.title,
                    "Duration (s)": round(track.duration / 1000) if track.duration else "",
                    "Genre":        genres,
                    "File Path":    file_path,
                })
                log_rows.append(log_row(
                    "export", "", artist.title, album.title, track.title, "success"
                ))

    print(f"\nExported {len(rows)} tracks across {total} artists.")
    write_csv(output_file, EXPORT_FIELDS, rows)
    append_log(log_file, log_rows)
    print(f"Data : {output_file}")
    print(f"Log  : {log_file}")


def export_playlists(plex: PlexServer, playlist_names: list | None,
                     output_file: str, log_file: str):
    all_playlists = [p for p in plex.playlists() if p.playlistType == "audio"]
    if not all_playlists:
        print("No music playlists found on this Plex server.")
        sys.exit(1)

    if playlist_names:
        name_set = {n.lower() for n in playlist_names}
        selected = [p for p in all_playlists if p.title.lower() in name_set]
        missing = name_set - {p.title.lower() for p in selected}
        if missing:
            print(f"Playlist(s) not found: {', '.join(missing)}")
            print("Available music playlists:")
            for p in all_playlists:
                print(f"  - {p.title}")
            if not selected:
                sys.exit(1)
    else:
        selected = all_playlists

    rows, log_rows = [], []
    total = len(selected)

    for i, playlist in enumerate(selected, 1):
        items = playlist.items()
        print(f"  [{i}/{total}] '{playlist.title}' — {len(items)} tracks")
        for track in items:
            artist = track.grandparentTitle or ""
            album  = track.parentTitle or ""
            year   = getattr(track, "parentYear", "") or ""
            genres = ", ".join(g.tag for g in (getattr(track, "genres", None) or []))
            file_path = track.media[0].parts[0].file if track.media else ""
            rows.append({
                "Playlist":     playlist.title,
                "Artist":       artist,
                "Album":        album,
                "Year":         year,
                "Track Number": track.trackNumber or "",
                "Track Title":  track.title,
                "Duration (s)": round(track.duration / 1000) if track.duration else "",
                "Genre":        genres,
                "File Path":    file_path,
            })
            log_rows.append(log_row(
                "export", playlist.title, artist, album, track.title, "success"
            ))

    print(f"\nExported {len(rows)} tracks across {total} playlist(s).")
    write_csv(output_file, EXPORT_FIELDS, rows)
    append_log(log_file, log_rows)
    print(f"Data : {output_file}")
    print(f"Log  : {log_file}")


# ─── Import ──────────────────────────────────────────────────────────────────

def build_track_index(library) -> tuple[dict, dict]:
    """
    Build two lookup dicts for fast track matching:
      by_path          — file path  → Track object
      by_artist_title  — (artist_lower, title_lower) → Track object
    """
    print("Indexing library (this may take a moment for large libraries)...")
    by_path: dict = {}
    by_artist_title: dict = {}

    artists = library.all()
    total = len(artists)
    for i, artist in enumerate(artists, 1):
        print(f"  [{i}/{total}] {artist.title}                    ", end="\r")
        for album in artist.albums():
            for track in album.tracks():
                if track.media:
                    path = track.media[0].parts[0].file
                    if path:
                        by_path[path] = track
                key = (artist.title.lower(), track.title.lower())
                if key not in by_artist_title:
                    by_artist_title[key] = track

    print(f"\nIndexed {len(by_path)} tracks.")
    return by_path, by_artist_title


def import_playlists(plex: PlexServer, library_name: str | None,
                     input_file: str, log_file: str):
    try:
        with open(input_file, newline="", encoding="utf-8") as f:
            csv_rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"Error: File not found: {input_file}")
        sys.exit(1)

    if not csv_rows:
        print("Error: CSV file is empty.")
        sys.exit(1)

    if "Playlist" not in csv_rows[0]:
        print("Error: CSV does not have a 'Playlist' column.")
        print("Only CSVs exported with --playlist or --all-playlists can be imported.")
        sys.exit(1)

    # Group rows by playlist name, skipping blank entries
    playlists: dict[str, list] = {}
    for row in csv_rows:
        name = row.get("Playlist", "").strip()
        if name:
            playlists.setdefault(name, []).append(row)

    if not playlists:
        print("No playlist entries found in CSV (all 'Playlist' values are blank).")
        sys.exit(1)

    library = find_music_library(plex, library_name)
    by_path, by_artist_title = build_track_index(library)
    log_rows = []

    for playlist_name, tracks in playlists.items():
        print(f"\nImporting '{playlist_name}' ({len(tracks)} tracks)...")
        matched, failed = [], 0

        for row in tracks:
            artist    = row.get("Artist", "").strip()
            title     = row.get("Track Title", "").strip()
            file_path = row.get("File Path", "").strip()
            album     = row.get("Album", "").strip()

            track_obj = None

            # 1) Match by file path (most reliable)
            if file_path and file_path in by_path:
                track_obj = by_path[file_path]

            # 2) Fallback: match by artist + title
            if not track_obj:
                key = (artist.lower(), title.lower())
                track_obj = by_artist_title.get(key)

            if track_obj:
                matched.append(track_obj)
                log_rows.append(log_row(
                    "import", playlist_name, artist, album, title,
                    "success", "Matched and added to playlist"
                ))
                print(f"  [OK]   {artist} – {title}")
            else:
                failed += 1
                log_rows.append(log_row(
                    "import", playlist_name, artist, album, title,
                    "error", "Track not found in Plex library"
                ))
                print(f"  [MISS] {artist} – {title}")

        if matched:
            # Replace existing playlist if it exists
            for existing in plex.playlists():
                if existing.title == playlist_name:
                    existing.delete()
                    print(f"  Removed existing playlist '{playlist_name}'")
                    break
            plex.createPlaylist(playlist_name, items=matched)
            print(f"  Created '{playlist_name}': {len(matched)} added, {failed} not found")
        else:
            print(f"  No tracks matched — playlist '{playlist_name}' was not created")

    append_log(log_file, log_rows)

    total_ok  = sum(1 for r in log_rows if r["Status"] == "success")
    total_err = sum(1 for r in log_rows if r["Status"] == "error")
    print(f"\nImport complete: {total_ok} tracks added, {total_err} not found.")
    print(f"Log  : {log_file}")


# ─── List playlists ───────────────────────────────────────────────────────────

def list_playlists(plex: PlexServer):
    playlists = [p for p in plex.playlists() if p.playlistType == "audio"]
    if not playlists:
        print("No music playlists found.")
        return
    print(f"Found {len(playlists)} music playlist(s):")
    for p in playlists:
        print(f"  - {p.title}  ({p.leafCount} tracks)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    load_dotenv()

    base_url     = os.getenv("PLEX_URL", "http://localhost:32400")
    token        = os.getenv("PLEX_TOKEN", "")
    library_name = os.getenv("PLEX_LIBRARY_NAME", "") or None
    default_out  = os.getenv("OUTPUT_FILE", "plex_music_export.csv")
    default_log  = os.getenv("LOG_FILE",    "plex_music_log.csv")
    default_imp  = os.getenv("IMPORT_FILE", "plex_music_export.csv")

    parser = argparse.ArgumentParser(
        prog="plex_music.py",
        description="Export and import Plex music playlists and libraries.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── export ──
    exp = sub.add_parser("export", help="Export music to CSV")
    grp = exp.add_mutually_exclusive_group()
    grp.add_argument(
        "--library", action="store_true",
        help="Export the full music library (default when no playlist flag is given)",
    )
    grp.add_argument(
        "--playlist", metavar="NAME", nargs="+",
        help="Export one or more named playlists",
    )
    grp.add_argument(
        "--all-playlists", action="store_true",
        help="Export all music playlists",
    )
    exp.add_argument("--output", default=default_out, metavar="FILE",
                     help=f"Output CSV file (default: {default_out})")
    exp.add_argument("--log",    default=default_log, metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")

    # ── import ──
    imp = sub.add_parser("import", help="Import playlists from a CSV into Plex")
    imp.add_argument("--file", default=default_imp, metavar="FILE",
                     help=f"CSV file to import (default: {default_imp})")
    imp.add_argument("--log",  default=default_log, metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")

    # ── list-playlists ──
    sub.add_parser("list-playlists", help="List all music playlists on the server")

    args = parser.parse_args()

    if not token:
        print("Error: PLEX_TOKEN is not set. Add it to your .env file.")
        print("Find your token: https://support.plex.tv/articles/204059436")
        sys.exit(1)

    plex = get_plex_server(base_url, token)

    if args.command == "export":
        if args.playlist:
            export_playlists(plex, args.playlist, args.output, args.log)
        elif args.all_playlists:
            export_playlists(plex, None, args.output, args.log)
        else:
            export_library(plex, library_name, args.output, args.log)

    elif args.command == "import":
        import_playlists(plex, library_name, args.file, args.log)

    elif args.command == "list-playlists":
        list_playlists(plex)


if __name__ == "__main__":
    main()
