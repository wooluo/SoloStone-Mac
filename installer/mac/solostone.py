#!/usr/bin/env python3
"""SoloStone on macOS: what the Windows launcher does, for a Hearthstone.app.

The Windows launcher (installer/SoloStone.Launcher) copies the game, adds SoloStone's files, adds firewall rules and starts the copy.
It is a WinForms program that cannot run here, so this script does the same steps for a Mac client, with the same checks and the same
install record (install.json, schema 2) that the plugin's admission reads:

    python3 installer/mac/solostone.py setup   [--source DIR] [--root DIR] [--plugin DIR] [--references DIR] [--accept-warning]
    python3 installer/mac/solostone.py launch  [--root DIR]
    python3 installer/mac/solostone.py app     [--dest APP] [--banner JPG] [--python EXE]
    python3 installer/mac/solostone.py status  [--root DIR]
    python3 installer/mac/solostone.py uninstall [--root DIR] [--delete-saves]

The plugin is not built here. Build it from this client first (docs/BUILDING.md, steps 1 and 2); setup reads it from the plugin
project's output folder, or from --plugin.

What stays as on Windows: the copy is separate from the player's game and never changes it; the copy gets its own company name, so
its settings stay out of the player's; every recorded file is hashed before each launch; the game starts only with a launch grant
that the plugin checks; the plugin reads back the network block before it hooks anything.

What is different on macOS:
- The network block is a seatbelt profile (sandbox-exec) that denies the copy every connection, where Windows has firewall rules.
  Setup moves the game to Hearthstone.app/Contents/MacOS/Hearthstone.bin and puts a start script in its place, so the block travels
  with the copy: however the copy is started it cannot reach the internet, and without SoloStone's launcher the plugin refuses it.
  The copy is signed with this Mac's own signature first (resign_game): Blizzard's signature does not survive the move.
- The copy's application identifier changes too (Info.plist). Unity keeps the game's PlayerPrefs by that identifier, so without
  this the copy would write into the player's own Hearthstone settings.
- The loader is the macOS set (installer/loader-inputs-mac.json): Doorstop 4.6.0, BepInEx 5.4.23.6 and the Linux/macOS class libraries.
- No window, no update check and no save backups.
"""
import argparse
import hashlib
import json
import os
import plistlib
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

sys.dont_write_bytecode = True
INSTALLER = Path(__file__).resolve().parents[1]
REPOSITORY = INSTALLER.parent
sys.path.insert(0, str(INSTALLER))
import loader_inputs  # noqa: E402

HOME = Path.home()
DEFAULT_ROOT = HOME / 'Library' / 'Application Support' / 'SoloStone'
DEFAULT_SOURCE = Path('/Applications/Hearthstone')
BUNDLE = 'Hearthstone.app'
INSIDE = BUNDLE + '/Contents'
GAME = INSIDE + '/MacOS/Hearthstone'              # the start script, in the game's place (the bundle's executable keeps its name)
GAME_BINARY = INSIDE + '/MacOS/Hearthstone.bin'   # the game itself
INFO_PLIST = INSIDE + '/Info.plist'
DATA = INSIDE + '/Resources/Data'
MANAGED = DATA + '/Managed'
PINNED = [GAME_BINARY, INSIDE + '/Frameworks/UnityPlayer.dylib', MANAGED + '/Assembly-CSharp.dll', MANAGED + '/blizzard.bgsclient.dll']
COMPANY_FILES = [DATA + '/globalgamemanagers', DATA + '/app.info']
OLD_COMPANY, NEW_COMPANY = b'Blizzard Entertainment', b'SoloStone Offline Mods'
OLD_IDENTIFIER = 'unity.' + OLD_COMPANY.decode('ascii') + '.Hearthstone'
NEW_IDENTIFIER = 'unity.' + NEW_COMPANY.decode('ascii') + '.Hearthstone'
# The plugin is compiled against these (tests/run.py CLIENT_FILES), so the copy must be the client they came from.
REFERENCE_FILES = ['Assembly-CSharp.dll', 'blizzard.bgsclient.dll', 'blizzard.bgssdk.dll', 'Blizzard.T5.Core.dll', 'Blizzard.T5.JobQueue.dll',
                   'Blizzard.T5.MiniJSON.dll', 'Blizzard.T5.ServiceLocator.dll', 'UnityEngine.dll', 'UnityEngine.CoreModule.dll',
                   'UnityEngine.IMGUIModule.dll', 'UnityEngine.InputLegacyModule.dll', 'UnityEngine.PhysicsModule.dll',
                   'UnityEngine.ScreenCaptureModule.dll']
