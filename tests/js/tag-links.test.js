/**
 * Unit tests for the pure [[tag_name]] wiki-link helpers from tag-links.js:
 * markdown preprocessing before marked.parse and href classification after
 * rendering.
 */
const {
    TAG_LINK_HREF_PREFIX,
    preprocessTagLinks,
    parseTagLinkHref,
    parseTagPageHref,
} = require('../../app/static/js/tag-links.js');

describe('preprocessTagLinks', () => {
    test('converts a single wiki-link into a markdown link', () => {
        expect(preprocessTagLinks('See [[red_fox]] today.')).toBe(
            'See [red_fox](#tag=red_fox) today.'
        );
    });

    test('converts several wiki-links in one pass', () => {
        const out = preprocessTagLinks('[[a_b]] and [[c]]');
        expect(out).toBe('[a_b](#tag=a_b) and [c](#tag=c)');
    });

    test('encodes special characters in the href but not the label', () => {
        // encodeURIComponent leaves balanced parentheses literal, which is
        // still a valid CommonMark link destination.
        const out = preprocessTagLinks('[[name (2)]]');
        expect(out).toBe('[name (2)](#tag=name%20(2))');
        expect(parseTagLinkHref('#tag=name%20(2)')).toBe('name (2)');
    });

    test('trims whitespace inside the brackets', () => {
        expect(preprocessTagLinks('[[  spaced  ]]')).toBe(
            '[spaced](#tag=spaced)'
        );
    });

    test('leaves single brackets and empty links untouched', () => {
        expect(preprocessTagLinks('[not a link] and [[]]')).toBe(
            '[not a link] and [[]]'
        );
    });

    test('does not match across newlines', () => {
        const text = '[[a\nb]]';
        expect(preprocessTagLinks(text)).toBe(text);
    });

    test('returns non-string and link-free input unchanged', () => {
        expect(preprocessTagLinks('')).toBe('');
        expect(preprocessTagLinks(null)).toBe(null);
        expect(preprocessTagLinks('plain text')).toBe('plain text');
    });
});

describe('parseTagLinkHref', () => {
    test('round-trips a preprocessed href back to the tag name', () => {
        const href = TAG_LINK_HREF_PREFIX + encodeURIComponent('some_tag');
        expect(parseTagLinkHref(href)).toBe('some_tag');
    });

    test('decodes encoded names', () => {
        expect(parseTagLinkHref('#tag=name%20%282%29')).toBe('name (2)');
    });

    test('rejects other fragment and regular hrefs', () => {
        expect(parseTagLinkHref('#section')).toBe(null);
        expect(parseTagLinkHref('/tags?tag=x')).toBe(null);
        expect(parseTagLinkHref('https://example.com')).toBe(null);
        expect(parseTagLinkHref('#tag=')).toBe(null);
        expect(parseTagLinkHref(null)).toBe(null);
    });

    test('rejects malformed percent-encoding', () => {
        expect(parseTagLinkHref('#tag=%zz')).toBe(null);
    });
});

describe('parseTagPageHref', () => {
    test.each([
        ['?tag=red_fox', 'red_fox'],
        ['/tags?tag=red_fox', 'red_fox'],
        ['/synth/tags?tag=red_fox', 'red_fox'],
        ['tags?tag=red_fox', 'red_fox'],
    ])('parses %s as tag %s', (href, expected) => {
        expect(parseTagPageHref(href)).toBe(expected);
    });

    test('rejects links that are not the tag page or have no tag param', () => {
        expect(parseTagPageHref('/gallery?tag=x')).toBe(null);
        expect(parseTagPageHref('/tags?q=x')).toBe(null);
        expect(parseTagPageHref('/tags')).toBe(null);
        expect(parseTagPageHref('#tag=x')).toBe(null);
    });
});
