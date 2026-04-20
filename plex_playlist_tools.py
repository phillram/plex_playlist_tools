"""
Plex Playlist Tools
Export, import, and generate Plex music playlists.

Usage:
  python plex_playlist_tools.py [--url URL] [--token TOKEN] <command> [options]

Commands:
  export          Export full library or playlist(s) to CSV
  import          Import playlists from a CSV back into Plex
  suggest         Scan library and suggest playlists to create
  generate        Create a playlist from a natural language description
  list-playlists  List all music playlists on the server
  dedupe          Find and remove duplicate tracks from playlist(s)
  shuffle         Create a shuffled copy of a playlist
  sync            Mirror playlist(s) from this server to another Plex server
  rename          Rename a playlist
  merge           Combine multiple playlists into one

Connection (in order of precedence):
  1. --url / --token CLI flags
  2. PLEX_URL / PLEX_TOKEN in .env
  3. Auto-detected from local Plex Media Server Preferences.xml
"""

import argparse
import csv
import json
import os
import platform
import random
import re
import socket
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def download_all_playlist_images(plex: PlexServer, playlist, images_dir: str) -> list[dict]:
    """
    Download every poster associated with a playlist into a sub-folder named
    after the playlist.  Files are named:
        {playlist name}-{index}[-selected].{ext}
    where the -selected suffix marks the currently active cover.

    Returns a list of result dicts with keys: path, selected, status, message.
    """
    safe_name   = sanitize_filename(playlist.title)
    dest_dir    = os.path.join(images_dir, safe_name)
    posters_url = f"{plex._baseurl}/library/metadata/{playlist.ratingKey}/posters"

    results = []

    try:
        resp = plex._session.get(
            posters_url, params={"X-Plex-Token": plex._token}, timeout=15
        )
        resp.raise_for_status()
        root    = ET.fromstring(resp.content)
        posters = root.findall("Photo")
    except Exception as e:
        return [{"path": None, "selected": False, "status": "error",
                 "message": f"Could not fetch poster list: {e}"}]

    if not posters:
        return [{"path": None, "selected": False, "status": "skipped",
                 "message": "No posters found"}]

    os.makedirs(dest_dir, exist_ok=True)

    for i, poster in enumerate(posters, 1):
        thumb    = poster.get("thumb") or poster.get("key") or ""
        selected = poster.get("selected") == "1"

        suffix   = "-selected" if selected else ""
        filename = f"{safe_name}-{i}{suffix}"

        url = thumb if thumb.startswith("http") else f"{plex._baseurl}{thumb}"
        try:
            img_resp = plex._session.get(
                url, params={"X-Plex-Token": plex._token}, timeout=15
            )
            img_resp.raise_for_status()
            content_type = img_resp.headers.get("Content-Type", "image/jpeg")
            ext  = "png" if "png" in content_type else "jpg"
            path = os.path.join(dest_dir, f"{filename}.{ext}")
            with open(path, "wb") as f:
                f.write(img_resp.content)
            results.append({"path": path, "selected": selected,
                             "status": "success", "message": ""})
        except Exception as e:
            results.append({"path": None, "selected": selected,
                             "status": "error", "message": str(e)})

    return results


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
    "Playlist", "Summary", "Artist", "Album", "Year", "Track Number",
    "Track Title", "Duration (s)", "Genre", "File Path",
]

LOG_FIELDS = [
    "Timestamp", "Operation", "Playlist", "Artist", "Album",
    "Track Title", "Status", "Message",
]


def ensure_csv_path(path: str, default_filename: str) -> str:
    """
    If `path` has no .csv extension (i.e. looks like a directory), append
    `default_filename` and print a warning so the user knows what happened.
    """
    _, ext = os.path.splitext(path)
    if ext.lower() != ".csv":
        fixed = os.path.join(path, default_filename)
        print(
            f"Warning: '{path}' looks like a directory, not a CSV file. "
            f"Defaulting to: {fixed}"
        )
        return fixed
    return path