PLUGIN_FILES = ['SoloStone.Plugin.dll', 'SabberStoneCore.dll']
LOADER_LIST = INSTALLER / 'loader-inputs-mac.json'
LOADER_CACHE = HOME / 'Library' / 'Caches' / 'SoloStone' / 'loader'
TESTED_CLIENT = INSTALLER / 'tested-client.json'
SAVES = DEFAULT_ROOT / 'saves'   # PublicNames.SaveRoot(HostPlatform.LocalApplicationData())
MARKER = '.solostone-client'
RECORD = 'install.json'
PENDING = 'install.pending.json'
CLIENT = 'client'
STAGING = 'client.solostone-staging'
SANDBOX = 'solostone-offline.sb'
SANDBOX_PROFILE = '(version 1)\n(allow default)\n(deny network*)\n'
GAME_UID = 'SoloStone'   # ClientIdentity.GameUid: the game's -uid, so its options folder is its own
# The double-click app (`app`): an AppleScript applet in /Applications that runs this script's launch and shows what it answers.
APP_IDENTIFIER = 'local.solostone.launcher'
APP_NAME = 'SoloStone'
APP_DEST = Path('/Applications/SoloStone.app')
BANNER = REPOSITORY / 'docs' / 'images' / 'banner.jpg'
ICON_SIZES = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]
# Left out of the copy, as on Windows (Installer.Excluded): folders at the top of the client, and files by name.
EXCLUDED_FOLDERS = {'bepinex', 'logs', 'reports'}
EXCLUDED_FILES = {'client.config', 'winhttp.dll', 'doorstop_config.ini', '.doorstop_version', '.solostone-client', '.solostone-app', SANDBOX}
PROBES = [('223.5.5.5', 443), ('1.1.1.1', 443), ('www.baidu.com', 443)]
EXIT_TEXT = {
    40: 'The plugin refused to start: this copy was not started by SoloStone.',
    41: 'The plugin reached the internet and stopped. The copy is not offline; do not play it until its network block holds.',
    42: 'This plugin does not fit this Hearthstone client. See reports/client-check.txt and BepInEx/LogOutput.log.',
    44: 'Startup timed out.',
    45: 'A match could not be started.',
    47: "SoloStone's network block is missing, so the plugin stopped before it touched anything.",
    48: 'The launch was refused.',
    49: 'Setup failed inside the plugin: a save or data file could not be read or written.',
    50: 'Startup error inside the plugin. See BepInEx/LogOutput.log.',
}
# The copy's start script (the bundle's executable). Doorstop's settings go in as environment variables, as in BepInEx's run_bepinex.sh.
START_SCRIPT = '''#!/bin/sh
# SoloStone's copy of Hearthstone. SoloStone's macOS launcher (installer/mac/solostone.py) put this script in the game's place and
# moved the game to Hearthstone.bin. The game starts only inside the sandbox that denies it every network connection, with SoloStone's
# loader attached: the copy stays offline however it is started, as the firewall rules keep the Windows copy offline. A start without
# SoloStone's launcher ends in the plugin, which refuses it (exit 40).
set -e
# This script sits at <copy>/Hearthstone.app/Contents/MacOS/Hearthstone: two levels up is Contents' parent, three the copy's root.
CONTENTS="$(cd "$(dirname "$0")/.." && pwd -P)"
ROOT="$(cd "$CONTENTS/../.." && pwd -P)"
if [ ! -f "$ROOT/%(sandbox)s" ] || [ ! -x /usr/bin/sandbox-exec ]; then
    echo "SoloStone: the files that keep this copy offline are missing, so it was not started." >&2
    exit 48
fi
cd "$ROOT"
export DOORSTOP_ENABLED=1
export DOORSTOP_TARGET_ASSEMBLY="$ROOT/BepInEx/core/BepInEx.Preloader.dll"
export DOORSTOP_IGNORE_DISABLED_ENV=0
export DOORSTOP_MONO_DLL_SEARCH_PATH_OVERRIDE="$ROOT/BepInEx/unstripped_corlib"
export DOORSTOP_MONO_DEBUG_ENABLED=0
export DYLD_LIBRARY_PATH="$ROOT"
# Apple Silicon runs a universal game as arm64 through arch, which drops DYLD_INSERT_LIBRARIES: it goes back in through -e.
export ARCHPREFERENCE="arm64,x86_64"
exec /usr/bin/sandbox-exec -f "$ROOT/%(sandbox)s" /usr/bin/arch -e DYLD_INSERT_LIBRARIES=libdoorstop.dylib \\
    "$CONTENTS/MacOS/Hearthstone.bin" "$@"
''' % {'sandbox': SANDBOX}


class Refused(Exception):
    """The launch or the setup is refused, and nothing was started."""


# ---- paths and files

def canonical(path):
    """The path as the plugin's Safety.Canonical keeps it: full, no trailing slash."""
    return os.path.abspath(os.path.expanduser(str(path))).rstrip('/') or '/'


def within(path, parent):
    """Spelling only: path is parent or lies in it."""
    path, parent = canonical(path).lower(), canonical(parent).lower()
    return path == parent or path.startswith(parent.rstrip('/') + '/')


def file_hash(path):
    """SHA-256 as the plugin's Hashes.FileHash spells it: uppercase hex."""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest().upper()


def same_hash(left, right):
    return bool(left) and bool(right) and left.lower() == right.lower()


def files_under(folder):
    """Relative ('/'-separated) paths of the files under folder. Refuses a link anywhere on the way, as the plugin does."""
    for current, names, files in os.walk(folder):
        for name in names + files:
            if os.path.islink(os.path.join(current, name)):
                raise Refused('linked files and folders are not supported: %s' % os.path.join(current, name))
        for name in files:
            yield os.path.relpath(os.path.join(current, name), folder).replace(os.sep, '/')


def under(folder, relative):
    return Path(folder).joinpath(*relative.split('/'))


def tail(path, lines=12):
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            return [line.rstrip() for line in handle.read().splitlines()[-lines:]]
    except OSError:
        return []


