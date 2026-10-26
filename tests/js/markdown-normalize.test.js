/**
 * Unit tests for normalizeMarkdownLists from markdown-normalize.js:
 * booru/MediaWiki-style multi-asterisk list prefixes must become standard
 * indented Markdown lists before marked.parse sees the text.
 */
const { normalizeMarkdownLists } = require('../../app/static/js/markdown-normalize.js');

describe('normalizeMarkdownLists', () => {
    test('demotes a second-level booru list to an indented markdown list', () => {
        expect(normalizeMarkdownLists('** [[andromorph]] — male body with vulva'))
            .toBe('  * [[andromorph]] — male body with vulva');
    });

    test('rewrites the full gender-tags example', () => {
        const input = [
            '* [[ambiguous_gender]]',
            '* [[male]]',
            '* [[female]]',
            '* [[intersex]]',
            '** [[andromorph]] — male body with vulva, no penis',
            '** [[gynomorph]] — female body with penis, no vulva',
            '** [[herm]] — female body with both vulva and penis',
            '** [[maleherm]] — male body with both vulva and penis',
        ].join('\n');
        const expected = [
            '* [[ambiguous_gender]]',
            '* [[male]]',
            '* [[female]]',
            '* [[intersex]]',
            '  * [[andromorph]] — male body with vulva, no penis',
            '  * [[gynomorph]] — female body with penis, no vulva',
            '  * [[herm]] — female body with both vulva and penis',
            '  * [[maleherm]] — male body with both vulva and penis',
        ].join('\n');
        expect(normalizeMarkdownLists(input)).toBe(expected);
    });

    test('indents one extra step per additional asterisk level', () => {
        expect(normalizeMarkdownLists('* a\n** b\n*** c\n**** d')).toBe(
            '* a\n  * b\n    * c\n      * d'
        );
    });

    test('leaves single-asterisk list items untouched', () => {
        expect(normalizeMarkdownLists('* a\n* b')).toBe('* a\n* b');
    });

    test('leaves bold spans untouched (no space after the asterisks)', () => {
        expect(normalizeMarkdownLists('**bold** text\nand **more**'))
            .toBe('**bold** text\nand **more**');
    });

    test('leaves a thematic break untouched', () => {
        expect(normalizeMarkdownLists('***\n---')).toBe('***\n---');
    });

    test('leaves code fences and their content untouched', () => {
        const input = '* a\n```\n** not a list\n*** inside code\n```\n** b';
        expect(normalizeMarkdownLists(input)).toBe(
            '* a\n```\n** not a list\n*** inside code\n```\n  * b'
        );
    });

    test('a tilde fence does not close a backtick fence', () => {
        const input = '```\n~~~\n** code\n~~~\n```\n** list';
        expect(normalizeMarkdownLists(input)).toBe(
            '```\n~~~\n** code\n~~~\n```\n  * list'
        );
    });

    test('converts a list marker with tab whitespace', () => {
        expect(normalizeMarkdownLists('**\tsub')).toBe('  * sub');
    });

    test('passes through non-markdown text unchanged', () => {
        expect(normalizeMarkdownLists('plain text')).toBe('plain text');
        expect(normalizeMarkdownLists('')).toBe('');
        expect(normalizeMarkdownLists(null)).toBe(null);
        expect(normalizeMarkdownLists(undefined)).toBe(undefined);
    });
});