def write_csv(path: str, fields: list, rows: list):
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError:
        sys.exit(
            f"Error: cannot write to '{path}' — the file may be open in another program (e.g. Excel). "
            "Close it and try again."
        )


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
                     output_file: str, log_file: str,
                     images_dir: str | None = None, all_images: bool = False):
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
        summary = getattr(playlist, "summary", "") or ""
        for track in items:
            artist = track.grandparentTitle or ""
            album  = track.parentTitle or ""
            year   = getattr(track, "parentYear", "") or ""
            genres = ", ".join(g.tag for g in (getattr(track, "genres", None) or []))
            file_path = track.media[0].parts[0].file if track.media else ""
            rows.append({
                "Playlist":     playlist.title,
                "Summary":      summary,
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

        if images_dir and all_images:
            results = download_all_playlist_images(plex, playlist, images_dir)
            ok_count = sum(1 for r in results if r["status"] == "success")
            print(f"    Images: {ok_count}/{len(results)} downloaded → {images_dir}/{sanitize_filename(playlist.title)}/")
            for r in results:
                sel_tag = " [selected]" if r["selected"] else ""
                if r["status"] == "success":
                    log_rows.append(log_row(
                        "export_image", playlist.title, "", "", "",
                        "success", f"Saved to {r['path']}{sel_tag}"
                    ))
                else:
                    log_rows.append(log_row(
                        "export_image", playlist.title, "", "", "",
                        r["status"], r["message"] + sel_tag
                    ))
        elif images_dir:
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
    if images_dir and all_images:
        print(f"Images: {images_dir}/<playlist name>/  (all covers)")
    elif images_dir:
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
                     input_file: str, log_file: str,
                     images_dir: str | None = None, mode: str = "replace"):
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
            existing_playlist = next(
                (p for p in plex.playlists() if p.title == playlist_name), None
            )

            if mode == "append" and existing_playlist:
                existing_keys = {t.ratingKey for t in existing_playlist.items()}
                new_tracks = [t for t in matched if t.ratingKey not in existing_keys]
                duplicates  = len(matched) - len(new_tracks)
                for t in matched:
                    if t.ratingKey in existing_keys:
                        log_rows.append(log_row(
                            "import", playlist_name,
                            t.grandparentTitle, t.parentTitle, t.title,
                            "skipped", "Already in playlist"
                        ))
                if new_tracks:
                    _add_items_batched(existing_playlist, new_tracks)
                    print(f"  Appended {len(new_tracks)} track(s) to '{playlist_name}' "
                          f"({duplicates} already present, {failed} not found)")
                else:
                    print(f"  Nothing to append to '{playlist_name}' "
                          f"(all {duplicates} track(s) already present)")
                new_playlist = existing_playlist
            else:
                if existing_playlist:
                    existing_playlist.delete()
                    print(f"  Removed existing playlist '{playlist_name}'")
                new_playlist = _create_playlist_batched(plex, playlist_name, matched)
                print(f"  Created '{playlist_name}': {len(matched)} added, {failed} not found")

            # Restore playlist summary if the CSV carries one
            summary = next(
                (r.get("Summary", "").strip() for r in tracks if r.get("Summary", "").strip()),
                "",
            )
            if summary and new_playlist:
                try:
                    new_playlist.edit(summary=summary)
                except Exception:
                    pass

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


# ─── Playlist Generation ─────────────────────────────────────────────────────

MOOD_MAP: dict[str, list[str]] = {
    "high energy":  ["Electronic", "Dance", "Metal", "Punk", "Hard Rock",
                     "Drum and Bass", "Hardcore", "Techno", "House", "Rave"],
    "workout":      ["Electronic", "Dance", "Metal", "Punk", "Hard Rock",
                     "Hip-Hop", "Drum and Bass", "Techno", "Dubstep"],
    "party":        ["Dance", "Pop", "Hip-Hop", "Electronic", "R&B",
                     "Disco", "House", "Funk"],
    "chill":        ["Ambient", "Acoustic", "Lo-Fi", "Chillout", "Jazz",
                     "New Age", "Downtempo", "Trip-Hop"],
    "lofi":         ["Lo-Fi", "Chillhop", "Hip-Hop Beats", "Ambient",
                     "Instrumental Hip-Hop", "Lofi"],
    "study":        ["Ambient", "Classical", "Lo-Fi", "Instrumental",
                     "Post-Rock", "New Age", "Chillout"],
    "focus":        ["Ambient", "Classical", "Lo-Fi", "Instrumental",
                     "Post-Rock", "Minimal"],
    "sleep":        ["Ambient", "New Age", "Classical", "Acoustic",
                     "Drone", "Meditation"],
    "sad":          ["Blues", "Acoustic", "Folk", "Indie",
                     "Singer-Songwriter", "Emo", "Slowcore"],
    "happy":        ["Pop", "Reggae", "Soul", "Dance", "Ska", "Swing"],
    "romantic":     ["Jazz", "Soul", "R&B", "Classical", "Acoustic",
                     "Bossa Nova", "Smooth Jazz"],
    "morning":      ["Acoustic", "Folk", "Indie Pop", "Jazz", "Pop"],
    "night":        ["Electronic", "Jazz", "Ambient", "R&B", "Soul",
                     "Trip-Hop", "Neo-Soul"],
    "driving":      ["Rock", "Country", "Electronic", "Pop", "Hip-Hop",
                     "Classic Rock"],
    "jazz":         ["Jazz", "Bebop", "Swing", "Bossa Nova", "Smooth Jazz",
                     "Cool Jazz", "Fusion", "Big Band"],
    "classical":    ["Classical", "Orchestral", "Chamber", "Opera",
                     "Baroque", "Romantic", "Symphony"],
    "rock":         ["Rock", "Classic Rock", "Alternative Rock", "Indie Rock",
                     "Hard Rock", "Progressive Rock", "Alternative"],
    "pop":          ["Pop", "Indie Pop", "Synth-Pop", "Dance Pop",
                     "Electropop", "Power Pop"],
    "hip hop":      ["Hip-Hop", "Rap", "Hip Hop", "Trap", "R&B",
                     "Boom Bap", "Conscious Hip-Hop"],
    "hip-hop":      ["Hip-Hop", "Rap", "Hip Hop", "Trap", "R&B"],
    "rap":          ["Hip-Hop", "Rap", "Trap", "Boom Bap"],
    "electronic":   ["Electronic", "Techno", "House", "EDM", "Trance",
                     "Ambient", "Drum and Bass", "Dubstep", "Synthwave"],
    "country":      ["Country", "Country Pop", "Bluegrass", "Americana",
                     "Outlaw Country"],
    "folk":         ["Folk", "Acoustic", "Indie Folk", "Americana", "Celtic",
                     "Singer-Songwriter"],
    "blues":        ["Blues", "Delta Blues", "Electric Blues", "Soul Blues",
                     "Chicago Blues"],
    "soul":         ["Soul", "R&B", "Funk", "Neo-Soul", "Motown"],
    "r&b":          ["R&B", "Soul", "Funk", "Neo-Soul", "Contemporary R&B"],
    "reggae":       ["Reggae", "Ska", "Dub", "Dancehall"],
    "metal":        ["Metal", "Heavy Metal", "Death Metal", "Black Metal",
                     "Thrash Metal", "Doom Metal"],
    "punk":         ["Punk", "Pop Punk", "Hardcore", "Post-Punk", "Skate Punk"],
    "indie":        ["Indie", "Indie Rock", "Indie Pop", "Indie Folk"],
    "alternative":  ["Alternative", "Alternative Rock", "Indie",
                     "Post-Punk", "Grunge"],
    "ambient":      ["Ambient", "Drone", "New Age", "Space Music",
                     "Dark Ambient"],
    "acoustic":     ["Acoustic", "Folk", "Singer-Songwriter", "Unplugged"],
    "instrumental": ["Instrumental", "Ambient", "Classical", "Post-Rock",
                     "Jazz", "Cinematic"],
    "latin":        ["Latin", "Salsa", "Bossa Nova", "Latin Pop",
                     "Reggaeton", "Cumbia", "Merengue"],
    "disco":        ["Disco", "Funk", "Dance", "Nu-Disco"],
    "funk":         ["Funk", "Soul", "R&B", "Disco", "G-Funk"],
    "gospel":       ["Gospel", "Christian", "Contemporary Christian",
                     "Worship"],
    "world":        ["World Music", "African", "Celtic", "Latin", "Asian",
                     "Middle Eastern", "World"],
    "synthwave":    ["Synthwave", "Retrowave", "Darksynth", "Outrun",
                     "80s Electronic"],
    "emo":          ["Emo", "Post-Hardcore", "Screamo", "Midwest Emo"],
    "grunge":       ["Grunge", "Alternative Rock", "Post-Grunge"],
    "oldies":       ["Oldies", "Classic Rock", "Doo-Wop", "Classic Pop"],
}

DECADE_KEYWORDS: dict[str, tuple[int, int]] = {
    "60s":    (1960, 1969), "sixties":   (1960, 1969),
    "70s":    (1970, 1979), "seventies": (1970, 1979),
    "80s":    (1980, 1989), "eighties":  (1980, 1989),
    "90s":    (1990, 1999), "nineties":  (1990, 1999),
    "2000s":  (2000, 2009), "noughties": (2000, 2009),
    "2010s":  (2010, 2019),
    "2020s":  (2020, 2029),
}

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "that", "this", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "some", "like", "give", "make",
    "want", "need", "please", "create", "playlist", "music", "songs", "tracks",
    "play", "me", "my", "all", "new", "from", "good", "great", "best", "top",
    "mix", "list", "based", "kind", "type", "style", "genre", "about", "more",
    "just", "only", "really", "something", "anything", "everything",
})


