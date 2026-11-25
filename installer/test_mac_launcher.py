"""Checks for mac/solostone.py, SoloStone's macOS launcher: unittest, no game and no network. Run from the repository root:
    python3 -I installer/test_mac_launcher.py
The parts that need a game (the copy, the sandbox, the launch itself) are not checked here.
"""
import contextlib
import importlib.util
import io
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
INSTALLER = Path(__file__).resolve().parent
sys.path.insert(0, str(INSTALLER))
import loader_inputs  # noqa: E402

spec = importlib.util.spec_from_file_location('solostone_mac', INSTALLER / 'mac' / 'solostone.py')
mac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mac)

TESTED = {'version': '37.0.0', 'build': '254477'}
IDENTIFIER = b'<key>CFBundleIdentifier</key>\n    <string>unity.Blizzard Entertainment.Hearthstone</string>'


class Rules(unittest.TestCase):
    def test_excluded_is_the_windows_rule(self):
        for relative in ['Logs/hearthstone_1.log', 'LOGS/x.txt', 'BepInEx/core/x.dll', 'reports/a.txt', 'client.config',
                         'Hearthstone.app/Contents/Resources/Data/client.config', 'winhttp.dll', 'doorstop_config.ini',
                         'Hearthstone.app/doorstop_config.ini', '.doorstop_version', '.solostone-client', 'solostone-offline.sb',
                         'Errors/deep/error.log']:
            self.assertTrue(mac.excluded(relative), relative)
        for relative in ['Hearthstone.app/Contents/Info.plist', 'Hearthstone.app/Contents/Resources/Data/app.info', 'Data/OSX/a.unity3d',
                         'Errors/error.txt', 'Strings/zhCN/GLOBAL.txt', 'Hearthstone.app/Contents/MacOS/Hearthstone', '.product.db',
                         'client.config.bak', 'Logs.txt']:
            self.assertFalse(mac.excluded(relative), relative)

    def test_within_is_spelling_only(self):
        self.assertTrue(mac.within('/a/b/c', '/a/b'))
        self.assertTrue(mac.within('/a/b/', '/a/b'))
        self.assertTrue(mac.within('/A/B/X', '/a/b'))
        self.assertFalse(mac.within('/a/bc', '/a/b'))
        self.assertFalse(mac.within('/a', '/a/b'))
        self.assertTrue(mac.within('b/c', '.'))

    def test_company_patch_is_the_windows_one(self):
        data = b'xx' + mac.OLD_COMPANY + b'yy' + b'Z' * 40
        patched = mac.patch_company(data)
        self.assertEqual(len(patched), len(data))
        self.assertEqual(patched, b'xx' + mac.NEW_COMPANY + b'yy' + b'Z' * 40)
        for bad in (b'nothing here', mac.OLD_COMPANY * 2, mac.NEW_COMPANY, mac.OLD_COMPANY + mac.NEW_COMPANY):
            with self.assertRaises(mac.Refused):
                mac.patch_company(b'..' + bad + b'..')

    def test_identifier_patch_keeps_the_plist_readable(self):
        head, tail = b'<?xml version="1.0"?><dict>', b'<key>CFBundleExecutable</key><string>Hearthstone</string></dict>'
        patched = mac.patch_identifier(head + IDENTIFIER + tail)
        self.assertEqual(len(patched), len(head + IDENTIFIER + tail))
        self.assertEqual(patched, head + IDENTIFIER.replace(mac.OLD_IDENTIFIER.encode(), mac.NEW_IDENTIFIER.encode()) + tail)
        self.assertEqual(plistlib.loads(patched)['CFBundleIdentifier'], mac.NEW_IDENTIFIER)
        self.assertEqual(plistlib.loads(patched)['CFBundleExecutable'], 'Hearthstone')
        self.assertEqual(len(mac.NEW_IDENTIFIER), len(mac.OLD_IDENTIFIER))
        for data in (b'<dict/>', IDENTIFIER + IDENTIFIER, IDENTIFIER.replace(mac.OLD_IDENTIFIER.encode(), mac.NEW_IDENTIFIER.encode())):
            with self.assertRaises(mac.Refused):
                mac.patch_identifier(data)

    def test_own_names_mean_one_of_ours_and_none_of_theirs(self):
        company, patched = mac.patch_company(b'.' + mac.OLD_COMPANY + b'.'), mac.patch_identifier(IDENTIFIER)
        self.assertTrue(mac.carries_own_names(company))
        self.assertTrue(mac.carries_own_names(patched, identifier=True))
        for data in (b'.' + mac.OLD_COMPANY + b'.', b'', company + mac.NEW_COMPANY, b'.' + mac.NEW_COMPANY + mac.OLD_COMPANY + b'.'):
            self.assertFalse(mac.carries_own_names(data), data)
        for data in (IDENTIFIER, b'', patched + mac.NEW_IDENTIFIER.encode(), b''):
            self.assertFalse(mac.carries_own_names(data, identifier=True), data)
        # Each file is asked by its own rule: a file with only the company is no Info.plist.
        self.assertFalse(mac.carries_own_names(company, identifier=True))

    def test_version_verdict_follows_the_windows_policy(self):
        self.assertEqual(mac.version_verdict('37.0.255790', False, TESTED), 'allow')
        self.assertEqual(mac.version_verdict('37.0.254477', False, TESTED), 'allow')
        self.assertEqual(mac.version_verdict('37.3.1', True, TESTED), 'warning')
        for version, accepted in [('37.3.1', False), ('38.0.1', False), ('38.0.1', True), ('36.0.1', True)]:
            with self.assertRaises(mac.Refused):
                mac.version_verdict(version, accepted, TESTED)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mac.version_verdict('37.3.1', True, TESTED), 'warning')
        self.assertIn('not been tested', out.getvalue())


