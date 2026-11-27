using System;
using System.Diagnostics;
using System.IO;

// What differs between the systems the plugin runs on. Pure (no Unity, no client types) and self-contained: a suite that compiles a file
// which asks it links this one file and nothing else. Windows is the reference, where every answer is what the code read inline before.
// macOS is the port. Its client is a bundle: <client>/Hearthstone.app/Contents/MacOS/Hearthstone is the game, its libraries are in
// Hearthstone.app/Contents/Resources/Data/Managed and its data in <client>/Data/OSX.
internal static class HostPlatform
{
    // '/' also separates Linux paths. This plugin does not run there, so to it every non-Windows system is macOS.
    internal static bool Windows => Path.DirectorySeparatorChar == '\\';

    // Who is playing, which the launcher compares with its own answer (reports/plugin-loaded.txt). Windows has domain accounts
    // (MACHINE\user); macOS has the login name.
    internal static string UserName() => Windows ? System.Security.Principal.WindowsIdentity.GetCurrent().Name : Environment.UserName;

    // This process's executable, which admission compares with the copy's game.
    internal static string ExecutablePath() => Process.GetCurrentProcess().MainModule?.FileName
        ?? throw new InvalidOperationException("The path of the running executable could not be read.");

    // The user's own application data, which holds SoloStone's saves (PublicNames.SaveRoot): %LOCALAPPDATA% on Windows and
    // ~/Library/Application Support on macOS, where the system keeps every program's data (Mono answers ~/.local/share there).
    internal static string LocalApplicationData() => Windows
        ? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Library", "Application Support");

    // The copy's game, relative to the client folder. On macOS the launcher moves the game aside and puts a start script in its place
    // (a bundle's executable has to keep its name), so there the game itself is Hearthstone.bin.
    internal static string GameExecutable => Windows ? "Hearthstone.exe" : "Hearthstone.app/Contents/MacOS/Hearthstone.bin";

#if SOLOSTONE_INSTALLER
    // Every folder a Battle.net Hearthstone is installed in, so neither the copy nor the install root is made in or around one.
    // Windows keeps that in the registry; macOS has none, so it is where Battle.net puts the game.
    internal static string[] RegisteredGameFolders() => Windows ? SoloStone.WindowsInstallPaths.RegisteredFolders() : new[] {
        "/Applications/Hearthstone",
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Applications", "Hearthstone") };

    // The network block, read back before any hook: the firewall rules on Windows, the sandbox the macOS launcher starts the copy in.
    internal static void VerifyNetworkBlock(SoloStone.InstallRecord install)
    {
        if (Windows) SoloStone.FirewallRegistry.Verify(install, SoloStone.FirewallProtection.Executables(install));
        else MacNetworkBlock.Verify();
    }
#endif
}