def run(command):
    return subprocess.run(command, capture_output=True, text=True)


def log_line(root, message):
    try:
        with open(root / 'installer.log', 'a', encoding='utf-8') as handle:
            handle.write(time.strftime('%Y-%m-%dT%H:%M:%S ') + message + '\n')
    except OSError:
        pass


def user_names():
    """What the plugin may answer for the player on macOS: Mono reads USER, and falls back to the password file."""
    names = {os.environ.get('USER'), pwd.getpwuid(os.getuid()).pw_name}
    return {name for name in names if name}


# ---- the player's game

def excluded(relative):
    top, name = relative.split('/')[0].lower(), relative.rsplit('/', 1)[-1].lower()
    return top in EXCLUDED_FOLDERS or name in EXCLUDED_FILES or name.endswith('.log')


def company_problem(data):
    """Null when the file is fit to patch: one old name and no new one (ClientIdentity.Problem)."""
    old, current = data.count(OLD_COMPANY), data.count(NEW_COMPANY)
    if old != 1:
        return '%d matches of the company name, expected 1' % old
    return 'it already holds SoloStone\'s name' if current else None


def patch_company(data):
    problem = company_problem(data)
    if problem:
        raise Refused(problem)
    at = data.index(OLD_COMPANY)
    return data[:at] + NEW_COMPANY + data[at + len(OLD_COMPANY):]   # same length, so nothing else in the file moves


def patch_identifier(data):
    """Info.plist with the copy's own application identifier: Unity keeps PlayerPrefs by it."""
    old, new = OLD_IDENTIFIER.encode('ascii'), NEW_IDENTIFIER.encode('ascii')
    if data.count(old) != 1 or data.count(new):
        raise Refused('%d matches of the application identifier, expected 1' % data.count(old))
    at = data.index(old)
    return data[:at] + new + data[at + len(old):]


def carries_own_names(data, identifier=False):
    """A settings file as the copy needs it: one of SoloStone's names and none of the player's game's. The two data files carry the
    company (the Windows rule, ClientIdentity.Verify); Info.plist carries the application identifier."""
    new, old = (NEW_IDENTIFIER.encode('ascii'), OLD_IDENTIFIER.encode('ascii')) if identifier else (NEW_COMPANY, OLD_COMPANY)
    return data.count(new) == 1 and not data.count(old)


def client_version(source):
    with open(source / INFO_PLIST, 'rb') as handle:
        version = plistlib.load(handle).get('CFBundleVersion')
    parts = (version or '').split('.')
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        raise Refused('SoloStone could not read the Hearthstone version from %s.' % (source / INFO_PLIST))
    return version


def version_verdict(version, accept_warning, tested):
    """'allow' or 'warning' for a client this release runs on; Refused for one it does not (ClientPolicy.Decide)."""
    mine, theirs = version.split('.')[:2], tested['version'].split('.')[:2]
    if mine == theirs:
        return 'allow'
    if mine[0] != theirs[0]:
        raise Refused('This SoloStone release runs on Hearthstone %s only, and your client is Hearthstone %s.' % (theirs[0], mine[0]))
    if not accept_warning:
        raise Refused('SoloStone has not been tested with this Hearthstone version (%s; tested %s build %s). Matches can break. Run '
                      'setup again with --accept-warning to play anyway.' % (version, tested['version'], tested['build']))
    print('WARNING: SoloStone has not been tested with Hearthstone %s (tested %s build %s).' % (version, tested['version'], tested['build']))
    return 'warning'


def reference_problem(source, references):
    """Null when the plugin's reference libraries are this client's, so the plugin was built from this client."""
    for name in REFERENCE_FILES:
        if not (references / name).is_file():
            return ('%s is missing from %s, so SoloStone cannot tell which client the plugin was built from. Copy this client\'s '
                    'libraries there first (docs/BUILDING.md, step 1).' % (name, references))
        if not same_hash(file_hash(references / name), file_hash(source / MANAGED / name)):
            return ('%s is not this client\'s library, so the plugin was built from another Hearthstone. Build it from this client '
                    'first (docs/BUILDING.md, steps 1 and 2).' % name)
    return None


def source_problem(source, references):
    """Read-only: the first problem in a Hearthstone folder, or null."""
    for relative in [GAME, INFO_PLIST] + COMPANY_FILES + PINNED[1:]:
        if not (source / relative).is_file():
            return '%s is not a Hearthstone folder: %s is missing.' % (source, relative)
    for relative in COMPANY_FILES:
        with open(source / relative, 'rb') as handle:
            problem = company_problem(handle.read())
        if problem:
            return "This client's files aren't what SoloStone expects (%s: %s)." % (relative, problem)
    with open(source / INFO_PLIST, 'rb') as handle:
        count = handle.read().count(OLD_IDENTIFIER.encode('ascii'))
    if count != 1:
        return "This client's files aren't what SoloStone expects (%s: %d matches of the application identifier, expected 1)." % (
            INFO_PLIST, count)
    list(files_under(source))
    return reference_problem(source, references)


# What an uninstall leaves on purpose, so a new setup finds it in the folder: SoloStone's own log, and the saves when the root is
# the default one (elsewhere the saves have their own place).
KEPT_BY_UNINSTALL = ('installer.log', 'saves')


