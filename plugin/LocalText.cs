using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

// SoloStone's own text in the client's language. Pure (no Unity, no client types) and self-contained: a suite that compiles a file that
// asks it links this one file and nothing else.
// The English is what the code says, and it is the key: Get(english) answers the translation for the running language when the table has
// one for exactly that English, and the English itself when it has none. So a sentence edited since it was translated shows English, never
// a translation that says something else, and a language without a table is English throughout.
// Text is translated where it is shown and never where it is saved: saves, ids, keys and log lines stay English.
// A language's table is a few files, one per area of SoloStone's text (plugin/text/<area>.<locale>.json, each a flat object from English
// to translation), so each area can be written and read on its own. They are embedded in the plugin and read once, on first use, into
// one table per language. A closed-schema reader, as for the card text: Unity's Mono has no usable JSON serializer.
internal static class LocalText
{
    // The languages SoloStone's own text has a table for.
    private static readonly string[] Languages = { "zhCN" };
    // The areas, each one file. A language needs none of them: what it has no file for, or no entry for, shows English.
    internal static readonly string[] Tables = { "ui", "core", "achievements", "shop", "recipes" };
    internal const string ResourcePrefix = "SoloStone.Text.";
    // The client's language (a client locale such as zhCN, null for English), asked whenever text is shown. NativeLanguageOption sets
    // it once it knows the language. While it is unset, as in the suites, every text is English.
    internal static Func<string> Locale;
    // Opens a table by its resource name; null when there is none. The plugin reads its assembly, the tests read plugin/text/.
    internal static Func<string, Stream> Open = name => typeof(LocalText).Assembly.GetManifestResourceStream(name);

    private static readonly object gate = new object();
    private static readonly Dictionary<string, Dictionary<string, string>> loaded = new Dictionary<string, Dictionary<string, string>>(StringComparer.Ordinal);

    internal static string ResourceName(string table, string locale) => ResourcePrefix + table + "." + locale + ".json";

    // Null for English, for a language SoloStone has no text for, and for anything that must never name a resource.
    internal static string Normalize(string locale) => locale != null && Array.IndexOf(Languages, locale) >= 0 ? locale : null;

    // The text in the running language.
    internal static string Get(string english)
    {
        Func<string> locale = Locale;
        return Get(english, locale == null ? null : locale());
    }

    internal static string Get(string english, string locale)
    {
        if (string.IsNullOrEmpty(english)) return english;
        var table = Load(locale);
        string translated;
        return table != null && table.TryGetValue(english, out translated) && !string.IsNullOrEmpty(translated) ? translated : english;
    }

    // A sentence with holes, so a translation can put them in another order: Format("Rank: {0} → {1}", before, after).
    internal static string Format(string english, params object[] args)
    {
        Func<string> locale = Locale;
        return string.Format(Get(english, locale == null ? null : locale()), args);
    }

    // One table for the language, all its areas joined; the first file wins an English that two of them have (the tests refuse that).
    // Null when no file reads: remembered either way, so a broken file is not re-read on every text.
    internal static Dictionary<string, string> Load(string locale)
    {
        locale = Normalize(locale);
        if (locale == null) return null;
        lock (gate)
        {
            if (loaded.TryGetValue(locale, out var table)) return table;
            try
            {
                table = new Dictionary<string, string>(StringComparer.Ordinal);
                foreach (string area in Tables)
                {
                    Dictionary<string, string> part;
                    using (var stream = Open(ResourceName(area, locale)))
                    {
                        if (stream == null) continue;   // the language has no file for this area
                        part = Read(stream);
                    }
                    foreach (var row in part) if (!table.ContainsKey(row.Key)) table.Add(row.Key, row.Value);
                }
            }
            catch (Exception) { table = null; }
            loaded[locale] = table;
            return table;
        }
    }

    internal static void Reset() { lock (gate) loaded.Clear(); }

    // A flat object from English to translation. Nothing else: no nesting, no numbers, no comments.
    internal static Dictionary<string, string> Read(Stream stream)
    {
        string json;
        using (var reader = new StreamReader(stream, new UTF8Encoding(false), false)) json = reader.ReadToEnd();
        var table = new Dictionary<string, string>(StringComparer.Ordinal);
        var parser = new Reader(json);
        parser.Expect('{');
        if (parser.Peek() == '}') parser.Next('}');   // an empty table: English throughout
        else
            do
            {
                string english = parser.Str();
                parser.Expect(':');
                string translated = parser.Str();
                if (table.ContainsKey(english)) throw parser.Bad("the same English twice");
                table.Add(english, translated);
            } while (parser.Next('}'));
        parser.Space();
        if (parser.At != json.Length) throw parser.Bad("trailing data");
        return table;
    }

    private sealed class Reader
    {
        private readonly string json;
        private int at;

        internal Reader(string text) { json = text; }
        internal int At => at;

        internal string Str()
        {
            Space();
            if (Peek() != '"') throw Bad("string expected");
            at++;
            var builder = new StringBuilder();
            while (true)
            {
                if (at >= json.Length) throw Bad("unterminated string");
                char c = json[at++];
                if (c == '"') return builder.ToString();
                if (c < 0x20) throw Bad("control character in string");
                if (c != '\\') { builder.Append(c); continue; }
                if (at >= json.Length) throw Bad("unterminated escape");
                char e = json[at++];
                switch (e)
                {
                    case '"': case '\\': case '/': builder.Append(e); break;
                    case 'b': builder.Append('\b'); break;
                    case 'f': builder.Append('\f'); break;
                    case 'n': builder.Append('\n'); break;
                    case 'r': builder.Append('\r'); break;
                    case 't': builder.Append('\t'); break;
                    case 'u':
                        if (at + 4 > json.Length) throw Bad("bad escape");
                        int code = 0;
                        for (int i = 0; i < 4; i++)
                        {
                            int digit = "0123456789abcdef".IndexOf(char.ToLowerInvariant(json[at + i]));
                            if (digit < 0) throw Bad("bad escape");
                            code = code * 16 + digit;
                        }
                        builder.Append((char)code);
                        at += 4;
                        break;
                    default: throw Bad("bad escape");
                }
            }
        }

        internal bool Next(char close)
        {
            Space();
            if (at >= json.Length) throw Bad("unexpected end");
            char c = json[at++];
            if (c == ',') return true;
            if (c == close) return false;
            throw Bad("expected , or " + close);
        }

        internal void Expect(char c)
        {
            Space();
            if (at >= json.Length || json[at] != c) throw Bad("expected " + c);
            at++;
        }

        internal char Peek() => at < json.Length ? json[at] : '\0';
        internal void Space() { while (at < json.Length && (json[at] == ' ' || json[at] == '\n' || json[at] == '\r' || json[at] == '\t')) at++; }
        internal InvalidDataException Bad(string what) => new InvalidDataException("text table: " + what + " at " + at);
    }
}