class Record(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()
        self.root, self.source, self.client = self.dir / 'root', self.dir / 'Hearthstone', self.dir / 'root' / 'client'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative, data=b'x'):
        path = self.client.joinpath(*relative.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def build(self):
        """A copy laid out as setup leaves it, with its record."""
        files, identity, payload = {}, {}, {'schema': 1, 'bepInExVersion': '5.4.23.6', 'doorstopVersion': '4.6.0', 'sourceRevision': 't',
                                            'files': {}}
        for relative in mac.PINNED:
            self.write(relative, b'game ' + relative.encode())
            files[relative] = mac.file_hash(self.client.joinpath(*relative.split('/')))
        for relative in mac.COMPANY_FILES + [mac.INFO_PLIST]:
            path = self.write(relative, mac.patch_identifier(IDENTIFIER) if relative == mac.INFO_PLIST
                              else mac.patch_company(b'.' + mac.OLD_COMPANY + b'.'))
            identity[relative] = mac.file_hash(path)
        for relative in ['libdoorstop.dylib', 'doorstop_config.ini', 'client.config', 'BepInEx/core/BepInEx.dll',
                         'BepInEx/core/BepInEx.Preloader.dll', 'BepInEx/unstripped_corlib/mscorlib.dll', 'BepInEx/core/0Harmony.dll',
                         'BepInEx/plugins/SoloStone.Plugin.dll', 'BepInEx/plugins/SabberStoneCore.dll', mac.SANDBOX, mac.GAME]:
            self.write(relative, b'payload ' + relative.encode())
            payload['files'][relative] = mac.file_hash(self.client.joinpath(*relative.split('/')))
        payload['pluginSha256'] = payload['files']['BepInEx/plugins/SoloStone.Plugin.dll']
        payload['engineSha256'] = payload['files']['BepInEx/plugins/SabberStoneCore.dll']
        record = mac.install_record(self.root, self.source, self.client, 'a' * 32, '37.0.255790', files, payload, identity)
        (self.client / mac.MARKER).write_text('a' * 32)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / mac.RECORD).write_text(json.dumps(record, indent=1, sort_keys=True), encoding='utf-8')
        return record

    def test_record_is_what_the_plugin_reads(self):
        record = self.build()
        text = json.dumps(record, sort_keys=True)
        again = json.loads(text)
        self.assertEqual(again['schema'], 2)
        self.assertEqual(set(again), {'schema', 'installId', 'root', 'source', 'clientPath', 'clientVersion', 'releaseVersion', 'files',
                                      'payload', 'identityFiles'})
        self.assertTrue(all(isinstance(value, int) for value in [again['schema'], again['payload']['schema']]))
        self.assertEqual(again['root'], str(self.root))
        self.assertEqual(again['clientPath'], str(self.client))
        self.assertEqual(set(again['payload']), {'schema', 'bepInExVersion', 'doorstopVersion', 'sourceRevision', 'engineSha256',
                                                 'pluginSha256', 'files'})
        self.assertTrue(all(len(value) == 64 and value == value.upper() and int(value, 16) >= 0
                            for group in ('files', 'identityFiles') for value in again[group].values()))

    def test_unchanged_copy_verifies_and_each_change_is_named(self):
        record = self.build()
        self.assertEqual(mac.verify_copy(self.client, record), [])
        loaded, client = mac.load_record(self.root)
        self.assertEqual((loaded['installId'], client), ('a' * 32, self.client))
        self.write('BepInEx/plugins/SoloStone.Plugin.dll', b'another plugin')
        self.assertEqual(mac.verify_copy(self.client, record), ['SoloStone file check failed: BepInEx/plugins/SoloStone.Plugin.dll'])
        self.write('BepInEx/plugins/SoloStone.Plugin.dll', b'payload BepInEx/plugins/SoloStone.Plugin.dll')
        self.assertEqual(mac.verify_copy(self.client, record), [])
        saved = record['payload']['pluginSha256']
        record['payload']['pluginSha256'] = '0' * 64   # a record edited to bless another plugin
        self.assertEqual(mac.verify_copy(self.client, record), ['SoloStone payload does not match its record'])
        record['payload']['pluginSha256'] = saved
        game = self.write(mac.GAME_BINARY, b'a newer client')
        self.assertEqual(mac.verify_copy(self.client, record), ['file check failed: ' + mac.GAME_BINARY])
        game.unlink()
        self.write(mac.GAME_BINARY, b'game ' + mac.GAME_BINARY.encode())
        self.write(mac.INFO_PLIST, IDENTIFIER)   # the player's identifier put back: the copy would write the player's settings
        self.assertEqual(mac.verify_copy(self.client, record),
                         ["the copy's settings files no longer carry SoloStone's own name: " + mac.INFO_PLIST])
        self.write(mac.INFO_PLIST, mac.patch_identifier(IDENTIFIER))
        (self.client / mac.SANDBOX).unlink()
        self.assertEqual(mac.verify_copy(self.client, record), ['SoloStone file check failed: ' + mac.SANDBOX])

    def test_no_record_no_launch_and_no_removal(self):
        with self.assertRaises(mac.Refused):
            mac.load_record(self.root)
        self.build()
        (self.client / mac.MARKER).write_text('b' * 32)
        with self.assertRaises(mac.Refused):
            mac.load_record(self.root)
        (self.client / mac.MARKER).write_text('a' * 32)
        record = json.loads((self.root / mac.RECORD).read_text(encoding='utf-8'))
        for outside in (self.dir / 'Hearthstone', self.dir, self.root):
            record['clientPath'] = str(outside)
            (self.root / mac.RECORD).write_text(json.dumps(record), encoding='utf-8')
            with self.assertRaises(mac.Refused):
                mac.load_record(self.root)
        record['schema'] = 3
        (self.root / mac.RECORD).write_text(json.dumps(record), encoding='utf-8')
        with self.assertRaises(mac.Refused):
            mac.load_record(self.root)

    def test_add_payload_ships_the_loader_list_and_what_the_plugin_requires(self):
        loader = self.dir / 'loader'
        data = loader_inputs.load(mac.LOADER_LIST)
        for entry in data['files']:
            path = loader.joinpath(*entry['path'].split('/'))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'loader ' + entry['path'].encode())
        plugin = self.dir / 'plugin'
        plugin.mkdir()
        for name in mac.PLUGIN_FILES:
            (plugin / name).write_bytes(b'built ' + name.encode())
        self.write(mac.GAME_BINARY, b'game')
        payload = mac.add_payload(self.client, plugin, loader, self.client)
        self.assertEqual(sorted(payload['files']), sorted([entry['path'] for entry in data['files']] + [
            'BepInEx/plugins/' + name for name in mac.PLUGIN_FILES] + ['client.config', 'doorstop_config.ini', mac.SANDBOX, mac.GAME]))
        required = {'libdoorstop.dylib', 'doorstop_config.ini', 'client.config', 'BepInEx/core/BepInEx.dll', 'BepInEx/core/BepInEx.Preloader.dll',
                    'BepInEx/unstripped_corlib/mscorlib.dll', 'BepInEx/core/0Harmony.dll', 'BepInEx/plugins/SoloStone.Plugin.dll',
                    'BepInEx/plugins/SabberStoneCore.dll'}   # Payload.Verify
        self.assertTrue(required <= set(payload['files']), required - set(payload['files']))
        for relative, expected in payload['files'].items():
            self.assertEqual(mac.file_hash(self.client.joinpath(*relative.split('/'))), expected, relative)
        self.assertEqual((payload['pluginSha256'], payload['engineSha256']),
                         (mac.file_hash(plugin / 'SoloStone.Plugin.dll'), mac.file_hash(plugin / 'SabberStoneCore.dll')))
        self.assertEqual((payload['bepInExVersion'], payload['doorstopVersion']), (data['bepInExVersion'], data['doorstopVersion']))
        self.assertEqual((self.client / 'client.config').read_bytes(), (mac.REPOSITORY / 'client.config').read_bytes())
        self.assertEqual((self.client / mac.SANDBOX).read_text(), mac.SANDBOX_PROFILE)
        self.assertEqual((self.client / 'BepInEx' / 'plugins' / 'SoloStone.Plugin.dll').read_bytes(), b'built SoloStone.Plugin.dll')
        self.assertIn(str(self.client) + '/BepInEx/core/BepInEx.Preloader.dll', (self.client / 'doorstop_config.ini').read_text())
        self.assertEqual((self.client / mac.GAME).read_text(), mac.START_SCRIPT)
        self.assertEqual((self.client / mac.GAME).stat().st_mode & 0o111, 0o111)

    def test_the_copy_is_the_game_without_the_excluded_entries(self):
        source = self.dir / 'source'
        for relative in [mac.GAME, mac.INFO_PLIST, 'Logs/a.log', 'BepInEx/core/other.dll', 'client.config', 'Data/OSX/a.unity3d', 'Strings/zhCN/GLOBAL.txt']:
            path = source.joinpath(*relative.split('/'))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b's')
        (source / mac.GAME).chmod(0o755)
        stage = self.dir / 'stage'
        self.assertEqual(sorted(mac.copy_game(source, stage)), ['BepInEx/core/other.dll', 'Logs/a.log', 'client.config'])
        self.assertEqual(sorted(mac.files_under(stage)), ['Data/OSX/a.unity3d', 'Hearthstone.app/Contents/Info.plist',
                                                           'Hearthstone.app/Contents/MacOS/Hearthstone', 'Strings/zhCN/GLOBAL.txt'])
        self.assertEqual(sorted(mac.files_under(source)), ['BepInEx/core/other.dll', 'Data/OSX/a.unity3d', 'Hearthstone.app/Contents/Info.plist',
                                                            'Hearthstone.app/Contents/MacOS/Hearthstone', 'Logs/a.log', 'Strings/zhCN/GLOBAL.txt',
                                                            'client.config'])   # the player's game is only read
        self.assertEqual((stage / mac.GAME).stat().st_mode & 0o111, 0o111)
        with self.assertRaises(mac.Refused):
            mac.copy_game(self.root, stage)   # nothing to copy
        self.assertFalse(stage.exists())


