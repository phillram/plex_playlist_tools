"""
Plex Playlist Tools
Export and import Plex music playlists and libraries.

Usage:
  python plex_playlist_tools.py [--url URL] [--token TOKEN] <command> [options]

Commands:
  export          Export full library or playlist(s) to CSV
  import          Import playlists from a CSV back into Plex
  list-playlists  List all music playlists on the server

Connection (in order of precedence):
  1. --url / --token CLI flags
  2. PLEX_URL / PLEX_TOKEN in .env
  3. Auto-detected from local Plex Media Server Preferences.xml
"""

import argparse
import csv
import os
import platform
import re
import sys
import xml.etree.ElementTree as ET
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


def auto_detect_plex_token() -> str | None:
    """
    Try to read the Plex auth token from the local Plex Media Server Preferences.xml.
    Supports Windows, macOS, and Linux. Returns the token string or None if not found.
    """
    system = platform.system()

    if system == "Windows":
        local_app_data = os.getenv("LOCALAPPDATA", "")
        prefs_path = os.path.join(local_app_data, "Plex Media Server", "Preferences.xml")
    elif system == "Darwin":
        prefs_path = os.path.expanduser(
            "~/Library/Application Support/Plex Media Server/Preferences.xml"
        )
    else:  # Linux / Docker
        candidates = [
            "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml",
            "/config/Library/Application Support/Plex Media Server/Preferences.xml",
        ]
        prefs_path = next((p for p in candidates if os.path.exists(p)), "")

    if not prefs_path or not os.path.exists(prefs_path):
        return None

    try:
        root = ET.parse(prefs_path).getroot()
        return root.get("PlexOnlineToken") or None
    except Exception:
        return None