def root_problem(root, source):
    """Where SoloStone may be installed: a new folder, or one with nothing left of an earlier install in it (its log and the saves
    an uninstall keeps may stay), and never in or around a Hearthstone."""
    if within(root, source) or within(source, root):
        return 'SoloStone cannot be installed in or around your Hearthstone folder. Choose another folder with --root.'
    if root.exists():
        left = sorted(entry.name for entry in root.iterdir()
                      if entry.name not in KEPT_BY_UNINSTALL or (entry.name == 'saves' and not entry.is_dir()))
        if left:
            return '%s still holds %s from an earlier install. Uninstall SoloStone first, or choose another folder with --root.' % (
                root, ', '.join(left[:5]))
    if not root.parent.is_dir():
        return '%s does not exist. Create it first, or choose another folder with --root.' % root.parent
    return None


def idle_problem(processes, client=None):
    """Null when nothing is in the way: Battle.net and its Agent are closed, and no Hearthstone is running. `processes` are (state,
    executable) as ps lists them. One the kernel is already tearing down (E) or has finished (Z) is not a running game."""
    live = [path.strip() for state, path in processes if 'E' not in state and 'Z' not in state]
    names = {os.path.basename(path).lower() for path in live}
    if 'battle.net' in names:
        return 'Battle.net is running. Quit Battle.net before using SoloStone.'
    if 'agent' in names:
        return 'Battle.net is still closing. Wait a moment, then try again.'
    for path in live:
        if '/Hearthstone.app/Contents/MacOS/' in path:
            if client and within(path, client):
                return "SoloStone's game is already running. Quit it first."
            return 'Hearthstone is running. Quit it first.'
    return None


def running_problem(client=None):
    listing = [line.split(None, 1) for line in run(['ps', '-axo', 'stat=,comm=']).stdout.splitlines() if line.strip()]
    return idle_problem([(parts[0], parts[-1]) for parts in listing], client)


def reaches(host, port, profile=None):
    command = ['/usr/bin/sandbox-exec', '-f', str(profile)] if profile else []
    return subprocess.run(command + ['/usr/bin/nc', '-z', '-G', '3', host, str(port)], capture_output=True).returncode == 0


def block_problem(profile):
    """A sandbox that lets nothing through answers null. Null as well when this Mac reaches none of the probe addresses right now:
    there is nothing to compare with, and the plugin reads the sandbox itself before it starts."""
    for host, port in PROBES:
        if reaches(host, port):
            break
    else:
        return None
    for host, port in PROBES:
        if reaches(host, port, profile):
            return 'the sandbox lets a connection to %s through' % host
    return None


# ---- setup

def fetch_loader():
    """The macOS loader files, downloaded, hashed and assembled by the project's own tool (which caches each archive by its hash)."""
    folder, data = LOADER_CACHE / 'ready', loader_inputs.load(LOADER_LIST)
    for attempt in range(8):
        try:
            loader_inputs.fetch(folder, LOADER_CACHE, data)
            return folder
        except loader_inputs.Refused as error:
            if attempt == 7 or 'download failed' not in str(error):
                raise Refused('loader: %s' % error)
            time.sleep(2 + 2 * attempt)
    return folder


def copy_game(source, stage):
    """The game without the excluded entries, in a folder that does not exist yet. APFS clones it file by file (copy on write:
    seconds, and no room of its own); a volume that cannot clone gets a full copy. Returns what was left out."""
    for option in ('-cR', '-R'):
        shutil.rmtree(stage, ignore_errors=True)
        copied = run(['cp', option, str(source), str(stage)])
        if copied.returncode == 0:
            break
    else:
        raise Refused('your Hearthstone could not be copied: %s' % copied.stderr.strip())
    left_out = []
    for relative in sorted(files_under(stage), key=len):
        if excluded(relative) and under(stage, relative).exists():
            path = under(stage, relative)
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            left_out.append(relative)
    return left_out


def add_payload(stage, plugin, loader, client):
    """SoloStone's files into the copy, and the payload half of the install record."""
    data = loader_inputs.load(LOADER_LIST)
    shipped = [entry['path'] for entry in data['files']]
    for entry in data['files']:
        under(stage, entry['path']).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(under(loader, entry['path']), under(stage, entry['path']))
    (stage / 'BepInEx' / 'plugins').mkdir(parents=True, exist_ok=True)
    for name in PLUGIN_FILES:
        shutil.copyfile(plugin / name, stage / 'BepInEx' / 'plugins' / name)
    shutil.copyfile(REPOSITORY / 'client.config', stage / 'client.config')
    # Read only by a start that comes without the launcher's environment; the paths are this install's own.
    (stage / 'doorstop_config.ini').write_text(
        '[General]\nenabled=true\ntarget_assembly=%s/BepInEx/core/BepInEx.Preloader.dll\nredirect_output_log=false\n'
        'ignore_disable_switch=false\n\n[UnityMono]\ndll_search_path_override=%s/BepInEx/unstripped_corlib\n' % (client, client),
        encoding='ascii')
    (stage / SANDBOX).write_text(SANDBOX_PROFILE, encoding='ascii')
    under(stage, GAME).write_text(START_SCRIPT, encoding='ascii')
    os.chmod(under(stage, GAME), 0o755)
    shipped += ['BepInEx/plugins/' + name for name in PLUGIN_FILES] + ['client.config', 'doorstop_config.ini', SANDBOX, GAME]
    files = {relative: file_hash(under(stage, relative)) for relative in shipped}
    return {'schema': 1, 'bepInExVersion': data['bepInExVersion'], 'doorstopVersion': data['doorstopVersion'],
            'sourceRevision': source_revision(), 'engineSha256': files['BepInEx/plugins/SabberStoneCore.dll'],
            'pluginSha256': files['BepInEx/plugins/SoloStone.Plugin.dll'], 'files': files}