class Texts(unittest.TestCase):
    def test_start_script_starts_the_game_inside_the_offline_sandbox(self):
        script = mac.START_SCRIPT
        self.assertTrue(script.startswith('#!/bin/sh\n'))
        for needed in ['set -e', '/usr/bin/sandbox-exec -f "$ROOT/' + mac.SANDBOX + '"', '-e DYLD_INSERT_LIBRARIES=libdoorstop.dylib',
                       '"$CONTENTS/MacOS/Hearthstone.bin" "$@"', 'DOORSTOP_TARGET_ASSEMBLY="$ROOT/BepInEx/core/BepInEx.Preloader.dll"',
                       'DOORSTOP_MONO_DLL_SEARCH_PATH_OVERRIDE="$ROOT/BepInEx/unstripped_corlib"', 'exit 48']:
            self.assertIn(needed, script)
        self.assertEqual(script.count('\\\n'), 1)   # one continued line, and a single backslash ends it
        self.assertNotIn('%(', script)
        checked = subprocess.run(['/bin/sh', '-n'], input=script, capture_output=True, text=True)
        self.assertEqual((checked.returncode, checked.stderr), (0, ''))
        self.assertEqual(mac.SANDBOX_PROFILE, '(version 1)\n(allow default)\n(deny network*)\n')

    def test_start_script_finds_the_copy_root_from_where_setup_puts_it(self):
        # The script is the bundle's executable: <copy>/Hearthstone.app/Contents/MacOS/Hearthstone. Its two path lines, run from there.
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp).resolve() / 'client'
            script = copy / mac.GAME
            script.parent.mkdir(parents=True)
            script.write_text(mac.START_SCRIPT, encoding='ascii')
            (copy / mac.SANDBOX).write_text(mac.SANDBOX_PROFILE, encoding='ascii')
            self.assertTrue((copy / 'Hearthstone.app' / 'Contents').is_dir())
            lines = [line for line in mac.START_SCRIPT.splitlines() if line.startswith(('CONTENTS=', 'ROOT='))]
            found = subprocess.run(['/bin/sh', '-c', '\n'.join(lines) + '\necho "$ROOT"\ncd "$ROOT" && pwd -P\n', str(script)],
                                   capture_output=True, text=True)
            self.assertEqual((found.returncode, found.stderr), (0, ''))
            self.assertEqual(found.stdout.split(), [str(copy), str(copy)])

    def test_a_start_without_the_offline_files_is_refused_before_anything_runs(self):
        # What the copy answers when its sandbox profile is gone: exit 48, and no game.
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp).resolve() / 'client'
            script = copy / mac.GAME
            script.parent.mkdir(parents=True)
            script.write_text(mac.START_SCRIPT, encoding='ascii')
            script.chmod(0o755)   # add_payload does the same: it is the bundle's executable
            binary = copy / mac.GAME_BINARY
            binary.write_text('#!/bin/sh\necho "THE GAME RAN"\n', encoding='ascii')
            binary.chmod(0o755)
            refused = subprocess.run([str(script)], capture_output=True, text=True)
            self.assertEqual((refused.returncode, refused.stdout), (48, ''))
            self.assertIn('it was not started', refused.stderr)

    def test_every_plugin_exit_code_has_a_line_for_the_player(self):
        self.assertEqual(set(mac.EXIT_TEXT), {40, 41, 42, 44, 45, 47, 48, 49, 50})
        self.assertTrue(all(text.endswith('.') for text in mac.EXIT_TEXT.values()))

    def test_the_loader_list_is_the_macos_set_and_leaves_the_windows_one_alone(self):
        mac_list, windows = loader_inputs.load(mac.LOADER_LIST), loader_inputs.load()
        self.assertEqual((mac_list['doorstopVersion'], mac_list['bepInExVersion']), ('4.6.0', '5.4.23.6'))
        listed = {entry['path'] for entry in mac_list['files']}
        self.assertTrue({'libdoorstop.dylib', '.doorstop_version', 'BepInEx/core/BepInEx.Preloader.dll', 'BepInEx/core/0Harmony.dll',
                         'BepInEx/unstripped_corlib/mscorlib.dll'} <= listed)
        self.assertFalse({'winhttp.dll'} & listed)
        self.assertEqual((windows['doorstopVersion'], windows['bepInExVersion']), ('4.5.0', '5.4.23.5'))
        self.assertIn('winhttp.dll', {entry['path'] for entry in windows['files']})

    def test_idle_needs_battle_net_closed_and_no_hearthstone(self):
        copy = '/Volumes/Games/SoloStone/client'
        game = copy + '/Hearthstone.app/Contents/MacOS/Hearthstone.bin'
        self.assertIsNone(mac.idle_problem([('S', '/bin/zsh'), ('Ss', '/Applications/Safari.app/Contents/MacOS/Safari')]))
        self.assertIsNone(mac.idle_problem([('UE', game), ('Z', game)], copy), 'a process the kernel is tearing down is no running game')
        self.assertEqual(mac.idle_problem([('S', '/Applications/Battle.net.app/Contents/MacOS/Battle.net')]),
                         'Battle.net is running. Quit Battle.net before using SoloStone.')
        self.assertEqual(mac.idle_problem([('S', '/Applications/Battle.net.app/Contents/Agent/Agent')]),
                         'Battle.net is still closing. Wait a moment, then try again.')
        self.assertEqual(mac.idle_problem([('S', game)], copy), "SoloStone's game is already running. Quit it first.")
        self.assertEqual(mac.idle_problem([('S', game)]), 'Hearthstone is running. Quit it first.')
        self.assertEqual(mac.idle_problem([('S', '/Applications/Hearthstone/Hearthstone.app/Contents/MacOS/Hearthstone')], copy),
                         'Hearthstone is running. Quit it first.')

    def test_same_hash_reads_both_spellings_and_nothing_else(self):
        self.assertTrue(mac.same_hash('AB', 'ab'))
        self.assertFalse(mac.same_hash('AB', 'AC') or mac.same_hash('', '') or mac.same_hash(None, 'a'))

    def test_an_install_folder_may_hold_what_an_uninstall_keeps_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, root = Path(tmp).resolve() / 'Hearthstone', Path(tmp).resolve() / 'SoloStone'
            self.assertIsNone(mac.root_problem(root, source))
            root.mkdir()
            self.assertIsNone(mac.root_problem(root, source))
            (root / 'installer.log').write_text('earlier install\n')
            self.assertIsNone(mac.root_problem(root, source), 'the log an uninstall leaves does not block a new install')
            (root / 'client').mkdir()
            self.assertIn('still holds client', mac.root_problem(root, source))
            (root / 'client').rmdir()
            (root / 'saves').mkdir()
            (root / 'saves' / 'profile.xml').write_text('<profile />')
            self.assertIsNone(mac.root_problem(root, source), 'the saves an uninstall keeps are installed beside, never removed')
            self.assertTrue((root / 'saves' / 'profile.xml').is_file())
            for leftover in ('install.json', 'reports', 'client.solostone-staging', 'saves.bak'):
                (root / leftover).write_text('x') if leftover != 'reports' else (root / leftover).mkdir()
                self.assertIn('still holds ' + leftover, mac.root_problem(root, source), leftover)
                (root / leftover).rmdir() if leftover == 'reports' else (root / leftover).unlink()
            (root / 'saves' / 'profile.xml').unlink()
            (root / 'saves').rmdir()
            (root / 'saves').write_text('not a folder')
            self.assertIn('still holds saves', mac.root_problem(root, source), 'a file named saves is no saves folder')
            self.assertIn('in or around', mac.root_problem(Path(tmp).resolve() / 'Hearthstone' / 'SoloStone', source))
            self.assertIn('in or around', mac.root_problem(Path(tmp).resolve(), source))

    def test_wait_for_plugin_stops_as_soon_as_the_game_is_gone(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.assertIsNone(mac.plugin_confirmed(root, 'run'))
            self.assertIs(mac.wait_for_plugin(root, 'run', None, seconds=0.3), None)
            gone = subprocess.Popen(['/bin/sh', '-c', 'exit 48'])
            gone.wait()
            self.assertEqual(mac.wait_for_plugin(root, 'run', gone, seconds=30), 'exited')
            (root / 'reports').mkdir()
            (root / 'reports' / 'plugin-loaded.txt').write_text('another-run\nsome-player\n')
            self.assertIsNone(mac.plugin_confirmed(root, 'run'))
            (root / 'reports' / 'plugin-loaded.txt').write_text('run\nsomeone-else\n')
            self.assertEqual(mac.plugin_confirmed(root, 'run'), 'no')
            me = sorted(mac.user_names())[0]
            (root / 'reports' / 'plugin-loaded.txt').write_text('run\n' + me + '\n')
            self.assertEqual(mac.plugin_confirmed(root, 'run'), 'yes')
            started = time.time()
            alive = subprocess.Popen(['/bin/sh', '-c', 'sleep 3'])
            try:
                self.assertEqual(mac.wait_for_plugin(root, 'run', alive, seconds=30), 'yes')
                self.assertLess(time.time() - started, 2)
            finally:
                alive.kill()
                alive.wait()


if __name__ == '__main__':
    unittest.main()
