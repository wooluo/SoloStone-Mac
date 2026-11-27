# SoloStone on macOS

SoloStone's launcher is a Windows program. On macOS its job is done by a script, `installer/mac/solostone.py`, which gives a Mac
Hearthstone the same SoloStone plugin: the same offline game against computer opponents, with the same checks before every start.
It is not a release: it was tried on one Mac (Apple Silicon, macOS 15.7) with Hearthstone 37.0.255790 and the plugin built from
this repository. Intel Macs and other client versions are untried.

## What you need

| Need | Detail |
|---|---|
| macOS | Apple Silicon was tried. |
| Hearthstone | Installed through Battle.net, at `/Applications/Hearthstone` or wherever you chose. SoloStone reads it and never changes it. |
| .NET SDK | 10.0.402, as `global.json` demands: the plugin is built on your Mac, not downloaded. |
| Python 3 | On your `PATH` as `python3`, 3.10 or newer. |
| Internet | Once, for the loader's three packages (BepInEx, Unity Doorstop and Unity's Mono class libraries). Nothing downloads after that. |
| Room | Almost none: the copy is a clone (APFS copies on write). On a volume that cannot clone, the size of your Hearthstone folder. |

## Install

Run every command from the repository root.

1. **Put the game's libraries where the plugin is built from.** They are yours and stay out of the repository:

   ```text
   python3 tests/run.py --client "/Applications/Hearthstone" --suite run-tool
   ```

2. **Build the plugin from this client:**

   ```text
   dotnet build plugin/OfflineProbe.csproj -c Debug -p:EngineSource=true -p:Installer=true -p:NuGetAudit=false
   ```

   The copy you play must be made from the same Hearthstone: `setup` compares the two and refuses another.

3. **Quit Battle.net** (its icon in the menu bar, then Quit).

4. **Make the copy and add SoloStone to it:**

   ```text
   python3 installer/mac/solostone.py setup
   ```

   It takes under a minute. Everything about your game and the copy's place is checked before anything is written there. To use
   another game folder or another place for SoloStone, pass `--source DIR` or `--root DIR`.

5. **Play — with a double-click, or from the terminal:**

   ```text
   python3 installer/mac/solostone.py app
   ```

   builds a SoloStone app (default `/Applications/SoloStone.app`), whose icon is the repository's mascot. From then on, double-click
   it to play: it runs `launch` for you, shows the launcher's answer in a dialog when a start is refused or a game stops by itself,
   and stays quiet when a game ends normally. It has no Dock icon while the game runs; quit the game and it goes away. The app
   records where the repository and your `python3` are, so move the repository or change Python and build it again. Without the app:

   ```text
   python3 installer/mac/solostone.py launch
   ```

`python3 installer/mac/solostone.py status` says what is installed and whether the copy is unchanged.

## Where things are

By default everything is in `~/Library/Application Support/SoloStone`:

| Path | What |
|---|---|
| `client/` | The copy of your game with SoloStone's files in it |
| `saves/` | Your saves. The same place the Windows version uses, `SoloStone\saves` under your application data |
| `reports/` | What each start wrote: the game's log (`Unity.log`), what the game, the loader and Unity printed (`game-console.log`), the plugin's checks, its start marker |
| `client/BepInEx/LogOutput.log` | What the plugin did |
| `installer.log` | What setup and each launch decided |

## What setup does

1. Checks your Hearthstone: that it is one this SoloStone runs on, that its files are what SoloStone expects, and that the plugin
   was built from it.
2. Checks where the copy goes: a new or empty folder, never in or around a Hearthstone.
3. Copies your Hearthstone and records a hash of the game, of Unity and of the two libraries the plugin binds to. Your own game is
   only read.
4. Gives the copy its own names and adds SoloStone's files. The company name in the copy's two data files becomes "SoloStone Offline
   Mods" and its application identifier changes with it, so the copy keeps its settings, its PlayerPrefs and its options out of your
   own Hearthstone's. The plugin, its engine and the macOS loader are placed and recorded.
5. Checks that the copy cannot reach the internet.

## How the copy is kept offline

Windows has firewall rules for the copy. macOS has none that a program can add without an administrator's password, so the launcher
uses the system's sandbox instead:

- Setup moves the game to `Hearthstone.app/Contents/MacOS/Hearthstone.bin` and puts a small start script in its place. The script
  starts the game only inside a sandbox that denies it every network connection, with SoloStone's loader attached. The block
  therefore belongs to the copy itself: however the copy is started, it cannot reach the internet. Started without SoloStone's
  launcher, the plugin finds no launch grant and stops the game at once (exit 40).
- For that move the copy's game is signed with your Mac's own signature. Blizzard's signature belongs to Blizzard's unchanged
  program, and macOS stops the game under another name while that signature is on it.
- The plugin reads the sandbox back before it hooks anything (`MacNetworkBlock`): a copy that is not known to be cut off is
  refused (exit 47), as a missing firewall rule is on Windows.
- The plugin's own canary, which tries two addresses on the internet, runs after that as on Windows.

## If something goes wrong

The launcher prints the reason and does not start the game:

| Message | What to do |
|---|---|
| `Battle.net is running` | Quit Battle.net and try again. |
| `Hearthstone is running` | Quit the game, including a copy of it, and try again. |
| `… is not built` | Build the plugin (step 2). |
| `… is not this client's library` | Run step 1 again with the Hearthstone you play, then step 2. |
| `SoloStone has not been tested with this Hearthstone version` | Your client is another minor version. `--accept-warning` plays anyway; matches can break. |
| `… still holds … from an earlier install` | `uninstall` first, or `--root`. Your saves may stay where they are: setup installs beside them. |
| `SoloStone file check failed: …` | A recorded file changed. Run `setup` again to make the copy anew; your saves stay. |
| `SoloStone cannot keep the copy offline (…)` | The sandbox let a connection through. Do not play the copy; tell the project. |

When the game stops by itself, the launcher says which exit code the plugin used and shows the last lines of its logs:

| Exit code | Meaning |
|---|---|
| 40 | The plugin was not started by SoloStone, or the copy is not where SoloStone put it |
| 41 | The plugin reached the internet and stopped |
| 42 | The plugin does not fit this Hearthstone client: see `reports/client-check.txt` |
| 47 | The network block is missing |
| 48, 49, 50 | Refused, a save or data file could not be handled, a startup error: see `client/BepInEx/LogOutput.log` |

Exit 0 after a match is a normal end.

## Remove it

```text
python3 installer/mac/solostone.py uninstall              # the copy goes; your saves stay
python3 installer/mac/solostone.py uninstall --delete-saves
```

Only what SoloStone made is removed: the copy, its record and reports, and what the copy wrote under SoloStone's own names.

## What is not here yet

- No window: the launcher is a few commands, plus the double-click app (`app`), which only shows what the launcher answers.
- No update check and no save backups. Copy `saves/` somewhere before you experiment.
- SoloStone's own menus and texts are in English. The game and its cards follow the language chosen in Options.
- The rest of [INSTALL.md](INSTALL.md) describes the Windows launcher.

## For developers

The script keeps to the launcher's rules and reads what the plugin reads:

- The install record is the Windows one (`install.json`, schema 2). The plugin's admission (`plugin/InstallerRuntime.cs`) checks
  it, the copy marker, the launch grant and every recorded hash unchanged; it asks `HostPlatform` for what differs per system.
- `plugin/HostPlatform.cs` answers for each system: the user's name, the running executable, where application data lives, which
  file is the game, where Battle.net keeps Hearthstones, and how the network block is read back (`plugin/MacNetworkBlock.cs`).
- The loader is pinned in `installer/loader-inputs-mac.json` and fetched by `installer/loader_inputs.py`, the Windows loader's tool.
  It needs Doorstop 4.6.0 for this client (4.5.0 cannot read its binaries), BepInEx 5.4.23.6 for Apple Silicon (5.4.23.5 stops
  there), and the Linux/macOS Mono class libraries (the Windows ones call Windows functions and crash the game).
- Checks: `python3 tests/run.py --suite launcher --suite installer-python`. The launcher tests hold the platform answers, the
  sandbox rule (one run inside `sandbox-exec`) and an admission of a macOS copy; the Python tests hold this script's rules.