def source_revision():
    revision = subprocess.run(['git', '-C', str(REPOSITORY), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return revision.stdout.strip() if revision.returncode == 0 else 'unknown'


def release_version():
    try:
        return json.loads((REPOSITORY / 'release-manifest.json').read_text(encoding='utf-8'))['latest']['version']
    except (OSError, KeyError, ValueError):
        return None


def install_record(root, source, client, install_id, version, files, payload, identity):
    """What the plugin's InstallRecord reads: schema 2, whole numbers only, every path in full."""
    return {'schema': 2, 'installId': install_id, 'root': canonical(root), 'source': canonical(source), 'clientPath': canonical(client),
            'clientVersion': version, 'releaseVersion': release_version(), 'files': files, 'payload': payload, 'identityFiles': identity}


def resign_game(copy):
    """The copy's game under this Mac's own (ad hoc) signature, after its names were changed and before the game is moved aside.
    Blizzard's signature seals the bundle's Info.plist, and macOS lets the signed game start under its own name although that plist
    changed, but not under another one: the kernel kills it at start ("The code contains a Team ID, but validating its signature
    failed"). A copy SoloStone has changed is not Blizzard's unchanged program anyway.
    The game is signed apart from its bundle: codesign refuses anything inside it, one of its plug-ins being no bundle it accepts. The
    file in the copy keeps its place and mode, so only its bytes change; they are recorded, and checked before every launch."""
    game = copy / GAME
    with tempfile.TemporaryDirectory() as apart:
        lone = Path(apart) / 'Hearthstone'
        shutil.copyfile(game, lone)
        signed = run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(lone)])
        if signed.returncode != 0:
            raise Refused('the copy could not be signed for this Mac: %s' % signed.stderr.strip())
        shutil.copyfile(lone, game)


def setup(source, root, plugin, references, accept_warning):
    source, root = Path(canonical(source)), Path(canonical(root))
    problem = running_problem() or source_problem(source, references) or root_problem(root, source)
    if problem:
        raise Refused(problem)
    version = client_version(source)
    verdict = version_verdict(version, accept_warning, json.loads(TESTED_CLIENT.read_text(encoding='utf-8')))
    for name in PLUGIN_FILES:
        if not (plugin / name).is_file():
            raise Refused('%s is not built. Build the plugin first (docs/BUILDING.md, step 2), or pass --plugin.' % (plugin / name))
    loader = fetch_loader()
    print('1. Checked your Hearthstone: %s, version %s (%s).' % (source, version, verdict))
    print('2. Checked the copy location: %s' % (root / CLIENT))
    stage, client = root / STAGING, root / CLIENT
    try:
        root.mkdir(parents=True, exist_ok=True)
        left_out = copy_game(source, stage)
        for relative in PINNED:
            copied = GAME if relative == GAME_BINARY else relative   # the game still has its name at this point
            if not same_hash(file_hash(under(stage, copied)), file_hash(under(source, copied))):
                raise Refused('%s changed while it was copied. Quit Battle.net, then run setup again.' % copied)
        print('3. Copied your Hearthstone and checked every recorded file (%d entries left out).' % len(left_out))
        install_id = uuid.uuid4().hex
        (stage / MARKER).write_text(install_id, encoding='ascii')
        # The copy's own names: the company in the two data files, the application identifier in Info.plist.
        identity = {}
        for relative in COMPANY_FILES + [INFO_PLIST]:
            path = under(stage, relative)
            path.write_bytes(patch_identifier(path.read_bytes()) if relative == INFO_PLIST else patch_company(path.read_bytes()))
            identity[relative] = file_hash(path)
        resign_game(stage)
        os.rename(under(stage, GAME), under(stage, GAME_BINARY))
        files = {relative: file_hash(under(stage, relative)) for relative in PINNED}
        payload = add_payload(stage, plugin, loader, client)
        record = install_record(root, source, client, install_id, version, files, payload, identity)
        record['verdict'] = verdict   # the launcher's own note: an accepted warning lasts as long as the install
        (root / PENDING).write_text(json.dumps(record, indent=1, sort_keys=True), encoding='utf-8')
        (root / 'reports').mkdir(exist_ok=True)
        os.rename(stage, client)
        os.rename(root / PENDING, root / RECORD)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        if (root / PENDING).is_file():
            (root / PENDING).unlink()
        raise
    print("4. Added SoloStone's offline files and gave the copy its own identity.")
    block = block_problem(client / SANDBOX)
    print('5. %s' % ('Kept the copy from the internet: inside its sandbox it reaches nothing.' if not block else
                     'WARNING, the copy may reach the internet: %s. Do not play it; run setup again.' % block))
    log_line(root, 'setup: Hearthstone %s from %s, verdict %s' % (version, source, verdict))
    return 0 if not block else 1


