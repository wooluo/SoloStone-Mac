using System;
using System.Runtime.InteropServices;

// macOS has no firewall rule to read back: its launcher starts the copy inside a seatbelt profile that denies all networking
// (sandbox-exec), where the Windows launcher adds firewall rules. The sandbox can be asked what it would refuse, so admission holds
// this process to the rule it has on Windows: no launch unless the network is known to be blocked. An answer that cannot be read,
// or any network operation the sandbox still allows, counts as not blocked, which is exit 47 like a missing rule.
internal static class MacNetworkBlock
{
    // sandbox_check(pid, operation, SANDBOX_FILTER_GLOBAL): 1 when the sandbox would deny the operation, 0 when it would allow it,
    // -1 on error. The call is variadic, but no argument follows a global filter.
    [DllImport("/usr/lib/libsandbox.dylib", EntryPoint = "sandbox_check")]
    private static extern int Check(int pid, string operation, int type);

    private static readonly string[] Operations = { "network-outbound", "network-inbound", "network-bind" };

    // Null when the network is blocked, else why that is not known.
    internal static string Problem() => Problem(Ask);

    // The same rule over answers to each operation, so it can be tested without a sandbox.
    internal static string Problem(Func<string, int> answer)
    {
        foreach (string operation in Operations)
        {
            int denied;
            try { denied = answer(operation); }
            catch (Exception error) { return "the sandbox could not be asked (" + error.GetType().Name + ")"; }
            if (denied != 1) return "the sandbox does not deny " + operation + " (answer " + denied + ")";
        }
        return null;
    }

    private static int Ask(string operation) => Check(System.Diagnostics.Process.GetCurrentProcess().Id, operation, 0);

    // The refusal admission reports as exit 47 when the network is not blocked.
    internal static void Verify()
    {
        string problem = Problem();
        if (problem != null)
            throw new InvalidOperationException("SoloStone's network block is missing: " + problem + ". Start the game from the SoloStone launcher.");
    }
}
