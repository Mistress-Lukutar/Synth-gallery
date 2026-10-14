/**
 * Shared Markdown preprocessing used by every marked.parse call site
 * (notes, AI chat, tag descriptions, item descriptions).
 *
 * normalizeMarkdownLists rewrites booru/MediaWiki-style multi-level list
 * prefixes into standard Markdown: "** item" / "*** item" become a "* item"
 * line indented by (level-1) two-space steps. Standard Markdown has no "**"
 * list marker, so marked flattens those lines into the previous item's
 * paragraph and pairs the asterisks up as emphasis. Single "* item" lines,
 * emphasis like "**bold**" (no whitespace after the asterisks) and thematic
 * breaks ("***") are left untouched; fenced code blocks are skipped.
 *
 * Pure string helper, exported via a CommonJS guard so Jest can test it.
 */

function normalizeMarkdownLists(text) {
    if (!text || typeof text !== 'string' || text.indexOf('*') === -1) {
        return text;
    }
    var lines = text.split('\n');
    var fence = null; // opening fence marker (char + length) while inside a block
    for (var i = 0; i < lines.length; i++) {
        var fenceMatch = lines[i].match(/^ {0,3}(`{3,}|~{3,})/);
        if (fenceMatch) {
            if (!fence) {
                fence = fenceMatch[1];
            } else if (
                fenceMatch[1].charAt(0) === fence.charAt(0) &&
                fenceMatch[1].length >= fence.length
            ) {
                fence = null; // closing fence
            }
            continue;
        }
        if (fence) continue;
        var listMatch = lines[i].match(/^(\*{2,})[ \t]+/);
        if (listMatch) {
            var indent = new Array(listMatch[1].length).join('  ');
            lines[i] = indent + '* ' + lines[i].slice(listMatch[0].length);
        }
    }
    return lines.join('\n');
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        normalizeMarkdownLists: normalizeMarkdownLists,
    };
}