def _genre_matches(track_genres: list[str], targets: list[str]) -> bool:
    """Case-insensitive substring match between a track's genre list and a target list."""
    for tg in track_genres:
        tgl = tg.lower()
        for target in targets:
            tl = target.lower()
            if tl in tgl or tgl in tl:
                return True
    return False


def scan_library(library) -> list[dict]:
    """
    Traverse artist → album → track and return lightweight metadata dicts,
    each holding the Track object plus the metadata needed for suggestions.
    """
    print("Scanning library...")
    data: list[dict] = []
    artists = library.all()
    total = len(artists)
    for i, artist in enumerate(artists, 1):
        print(f"  [{i}/{total}] {artist.title}                    ", end="\r")
        artist_genres = [g.tag for g in (artist.genres or [])]
        for album in artist.albums():
            album_genres = [g.tag for g in (album.genres or [])]
            for track in album.tracks():
                # Use the most specific genre tags available: track > album > artist
                track_genres = [g.tag for g in (getattr(track, "genres", None) or [])]
                genres = track_genres or album_genres or artist_genres
                data.append({
                    "obj":    track,
                    "artist": artist.title,
                    "album":  album.title,
                    "year":   album.year,
                    "title":  track.title,
                    "genres": genres,
                })
    print(f"\nScanned {len(data)} tracks across {total} artists.")
    return data


# ─── MusicBrainz helpers ─────────────────────────────────────────────────────

_MB_RATE_LIMIT   = 1.05   # seconds between requests (MB ToS: max 1 req/sec)
_MB_TIMEOUT      = 20     # seconds before a single HTTP request is abandoned
_MB_WORKERS      = 4      # concurrent artists; rate limit is still 1 req/sec total
_mb_last_request = 0.0
_mb_lock         = threading.Lock()


def _load_mb_cache(cache_file: str) -> dict:
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_mb_cache(cache_file: str, cache: dict) -> None:
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except OSError as e:
        print(f"Warning: could not save MusicBrainz cache: {e}")


def _mb_call(fn, *args, **kwargs):
    """Thread-safe, rate-limited wrapper for musicbrainzngs calls (1 req/sec global)."""
    global _mb_last_request
    while True:
        with _mb_lock:
            now = time.time()
            elapsed = now - _mb_last_request
            if elapsed >= _MB_RATE_LIMIT:
                _mb_last_request = now
                break
            wait = _MB_RATE_LIMIT - elapsed
        time.sleep(wait)   # outside lock — other workers can check rate limit while waiting
    return fn(*args, **kwargs)


def _batch_fetch_artist_mbids(artist_names: list, cache: dict) -> None:
    """
    Pre-fetch MusicBrainz artist IDs for a list of artists using batched Lucene OR queries
    (25 artists per request) instead of one search per artist.
    """
    import musicbrainzngs
    uncached = [n for n in artist_names if f"artist_mbid|{n.lower()}" not in cache]
    if not uncached:
        return
    _BATCH = 25
    for i in range(0, len(uncached), _BATCH):
        chunk = uncached[i:i + _BATCH]
        terms = " OR ".join(f'artist:"{n.replace(chr(34), " ")}"' for n in chunk)
        try:
            result  = _mb_call(musicbrainzngs.search_artists, query=terms, limit=100)
            by_name = {
                a.get("name", "").lower(): a["id"]
                for a in result.get("artist-list", [])
            }
            for name in chunk:
                cache[f"artist_mbid|{name.lower()}"] = by_name.get(name.lower())
        except Exception:
            for name in chunk:
                cache.setdefault(f"artist_mbid|{name.lower()}", None)



def _enrich_artist_tracks(artist_name: str, tracks: list[dict], cache: dict) -> None:
    """
    Enrich tracks for one artist using a single get_artist_by_id call (1 MB request).
    Artist-level tags are applied to every track — much faster than per-recording browse.
    MBID is expected to already be in cache from _batch_fetch_artist_mbids; a fallback
    search is performed if it's missing.
    """
    import musicbrainzngs

    # Step 1: resolve artist MBID (usually already populated by the batch pre-fetch)
    artist_key = f"artist_mbid|{artist_name.lower()}"
    if artist_key not in cache:
        try:
            result = _mb_call(musicbrainzngs.search_artists, artist=artist_name, limit=3)
            found  = result.get("artist-list", [])
            cache[artist_key] = found[0]["id"] if found else None
        except Exception:
            cache[artist_key] = None

    artist_mbid = cache[artist_key]
    if not artist_mbid:
        for t in tracks:
            cache.setdefault(f"{artist_name.lower()}|{t['title'].lower()}", [])
        return

    # Step 2: fetch artist-level tags (1 request, no pagination)
    artist_tags_key = f"artist_tags|{artist_mbid}"
    if artist_tags_key not in cache:
        try:
            resp     = _mb_call(musicbrainzngs.get_artist_by_id, artist_mbid,
                                includes=["tags"])
            tag_list = resp.get("artist", {}).get("tag-list", [])
            tag_list.sort(key=lambda t: -int(t.get("count", 0)))
            cache[artist_tags_key] = [t["name"] for t in tag_list[:20]]
        except Exception:
            cache[artist_tags_key] = []

    artist_tags = cache[artist_tags_key]

    # Step 3: apply the same artist tags to every uncached track
    for t in tracks:
        key = f"{artist_name.lower()}|{t['title'].lower()}"
        if key not in cache:
            cache[key] = artist_tags