# ─── Image helpers ───────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    """Strip characters that are invalid in filenames."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def get_selected_poster_url(plex: PlexServer, playlist) -> str | None:
    """
    Query /library/metadata/{ratingKey}/posters and return the relative URL of
    whichever poster is currently selected (custom or auto-generated composite).
    Falls back to playlist.thumb if the endpoint is unavailable.
    """
    posters_url = f"{plex._baseurl}/library/metadata/{playlist.ratingKey}/posters"
    try:
        resp = plex._session.get(
            posters_url, params={"X-Plex-Token": plex._token}, timeout=15
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        # Find the poster marked selected="1" first, then fall back to the first listed
        posters = root.findall("Photo")
        selected = next((p for p in posters if p.get("selected") == "1"), None)
        if selected is None:
            selected = posters[0] if posters else None
        if selected is not None:
            return selected.get("thumb") or selected.get("key")
    except Exception:
        pass
    # Last resort: use the composite thumb Plex exposes on the object itself
    return getattr(playlist, "thumb", None) or None


def download_playlist_image(plex: PlexServer, playlist, images_dir: str) -> str | None:
    """
    Download the currently selected poster for a playlist (custom image if one
    has been set, otherwise the auto-generated composite).
    Returns the saved file path, or None on failure.
    """
    poster_path = get_selected_poster_url(plex, playlist)
    if not poster_path:
        return None
    os.makedirs(images_dir, exist_ok=True)
    url = f"{plex._baseurl}{poster_path}"
    try:
        resp = plex._session.get(url, params={"X-Plex-Token": plex._token}, timeout=15)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        ext = "png" if "png" in content_type else "jpg"
        path = os.path.join(images_dir, f"{sanitize_filename(playlist.title)}.{ext}")
        with open(path, "wb") as f:
            f.write(resp.content)
        return path
    except Exception:
        return None


def upload_playlist_image(plex: PlexServer, playlist, images_dir: str) -> bool:
    """Upload a saved thumbnail to a playlist. Returns True on success, False if no file found."""
    safe_name = sanitize_filename(playlist.title)
    image_path = None
    for ext in ("jpg", "jpeg", "png"):
        candidate = os.path.join(images_dir, f"{safe_name}.{ext}")
        if os.path.exists(candidate):
            image_path = candidate
            break
    if not image_path:
        return False

    content_type = "image/png" if image_path.endswith(".png") else "image/jpeg"
    url = f"{plex._baseurl}/library/metadata/{playlist.ratingKey}/posters"
    try:
        with open(image_path, "rb") as f:
            resp = plex._session.post(
                url,
                params={"X-Plex-Token": plex._token},
                data=f.read(),
                headers={"Content-Type": content_type},
            )
        return resp.ok
    except Exception:
        return False


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
                     output_file: str, log_file: str, images_dir: str | None = None):
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

        if images_dir:
            saved_path = download_playlist_image(plex, playlist, images_dir)
            if saved_path:
                print(f"    Image saved: {saved_path}")
                log_rows.append(log_row(
                    "export_image", playlist.title, "", "", "",
                    "success", f"Saved to {saved_path}"
                ))
            else:
                print(f"    Image: none available for '{playlist.title}'")
                log_rows.append(log_row(
                    "export_image", playlist.title, "", "", "",
                    "skipped", "No thumbnail available"
                ))

    print(f"\nExported {len(rows)} tracks across {total} playlist(s).")
    write_csv(output_file, EXPORT_FIELDS, rows)
    append_log(log_file, log_rows)
    print(f"Data : {output_file}")
    if images_dir:
        print(f"Images: {images_dir}/")
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
                     input_file: str, log_file: str, images_dir: str | None = None):
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
            new_playlist = None
            for existing in plex.playlists():
                if existing.title == playlist_name:
                    existing.delete()
                    print(f"  Removed existing playlist '{playlist_name}'")
                    break
            new_playlist = plex.createPlaylist(playlist_name, items=matched)
            print(f"  Created '{playlist_name}': {len(matched)} added, {failed} not found")

            if images_dir and new_playlist:
                ok = upload_playlist_image(plex, new_playlist, images_dir)
                if ok:
                    print(f"  Image uploaded for '{playlist_name}'")
                    log_rows.append(log_row(
                        "import_image", playlist_name, "", "", "",
                        "success", "Playlist image uploaded"
                    ))
                else:
                    print(f"  No image found for '{playlist_name}' in {images_dir}/")
                    log_rows.append(log_row(
                        "import_image", playlist_name, "", "", "",
                        "skipped", f"No image file found in {images_dir}/"
                    ))
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

    # File path and library defaults from .env (used as argparse defaults)
    library_name = os.getenv("PLEX_LIBRARY_NAME", "") or None
    default_out  = os.getenv("OUTPUT_FILE", "plex_music_export.csv")
    default_log  = os.getenv("LOG_FILE",    "plex_music_log.csv")
    default_imp  = os.getenv("IMPORT_FILE", "plex_music_export.csv")
    default_imgs = os.getenv("IMAGES_DIR",  "") or None

    parser = argparse.ArgumentParser(
        prog="plex_playlist_tools.py",
        description="Export and import Plex music playlists and libraries.",
    )

    # ── Global connection flags (override .env) ──
    parser.add_argument(
        "--url", metavar="URL", default=None,
        help="Plex server URL (overrides PLEX_URL in .env)",
    )
    parser.add_argument(
        "--token", metavar="TOKEN", default=None,
        help="Plex auth token (overrides PLEX_TOKEN in .env; auto-detected from local Plex if neither is set)",
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
    exp.add_argument("--output",     default=default_out,  metavar="FILE",
                     help=f"Output CSV file (default: {default_out})")
    exp.add_argument("--log",        default=default_log,  metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")
    exp.add_argument("--images-dir", default=default_imgs, metavar="DIR",
                     help="Directory to save playlist cover images (skipped if not set)")

    # ── import ──
    imp = sub.add_parser("import", help="Import playlists from a CSV into Plex")
    imp.add_argument("--file",       default=default_imp,  metavar="FILE",
                     help=f"CSV file to import (default: {default_imp})")
    imp.add_argument("--log",        default=default_log,  metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")
    imp.add_argument("--images-dir", default=default_imgs, metavar="DIR",
                     help="Directory to read playlist cover images from (skipped if not set)")

    # ── list-playlists ──
    sub.add_parser("list-playlists", help="List all music playlists on the server")

    args = parser.parse_args()

    # ── Resolve connection settings: CLI flag > .env > auto-detect ──
    base_url = args.url   or os.getenv("PLEX_URL",   "http://localhost:32400")
    token    = args.token or os.getenv("PLEX_TOKEN", "")

    if not token:
        token = auto_detect_plex_token() or ""
        if token:
            print("Plex token auto-detected from local Plex Media Server.")

    if not token:
        print("Error: No Plex token found. Provide one via:")
        print("  --token YOUR_TOKEN")
        print("  PLEX_TOKEN=... in .env")
        print("  Or install Plex Media Server locally (token is auto-detected)")
        print("Find your token: https://support.plex.tv/articles/204059436")
        sys.exit(1)

    plex = get_plex_server(base_url, token)

    if args.command == "export":
        images_dir = getattr(args, "images_dir", None)
        if args.playlist:
            export_playlists(plex, args.playlist, args.output, args.log, images_dir)
        elif args.all_playlists:
            export_playlists(plex, None, args.output, args.log, images_dir)
        else:
            export_library(plex, library_name, args.output, args.log)

    elif args.command == "import":
        images_dir = getattr(args, "images_dir", None)
        import_playlists(plex, library_name, args.file, args.log, images_dir)

    elif args.command == "list-playlists":
        list_playlists(plex)


if __name__ == "__main__":
    main()