# ---- verify, launch, remove

def load_record(root):
    """The install record and the copy it names, both as the plugin accepts them."""
    if not (root / RECORD).is_file():
        raise Refused('SoloStone is not installed in %s. Run setup first.' % root)
    record = json.loads((root / RECORD).read_text(encoding='utf-8'))
    client = Path(canonical(record.get('clientPath') or ''))
    if record.get('schema') != 2 or len(record.get('installId') or '') != 32 or not within(client, root / CLIENT):
        raise Refused("SoloStone's installation record is invalid. No files were removed or launched.")
    marker = client / MARKER
    if not marker.is_file() or marker.read_text(encoding='ascii', errors='replace').strip() != record['installId']:
        raise Refused("SoloStone's copy marker is missing or does not match. No files were removed or launched.")
    return record, client


def verify_copy(client, record):
    """Every recorded file as it was, and the copy's own names in place: the problems, empty when all is well."""
    problems = []

    def changed(files, what):
        for relative, expected in sorted(files.items()):
            if relative.startswith('/') or '..' in relative.split('/') or not under(client, relative).is_file() \
                    or not same_hash(file_hash(under(client, relative)), expected):
                problems.append('%s: %s' % (what, relative))
    changed(record['files'], 'file check failed')
    payload = record['payload']
    changed(payload['files'], 'SoloStone file check failed')
    if not same_hash(payload['files']['BepInEx/plugins/SoloStone.Plugin.dll'], payload['pluginSha256']) \
            or not same_hash(payload['files']['BepInEx/plugins/SabberStoneCore.dll'], payload['engineSha256']):
        problems.append('SoloStone payload does not match its record')
    for relative, expected in sorted(record['identityFiles'].items()):
        path = under(client, relative)
        data = path.read_bytes() if path.is_file() else b''
        if not carries_own_names(data, relative == INFO_PLIST) or not same_hash(hashlib.sha256(data).hexdigest().upper(), expected):
            problems.append("the copy's settings files no longer carry SoloStone's own name: %s" % relative)
    return problems


def grant(root, run_id, verdict, version):
    """One launch's admission (LaunchGrant.Check reads it back): five lines, the last one empty."""
    reports = root / 'reports'
    reports.mkdir(exist_ok=True)
    (reports / 'launch-grant.txt').write_text('schema=1\nrunId=%s\nverdict=%s\nclientVersion=%s\n' % (run_id, verdict, version),
                                              encoding='ascii')


def start_game(root, client, run_id, extra=(), console=None):
    """The copy's start script with this launch's selectors. The game, the loader and Unity print to their console while they run (a
    Windows game has none): it goes to a file (`console`), and its last lines are shown when a start fails."""
    command = [str(client / GAME), '-launch', '-uid', GAME_UID, '-hs-native-ui-run', run_id, '-hs-installer-root', canonical(root)]
    return subprocess.Popen(command + list(extra) + ['-logFile', str(root / 'reports' / 'Unity.log')], cwd=str(client),
                            stdout=console, stderr=subprocess.STDOUT if console is not None else None)


def plugin_confirmed(root, run_id):
    """'yes' once the plugin confirmed this launch, 'no' once it confirmed it as another user, else None: not yet."""
    marker = root / 'reports' / 'plugin-loaded.txt'
    if not marker.is_file():
        return None
    lines = marker.read_text(encoding='utf-8', errors='replace').splitlines()
    if len(lines) < 2 or lines[0] != run_id:
        return None
    return 'yes' if lines[1] in user_names() else 'no'


def wait_for_plugin(root, run_id, game=None, seconds=120):
    """'yes' once the plugin confirmed this launch, 'no' once it confirmed it as another user, 'exited' when the game is gone before
    it did, and None when time ran out."""
    deadline = time.time() + seconds
    while True:
        answer = plugin_confirmed(root, run_id)
        if answer:
            return answer
        if game is not None and game.poll() is not None:
            return 'exited'
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def relaunch_request(run_id):
    """The save switch a finished run asked for (exit 46), or null: its token. The run id must be the one that just ended."""
    try:
        fields = {child.tag: child.text for child in ET.parse(SAVES / 'profile-switch.xml').getroot()}
    except (OSError, ET.ParseError):
        return None
    if fields.get('Version') != '1' or fields.get('RunId') != run_id or not fields.get('Token') or not fields.get('TargetId'):
        return None
    return fields['Token']


def report_startup(root, client):
    for path in (root / 'reports' / 'client-check.txt', client / 'BepInEx' / 'LogOutput.log', root / 'reports' / 'game-console.log'):
        lines = tail(path)
        if lines:
            print('\nLast lines of %s:' % path)
            print('\n'.join(lines))


