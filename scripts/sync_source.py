#!/usr/bin/env python3
"""Mirror approved iOS releases and generate an AltStore Classic source."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import subprocess
import tempfile
import urllib.request
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'iMacintoshPlus/altstore-source'
APPS = {
    'abyssal': ('Abyssal', 'com.imacintoshplus.abyssal', 'DEEP JAR'),
    'galaxian': ('Galaxian', 'com.imacintoshplus.galaxian', 'Galaxy on Fire IPA'),
}


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True)


def download(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def inspect_ipa(data, bundle):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        plists = [n for n in names if len(PurePosixPath(n).parts) == 3
                  and n.startswith('Payload/') and n.endswith('.app/Info.plist')]
        if len(plists) != 1:
            raise ValueError('Expected exactly one application')
        if any('/_CodeSignature/' in n or '/PlugIns/' in n or
               n.endswith(('embedded.mobileprovision', '.jar', '.abyss', '.ipa')) for n in names):
            raise ValueError('IPA contains signing data, extensions, or original game archives')
        info = plistlib.loads(archive.read(plists[0]))
        if info['CFBundleIdentifier'] != bundle:
            raise ValueError('Unexpected bundle identifier')
        for key in ('CFBundleShortVersionString', 'CFBundleVersion', 'MinimumOSVersion'):
            if not isinstance(info.get(key), str) or not info[key]:
                raise ValueError('Missing IPA metadata: ' + key)
        return info


def published_releases(repository):
    pages = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repository}/releases?per_page=100'))
    return sorted((r for page in pages for r in page if not r['draft'] and r['published_at']),
                  key=lambda r: r['published_at'], reverse=True)


def mirror(app, release, asset, data):
    tag = app + '-' + release['tag_name']
    digest = hashlib.sha256(data).hexdigest()
    existing = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPOSITORY,
                               '--json', 'tagName'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    with tempfile.TemporaryDirectory() as temporary:
        ipa = Path(temporary) / asset['name']
        ipa.write_bytes(data)
        checksum = Path(temporary) / 'SHA256SUMS'
        checksum.write_text(f'{digest}  {asset["name"]}\n')
        if existing.returncode:
            notes = Path(temporary) / 'notes.txt'
            notes.write_text(f'Approved iOS release mirrored from {release["html_url"]}.\n\n'
                             'Unsigned IPA for sideloading. Original game content is not included.\n')
            subprocess.run(['gh', 'release', 'create', tag, str(ipa), str(checksum),
                            '--repo', REPOSITORY, '--target', 'main', '--title',
                            f'{APPS[app][0]} — {release["tag_name"]}', '--notes-file', str(notes),
                            '--draft'], check=True)
        central = json.loads(gh('api', f'repos/{REPOSITORY}/releases/tags/{tag}'))
        old_asset = next((a for a in central['assets'] if a['name'] == asset['name']), None)
        if old_asset is None or old_asset.get('digest') != 'sha256:' + digest:
            subprocess.run(['gh', 'release', 'upload', tag, str(ipa), str(checksum),
                            '--repo', REPOSITORY, '--clobber'], check=True)
        if central['draft']:
            subprocess.run(['gh', 'release', 'edit', tag, '--repo', REPOSITORY,
                            '--draft=false', '--prerelease=' + str(release['prerelease']).lower()], check=True)
    return f'https://github.com/{REPOSITORY}/releases/download/{urllib.parse.quote(tag, safe="")}/{asset["name"]}'


def sync():
    apps = []
    for app, (name, bundle, archive_name) in APPS.items():
        versions, seen, privacy = [], set(), {}
        for release in published_releases(f'iMacintoshPlus/{app}'):
            assets = [a for a in release['assets'] if a['name'] == f'{name}-iOS-unsigned.ipa']
            if not assets:
                continue
            if len(assets) != 1:
                raise ValueError('Expected one unsigned IPA per release')
            asset = assets[0]
            data = download(asset['browser_download_url'])
            if len(data) != asset['size']:
                raise ValueError('Downloaded size differs from release metadata')
            checksums = [a for a in release['assets'] if a['name'] == 'SHA256SUMS']
            if len(checksums) != 1:
                raise ValueError('Release must have SHA256SUMS')
            entries = [line.split() for line in download(checksums[0]['browser_download_url']).decode().splitlines()]
            expected = [parts[0] for parts in entries if len(parts) == 2 and parts[1].lstrip('*') == asset['name']]
            if expected != [hashlib.sha256(data).hexdigest()]:
                raise ValueError('Release IPA checksum mismatch')
            info = inspect_ipa(data, bundle)
            identity = (info['CFBundleShortVersionString'], info['CFBundleVersion'])
            if identity in seen:
                continue
            seen.add(identity)
            url = mirror(app, release, asset, data)
            versions.append({'version': identity[0], 'buildVersion': identity[1],
                             'date': release['published_at'], 'downloadURL': url, 'size': len(data),
                             'minOSVersion': info['MinimumOSVersion'],
                             'localizedDescription': release.get('body') or release['tag_name']})
            for key, value in info.items():
                if 'UsageDescription' in key:
                    privacy.setdefault(key, value)
        if not versions:
            raise ValueError(f'No published {name} IPA; source left unchanged')
        apps.append({'name': name, 'bundleIdentifier': bundle,
                     'developerName': 'TheWWWorm; iOS port by iMacintoshPlus',
                     'localizedDescription': f'Native iOS port of {name}. Import your own {archive_name} on first launch. Original game content is not included.',
                     'iconURL': f'https://raw.githubusercontent.com/{REPOSITORY}/main/icons/{app}.png',
                     'category': 'games', 'versions': versions,
                     'appPermissions': {'entitlements': [], 'privacy': privacy}})
    source = {'name': 'iMacintoshPlus', 'identifier': 'com.imacintoshplus.source',
              'subtitle': 'iOS ports by iMacintoshPlus',
              'website': f'https://github.com/{REPOSITORY}', 'apps': apps, 'news': []}
    (ROOT / 'source.json').write_text(json.dumps(source, indent=2) + '\n')
    print(f'AltStore Classic source updated: {len(apps)} apps')


if __name__ == '__main__':
    sync()
