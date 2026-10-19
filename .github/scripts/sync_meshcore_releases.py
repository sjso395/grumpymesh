#!/usr/bin/env python3
"""Mirror Grumpy Node firmware from the MeshCore fork's GitHub releases.

GitHub release downloads don't send CORS headers, so the browser flasher can't
fetch them directly. This copies every release's .bin assets into
Firmware/meshcore/<tag>/ and writes Firmware/meshcore/releases.json, which
flasher.html reads from the same origin.

Usage: python3 .github/scripts/sync_meshcore_releases.py
Set GITHUB_TOKEN to avoid the unauthenticated API rate limit.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.request
from pathlib import Path

REPO = os.environ.get('MESHCORE_REPO', 'Cloud-121/MeshCore')
ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / 'Firmware' / 'meshcore'
SITE_PREFIX = 'Firmware/meshcore'
# Grumpy_Node_<variant>-<version>-<commit>[-merged].bin
ASSET_RE = re.compile(r'^Grumpy_Node_(?P<variant>[a-z0-9_]+)-.+?(?P<merged>-merged)?\.bin$')


def request(url, accept='application/vnd.github+json'):
    headers = {'Accept': accept, 'User-Agent': 'grumpymesh-firmware-sync'}
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)


def fetch_releases():
    releases, page = [], 1
    while True:
        with request(f'https://api.github.com/repos/{REPO}/releases?per_page=100&page={page}') as response:
            batch = json.load(response)
        releases += batch
        if len(batch) < 100:
            return releases
        page += 1


def download(asset, dest):
    expected = (asset.get('digest') or '').removeprefix('sha256:')
    if dest.exists() and dest.stat().st_size == asset['size']:
        if not expected or hashlib.sha256(dest.read_bytes()).hexdigest() == expected:
            return False
    with request(asset['browser_download_url'], accept='application/octet-stream') as response:
        data = response.read()
    if len(data) != asset['size']:
        raise RuntimeError(f"{asset['name']}: expected {asset['size']} bytes, got {len(data)}")
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError(f"{asset['name']}: sha256 mismatch")
    dest.write_bytes(data)
    return True


def main():
    releases = [r for r in fetch_releases() if not r['draft']]
    releases.sort(key=lambda r: r['published_at'] or '', reverse=True)
    stable = [r for r in releases if not r['prerelease']]
    latest_tag = (stable or releases or [{}])[0].get('tag_name')

    manifest = {'repo': REPO, 'latest': latest_tag, 'releases': []}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for release in releases:
        tag = release['tag_name']
        release_dir = OUT_DIR / tag
        release_dir.mkdir(exist_ok=True)
        firmware = {}
        for asset in release['assets']:
            match = ASSET_RE.match(asset['name'])
            if not match:
                continue
            if download(asset, release_dir / asset['name']):
                print('downloaded', tag, asset['name'])
            key = 'mergedFile' if match['merged'] else 'file'
            firmware.setdefault(match['variant'], {})[key] = f'{SITE_PREFIX}/{tag}/{asset["name"]}'
        firmware = {variant: dict(sorted(files.items())) for variant, files in sorted(firmware.items()) if len(files) == 2}
        if not firmware:
            shutil.rmtree(release_dir)
            continue
        manifest['releases'].append({
            'tag': tag,
            'name': release['name'] or tag,
            'published': release['published_at'],
            'prerelease': release['prerelease'],
            'url': release['html_url'],
            'firmware': firmware,
        })

    # Drop mirrored releases that were deleted upstream.
    kept = {release['tag'] for release in manifest['releases']}
    for child in OUT_DIR.iterdir():
        if child.is_dir() and child.name not in kept:
            print('removing', child.name)
            shutil.rmtree(child)

    (OUT_DIR / 'releases.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f"{len(manifest['releases'])} release(s), latest {manifest['latest']}")


if __name__ == '__main__':
    sys.exit(main())