def launch(root):
    record, client = load_record(root)
    problem = running_problem(client)
    if problem:
        raise Refused(problem)
    problems = verify_copy(client, record)
    if problems:
        raise Refused('%s\nOnly the unchanged client and SoloStone\'s own files can be used. Run setup again to repair the copy.'
                      % '\n'.join(problems[:5]))
    block = block_problem(client / SANDBOX)
    if block:
        raise Refused('SoloStone cannot keep the copy offline (%s), so the game was not started.' % block)
    for stale in ('plugin-loaded.txt', 'save-bound.txt', 'save-bound.txt.tmp'):
        if (root / 'reports' / stale).is_file():
            (root / 'reports' / stale).unlink()
    # What the game, the loader and Unity print goes to a file, not to this terminal: it is long, and its last lines matter when a start fails.
    console = open(root / 'reports' / 'game-console.log', 'ab')
    console.write(('\n=== %s ===\n' % time.strftime('%Y-%m-%d %H:%M:%S')).encode('ascii'))
    try:
        return play(root, client, record, console)
    finally:
        console.close()


def play(root, client, record, console, extra=()):
    """One launch, and the save switch a finished run may ask for (exit 46), which starts the next launch itself."""
    while True:
        run_id = str(uuid.uuid4()).upper()
        grant(root, run_id, record.get('verdict') or 'allow', record['clientVersion'])
        game = start_game(root, client, run_id, extra, console)
        answer = wait_for_plugin(root, run_id, game)
        if answer != 'yes':
            if answer != 'exited':
                game.kill()
            game.wait()
            if (root / 'reports' / 'launch-grant.txt').is_file():
                (root / 'reports' / 'launch-grant.txt').unlink()
            print({
                'exited': "SoloStone's game stopped before the plugin confirmed its start (exit code %s)." % game.returncode,
                'no': "SoloStone's game confirmed its start as another user, so it was stopped.",
            }.get(answer, "SoloStone's game did not confirm its start in time, so it was stopped."))
            log_line(root, 'run %s: no start (%s)' % (run_id, answer or 'timeout'))
            report_startup(root, client)
            return 1
        (root / 'reports' / 'launch-grant.txt').unlink()
        code = game.wait()
        log_line(root, 'run %s exit %s' % (run_id, code))
        if code == 46:
            token = relaunch_request(run_id)
            if token:
                extra = ('-hs-profile-previous-run', run_id, '-hs-profile-relaunch-token', token)
                continue
        if code == 0:
            return 0
        print(EXIT_TEXT.get(code, 'The game stopped with exit code %s.' % code))
        report_startup(root, client)
        return 1


def status(root):
    if not (root / RECORD).is_file():
        print('SoloStone is not installed in %s.' % root)
        return 1
    record, client = load_record(root)
    print('SoloStone %s in %s' % (record.get('releaseVersion') or '?', root))
    print('  copy:    %s' % client)
    print('  client:  Hearthstone %s, copied from %s' % (record['clientVersion'], record['source']))
    print('  saves:   %s' % SAVES)
    problems = verify_copy(client, record)
    for line in problems[:8]:
        print('  PROBLEM: %s' % line)
    print('  copy:    %s' % ('every recorded file is as it was' if not problems else 'changed, so launch refuses it; run setup again'))
    return 1 if problems else 0


def uninstall(root, delete_saves):
    record, client = load_record(root)
    problem = running_problem(client)
    if problem:
        raise Refused(problem)
    shutil.rmtree(client)   # the copy marker goes with it
    for stale in (root / 'reports', root / RECORD, root / PENDING):
        shutil.rmtree(stale, ignore_errors=True) if stale.is_dir() else stale.unlink(missing_ok=True)
    # What the copy wrote under SoloStone's own names only (ClientIdentity.RemoveTraces): its options (-uid), its PlayerPrefs and
    # its data folder (both by its application identifier). Never the player's game's.
    for folder in (HOME / 'Library' / 'Preferences' / 'Blizzard' / 'Hearthstone' / GAME_UID,
                   HOME / 'Library' / 'Application Support' / NEW_IDENTIFIER):
        shutil.rmtree(folder, ignore_errors=True)
    run(['defaults', 'delete', NEW_IDENTIFIER])
    (HOME / 'Library' / 'Preferences' / (NEW_IDENTIFIER + '.plist')).unlink(missing_ok=True)
    if delete_saves:
        shutil.rmtree(SAVES, ignore_errors=True)
    print('SoloStone was uninstalled. Your saves are kept (uninstall --delete-saves removes them too).' if not delete_saves
          else 'SoloStone and its saves were uninstalled.')
    return 0


# ---- the double-click app

LAUNCH_APPLET = '''-- SoloStone's app: double-click it (or click its icon again while it is running) to start the copy.
-- Every check stays in the launcher script (installer/mac/solostone.py); this app only shows what it answers,
-- and stays quiet when a game ends normally.
on run
    playSoloStone()
end run

on reopen
    playSoloStone()
end reopen

on playSoloStone()
    set repo to %(repository)s
    set py to %(python)s
    set launcher to %(launcher)s
    try
        set said to do shell script "cd " & quoted form of repo & " && " & quoted form of py & " " & quoted form of launcher & " launch 2>&1"
        if said is not "" then
            soloStoneSays(said)
        end if
    on error said
        soloStoneSays(said)
    end try
    tell me to quit
end playSoloStone

on soloStoneSays(said)
    display alert "SoloStone" message said buttons {"OK"} default button "OK"
end soloStoneSays
'''


