using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using SoloStone;

// What differs per system (plugin/HostPlatform.cs, plugin/MacNetworkBlock.cs, and what admission makes of it): Windows keeps its answers,
// macOS gets its own. The macOS half runs on macOS; the rules run anywhere.
static partial class Program
{
    // --sandbox-probe: started by HostPlatformTests inside sandbox-exec, prints what the plugin's own check would say there.
    static int SandboxProbe() { Console.WriteLine("blocked=" + (MacNetworkBlock.Problem() == null)); return 0; }

    static void HostPlatformTests(string temp) {
        bool windows = Path.DirectorySeparatorChar == '\\';
        Check(HostPlatform.Windows == windows, "Windows is told by the path separator");
        Check(!string.IsNullOrWhiteSpace(HostPlatform.UserName()), "a user name is always read");
        Check(Path.IsPathRooted(HostPlatform.ExecutablePath()), "the executable path is rooted");
        Check(HostPlatform.GameExecutable == (windows ? "Hearthstone.exe" : "Hearthstone.app/Contents/MacOS/Hearthstone.bin"),
            "the game is the system's own file: the bundle's executable on macOS, where the launcher moved it aside");
        string data = HostPlatform.LocalApplicationData();
        Check(Path.IsPathRooted(data) && data == Path.GetFullPath(data), "application data is a full path");
        if (windows) {
            Check(data == Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)
                && HostPlatform.UserName() == System.Security.Principal.WindowsIdentity.GetCurrent().Name, "Windows answers as it always did");
            return;
        }
        Check(data == Path.Combine(Environment.GetEnvironmentVariable("HOME"), "Library", "Application Support") && !data.Contains('\\'),
            "macOS keeps application data in Library/Application Support");

