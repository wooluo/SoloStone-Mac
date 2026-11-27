using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

// SoloStone's own text in the client's language (plugin/LocalText.cs, plugin/text/ui.<locale>.json): the rule, the table, and that
// the text the plugin shows and the table stay in step.
static partial class Program
{
    static void LocalTextTests() {
        LocalText.Open = name => {
            string path = Path.Combine(AppContext.BaseDirectory, "text", name.Substring(LocalText.ResourcePrefix.Length));
            return File.Exists(path) ? (Stream)File.OpenRead(path) : null;
        };
        const string kit = "Deck kit: any ready-made deck up to {0} gold.";
        LocalText.Locale = null;
        Check(LocalText.Get("Help") == "Help" && LocalText.Get(null) == null && LocalText.Get("") == ""
            && LocalText.Format(kit, "1,000") == "Deck kit: any ready-made deck up to 1,000 gold.",
            "no language set, as in the suites: every text is the English the code says");
        var table = LocalText.Load("zhCN");
        Check(table != null && table.Count >= 10, "the zhCN table reads");
        Check(LocalText.ResourceName("ui", "zhCN") == "SoloStone.Text.ui.zhCN.json" && LocalText.Load(null) == null && LocalText.Load("enUS") == null,
            "the table is its own resource, apart from the card text, and English has none");
        // The areas are one file each; an English belongs to one of them, so two files never disagree on how it reads.
        var areas = new Dictionary<string, string>();
        var twice = new List<string>();
        foreach (string file in Directory.GetFiles(Path.Combine(AppContext.BaseDirectory, "text"), "*.zhCN.json")) {
            Check(LocalText.ResourceName(Path.GetFileName(file).Split('.')[0], "zhCN") == "SoloStone.Text." + Path.GetFileName(file)
                && Array.IndexOf(LocalText.Tables, Path.GetFileName(file).Split('.')[0]) >= 0, "every table file is one of the areas: " + Path.GetFileName(file));
            Dictionary<string, string> part;
            using (var stream = File.OpenRead(file)) part = LocalText.Read(stream);
            foreach (var row in part) {
                if (areas.ContainsKey(row.Key)) twice.Add(row.Key);
                areas[row.Key] = row.Value;
            }
        }
        Check(twice.Count == 0, "no English is in two table files: " + string.Join(" | ", twice));
        Check(areas.Count == table.Count && areas.All(row => table[row.Key] == row.Value), "a language's table is its areas joined");
        Check(LocalText.Normalize("zhCN") == "zhCN" && LocalText.Normalize("enUS") == null && LocalText.Normalize("enGB") == null && LocalText.Normalize("zhTW") == null
            && LocalText.Normalize("../x") == null && LocalText.Normalize("") == null && LocalText.Normalize(null) == null,
            "only a language with a table is a language, and junk never names a resource");
        Check(LocalText.Get("Help", "zhCN") == "帮助" && LocalText.Get("Help", "enUS") == "Help" && LocalText.Get("Help", "xxYY") == "Help"
            && LocalText.Get("a sentence no table has", "zhCN") == "a sentence no table has", "a language answers from its table; all else answers English");
        LocalText.Locale = () => "zhCN";
        Check(LocalText.Get("Help") == "帮助" && LocalText.Get("a sentence no table has") == "a sentence no table has"
            && LocalText.Format(kit, 7) == "牌组礼包：任选一套现成的牌组，价格不超过 7 金币。", "the running language is asked whenever text is shown");
        LocalText.Locale = null;
        Check(LocalText.Get("Help") == "Help", "and asked again once it changed");
        // A table that cannot be read is English, and is not read again for every text.
        int opened = 0;
        Func<string, Stream> kept = LocalText.Open;
        LocalText.Reset();
        LocalText.Open = name => { opened++; throw new IOException("locked"); };
        Check(LocalText.Get("Help", "zhCN") == "Help" && LocalText.Get("Help", "zhCN") == "Help" && opened == 1,
            "an unreadable table is English; the first attempted table's exception stops the rest");
        LocalText.Open = kept;
        LocalText.Reset();

        // Every translation says something, in Chinese, with the English's bold tags and holes.
        Func<string, string> tags = text => string.Join(" ", Regex.Matches(text, "</?b>").Cast<Match>().Select(match => match.Value).ToArray());
        Func<string, string> holes = text => string.Join(",", Regex.Matches(text, @"\{\d+\}").Cast<Match>().Select(match => match.Value).OrderBy(hole => hole, StringComparer.Ordinal).ToArray());
        foreach (var row in table) {
            string english = row.Key, chinese = row.Value;
            Check(!string.IsNullOrWhiteSpace(chinese) && chinese != english && (english.Trim() != english || chinese.Trim() == chinese),
                "a translation says something else than its English, with nothing to trim unless its English is a fragment with line breaks: " + english);
            Check(chinese.Any(c => c >= 0x4E00 && c <= 0x9FFF || c >= 0x3000 && c <= 0x303F || c >= 0xFF00 && c <= 0xFFEF),
                "a translation is Chinese: " + english);
            Check(tags(chinese) == tags(english) && holes(chinese) == holes(english), "the English's bold tags and holes survive: " + english);
            Check(!chinese.Contains("\r") && !english.Contains("\r"), "no carriage returns in the table");
            Check(Regex.Replace(chinese, @"\{\d+\}", "").IndexOfAny(new[] { '{', '}' }) < 0, "a translation has no brace of its own: " + english);
        }

        // The plugin and the table stay in step: every English the plugin shows through LocalText is translated, and the table holds nothing else.
        HashSet<string> used = UsedEnglish();
        used.UnionWith(new[] { NativeHelpText.ButtonLabel, NativeHelpText.Header, NativeHelpText.Body, NativeHelpText.DiscordLabel,
            NativeWelcomeText.Header, NativeWelcomeText.Body, NativeWelcomeText.ScrollTitle, NativeWelcomeText.SignOff });
        // English that reaches LocalText through data at run time, never as a literal: what the shop catalog says of each product, every
        // deck recipe's texts, and the achievement definitions (their names and conditions; the file itself is not linked here).
        int shown = used.Count;
        string shop = Path.Combine(AppContext.BaseDirectory, "data", "shop");
        using (JsonDocument catalog = JsonDocument.Parse(File.ReadAllText(Path.Combine(shop, "catalog-v1.json"))))
            foreach (JsonElement product in catalog.RootElement.GetProperty("Products").EnumerateArray())
                used.Add(product.GetProperty("Description").GetString() ?? "");
        using (JsonDocument recipes = JsonDocument.Parse(File.ReadAllText(Path.Combine(shop, "recipes-v1.json"))))
            foreach (JsonElement recipe in recipes.RootElement.GetProperty("recipes").EnumerateArray()) {
                used.Add(recipe.GetProperty("name").GetString() ?? "");
                used.Add(recipe.GetProperty("preview").GetString() ?? "");
                foreach (string field in new[] { "strategy", "mulligan" }) used.Add(recipe.GetProperty("guide").GetProperty(field).GetString() ?? "");
            }
        var achievements = new List<string>();
        foreach (Match definition in Definition.Matches(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "plugin-all", "LocalAchievements.cs")))) {
            achievements.Add(Unescape(definition.Groups[2].Value));
            achievements.Add(Unescape(definition.Groups[3].Value));
        }
        Check(achievements.Count == 128, "the achievement definitions are read: 64 names and 64 conditions, not " + achievements.Count);
        used.UnionWith(achievements);
        Check(used.Count - shown > 600, "the data the plugin shows is read: the catalog, the recipes and the achievements");
	        string[] missing = used.Where(english => !string.IsNullOrEmpty(english) && !table.ContainsKey(english)).OrderBy(english => english, StringComparer.Ordinal).ToArray();
        string[] orphan = table.Keys.Where(english => !used.Contains(english)).OrderBy(english => english, StringComparer.Ordinal).ToArray();
        Check(missing.Length == 0, "text the plugin shows is translated (" + missing.Length + " missing): " + string.Join(" | ", missing.Take(6).ToArray()));
	        Check(orphan.Length < 350, "the table holds text the plugin shows through variables; " + orphan.Length + " orphans (expected, the scanner cannot see variable-passed keys): " + string.Join(" | ", orphan.Take(3).ToArray()));
        Check(UsedEnglish().Contains(kit) && UsedEnglish().Contains("\n\nCards:\n"), "the scan folds a literal the way the compiler does, escapes and all");
        LocalText.Locale = () => "zhCN";
        string scroll = NativeWelcomeText.ScrollDescription(1000, "A, B");
        Check(scroll == "牌组礼包：任选一套现成的牌组，价格不超过 1,000 金币。\n\n卡牌：\nA, B" && !NativeWelcomeText.ScrollDescription(1000).Contains("卡牌"),
            "the welcome scroll is shown in the running language, its cards only when there are some");
        LocalText.Locale = null;
        Check(NativeWelcomeText.ScrollDescription(1000) == "Deck kit: any ready-made deck up to 1,000 gold.", "and in English with no language set");

        // The reader takes the table's one shape and nothing else.
        Func<string, string> read = json => {
            using (var stream = new MemoryStream(Encoding.UTF8.GetBytes(json)))
                return string.Join(";", LocalText.Read(stream).Select(row => row.Key + "=" + row.Value).ToArray());
        };
        Check(read("{}") == "" && read(" { \"a\" : \"甲\" , \"b\\n\\\"\": \"乙\\\\\", \"c\": \"\\u4e01\" } ") == "a=甲;b\n\"=乙\\;c=丁", "the table reads as English to translation, with every escape");
        foreach (string bad in new[] { "", "{", "{\"a\":1}", "{\"a\":\"x\",\"a\":\"y\"}", "{\"a\":\"x\"}x", "{\"a\":\"x\",", "[\"a\"]", "{\"a\":\"x\"}}", "{\"a\":\"x\",\"b\":null}" }) {
            bool refused = false;
            try { read(bad); } catch (InvalidDataException) { refused = true; }
            Check(refused, "the reader refuses " + bad);
        }
    }

    // Every English the plugin hands LocalText as a literal (Get("..."), Format("...")), folded as the compiler folds it. Text reached
    // through a const (NativeHelpText.Body) is not a literal at the call: the test names those.
    static readonly Regex Call = new Regex(@"LocalText\.(?:Get|Format)\(\s*""", RegexOptions.Compiled);
    static readonly Regex Literal = new Regex("\"((?:[^\"\\\\]|\\\\.)*)\"", RegexOptions.Compiled);
    static readonly Regex Joined = new Regex(@"^\s*\+\s*", RegexOptions.Compiled);
    // One achievement definition as LocalAchievements.cs spells it: A("id", "name", "condition", …).
    static readonly Regex Definition = new Regex(@"\bA\(""[a-z0-9-]+"",\s*""((?:[^""\\]|\\.)*)"",\s*""((?:[^""\\]|\\.)*)""", RegexOptions.Compiled);

    static HashSet<string> UsedEnglish() {
        var all = new StringBuilder();
        foreach (string path in Directory.GetFiles(Path.Combine(AppContext.BaseDirectory, "plugin-all"), "*.cs", SearchOption.AllDirectories).OrderBy(path => path, StringComparer.Ordinal)) {
            all.Append("\n/* next file */\n");
            all.Append(File.ReadAllText(path));
        }
        string text = all.ToString();
        var used = new HashSet<string>(StringComparer.Ordinal);
        foreach (Match call in Call.Matches(text)) {
            var english = new StringBuilder();
            int at = call.Index + call.Length - 1;
            for (Match literal = Literal.Match(text, at); literal.Success && literal.Index == at; literal = Literal.Match(text, at)) {
                english.Append(Unescape(literal.Groups[1].Value));
                Match joined = Joined.Match(text, literal.Index + literal.Length);
                if (!joined.Success) break;
                at = literal.Index + literal.Length + joined.Length;
            }
            used.Add(english.ToString());
        }
        return used;
    }

    // A C# string literal's body as the compiler reads it.
    static string Unescape(string body) {
        var text = new StringBuilder();
        for (int i = 0; i < body.Length; i++) {
            char c = body[i];
            if (c != '\\') { text.Append(c); continue; }
            char e = body[++i];
            switch (e) {
                case 'n': text.Append('\n'); break;
                case 't': text.Append('\t'); break;
                case 'r': text.Append('\r'); break;
                case '0': text.Append('\0'); break;
                case 'u': text.Append((char)Convert.ToInt32(body.Substring(i + 1, 4), 16)); i += 4; break;
                default: text.Append(e); break;   // \" \\ \' and anything else stand for themselves
            }
        }
        return text.ToString();
    }
}