def applescript_string(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def iconset_entries():
    return [('icon_%dx%d%s.png' % (px, px, '@2x' if s == 2 else ''), px * s) for px, s in ICON_SIZES]


def make_app(dest, banner, python):
    dest, banner = Path(dest), Path(banner)
    if dest.exists():
        try:
            identifier = plistlib.loads((dest / 'Contents' / 'Info.plist').read_bytes()).get('CFBundleIdentifier')
        except (OSError, ValueError):
            identifier = None
        if identifier != APP_IDENTIFIER:
            raise Refused("%s exists and is not SoloStone's launcher app. Choose another place with --dest." % dest)
        shutil.rmtree(dest)
    if not banner.is_file():
        raise Refused('the image the icon is cut from is missing: %s' % banner)
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise Refused('the app icon needs the Pillow package: python3 -m pip install pillow')
    script = LAUNCH_APPLET % {'repository': applescript_string(str(REPOSITORY)),
                              'python': applescript_string(python),
                              'launcher': applescript_string(str(Path(__file__).resolve()))}
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / 'launcher.applescript'
        source.write_text(script, encoding='utf-8')
        compiled = run(['/usr/bin/osacompile', '-o', str(dest), str(source)])
        if compiled.returncode != 0 or not dest.is_dir():
            raise Refused('the app could not be compiled: %s' % compiled.stderr.strip())
        picture = Image.open(banner).convert('RGB')
        box = (190, 200, 890, 900)
        if not (0 <= box[0] < box[2] <= picture.width and 0 <= box[1] < box[3] <= picture.height):
            side = min(picture.size)
            box = ((picture.width - side) // 2, (picture.height - side) // 2,
                   (picture.width + side) // 2, (picture.height + side) // 2)
        side = 1024
        square = picture.crop(box).resize((side, side), Image.LANCZOS)
        corners = Image.new('L', (side, side), 0)
        ImageDraw.Draw(corners).rounded_rectangle((0, 0, side - 1, side - 1), radius=round(side * 0.224), fill=255)
        iconset = Path(work) / 'applet.iconset'
        iconset.mkdir()
        for name, pixels in iconset_entries():
            entry = Image.new('RGBA', (pixels, pixels))
            entry.paste(square.resize((pixels, pixels), Image.LANCZOS).convert('RGBA'), (0, 0),
                        corners.resize((pixels, pixels), Image.LANCZOS))
            entry.save(iconset / name)
        icon = run(['/usr/bin/iconutil', '-c', 'icns', str(iconset),
                    '-o', str(dest / 'Contents' / 'Resources' / 'applet.icns')])
        if icon.returncode != 0:
            raise Refused('the app icon could not be built: %s' % icon.stderr.strip())
    info = plistlib.loads((dest / 'Contents' / 'Info.plist').read_bytes())
    info['CFBundleIdentifier'], info['CFBundleName'], info['LSUIElement'] = APP_IDENTIFIER, APP_NAME, True
    (dest / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(info))
    signed = run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(dest)])
    if signed.returncode != 0:
        raise Refused('the app could not be signed: %s' % signed.stderr.strip())
    print("Built %s. Double-click it to play; it shows the launcher's answer when a start is refused." % dest)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    setup_parser = sub.add_parser('setup', help='copy the game and add SoloStone\'s files')
    setup_parser.add_argument('--source', default=str(DEFAULT_SOURCE), help='your Hearthstone folder (default /Applications/Hearthstone)')
    setup_parser.add_argument('--root', default=str(DEFAULT_ROOT))
    setup_parser.add_argument('--plugin', default=str(REPOSITORY / 'plugin' / 'bin' / 'Debug' / 'net48'), help='the built plugin\'s folder')
    setup_parser.add_argument('--references', default=str(REPOSITORY / 'references' / 'game'),
                              help='the client libraries the plugin was built from')
    setup_parser.add_argument('--accept-warning', action='store_true', help='play on a Hearthstone version SoloStone was not tested with')
    sub.add_parser('launch', help='start SoloStone').add_argument('--root', default=str(DEFAULT_ROOT))
    sub.add_parser('status', help='what is installed and whether it is unchanged').add_argument('--root', default=str(DEFAULT_ROOT))
    uninstall_parser = sub.add_parser('uninstall', help='remove the copy; saves stay unless asked')
    uninstall_parser.add_argument('--root', default=str(DEFAULT_ROOT))
    uninstall_parser.add_argument('--delete-saves', action='store_true')
    app_parser = sub.add_parser('app', help='build the double-click launcher app (default /Applications/SoloStone.app)')
    app_parser.add_argument('--dest', default=str(APP_DEST), help='where the app goes')
    app_parser.add_argument('--banner', default=str(BANNER), help='the image the icon is cut from')
    app_parser.add_argument('--python', default=sys.executable, help='the python3 that runs the launcher')
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        print("SoloStone's macOS launcher runs on macOS only.", file=sys.stderr)
        return 1
    root = Path(canonical(getattr(args, 'root', str(DEFAULT_ROOT))))
    try:
        if args.command == 'setup':
            return setup(Path(args.source), root, Path(args.plugin), Path(args.references), args.accept_warning)
        if args.command == 'launch':
            return launch(root)
        if args.command == 'status':
            return status(root)
        if args.command == 'app':
            return make_app(Path(args.dest), Path(args.banner), args.python)
        return uninstall(root, args.delete_saves)
    except Refused as error:
        print('SOLOSTONE REFUSED: %s' % error, file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as error:
        print('SOLOSTONE FAILED: %s: %s' % (type(error).__name__, error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
