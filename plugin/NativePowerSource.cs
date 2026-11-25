using System;
using BepInEx.Logging;
using HarmonyLib;
using UnityEngine;

// Frame rate persistence (owner 2026-10-28). Native GraphicsManager.UpdateFramerateSettings runs at every start: when
// ITouchScreenService.GetBatteryMode() is BatteryPower and the saved GFX_TARGET_FRAME_RATE is above 30 it clamps the rate
// to 30 AND writes the 30 back to the options file. UnityTouchScreenService derives that from SystemInfo.batteryStatus,
// which Proton reports as Discharging on a handheld running on battery. So the Steam Deck "forgot" High at every boot.
// A handheld has no business throttling itself on battery and overwriting the player's choice: put the player's rate back (Deck host only).
// The first-run default (GFX_TARGET_FRAME_RATE absent -> 60) is untouched.
// Hearthstone build 255790 has neither UnityTouchScreenService nor its PowerSource, so there is no GetBatteryMode to report AC
// power through any more (it did until 254477). Only the restore below remains, and it alone puts the player's choice back.
internal static class NativePowerSource
{
    private static ManualLogSource log;

    // Deck host only: a Windows laptop keeps the native battery throttle (nobody asked to change it there).
    internal static void Initialize(Harmony harmony, ManualLogSource logger, bool deckHost)
    {
        log = logger;
        if (!deckHost) return;
        // Whatever lowers the saved rate inside UpdateFramerateSettings, the player's higher choice is put back.
        harmony.Patch(AccessTools.Method(typeof(GraphicsManager), "UpdateFramerateSettings")
            ?? throw new MissingMethodException("GraphicsManager", "UpdateFramerateSettings"),
            prefix: new HarmonyMethod(typeof(NativePowerSource), nameof(RememberRate)),
            postfix: new HarmonyMethod(typeof(NativePowerSource), nameof(KeepRate)));
    }

    private static int savedRate;

    private static void RememberRate()
    {
        try { savedRate = Options.Get().HasOption(Option.GFX_TARGET_FRAME_RATE) ? Options.Get().GetInt(Option.GFX_TARGET_FRAME_RATE) : 0; }
        catch { savedRate = 0; }
    }

    private static void KeepRate()
    {
        try
        {
            int now = Options.Get().GetInt(Option.GFX_TARGET_FRAME_RATE), restore = HandheldPolicy.RateToRestore(savedRate, now);
            if (restore == 0) return;
            log?.LogWarning("FRAME_RATE native start-up lowered the saved " + restore + " to " + now + "; restoring it");
            Options.Get().SetInt(Option.GFX_TARGET_FRAME_RATE, restore);
            Application.targetFrameRate = restore;
        }
        catch (Exception error) { log?.LogWarning("FRAME_RATE restore: " + error.Message); }
    }
}
