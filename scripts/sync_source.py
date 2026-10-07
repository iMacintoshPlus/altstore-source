#!/usr/bin/env python3
"""Store approved IPAs in the repository and generate an AltStore Classic source."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import plistlib
import re
import subprocess
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
    tag = release['tag_name']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', tag):
        raise ValueError('Release tag is not a safe folder name')
    if len(data) >= 100 * 1024 * 1024:
        raise ValueError('IPA exceeds GitHub repository file size limit')
    folder = ROOT / 'ipas' / app / tag
    folder.mkdir(parents=True, exist_ok=True)
    (folder / asset['name']).write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (folder / 'SHA256SUMS').write_text(f'{digest}  {asset["name"]}\n')
    relative = (folder / asset['name']).relative_to(ROOT).as_posix()
    return f'https://raw.githubusercontent.com/{REPOSITORY}/main/{urllib.parse.quote(relative, safe="/")}'


def sync():
    apps = []
    for app, (name, bundle, archive_name) in APPS.items():
        versions, seen, privacy = [], set(), {}
        for release in published_releases(f'iMacintoshPlus/{app}'):
            prefix = {'abyssal': 'abyssal-engine-', 'galaxian': 'gof1-'}[app]
            pattern = re.escape(prefix) + r'[0-9][A-Za-z0-9._+-]*-ios\.ipa'
            assets = [a for a in release['assets'] if a['name'] == f'{name}-iOS-unsigned.ipa'
                      or re.fullmatch(pattern, a['name'])]
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
            entries = [line.split() for line in download(checksums[0]['browser_download_url'] + '?asset=' + str(checksums[0]['id'])).decode().splitlines()]
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
    source_path = ROOT / 'source.json'
    if source_path.exists():
        source = {**json.loads(source_path.read_text()), 'apps': apps}
    source_path.write_text(json.dumps(source, indent=2) + '\n')
    print(f'AltStore Classic source updated: {len(apps)} apps')


if __name__ == '__main__':
    sync()
