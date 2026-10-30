/**
 * Regression test for the AI chat list mobile animation contract in style.css.
 *
 * BUG: On mobile (<=900px) the drawer `.ai-chat-list` paints above the panel
 * (z-index 165 > 160), so its desktop park position `translateX(100%)` makes
 * it visibly fly across the entire screen right-to-left on open.
 *
 * FIX: a `transform: translateX(-100%)` override for `.ai-chat-list` inside
 * the @media (max-width: 900px) block containing
 * `.ai-chat-panel { left: 0; width: 100% }` parks the closed drawer off the
 * left edge, mirroring the gallery sidebar pattern (.sidebar uses
 * translateX(-100%) on mobile).
 *
 * This test pins the CSS animation contract textually by scanning style.css
 * with a small brace-counting scanner (jsdom's CSSOM is unreliable for a
 * 7000-line sheet).
 */
const fs = require('fs');
const path = require('path');

const CSS_FILE = path.join(__dirname, '..', '..', 'app', 'static', 'style.css');

// --- CSS scanner -----------------------------------------------------------

/**
 * Strip block comments from CSS text while preserving line structure so that
 * character offsets remain valid.
 */
function stripComments(css) {
    return css.replace(/\/\*[\s\S]*?\*\//g, (match) =>
        match.replace(/[^\n]/g, ' ')
    );
}

/**
 * Parse a CSS text into an array of rule descriptors:
 *   { selector, declarations }            (top-level rules)
 *   { mediaQuery, rules: [{ selector, declarations }] }  (@media blocks)
 *
 * Uses depth-counting brace scanning to handle nested @media blocks.
 */
function parseRules(cssText) {
    const css = stripComments(cssText);
    const result = []; // top-level entries (rules + @media blocks)
    let i = 0;

    while (i < css.length) {
        // Skip whitespace
        if (/\s/.test(css[i])) { i++; continue; }

        // Detect start of an @media block
        if (css.startsWith('@media', i)) {
            const closeBrace = findMatchingBrace(css, i);
            if (closeBrace === -1) break;
            const mediaBlock = css.slice(i, closeBrace + 1);
            const queryEnd = mediaBlock.indexOf('{');
            const mediaQuery = mediaBlock.slice('@media'.length, queryEnd).trim();

            const inner = mediaBlock.slice(queryEnd + 1, -1);
            const rules = parseRules(inner);
            result.push({ type: 'media', mediaQuery, rules });
            i = closeBrace + 1;
            continue;
        }

        // Top-level rule: selector { declarations }
        const bracePos = css.indexOf('{', i);
        if (bracePos === -1) break;

        const selector = css.slice(i, bracePos).trim();
        const closeBrace = findMatchingBrace(css, bracePos);
        if (closeBrace === -1) break;

        const declarationsText = css.slice(bracePos + 1, closeBrace);
        const declarations = parseDeclarations(declarationsText);

        result.push({ type: 'rule', selector, declarations });
        i = closeBrace + 1;
    }

    return result;
}

/**
 * Find the index of the closing brace that matches the opening brace at `start`.
 * Returns -1 if not found.
 */
function findMatchingBrace(css, start) {
    let depth = 0;
    for (let j = start; j < css.length; j++) {
        if (css[j] === '{') depth++;
        else if (css[j] === '}') {
            depth--;
            if (depth === 0) return j;
        }
    }
    return -1;
}

/**
 * Split a declaration block into { property, value } pairs.
 * Splits on top-level semicolons only (values in this context don't contain braces).
 */
function parseDeclarations(block) {
    const decls = [];
    const parts = block.split(';');
    for (const part of parts) {
        const trimmed = part.trim();
        if (!trimmed) continue;
        const colonIdx = trimmed.indexOf(':');
        if (colonIdx === -1) continue;
        const property = trimmed.slice(0, colonIdx).trim().toLowerCase();
        const value = trimmed.slice(colonIdx + 1).trim();
        if (property && value) {
            decls.push({ property, value });
        }
    }
    return decls;
}

/**
 * Find the `transform` declaration value for a rule with an exact selector match.
 */
function findTransform(rules, exactSelector) {
    for (const entry of rules) {
        if (entry.type === 'rule' && entry.selector === exactSelector) {
            const decl = entry.declarations.find((d) => d.property === 'transform');
            return decl ? decl.value : null;
        }
    }
    return null;
}

/**
 * Collect all rules with a given exact selector from @media blocks.
 * Returns array of { mediaQuery, selector, declarations }.
 */
function findMediaRules(mediaBlocks, exactSelector) {
    const matches = [];
    for (const entry of mediaBlocks) {
        if (entry.type === 'media') {
            for (const rule of entry.rules) {
                if (rule.type === 'rule' && rule.selector === exactSelector) {
                    matches.push(rule);
                }
            }
        }
    }
    return matches;
}

// --- Load and parse CSS ----------------------------------------------------

const cssText = fs.readFileSync(CSS_FILE, 'utf8');
const topLevelRules = parseRules(cssText);

// Separate top-level rules from @media blocks
const topLevelOnly = [];
const mediaBlocks = [];
for (const entry of topLevelRules) {
    if (entry.type === 'rule') {
        topLevelOnly.push(entry);
    } else if (entry.type === 'media') {
        mediaBlocks.push(entry);
    }
}

// --- Assertions -------------------------------------------------------------

describe('ai-chat-list animation contract', () => {
    test('top-level .ai-chat-list declares transform: translateX(100%) (desktop park)', () => {
        const value = findTransform(topLevelOnly, '.ai-chat-list');
        expect(value).toBe('translateX(100%)');
    });

    test('body.ai-chat-open.ai-chat-list-open .ai-chat-list declares transform: translateX(0) (open state)', () => {
        const value = findTransform(topLevelOnly, 'body.ai-chat-open.ai-chat-list-open .ai-chat-list');
        expect(value).toBe('translateX(0)');
    });

    test('.ai-chat-list inside @media (max-width: 900px) declares transform: translateX(-100%) (mobile slide from left)', () => {
        const matches = findMediaRules(mediaBlocks, '.ai-chat-list');
        expect(matches.length).toBe(1);
        const decl = matches[0].declarations.find((d) => d.property === 'transform');
        expect(decl && decl.value).toBe('translateX(-100%)');
    });

    test('no .ai-chat-list inside @media (max-width: 900px) declares translateX(100%)', () => {
        const matches = findMediaRules(mediaBlocks, '.ai-chat-list');
        for (const rule of matches) {
            const decl = rule.declarations.find((d) => d.property === 'transform');
            if (decl) {
                expect(decl.value).not.toBe('translateX(100%)');
            }
        }
    });
});
