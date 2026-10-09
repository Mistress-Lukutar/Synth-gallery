/**
 * Unit tests for the protocol -> default base URL helper of ai-chat.js.
 */
const { defaultBaseUrl } = require('../../app/static/js/ai-chat.js');

describe('defaultBaseUrl', () => {
    test('openai_compatible -> api.openai.com/v1', () => {
        expect(defaultBaseUrl('openai_compatible')).toBe('https://api.openai.com/v1');
    });

    test('anthropic -> api.anthropic.com/v1', () => {
        expect(defaultBaseUrl('anthropic')).toBe('https://api.anthropic.com/v1');
    });

    test('google_gemini -> generativelanguage.googleapis.com/v1beta', () => {
        expect(defaultBaseUrl('google_gemini')).toBe(
            'https://generativelanguage.googleapis.com/v1beta'
        );
    });

    test('unknown protocol falls back to the OpenAI default', () => {
        expect(defaultBaseUrl('mistral')).toBe('https://api.openai.com/v1');
        expect(defaultBaseUrl('')).toBe('https://api.openai.com/v1');
        expect(defaultBaseUrl(undefined)).toBe('https://api.openai.com/v1');
        expect(defaultBaseUrl(null)).toBe('https://api.openai.com/v1');
    });
});
