/**
 * Shared [[tag_name]] wiki-link helpers for Markdown tag descriptions.
 *
 * [[name]] is converted into a same-document Markdown link before parsing
 * (#tag=<encoded>); parseTagLinkHref reverses such an href back into the
 * tag name. parseTagPageHref additionally recognizes plain Markdown links
 * pointing at the tag page (?tag=name, /tags?tag=name, <base>/tags?tag=name).
 *
 * Pure string helpers, exported via a CommonJS guard so Jest can test them.
 * Limitation by design: [[..]] inside code spans/blocks is converted too
 * (preprocessing runs before marked sees the text).
 */

var TAG_LINK_HREF_PREFIX = '#tag=';

function preprocessTagLinks(text) {
    if (!text || typeof text !== 'string' || text.indexOf('[[') === -1) {
        return text;
    }
    return text.replace(/\[\[([^\[\]\n]+)\]\]/g, function (match, inner) {
        var name = inner.trim();
        if (!name) return match;
        return '[' + name + '](' + TAG_LINK_HREF_PREFIX + encodeURIComponent(name) + ')';
    });
}

function parseTagLinkHref(href) {
    if (typeof href !== 'string' || href.indexOf(TAG_LINK_HREF_PREFIX) !== 0) {
        return null;
    }
    var encoded = href.slice(TAG_LINK_HREF_PREFIX.length);
    if (!encoded) return null;
    try {
        return decodeURIComponent(encoded) || null;
    } catch (err) {
        return null;
    }
}

function parseTagPageHref(href) {
    if (typeof href !== 'string') return null;
    var qIdx = href.indexOf('?');
    if (qIdx === -1) return null;
    var name = new URLSearchParams(href.slice(qIdx + 1)).get('tag');
    if (!name) return null;
    var path = href.slice(0, qIdx).split('#')[0];
    // Relative "?tag=...", a bare "tags?tag=..." or a path ending in the
    // tag page segment.
    if (path === '' || /\/tags$/.test(path) || path.replace(/^\/+/, '') === 'tags') {
        return name;
    }
    return null;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        TAG_LINK_HREF_PREFIX: TAG_LINK_HREF_PREFIX,
        preprocessTagLinks: preprocessTagLinks,
        parseTagLinkHref: parseTagLinkHref,
        parseTagPageHref: parseTagPageHref,
    };
}
