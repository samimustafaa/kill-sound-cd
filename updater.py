"""GitHub Release updater. Only runs against this project's fixed GitHub repository."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

VERSION = '0.3.0'
REPOSITORY = 'samimustafaa/kill-sound-cd'
RELEASE_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
ASSET_NAME = 'CD-Kill-Announcer-Setup.exe'


def version_tuple(version):
    text = version.lstrip('v')
    if not re.fullmatch(r'\d+\.\d+\.\d+', text):
        raise ValueError('Invalid version format')
    return tuple(map(int, text.split('.')))


def get_latest():
    request = urllib.request.Request(RELEASE_API, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'CDKillAnnouncer-Updater'})
    with urllib.request.urlopen(request, timeout=12) as response:
        data = json.load(response)
    version = data['tag_name']
    if version_tuple(version) <= version_tuple(VERSION):
        return None
    asset = next((item for item in data['assets'] if item['name'] == ASSET_NAME), None)
    if not asset:
        raise RuntimeError('The latest release does not contain the Windows installer.')
    return version, asset['browser_download_url']


def install_update(asset_url, on_progress):
    """Download official release installer and launch it after the app exits."""
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Updates require the installed Windows application.')
    exe = Path(sys.executable).resolve()
    if exe.name.lower() != 'cd-kill-announcer.exe':
        raise RuntimeError('Unexpected executable name.')
    temp = Path(tempfile.mkdtemp(prefix='cd-kill-update-'))
    installer = temp / ASSET_NAME
    if not asset_url.startswith(f'https://github.com/{REPOSITORY}/releases/download/'):
        raise RuntimeError('Unexpected release URL.')
    on_progress('Downloading installer...')
    req = urllib.request.Request(asset_url, headers={'User-Agent': 'CDKillAnnouncer-Updater'})
    with urllib.request.urlopen(req, timeout=60) as remote, installer.open('wb') as out:
        while chunk := remote.read(1024 * 1024):
            out.write(chunk)
    if installer.stat().st_size < 100_000 or installer.read_bytes()[:2] != b'MZ':
        raise RuntimeError('Invalid installer download.')
    # The installer is staged in TEMP; wait until the app exits to avoid locked files.
    script = temp / 'install.ps1'
    installer_string = str(installer).replace("'", "''")
    script.write_text(f"""$ErrorActionPreference = 'Stop'
$processIdToWait = {os.getpid()}
for ($i=0; $i -lt 120; $i++) {{
  if (-not (Get-Process -Id $processIdToWait -ErrorAction SilentlyContinue)) {{ break }}
  Start-Sleep -Seconds 1
}}
if (Get-Process -Id $processIdToWait -ErrorAction SilentlyContinue) {{ exit 1 }}
Start-Process -FilePath '{installer_string}' -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS' -Wait
""", encoding='utf-8')
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
    on_progress('Installer ready. Restarting...')
    return True