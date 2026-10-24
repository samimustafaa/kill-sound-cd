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

VERSION = '0.1.2'
REPOSITORY = 'samimustafaa/kill-sound-cd'
RELEASE_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
ASSET_NAME = 'CD-Kill-Announcer-Windows.zip'


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
        raise RuntimeError('The latest release does not contain the Windows ZIP.')
    return version, asset['browser_download_url']


def install_update(asset_url, on_progress):
    """Download and stage release, then spawn independent PowerShell replacement script.

    The bundled app must be in a writable installation directory (e.g. under user's profile).
    Settings are stored separately in LOCALAPPDATA and are not changed.
    """
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Auto-install works only in the packaged Windows application.')
    exe = Path(sys.executable).resolve()
    install_dir = exe.parent
    if exe.name.lower() != 'cd-kill-announcer.exe':
        raise RuntimeError('Unexpected application executable name.')
    temp = Path(tempfile.mkdtemp(prefix='cd-kill-update-'))
    archive = temp / ASSET_NAME
    on_progress('Downloading update...')
    req = urllib.request.Request(asset_url, headers={'User-Agent': 'CDKillAnnouncer-Updater'})
    with urllib.request.urlopen(req, timeout=45) as remote, archive.open('wb') as dest:
        while True:
            chunk = remote.read(1024 * 1024)
            if not chunk:
                break
            dest.write(chunk)
    on_progress('Checking update files...')
    staged = temp / 'package'
    staged.mkdir()
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        # Safe extraction: prevent path traversal and prohibit symlink entries.
        for member in members:
            normalized = member.filename.replace('\\', '/')
            pieces = Path(normalized).parts
            if not normalized.startswith('CD-Kill-Announcer/') or '..' in pieces or member.is_dir() and len(pieces) == 0:
                raise RuntimeError('Unexpected files in update package.')
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError('Symlinks are not allowed in update package.')
            relative = Path(*pieces[1:])
            if not relative.parts:
                continue
            target = staged / relative
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, target.open('wb') as dst:
                    import shutil
                    shutil.copyfileobj(src, dst)
    if not (staged / 'CD-Kill-Announcer.exe').is_file():
        raise RuntimeError('Update executable missing.')
    # Keep script OUTSIDE installation directory: app closes before replacing files.
    script = temp / 'apply-update.ps1'
    source = str(staged).replace("'", "''")
    target = str(install_dir).replace("'", "''")
    destination_exe = str(exe).replace("'", "''")
    script.write_text(f'''$ErrorActionPreference = 'Stop'
$pidToWait = {os.getpid()}
$sourceDir = '{source}'
$targetDir = '{target}'
$exe = '{destination_exe}'
for ($i=0; $i -lt 90; $i++) {{
  if (-not (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue)) {{ break }}
  Start-Sleep -Seconds 1
}}
if (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) {{ exit 1 }}
try {{
  Copy-Item -Path (Join-Path $sourceDir '*') -Destination $targetDir -Recurse -Force -ErrorAction Stop
  Start-Process -FilePath $exe -WorkingDirectory $targetDir
}} catch {{
  Add-Content -Path (Join-Path $env:TEMP 'CDKillAnnouncer-update-error.log') -Value $_.ToString()
  exit 1
}}
''', encoding='utf-8')
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
    on_progress('Update downloaded. Restarting...')
    return True