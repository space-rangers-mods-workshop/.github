"""Publish a new version of a mod to Nexus Mods through the official v3 API.

Endpoints, request bodies and the auth header are taken from the official spec
<https://api.nexusmods.com/openapi.yaml> and the `Nexus-Mods/upload-action`
source (base URL ``https://api.nexusmods.com/v3``, auth header ``apikey``):

    POST /uploads                  {size_bytes, filename} -> {id, presigned_url, state}
    PUT  <presigned_url>           raw bytes (single part, <= 100 MiB)
    POST /uploads/{id}/finalise
    GET  /uploads/{id}             poll until state == "available"
    POST /mod-files                {upload_id, mod_id, name, version, description,
                                    file_category, primary_mod_manager_download,
                                    allow_mod_manager_download, show_requirements_pop_up,
                                    update_mod_version}                       (NEW file)
    POST /mod-files/{id}/versions  {upload_id, name, version, file_category,
                                    primary_mod_manager_download, allow_mod_manager_download,
                                    show_requirements_pop_up, update_mod_version,
                                    archive_existing_file, previous_version_id}
                                                                       (new version of a file)
    POST /mods/{id}/changelogs     {version, changelog}

``file_category`` is one of ``main`` / ``optional`` / ``miscellaneous``.

Which endpoint to use is the release's choice:

* ``--mode new-file`` (``POST /mod-files``) — adds the new archive as a **separate file**
  on the mod page; the existing file is untouched (keeps its own name and download count).
* ``--mode new-version`` (``POST /mod-files/{id}/versions``) — adds the archive as a **new
  version of the existing file** (one download entry; the old version stays, categorised
  ``archived``/``old_version``, and the new one becomes the most recent entry).

The API key is read from ``NEXUS_API_KEY`` (or ``MO2_APIKEY``) — the environment, or a ``.env``
file, which must stay gitignored. Generate it at
<https://www.nexusmods.com/users/myaccount?tab=api> (Site preferences -> API tab ->
Personal API key -> Generate).

Note: ``is_primary`` (the mod page's default download) is a **read-only** response field in
the spec — there is no request field or endpoint to set it. The closest writable knob is
``primary_mod_manager_download`` ("default download for mod managers"). The new
file/version becomes the page's Primary either automatically (category ``main``) or with
one manual click.

Usage
-----
    # new separate file (2.0.1 next to the existing 2.0.0), changelog attached:
    python nexus_publish.py AMod_Spacejunk.zip --mod-id 59 --version 2.0.1 \\
        --changelog "The mod no longer blocks Steam achievements."

    # new version of an existing file:
    python nexus_publish.py AMod_Spacejunk.zip --mode new-version --file-id 123456 \\
        --version 2.0.1

    # dry run (no network):
    python nexus_publish.py AMod_Spacejunk.zip --mod-id 59 --version 2.0.1 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOL_NAME = "nexus_publish.py"
TOOL_VERSION = "1.0.0"
API_BASE = "https://api.nexusmods.com/v3"
SINGLE_PART_LIMIT = 100 * 1024 * 1024  # > 100 MiB must use POST /uploads/multipart
API_KEY_VARS = ("NEXUS_API_KEY", "MO2_APIKEY")  # accepted names, in order


def pick_api_key(env: dict[str, str]) -> str | None:
    for name in API_KEY_VARS:
        if env.get(name):
            return env[name]
    return None


def load_dotenv(path: Path) -> dict[str, str]:
    """Minimal ``.env`` reader: ``KEY=VALUE`` lines, ``#`` comments, optional quotes."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def request(method: str, url: str, api_key: str, body: bytes | None = None,
            content_type: str = "application/json") -> bytes:
    headers = {"apikey": api_key, "User-Agent": f"space-rangers-workshop/{TOOL_NAME}"}
    if body is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"ERROR: {method} {url} -> HTTP {exc.code}: {detail}") from None


def post_json(url: str, api_key: str, payload: dict) -> dict:
    raw = request("POST", url, api_key, json.dumps(payload).encode("utf-8"))
    body = json.loads(raw) if raw else {}
    return body.get("data", body)  # v3 responses are wrapped in {"data": {...}}