        // The block rule: every network operation must be denied, and nothing else counts.
        Check(MacNetworkBlock.Problem(_ => 1) == null, "denied everywhere is a block");
        Check(MacNetworkBlock.Problem(_ => 0) != null, "allowed anywhere is no block");
        Check(MacNetworkBlock.Problem(operation => operation == "network-inbound" ? 0 : 1).Contains("network-inbound"), "the rule names the operation the sandbox allows");
        Check(MacNetworkBlock.Problem(_ => -1).Contains("answer -1"), "an error answer is no block");
        Check(MacNetworkBlock.Problem(_ => { throw new DllNotFoundException("libsandbox"); }).Contains("could not be asked"), "an answer that cannot be read is no block");
        string outside = MacNetworkBlock.Problem();
        Check(outside != null && outside.Contains("does not deny"), "outside the launcher's sandbox the network is not blocked: " + outside);
        // The launcher's own sandbox, on this test program: the same check then passes.
        if (File.Exists("/usr/bin/sandbox-exec")) {
            string profile = Path.Combine(temp, "no-network.sb");
            File.WriteAllText(profile, "(version 1)\n(allow default)\n(deny network*)\n");
            var start = new ProcessStartInfo("/usr/bin/sandbox-exec") { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true };
            start.Environment["DOTNET_NOLOGO"] = "1"; start.Environment["DOTNET_SKIP_FIRST_TIME_EXPERIENCE"] = "1";
            foreach (string argument in new[] { "-f", profile, Environment.ProcessPath, typeof(Program).Assembly.Location, "--sandbox-probe" }) start.ArgumentList.Add(argument);
            string answer;
            using (var child = Process.Start(start)) { answer = child.StandardOutput.ReadToEnd(); child.StandardError.ReadToEnd(); child.WaitForExit(); }
            string line = answer.Split('\n').FirstOrDefault(text => text.StartsWith("blocked=", StringComparison.Ordinal))?.Trim();
            Check(line == "blocked=True", "inside the launcher's sandbox the network is blocked (" + line + ")");
        } else Skip("sandbox-exec is not on this system");

#if SOLOSTONE_INSTALLER
        // The copy's own record says which file is the game.
        var record = new InstallRecord { Files = new Dictionary<string, string> { { "Hearthstone.exe", new string('A', 64) } } };
        Check(InstallerRuntime.GameExecutableOf(record) == "Hearthstone.exe", "a record with Hearthstone.exe keeps it as the game");
        record.Files = new Dictionary<string, string> { { "Hearthstone.app/Contents/MacOS/Hearthstone", new string('A', 64) } };
        Check(InstallerRuntime.GameExecutableOf(record) == HostPlatform.GameExecutable, "a record without Hearthstone.exe names the system's game");
        Check(Payload.HasInjector(new Dictionary<string, string> { { "winhttp.dll", "x" } }) == "winhttp.dll"
            && Payload.HasInjector(new Dictionary<string, string> { { "libdoorstop.dylib", "x" } }) == "libdoorstop.dylib"
            && Payload.HasInjector(new Dictionary<string, string> { { "doorstop_config.ini", "x" } }) == null, "a payload names the loader its copy runs");
        MacLayoutAdmission(temp);
#endif
    }

    // A macOS copy, laid out the way the macOS launcher writes it, goes through the unchanged admission: only its bundle's executable,
    // only with a grant, only into this user's own application data.
    static void MacLayoutAdmission(string temp) {
        string root = Path.Combine(temp, "mac-root"), client = Path.Combine(root, "client"), reports = Path.Combine(root, "reports");
        Directory.CreateDirectory(reports);
        var files = new Dictionary<string, string>(StringComparer.Ordinal);
        string[] payload = { "libdoorstop.dylib", "doorstop_config.ini", "client.config", "BepInEx/core/BepInEx.dll", "BepInEx/core/BepInEx.Preloader.dll",
            "BepInEx/core/0Harmony.dll", "BepInEx/unstripped_corlib/mscorlib.dll", "BepInEx/plugins/SoloStone.Plugin.dll", "BepInEx/plugins/SabberStoneCore.dll" };
        foreach (string relative in new[] { HostPlatform.GameExecutable }.Concat(payload)) {
            string path = Path.Combine(client, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(path)); File.WriteAllText(path, "mac " + relative);
            files[relative] = Hashes.FileHash(path);
        }
        string id = Guid.NewGuid().ToString("N"), run = Guid.NewGuid().ToString("D"), exe = Path.Combine(client, HostPlatform.GameExecutable);
        File.WriteAllText(Path.Combine(client, InstallRecord.Marker), id);
        Func<IDictionary<string, string>, string> map = rows => string.Join(",", rows.OrderBy(row => row.Key, StringComparer.Ordinal)
            .Select(row => "\"" + row.Key + "\":\"" + row.Value + "\""));
        var shipped = payload.ToDictionary(relative => relative, relative => files[relative], StringComparer.Ordinal);
        File.WriteAllText(Path.Combine(root, "install.json"),
            "{\"schema\":2,\"installId\":\"" + id + "\",\"root\":" + Quoted(root) + ",\"source\":\"/Applications/Hearthstone\",\"clientPath\":" + Quoted(client)
            + ",\"clientVersion\":\"37.0.255790\",\"releaseVersion\":\"0.4.0\",\"files\":{" + map(files) + "},\"payload\":{\"schema\":1,\"bepInExVersion\":\"5.4.23.6\","
            + "\"doorstopVersion\":\"4.6.0\",\"sourceRevision\":\"test\",\"engineSha256\":\"" + files["BepInEx/plugins/SabberStoneCore.dll"]
            + "\",\"pluginSha256\":\"" + files["BepInEx/plugins/SoloStone.Plugin.dll"] + "\",\"files\":{" + map(shipped) + "}},\"identityFiles\":{}}");
        string data = HostPlatform.LocalApplicationData();
        string[] args = { "-hs-installer-root", root, "-hs-native-ui-run", run };
        Func<string, string> resolve = executable => InstallerRuntime.Resolve(executable, data, args, false, false, () => new string[0], _ => { }, () => "37.0");
        LaunchGrant.Write(reports, run, "allow", "37.0.255790");
        Check(resolve(exe) == root, "a granted launch of a macOS copy is admitted");
        Refuses(() => resolve(Path.Combine(client, "Hearthstone.exe")), "a file that is not the copy's game is refused");
        Refuses(() => InstallerRuntime.Resolve(exe, data, args, false, false, () => new string[0],
            _ => { throw new InvalidOperationException("SoloStone's network block is missing: the sandbox does not deny network-outbound (answer 0)"); }, () => "37.0"),
            "a macOS copy without the sandbox is refused");
        try { InstallerRuntime.Resolve(exe, data, args, false, false, () => new string[0], _ => { throw new InvalidOperationException("missing"); }, () => "37.0"); }
        catch (InstallerRefusal refusal) { Check(refusal.ExitCode == InstallerRuntime.ExitFirewallRefused, "the missing network block is exit 47, like a missing firewall rule"); }
        LaunchGrant.Remove(reports);
        Refuses(() => resolve(exe), "a direct launch of a macOS copy is refused");
    }

    static string Quoted(string text) => "\"" + text.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
}
