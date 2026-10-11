/**
 * AI agent avatar presets - preset icons and colors.
 *
 * Shared by the chat panel (ai-chat.js) and the agent editor
 * (settings-ai-agents.js). Icon/color names must stay in sync with
 * AGENT_ICONS / AGENT_COLOR_COUNT in
 * app/application/services/ai_agent_service.py.
 */
(function () {
    'use strict';

    function svg(inner, size) {
        var s = size || 16;
        return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
            'width="' + s + '" height="' + s + '" aria-hidden="true">' + inner + '</svg>';
    }

    var ICON_PATHS = {
        sparkles: '<path d="M12 3l1.9 5.7a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-2a2 2 0 0 0 1.3-1.3z"/><path d="M19 3v4"/><path d="M17 5h4"/>',
        tag: '<path d="M20.6 13.4 12 22 2 12V2h10l8.6 8.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1"/>',
        book: '<path d="M2 4h6a4 4 0 0 1 4 4v12a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v12a3 3 0 0 1 3-3h7z"/>',
        brush: '<path d="m14.6 3.4 6 6-8.8 8.8a4 4 0 0 1-5.2.4L2 22l3.4-4.6a4 4 0 0 1 .4-5.2z"/><path d="m9 15 6-6"/>',
        wrench: '<path d="M14.7 6.3a4.5 4.5 0 0 0 6 6l-8.4 8.4a2.1 2.1 0 0 1-3-3z"/><path d="m14.7 6.3 3-3 3 3-3 3"/>',
        folder: '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.2 3.9A2 2 0 0 0 7.5 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z"/>',
        image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
        note: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
        star: '<path d="M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8-6.2-3.2-6.2 3.2L7 14.2l-5-4.9 6.9-1z"/>',
        search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    };

    // Dark-theme friendly accents; index order is the color_index palette.
    var COLORS = [
        '#3b82f6',
        '#22c55e',
        '#f59e0b',
        '#ef4444',
        '#a855f7',
        '#06b6d4',
    ];

    /**
     * Inline SVG for a preset icon name (falls back to sparkles).
     */
    function agentIconSvg(name, size) {
        var inner = ICON_PATHS[name] || ICON_PATHS.sparkles;
        return svg(inner, size);
    }

    /**
     * CSS color for a palette index (clamped, falls back to accent blue).
     */
    function agentColor(index) {
        var i = typeof index === 'number' && index >= 0
            ? Math.min(index, COLORS.length - 1)
            : 0;
        return COLORS[i] || COLORS[0];
    }

    window.AGENT_ICONS = Object.keys(ICON_PATHS);
    window.AGENT_COLORS = COLORS;
    window.agentIconSvg = agentIconSvg;
    window.agentColor = agentColor;
})();
