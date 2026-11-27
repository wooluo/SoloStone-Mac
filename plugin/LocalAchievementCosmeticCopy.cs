using System;
using System.Linq;

// Player-facing acquisition copy for the closed achievement-exclusive cosmetic registry.
internal static class LocalAchievementCosmeticCopy
{
    internal static string ForHeroSkin(string heroSkinId, string retailFlavor)
    {
        if (!AchievementExclusiveCosmetics.ContainsHeroSkin(heroSkinId)) return null;
        return Build(retailFlavor, LocalAchievementCatalog.All.Single(row => row.Reward.HeroSkinId == heroSkinId));
    }

    internal static string ForCardBack(int cardBackId, string retailDescription)
    {
        if (!AchievementExclusiveCosmetics.ContainsCardBack(cardBackId)) return null;
        string flavor = (retailDescription ?? "").Replace("\r\n", "\n");
        int acquisition = flavor.IndexOf("\n\n", StringComparison.Ordinal);
        if (acquisition >= 0) flavor = flavor.Substring(0, acquisition);
        return Build(flavor, LocalAchievementCatalog.All.Single(row => row.Reward.CardBackId == cardBackId));
    }

    private static string Build(string flavor, LocalAchievementDefinition achievement)
    {
        // SoloStone's own text, in the running language; a name that brings its own full stop ends the sentence with it.
        string name = LocalText.Get(achievement.Name);
        char last = name[name.Length - 1];
        string acquisition = last == '.' || last == '!' || last == '?' || last == '…' ? LocalText.Format("Earned through {0}", name)
            : LocalText.Format("Earned through {0}.", name);
        string prefix = (flavor ?? "").Trim();
        return (prefix.Length == 0 ? "" : prefix + "\n\n")
            + acquisition + "\n" + LocalText.Get("Open Feats to view the condition.");
    }
}