def scan_library_deep(
    library,
    cache_file: str,
    reset_cache: bool = False,
    refresh_artists: list[str] | None = None,
) -> list[dict]:
    """
    Like scan_library but enriches each track with MusicBrainz tags.
    Uses artist-level browse batching: ~2 MB requests per artist instead of
    ~2 per track, giving a ~10-20x speed improvement on large libraries.
    Results are cached in a JSON file; interrupted runs resume automatically.

    reset_cache:      delete and rebuild the entire cache from scratch.
    refresh_artists:  list of artist names whose cache entries are cleared
                      before the run (partial refresh without a full reset).
    """
    try:
        import musicbrainzngs
    except ImportError:
        sys.exit(
            "Error: musicbrainzngs is required for --deep mode.\n"
            "Install it with:  pip install musicbrainzngs"
        )

    musicbrainzngs.set_useragent("PlexPlaylistTools", "1.0",
                                 "https://github.com/user/plex_playlist_tools")
    socket.setdefaulttimeout(_MB_TIMEOUT)  # prevent hung requests from blocking forever

    if reset_cache and os.path.exists(cache_file):
        os.remove(cache_file)
        print(f"Cache reset: deleted {cache_file}")

    cache = _load_mb_cache(cache_file)

    if refresh_artists:
        for artist_name in refresh_artists:
            al = artist_name.lower()
            # Remove artist MBID so the search is re-run
            cache.pop(f"artist_mbid|{al}", None)
            # Remove all track entries for this artist
            stale = [k for k in cache if k.startswith(f"{al}|")]
            for k in stale:
                del cache[k]
        print(f"Cache cleared for: {', '.join(refresh_artists)}")

    data  = scan_library(library)

    # Group tracks that still need a MB lookup by artist
    artist_groups: dict[str, list[dict]] = defaultdict(list)
    for track in data:
        key = f"{track['artist'].lower()}|{track['title'].lower()}"
        if key not in cache:
            artist_groups[track["artist"]].append(track)

    total_artists = len(artist_groups)
    if total_artists == 0:
        print("All tracks already cached — skipping MusicBrainz lookups.")
    else:
        # Pre-fetch all artist MBIDs in batches of 25 (ceil(artists/25) requests)
        batch_requests = (total_artists + 24) // 25
        # Each artist then needs 1 get_artist_by_id call; total ≈ batch + artists requests
        est_secs = max(1, batch_requests + total_artists)
        print(
            f"Enriching tracks for {total_artists} artist(s) via MusicBrainz "
            f"({_MB_WORKERS} workers, 1 request/artist after batch MBID lookup).\n"
            f"Estimated time: ~{est_secs}s  |  Cache: {cache_file}"
        )
        _batch_fetch_artist_mbids(list(artist_groups.keys()), cache)
        completed = 0
        with ThreadPoolExecutor(max_workers=_MB_WORKERS) as pool:
            futures = {
                pool.submit(_enrich_artist_tracks, artist_name, artist_tracks, cache): artist_name
                for artist_name, artist_tracks in artist_groups.items()
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass
                completed += 1
                print(f"  [{completed}/{total_artists}] artists enriched   ", end="\r")
                if completed % 40 == 0:
                    _save_mb_cache(cache_file, cache)

        _save_mb_cache(cache_file, cache)
        print(f"\nEnrichment complete. Cache saved to {cache_file}")

    # Apply cached tags to all tracks
    for track in data:
        key  = f"{track['artist'].lower()}|{track['title'].lower()}"
        tags = cache.get(key, [])
        if tags:
            track["genres"] = tags

    return data


def build_suggestions(
    track_data: list[dict],
    min_tracks: int = 10,
    min_artist_tracks: int = 20,
    include_best_of: bool = False,
) -> list[dict]:
    """
    Analyse track metadata and return a deduplicated, ranked list of
    playlist suggestions. Each entry: {name, description, tracks, category}.
    """
    suggestions: list[dict] = []

    # ── 1. Mood playlists ─────────────────────────────────────────────────────
    for mood, mood_genres in MOOD_MAP.items():
        matched = [t for t in track_data if _genre_matches(t["genres"], mood_genres)]
        if len(matched) >= min_tracks:
            preview = ", ".join(mood_genres[:3])
            suffix  = "…" if len(mood_genres) > 3 else ""
            suggestions.append({
                "name":        f"{mood.title()} Mix",
                "description": f"{len(matched)} tracks — {preview}{suffix}",
                "tracks":      [t["obj"] for t in matched],
                "category":    "mood",
            })

    # ── 2. Genre playlists ────────────────────────────────────────────────────
    genre_bucket: dict = defaultdict(list)
    for t in track_data:
        for g in t["genres"]:
            genre_bucket[g].append(t)

    for genre, tracks in sorted(genre_bucket.items(), key=lambda x: -len(x[1])):
        if len(tracks) >= min_tracks:
            suggestions.append({
                "name":        genre,
                "description": f"{len(tracks)} tracks",
                "tracks":      [t["obj"] for t in tracks],
                "category":    "genre",
            })

    # ── 3. Decade playlists ───────────────────────────────────────────────────
    decade_bucket: dict = defaultdict(list)
    for t in track_data:
        if t["year"]:
            decade_bucket[(t["year"] // 10) * 10].append(t)

    for decade in sorted(decade_bucket):
        tracks = decade_bucket[decade]
        if len(tracks) >= min_tracks:
            suggestions.append({
                "name":        f"{decade}s Hits",
                "description": f"{len(tracks)} tracks from {decade}–{decade + 9}",
                "tracks":      [t["obj"] for t in tracks],
                "category":    "decade",
            })

    # ── 4. Decade + Genre combos (top 20 by count) ───────────────────────────
    dg_bucket: dict = defaultdict(list)
    for t in track_data:
        if t["year"]:
            d = (t["year"] // 10) * 10
            for g in t["genres"]:
                dg_bucket[(d, g)].append(t)

    for (decade, genre), tracks in sorted(
        dg_bucket.items(), key=lambda x: -len(x[1])
    )[:20]:
        if len(tracks) >= min_tracks:
            suggestions.append({
                "name":        f"{decade}s {genre}",
                "description": f"{len(tracks)} {genre} tracks from {decade}–{decade + 9}",
                "tracks":      [t["obj"] for t in tracks],
                "category":    "decade + genre",
            })

    # ── 5. Artist "Best Of" (opt-in) ─────────────────────────────────────────
    if include_best_of:
        artist_bucket: dict = defaultdict(list)
        for t in track_data:
            artist_bucket[t["artist"]].append(t)

        for artist, tracks in sorted(artist_bucket.items(), key=lambda x: -len(x[1])):
            if len(tracks) >= min_artist_tracks:
                suggestions.append({
                    "name":        f"Best of {artist}",
                    "description": f"All {len(tracks)} tracks by {artist}",
                    "tracks":      [t["obj"] for t in tracks],
                    "category":    "artist",
                })

    # Deduplicate, then sort: category group first, track count descending within each group.
    # Artist "Best of" playlists always appear last.
    _CAT_ORDER = {"mood": 0, "genre": 1, "decade": 2, "decade + genre": 3, "artist": 4}
    seen: set[str] = set()
    unique: list[dict] = []
    for s in sorted(suggestions,
                    key=lambda x: (_CAT_ORDER.get(x["category"], 99), -len(x["tracks"]))):
        if s["name"] not in seen:
            seen.add(s["name"])
            unique.append(s)
    return unique


def find_tracks_for_prompt(
    prompt: str, track_data: list[dict]
) -> tuple[list, list[str]]:
    """
    Parse a natural language description and return matching Track objects
    plus a list of human-readable criteria labels explaining the match.
    """
    prompt_lower = prompt.lower()
    criteria: list[str] = []

    # 1. Decade keywords
    decade_range: tuple[int, int] | None = None
    for kw, (start, end) in DECADE_KEYWORDS.items():
        if re.search(rf'\b{re.escape(kw)}\b', prompt_lower):
            decade_range = (start, end)
            criteria.append(f"decade: {start}s")
            break

    # 2. Mood / genre keywords from the map
    target_genres: set[str] = set()
    for kw, genres in MOOD_MAP.items():
        if re.search(rf'\b{re.escape(kw)}\b', prompt_lower):
            target_genres.update(g.lower() for g in genres)
            criteria.append(f"keyword: '{kw}'")

    # 3. Direct word matching against library genre tags
    if not target_genres and not decade_range:
        words = [
            w for w in re.findall(r'[a-z]+', prompt_lower)
            if len(w) >= 4 and w not in _STOP_WORDS
        ]
        direct: set[str] = set()
        for t in track_data:
            for g in t["genres"]:
                if any(w in g.lower() for w in words):
                    direct.add(g)
        if direct:
            target_genres = {g.lower() for g in direct}
            criteria.append(f"genre match: {', '.join(sorted(direct)[:4])}")

    # 4. Artist name matching as final fallback
    if not target_genres and not decade_range:
        words = [
            w for w in re.findall(r'[a-z]+', prompt_lower)
            if len(w) >= 3 and w not in _STOP_WORDS
        ]
        artist_hits: set[str] = set()
        for t in track_data:
            if any(w in t["artist"].lower() for w in words):
                artist_hits.add(t["artist"])
        if artist_hits:
            criteria.append(f"artist: {', '.join(sorted(artist_hits)[:4])}")
            return [t["obj"] for t in track_data if t["artist"] in artist_hits], criteria

    if not target_genres and not decade_range:
        return [], criteria

    matching: list = []
    for t in track_data:
        if decade_range:
            if not t["year"] or not (decade_range[0] <= t["year"] <= decade_range[1]):
                continue
        if target_genres and not _genre_matches(t["genres"], list(target_genres)):
            continue
        matching.append(t["obj"])

    return matching, criteria


_BATCH_SIZE = 200


def _add_items_batched(playlist, items: list) -> None:
    """Call addItems in chunks to avoid Plex's URL-length limit."""
    for i in range(0, len(items), _BATCH_SIZE):
        playlist.addItems(items[i:i + _BATCH_SIZE])


def _create_playlist_batched(plex: PlexServer, name: str, items: list):
    """Create a playlist with the first batch, then addItems for the rest."""
    playlist = plex.createPlaylist(name, items=items[:_BATCH_SIZE])
    if len(items) > _BATCH_SIZE:
        _add_items_batched(playlist, items[_BATCH_SIZE:])
    return playlist


def _create_or_replace_playlist(
    plex: PlexServer, name: str, tracks: list, log_rows: list
) -> None:
    """Delete any existing playlist with this name then create a fresh one."""
    for existing in plex.playlists():
        if existing.title == name:
            existing.delete()
            break
    _create_playlist_batched(plex, name, tracks)
    log_rows.append(log_row("generate", name, "", "", "", "success",
                             f"Created with {len(tracks)} tracks"))


def cmd_suggest(
    plex: PlexServer,
    library_name: str | None,
    min_tracks: int,
    min_artist_tracks: int,
    limit: int,
    create_all: bool,
    deep: bool,
    cache_file: str,
    reset_cache: bool,
    refresh_artists: list[str] | None,
    include_best_of: bool,
    log_file: str,
) -> None:
    library    = find_music_library(plex, library_name)
    track_data = (
        scan_library_deep(library, cache_file, reset_cache, refresh_artists)
        if deep else scan_library(library)
    )
    all_suggestions = build_suggestions(
        track_data, min_tracks, min_artist_tracks, include_best_of
    )

    if not all_suggestions:
        print("No suggestions generated — try lowering --min-tracks.")
        return

    display = all_suggestions[:limit]
    cat_w   = max(len(s["category"]) for s in display)
    print(f"\n{len(all_suggestions)} suggestion(s) found. Showing top {len(display)}:\n")
    for i, s in enumerate(display, 1):
        cat = f"[{s['category']}]".ljust(cat_w + 2)
        print(f"  [{i:>2}]  {cat}  {s['name']:<42}  {s['description']}")

    if create_all:
        chosen = display
    else:
        print(
            "\nEnter number(s) to create (e.g. 1  or  1,3,5  or  all), "
            "or 'q' to quit:"
        )
        raw = input("> ").strip().lower()
        if raw in ("q", "quit", ""):
            print("Cancelled.")
            return
        if raw == "all":
            chosen = display
        else:
            try:
                indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip()]
                chosen  = [display[j] for j in indices if 0 <= j < len(display)]
            except ValueError:
                print("Invalid input — enter numbers separated by commas.")
                return

    if not chosen:
        print("Nothing selected.")
        return

    log_rows: list[dict] = []
    for s in chosen:
        _create_or_replace_playlist(plex, s["name"], s["tracks"], log_rows)
        print(f"  Created '{s['name']}' ({len(s['tracks'])} tracks)")

    append_log(log_file, log_rows)
    print(f"\nCreated {len(chosen)} playlist(s).")
    print(f"Log  : {log_file}")


def cmd_generate(
    plex: PlexServer,
    library_name: str | None,
    prompt: str,
    playlist_name: str | None,
    min_tracks: int,
    yes: bool,
    log_file: str,
) -> None:
    library    = find_music_library(plex, library_name)
    track_data = scan_library(library)

    print(f'\nSearching for: "{prompt}"')
    tracks, criteria = find_tracks_for_prompt(prompt, track_data)

    if criteria:
        print("Matched on:")
        for c in criteria:
            print(f"  · {c}")

    if not tracks:
        print(
            "\nNo matching tracks found.\n"
            "Tips: try different keywords, check your library's genre tags,\n"
            "      or run 'suggest' to see what your library can support."
        )
        return

    name = playlist_name or prompt.strip().title()
    print(f"\nFound {len(tracks)} track(s)  →  playlist name: '{name}'")

    if len(tracks) < min_tracks:
        print(f"Warning: only {len(tracks)} track(s) found (--min-tracks is {min_tracks}).")

    if not yes:
        raw = input(f"\nCreate '{name}' with {len(tracks)} tracks? [y/N] ").strip().lower()
        if raw not in ("y", "yes"):
            print("Cancelled.")
            return

    log_rows: list[dict] = []
    _create_or_replace_playlist(plex, name, tracks, log_rows)
    append_log(log_file, log_rows)
    print(f"Created playlist '{name}' with {len(tracks)} tracks.")
    print(f"Log  : {log_file}")


# ─── Dedupe ───────────────────────────────────────────────────────────────────

def cmd_dedupe(
    plex: PlexServer,
    playlist_names: list | None,
    yes: bool,
    log_file: str,
) -> None:
    all_music = [p for p in plex.playlists() if p.playlistType == "audio"]
    if not all_music:
        print("No music playlists found.")
        return

    if playlist_names:
        name_set = {n.lower() for n in playlist_names}
        targets = [p for p in all_music if p.title.lower() in name_set]
        missing = name_set - {p.title.lower() for p in targets}
        if missing:
            print(f"Playlist(s) not found: {', '.join(missing)}")
            if not targets:
                sys.exit(1)
    else:
        targets = all_music

    log_rows: list[dict] = []
    total_removed = 0

    for playlist in targets:
        items = playlist.items()
        seen_keys: set = set()
        dupes: list = []
        for track in items:
            if track.ratingKey in seen_keys:
                dupes.append(track)
            else:
                seen_keys.add(track.ratingKey)

        if not dupes:
            print(f"  '{playlist.title}' — no duplicates ({len(items)} tracks)")
            continue

        print(f"  '{playlist.title}' — {len(dupes)} duplicate(s) in {len(items)} tracks:")
        for t in dupes:
            print(f"    · {t.grandparentTitle} – {t.title}")

        if not yes:
            raw = input(
                f"  Remove {len(dupes)} duplicate(s) from '{playlist.title}'? [y/N] "
            ).strip().lower()
            if raw not in ("y", "yes"):
                print("  Skipped.")
                continue

        playlist.removeItems(dupes)
        total_removed += len(dupes)
        print(f"  Removed {len(dupes)} duplicate(s) from '{playlist.title}'.")
        for t in dupes:
            log_rows.append(log_row(
                "dedupe", playlist.title,
                t.grandparentTitle, t.parentTitle, t.title,
                "success", "Duplicate removed",
            ))

    append_log(log_file, log_rows)
    if total_removed:
        print(f"\nRemoved {total_removed} duplicate track(s) total.")
        print(f"Log  : {log_file}")
    else:
        print("\nNo duplicates found.")


# ─── Shuffle ──────────────────────────────────────────────────────────────────

def cmd_shuffle(
    plex: PlexServer,
    source_name: str,
    output_name: str | None,
    seed: int | None,
    yes: bool,
    log_file: str,
) -> None:
    all_music = [p for p in plex.playlists() if p.playlistType == "audio"]
    source = next((p for p in all_music if p.title.lower() == source_name.lower()), None)
    if source is None:
        print(f"Error: Playlist '{source_name}' not found.")
        print("Available music playlists:")
        for p in all_music:
            print(f"  - {p.title}")
        sys.exit(1)

    tracks = list(source.items())
    if not tracks:
        print(f"Playlist '{source_name}' is empty.")
        return

    rng = random.Random(seed)
    rng.shuffle(tracks)

    name = output_name or f"{source.title} (Shuffled)"
    seed_note = f" (seed: {seed})" if seed is not None else ""
    print(f"Shuffling '{source.title}' ({len(tracks)} tracks)  →  '{name}'{seed_note}")

    if not yes:
        raw = input(
            f"\nCreate '{name}' with {len(tracks)} shuffled tracks? [y/N] "
        ).strip().lower()
        if raw not in ("y", "yes"):
            print("Cancelled.")
            return

    for existing in plex.playlists():
        if existing.title == name:
            existing.delete()
            break

    _create_playlist_batched(plex, name, tracks)
    log_rows = [log_row(
        "shuffle", name, "", "", "", "success",
        f"Created from '{source.title}' — {len(tracks)} tracks shuffled{seed_note}",
    )]
    append_log(log_file, log_rows)
    print(f"Created playlist '{name}' with {len(tracks)} tracks.")
    print(f"Log  : {log_file}")


# ─── Sync ─────────────────────────────────────────────────────────────────────

def cmd_sync(
    plex: PlexServer,
    dest_plex: PlexServer,
    dest_library_name: str | None,
    playlist_names: list | None,
    mode: str,
    log_file: str,
) -> None:
    src_playlists = [p for p in plex.playlists() if p.playlistType == "audio"]
    if not src_playlists:
        print("No music playlists found on the source server.")
        return

    if playlist_names:
        name_set = {n.lower() for n in playlist_names}
        targets = [p for p in src_playlists if p.title.lower() in name_set]
        missing = name_set - {p.title.lower() for p in targets}
        if missing:
            print(f"Playlist(s) not found on source: {', '.join(missing)}")
            if not targets:
                sys.exit(1)
    else:
        targets = src_playlists

    dest_library = find_music_library(dest_plex, dest_library_name)
    by_path, by_artist_title = build_track_index(dest_library)

    log_rows: list[dict] = []
    total_synced = 0

    for playlist in targets:
        src_tracks = playlist.items()
        print(f"\nSyncing '{playlist.title}' ({len(src_tracks)} tracks)...")

        matched, failed = [], 0
        for track in src_tracks:
            artist    = track.grandparentTitle or ""
            title     = track.title or ""
            file_path = track.media[0].parts[0].file if track.media else ""

            dest_track = by_path.get(file_path) if file_path else None
            if not dest_track:
                dest_track = by_artist_title.get((artist.lower(), title.lower()))

            if dest_track:
                matched.append(dest_track)
            else:
                failed += 1
                log_rows.append(log_row(
                    "sync", playlist.title, artist, track.parentTitle or "", title,
                    "error", "Track not found on destination",
                ))

        if not matched:
            print("  No tracks matched on destination — skipped.")
            continue

        dest_existing = next(
            (p for p in dest_plex.playlists() if p.title == playlist.title), None
        )

        if mode == "append" and dest_existing:
            existing_keys = {t.ratingKey for t in dest_existing.items()}
            new_tracks = [t for t in matched if t.ratingKey not in existing_keys]
            dupes = len(matched) - len(new_tracks)
            if new_tracks:
                _add_items_batched(dest_existing, new_tracks)
                print(f"  Appended {len(new_tracks)} track(s) "
                      f"({dupes} already present, {failed} not found)")
            else:
                print(f"  Nothing to append ({dupes} already present, {failed} not found)")
        else:
            if dest_existing:
                dest_existing.delete()
            _create_playlist_batched(dest_plex, playlist.title, matched)
            print(f"  Created '{playlist.title}': {len(matched)} synced, {failed} not found")

        for t in matched:
            log_rows.append(log_row(
                "sync", playlist.title,
                t.grandparentTitle, t.parentTitle, t.title,
                "success", "",
            ))
        total_synced += len(matched)

    append_log(log_file, log_rows)
    print(f"\nSync complete: {total_synced} track(s) across {len(targets)} playlist(s).")
    print(f"Log  : {log_file}")


# ─── Rename ───────────────────────────────────────────────────────────────────

def cmd_rename(plex: PlexServer, old_name: str, new_name: str) -> None:
    playlists = [p for p in plex.playlists() if p.playlistType == "audio"]
    target = next((p for p in playlists if p.title.lower() == old_name.lower()), None)
    if target is None:
        print(f"Error: Playlist '{old_name}' not found.")
        print("Available music playlists:")
        for p in playlists:
            print(f"  - {p.title}")
        sys.exit(1)

    conflict = next(
        (p for p in playlists
         if p.title.lower() == new_name.lower() and p.ratingKey != target.ratingKey),
        None,
    )
    if conflict:
        print(f"Error: A playlist named '{new_name}' already exists.")
        sys.exit(1)

    target.edit(title=new_name)
    print(f"Renamed '{old_name}'  →  '{new_name}'.")


# ─── Merge ────────────────────────────────────────────────────────────────────

def cmd_merge(
    plex: PlexServer,
    source_names: list[str],
    output_name: str,
    allow_duplicates: bool,
    yes: bool,
    log_file: str,
) -> None:
    all_music = [p for p in plex.playlists() if p.playlistType == "audio"]
    sources: list = []
    for name in source_names:
        match = next((p for p in all_music if p.title.lower() == name.lower()), None)
        if match is None:
            print(f"Error: Playlist '{name}' not found.")
            print("Available music playlists:")
            for p in all_music:
                print(f"  - {p.title}")
            sys.exit(1)
        sources.append(match)

    merged: list = []
    seen_keys: set = set()
    dupes_skipped = 0

    for playlist in sources:
        for track in playlist.items():
            if allow_duplicates or track.ratingKey not in seen_keys:
                merged.append(track)
                seen_keys.add(track.ratingKey)
            else:
                dupes_skipped += 1

    dupe_note = f" ({dupes_skipped} duplicate(s) removed)" if dupes_skipped else ""
    print(f"Merging {len(sources)} playlist(s)  →  '{output_name}'")
    print(f"  Sources : {', '.join(p.title for p in sources)}")
    print(f"  Tracks  : {len(merged)}{dupe_note}")

    if not merged:
        print("No tracks to merge.")
        return

    if not yes:
        raw = input(
            f"\nCreate '{output_name}' with {len(merged)} tracks? [y/N] "
        ).strip().lower()
        if raw not in ("y", "yes"):
            print("Cancelled.")
            return

    for existing in plex.playlists():
        if existing.title == output_name:
            existing.delete()
            break

    _create_playlist_batched(plex, output_name, merged)
    log_rows = [log_row(
        "merge", output_name, "", "", "", "success",
        f"Merged from: {', '.join(p.title for p in sources)} — {len(merged)} tracks{dupe_note}",
    )]
    append_log(log_file, log_rows)
    print(f"Created '{output_name}' with {len(merged)} tracks.")
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
    exp.add_argument("--all-images", action="store_true",
                     help="Download every available cover for each playlist, not just the selected one (requires --images-dir)")

    # ── import ──
    imp = sub.add_parser("import", help="Import playlists from a CSV into Plex")
    imp.add_argument("--file",       default=default_imp,  metavar="FILE",
                     help=f"CSV file to import (default: {default_imp})")
    imp.add_argument("--log",        default=default_log,  metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")
    imp.add_argument("--images-dir", default=default_imgs, metavar="DIR",
                     help="Directory to read playlist cover images from (skipped if not set)")
    imp.add_argument("--mode", choices=["replace", "append"], default="append",
                     help="append: add only tracks not already in the playlist (default); "
                          "replace: delete and recreate the playlist")

    # ── suggest ──
    sug = sub.add_parser("suggest",
                         help="Scan library and suggest playlists to create interactively")
    sug.add_argument("--min-tracks",        type=int, default=10, metavar="N",
                     help="Minimum tracks for a suggestion to appear (default: 10)")
    sug.add_argument("--min-artist-tracks", type=int, default=20, metavar="N",
                     help="Minimum tracks by one artist for a 'Best of' suggestion (default: 20)")
    sug.add_argument("--limit",             type=int, default=25, metavar="N",
                     help="Maximum number of suggestions to display (default: 25)")
    sug.add_argument("--create-all",        action="store_true",
                     help="Create every suggestion without prompting")
    sug.add_argument("--deep",              action="store_true",
                     help="Look up each track on MusicBrainz for accurate per-song "
                          "mood/genre tags (slow on first run; results cached)")
    sug.add_argument("--cache-file",        default="mb_cache.json", metavar="FILE",
                     help="JSON cache file for MusicBrainz lookups (default: mb_cache.json)")
    sug.add_argument("--reset-cache",       action="store_true",
                     help="Delete the entire cache and re-fetch all artists from MusicBrainz")
    sug.add_argument("--refresh-artist",    nargs="+", metavar="ARTIST",
                     help="Clear and re-fetch cache for one or more specific artists "
                          "(e.g. --refresh-artist \"Pink Floyd\" \"David Bowie\")")
    sug.add_argument("--include-best-of",   action="store_true",
                     help="Include 'Best of <Artist>' suggestions (omitted by default)")
    sug.add_argument("--log",               default=default_log, metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")

    # ── generate ──
    gen = sub.add_parser("generate",
                         help="Create a playlist from a natural language description")
    gen.add_argument("prompt", metavar="DESCRIPTION",
                     help="What kind of playlist you want "
                          "(e.g. 'chill lo-fi for studying' or '90s rock')")
    gen.add_argument("--name",       metavar="NAME",
                     help="Playlist name (defaults to the description)")
    gen.add_argument("--min-tracks", type=int, default=5, metavar="N",
                     help="Warn if fewer than this many tracks are found (default: 5)")
    gen.add_argument("--yes", "-y",  action="store_true",
                     help="Skip confirmation and create the playlist immediately")
    gen.add_argument("--log",        default=default_log, metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")

    # ── list-playlists ──
    sub.add_parser("list-playlists", help="List all music playlists on the server")

    # ── dedupe ──
    ded = sub.add_parser("dedupe", help="Find and remove duplicate tracks from playlist(s)")
    ded_grp = ded.add_mutually_exclusive_group(required=True)
    ded_grp.add_argument("--playlist", metavar="NAME", nargs="+",
                         help="Playlist(s) to deduplicate")
    ded_grp.add_argument("--all-playlists", action="store_true",
                         help="Deduplicate all music playlists")
    ded.add_argument("--yes", "-y", action="store_true",
                     help="Remove duplicates without confirmation")
    ded.add_argument("--log", default=default_log, metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")

    # ── shuffle ──
    shuf = sub.add_parser("shuffle", help="Create a shuffled copy of a playlist")
    shuf.add_argument("playlist", metavar="PLAYLIST",
                      help="Name of the playlist to shuffle")
    shuf.add_argument("--name", metavar="NAME",
                      help="Name for the new shuffled playlist (default: '<name> (Shuffled)')")
    shuf.add_argument("--seed", type=int, metavar="N",
                      help="Random seed for a reproducible shuffle")
    shuf.add_argument("--yes", "-y", action="store_true",
                      help="Skip confirmation")
    shuf.add_argument("--log", default=default_log, metavar="FILE",
                      help=f"Log CSV file (default: {default_log})")

    # ── sync ──
    syn = sub.add_parser("sync",
                         help="Mirror playlist(s) from this server to another Plex server")
    syn_grp = syn.add_mutually_exclusive_group(required=True)
    syn_grp.add_argument("--playlist", metavar="NAME", nargs="+",
                         help="Playlist(s) to sync")
    syn_grp.add_argument("--all-playlists", action="store_true",
                         help="Sync all music playlists")
    syn.add_argument("--dest-url",     required=True, metavar="URL",
                     help="Destination Plex server URL")
    syn.add_argument("--dest-token",   required=True, metavar="TOKEN",
                     help="Destination Plex auth token")
    syn.add_argument("--dest-library", metavar="NAME",
                     help="Music library name on destination (uses first found if blank)")
    syn.add_argument("--mode", choices=["replace", "append"], default="replace",
                     help="replace: recreate the playlist (default); "
                          "append: add only new tracks")
    syn.add_argument("--log", default=default_log, metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")

    # ── rename ──
    ren = sub.add_parser("rename", help="Rename a playlist")
    ren.add_argument("old_name", metavar="OLD_NAME", help="Current playlist name")
    ren.add_argument("new_name", metavar="NEW_NAME", help="New playlist name")

    # ── merge ──
    mer = sub.add_parser("merge", help="Combine multiple playlists into one")
    mer.add_argument("sources", metavar="PLAYLIST", nargs="+",
                     help="Source playlist names to merge (two or more)")
    mer.add_argument("--name", required=True, metavar="NAME",
                     help="Name for the merged playlist")
    mer.add_argument("--allow-duplicates", action="store_true",
                     help="Keep duplicate tracks from different playlists "
                          "(default: duplicates are removed)")
    mer.add_argument("--yes", "-y", action="store_true",
                     help="Skip confirmation")
    mer.add_argument("--log", default=default_log, metavar="FILE",
                     help=f"Log CSV file (default: {default_log})")

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

    # Normalise all CSV file paths — warn and append default filename if a
    # directory path was given instead of a .csv filename.
    if hasattr(args, "output"):
        args.output = ensure_csv_path(args.output, default_out)
    if hasattr(args, "file"):
        args.file = ensure_csv_path(args.file, default_imp)
    if hasattr(args, "log"):
        args.log = ensure_csv_path(args.log, default_log)

    plex = get_plex_server(base_url, token)

    if args.command == "export":
        images_dir = getattr(args, "images_dir", None)
        all_images = getattr(args, "all_images", False)
        if all_images and not images_dir:
            print("Error: --all-images requires --images-dir to be set.")
            sys.exit(1)
        if args.playlist:
            export_playlists(plex, args.playlist, args.output, args.log, images_dir, all_images)
        elif args.all_playlists:
            export_playlists(plex, None, args.output, args.log, images_dir, all_images)
        else:
            export_library(plex, library_name, args.output, args.log)

    elif args.command == "import":
        images_dir = getattr(args, "images_dir", None)
        import_playlists(plex, library_name, args.file, args.log, images_dir, args.mode)

    elif args.command == "suggest":
        cmd_suggest(
            plex, library_name,
            args.min_tracks, args.min_artist_tracks,
            args.limit, args.create_all,
            args.deep, args.cache_file,
            args.reset_cache, getattr(args, "refresh_artist", None),
            args.include_best_of,
            args.log,
        )

    elif args.command == "generate":
        cmd_generate(
            plex, library_name,
            args.prompt, args.name,
            args.min_tracks, args.yes, args.log,
        )

    elif args.command == "list-playlists":
        list_playlists(plex)

    elif args.command == "dedupe":
        cmd_dedupe(
            plex,
            getattr(args, "playlist", None),
            args.yes,
            args.log,
        )

    elif args.command == "shuffle":
        cmd_shuffle(
            plex,
            args.playlist,
            getattr(args, "name", None),
            getattr(args, "seed", None),
            args.yes,
            args.log,
        )

    elif args.command == "sync":
        dest_plex = get_plex_server(args.dest_url, args.dest_token)
        cmd_sync(
            plex,
            dest_plex,
            getattr(args, "dest_library", None),
            getattr(args, "playlist", None),
            args.mode,
            args.log,
        )

    elif args.command == "rename":
        cmd_rename(plex, args.old_name, args.new_name)

    elif args.command == "merge":
        cmd_merge(
            plex,
            args.sources,
            args.name,
            getattr(args, "allow_duplicates", False),
            args.yes,
            args.log,
        )



if __name__ == "__main__":
    main()
