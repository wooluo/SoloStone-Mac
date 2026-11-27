using System;
using System.Linq;
using BepInEx.Configuration;
using MiniJSON;
using UnityEngine;

// The "SoloStone Offline Mod" label in the top-right corner (FpsCounterLayout.PlaceBrand), so the player can see at a glance that this
// is not retail. One IMGUI label drawn from the plugin's OnGUI, in every scene and match (mulligan, result screen and pack opening
// included), in the installer build too (OfflineProbe.OnGUI has no build-flavour conditional). It takes no input (a Label never does)
// and touches no game state. The FPS label (NativeFpsCounter) sits directly under it when both are on, and takes the corner itself
// when the label is off. The text extent is measured once per resolution, so a repaint allocates nothing.
// Its setting is [Display] BrandLabel in BepInEx/config/solostone.plugin.cfg, read once at startup. It is off by default in this fork:
// the corner stays as the game has it, and whoever wants the label back turns it on there.
// Every path fails closed: a draw error disables the label for the session with one warning, never throws into the frame.
internal static class NativeBrandLabel
{
    private static ConfigEntry<bool> enabled;
    private static GUIStyle text, shadow;
    private static int styleSize;
    private static readonly GUIContent content = new GUIContent(BrandLabelText.Text);
    private static Vector2 extent;
    private static bool failed;
    private static FpsRect drawn;
    private static int drawnFrame = -100, drawnWidth, drawnHeight;

    // The rect drawn in this very frame (empty when the label is off, was not drawn yet or failed): what the FPS label stacks under.
    internal static FpsRect PlacedThisFrame => drawnFrame == Time.frameCount ? drawn : default;

    // Global, like FPS Counter. Read once at startup; a change applies from the next start.
    internal static void Initialize(ConfigFile file)
    {
        enabled = file.Bind("Display", "BrandLabel", false,
            "Show \"SoloStone Offline Mod\" in the top-right corner of every screen, so it is plain this is not the retail game. "
            + "Applies from the next start. Off by default.");
    }

    // Called from OfflineProbe.OnGUI. Repaint only: Layout and input events never draw.
    internal static void Draw()
    {
        if (enabled == null || !enabled.Value) return;
        if (failed || Event.current.type != EventType.Repaint) return;
        try
        {
            int size = FpsCounterLayout.BrandFontSize(Screen.height);
            if (text == null || styleSize != size)
            {
                text = new GUIStyle(GUI.skin.label) { fontSize = size, wordWrap = false, clipping = TextClipping.Overflow };
                text.padding = new RectOffset(0, 0, 0, 0);
                text.normal.textColor = new Color(1f, 1f, 1f, .92f);
                shadow = new GUIStyle(text);
                shadow.normal.textColor = new Color(0f, 0f, 0f, .85f);
                styleSize = size; extent = text.CalcSize(content);
            }
            FpsRect place = FpsCounterLayout.PlaceBrand(Screen.width, Screen.height, extent.x, extent.y);
            var area = new Rect(place.X, place.Y, place.Width, place.Height);
            float offset = Mathf.Max(1f, size / 14f);
            GUI.Label(new Rect(area.x + offset, area.y + offset, area.width, area.height), content, shadow);
            GUI.Label(area, content, text);
            drawn = place; drawnFrame = Time.frameCount; drawnWidth = Screen.width; drawnHeight = Screen.height;
        }
        catch (Exception error)
        {
            failed = true;
            drawn = default;
            Debug.LogWarning("SoloStone brand label disabled for the session: " + error.Message);
        }
    }

    // Test control (state.brandLabel): the fixed text; `drawn`: a repaint in the last frames issued the label's draw call at the
    // current resolution (the call returned: not proof of pixels, so look at a screenshot); and where (normalized 1280x720,
    // top-left origin, like every ui rect and state.fpsCounter.screenRect).
    internal static JsonNode State()
    {
        int width = Screen.width, height = Screen.height;
        bool issued = enabled != null && enabled.Value && !failed && Time.frameCount - drawnFrame <= 2 && drawnWidth == width && drawnHeight == height
            && FpsCounterLayout.OnScreen(drawn, width, height);
        var node = new JsonNode {
            { "text", BrandLabelText.Text }, { "failed", failed }, { "drawn", issued }, { "fontSize", (long)styleSize },
            { "enabled", enabled != null && enabled.Value },
            { "screen", TestControlProtocol.List(new object[] { (long)width, (long)height }) }
        };
        node["screenRect"] = !issued ? null : TestControlProtocol.List(FpsCounterLayout.Normalized(drawn, width, height).Select(value => (object)Math.Round((double)value, 1)));
        return node;
    }
}
