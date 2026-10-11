/**
 * Reasoning-effort selector helpers in ai-chat.js.
 *
 * reasoningChoices maps the server-side effective UI style (settings
 * .reasoning_ui) to the composer dropdown options; reasoningLabel renders
 * the stored effort value ('default' covers null/undefined).
 */
const {
    reasoningChoices,
    reasoningLabel,
} = require('../../app/static/js/ai-chat.js');

describe('reasoningChoices', () => {
    test('levels style offers Default/Minimal/Low/Medium/High', () => {
        expect(reasoningChoices('levels')).toEqual([
            'default', 'minimal', 'low', 'medium', 'high',
        ]);
    });

    test('toggle styles offer Default/On/Off', () => {
        expect(reasoningChoices('toggle')).toEqual(['default', 'on', 'off']);
        expect(reasoningChoices('enable_toggle')).toEqual(['default', 'on', 'off']);
    });

    test('budget style offers Default/Off/Low/Medium/High', () => {
        expect(reasoningChoices('budget')).toEqual([
            'default', 'off', 'low', 'medium', 'high',
        ]);
    });

    test('unknown/off styles hide the selector', () => {
        expect(reasoningChoices('off')).toEqual([]);
        expect(reasoningChoices(null)).toEqual([]);
        expect(reasoningChoices(undefined)).toEqual([]);
    });
});

describe('reasoningLabel', () => {
    test('maps every choice value to a display label', () => {
        expect(reasoningLabel('default')).toBe('Default');
        expect(reasoningLabel('minimal')).toBe('Minimal');
        expect(reasoningLabel('low')).toBe('Low');
        expect(reasoningLabel('medium')).toBe('Medium');
        expect(reasoningLabel('high')).toBe('High');
        expect(reasoningLabel('on')).toBe('On');
        expect(reasoningLabel('off')).toBe('Off');
    });

    test('null/undefined falls back to Default (provider default)', () => {
        expect(reasoningLabel(null)).toBe('Default');
        expect(reasoningLabel(undefined)).toBe('Default');
    });
});