def upload_file(archive: Path, api_key: str, dry_run: bool) -> str:
    size = archive.stat().st_size
    print(f"[upload] {archive.name} ({size} bytes)")
    if size > SINGLE_PART_LIMIT:
        raise SystemExit("ERROR: file exceeds 100 MiB — it needs POST /uploads/multipart")
    if dry_run:
        return "<upload-id>"
    created = post_json(f"{API_BASE}/uploads", api_key, {"size_bytes": size, "filename": archive.name})
    upload_id, presigned = created["id"], created["presigned_url"]
    # The presigned URL signs content-type AND content-disposition — both must be sent as signed.
    put = urllib.request.Request(
        presigned, data=archive.read_bytes(), method="PUT",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{archive.name}"',
        },
    )
    with urllib.request.urlopen(put, timeout=300):
        pass
    request("POST", f"{API_BASE}/uploads/{upload_id}/finalise", api_key)
    for _ in range(30):
        polled = json.loads(request("GET", f"{API_BASE}/uploads/{upload_id}", api_key))
        state = polled.get("data", polled)["state"]
        if state == "available":
            print(f"[upload] finalised: {upload_id}")
            return upload_id
        time.sleep(2)
    raise SystemExit("ERROR: upload did not become 'available' in time")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("archive", help="path to the release archive (.zip) to upload")
    parser.add_argument("--version", required=True, help="version string shown on Nexus, e.g. 2.0.1")
    parser.add_argument("--mode", choices=["new-file", "new-version"], default="new-file",
                        help="new-file = a separate file on the page; new-version = a new version of an existing file")
    parser.add_argument("--mod-id", help="game-scoped mod id from the site URL (…/mods/<id>) — required for --mode new-file and for a changelog")
    parser.add_argument("--game", default="spacerangersawarapart",
                        help="game domain used to resolve --mod-id (default: spacerangersawarapart)")
    parser.add_argument("--file-id", help="Nexus file id — required for --mode new-version")
    parser.add_argument("--name", help="file/version display name (default: the archive name)")
    parser.add_argument("--description", help="file/version description")
    parser.add_argument("--category", choices=["main", "optional", "miscellaneous"], default="main")
    parser.add_argument("--changelog", help="changelog text for the mod (requires --mod-id)")
    parser.add_argument("--changelog-file", help="read the changelog from a file (overrides --changelog)")
    parser.add_argument("--primary-mod-manager-download", action="store_true",
                        help="mark this file as the default download for mod managers")
    parser.add_argument("--no-mod-manager-download", action="store_true",
                        help="disallow mod-manager download (default: allowed)")
    parser.add_argument("--update-mod-version", action="store_true",
                        help="also set the mod's own version to --version")
    parser.add_argument("--archive-existing", action="store_true",
                        help="(--mode new-version) archive the previous version of this file")
    parser.add_argument("--env-file", default=".env", help="file to read NEXUS_API_KEY from (default: ./.env)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without any network calls")
    args = parser.parse_args()

    if args.mode == "new-file" and not args.mod_id:
        raise SystemExit("ERROR: --mod-id is required for --mode new-file")
    if args.mode == "new-version" and not args.file_id:
        raise SystemExit("ERROR: --file-id is required for --mode new-version")
    if args.changelog and not args.mod_id:
        raise SystemExit("ERROR: --changelog requires --mod-id")

    archive = Path(args.archive)
    if not archive.is_file():
        raise SystemExit(f"ERROR: archive not found: {archive}")

    env_path = Path(args.env_file)
    api_key = None
    if env_path.is_file():
        api_key = pick_api_key(load_dotenv(env_path))
    if not api_key:
        api_key = pick_api_key(os.environ)
    if not api_key and not args.dry_run:
        raise SystemExit(
            f"ERROR: no API key — set {' or '.join(API_KEY_VARS)} "
            f"(looked in {env_path} and the environment)"
        )

    name = args.name or archive.stem
    changelog = args.changelog
    if args.changelog_file:
        changelog = Path(args.changelog_file).read_text(encoding="utf-8")

    # POST /mod-files and /mods/{id}/changelogs use the mod's *unique* id
    # (gameId << 32 | modId), not the game-scoped id from the site URL — resolve it.
    mod_uid = args.mod_id
    if not args.dry_run and args.mod_id:
        resolved = json.loads(request("GET", f"{API_BASE}/games/{args.game}/mods/{args.mod_id}", api_key))
        mod_uid = resolved.get("data", resolved)["id"]
        print(f"[resolve] {args.game}/mods/{args.mod_id} -> unique mod id {mod_uid}")

    if args.mode == "new-file":
        attach = {"endpoint": "POST /mod-files", "body": {
            "upload_id": "<upload-id>", "mod_id": mod_uid, "name": name, "version": args.version,
            "description": args.description, "file_category": args.category,
            "primary_mod_manager_download": args.primary_mod_manager_download,
            "allow_mod_manager_download": not args.no_mod_manager_download,
            "show_requirements_pop_up": False, "update_mod_version": args.update_mod_version,
        }}
    else:
        attach = {"endpoint": f"POST /mod-files/{args.file_id}/versions", "body": {
            "upload_id": "<upload-id>", "name": name, "version": args.version,
            "description": args.description, "file_category": args.category,
            "primary_mod_manager_download": args.primary_mod_manager_download,
            "allow_mod_manager_download": not args.no_mod_manager_download,
            "show_requirements_pop_up": False, "update_mod_version": args.update_mod_version,
            "archive_existing_file": args.archive_existing,
        }}

    print(f"{TOOL_NAME} {TOOL_VERSION}: publish {name} {args.version} ({args.mode})")
    print(f"  attach: {attach['endpoint']}")
    print(f"  body:   {json.dumps(attach['body'], ensure_ascii=False)}")
    if changelog:
        print(f"  changelog -> POST /mods/{mod_uid}/changelogs")
    if args.dry_run:
        print("dry-run: no network calls made")
        return

    upload_id = upload_file(archive, api_key, dry_run=False)
    attach["body"]["upload_id"] = upload_id
    result = post_json(f"{API_BASE}{attach['endpoint'].split(' ', 1)[1]}", api_key, attach["body"])
    print(f"[attach] {json.dumps(result, ensure_ascii=False)}")

    if changelog:
        post_json(f"{API_BASE}/mods/{mod_uid}/changelogs", api_key,
                  {"version": args.version, "changelog": changelog})
        print("[changelog] added")

    print("done — check the mod page; set the page's Primary download if it is not automatic")


if __name__ == "__main__":
    main()
