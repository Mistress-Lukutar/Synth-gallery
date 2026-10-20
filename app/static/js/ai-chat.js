/**
 * AI Chat panel - floating chat against the user's library.
 *
 * Talks to the conversation/chat REST + SSE endpoints:
 *   POST   /api/ai/chat/conversations
 *   GET    /api/ai/chat/conversations
 *   DELETE /api/ai/chat/conversations/{id}
 *   GET    /api/ai/chat/conversations/{id}/messages
 *   POST   /api/ai/chat/conversations/{id}/messages            (SSE stream)
 *   POST   /api/ai/chat/conversations/{id}/messages  edit_of_message_id
 *   POST   /api/ai/chat/conversations/{id}/branch          version switch
 *   POST   /api/ai/chat/conversations/{id}/vision/{req}/decision (SSE stream)
 *
 * Global API: window.AiChat = { open, close, toggle, isOpen, openWithSelection }
 *
 * Pure helpers (parseSSEFrames / applyChatEvent / defaultBaseUrl) are exported
 * through a CommonJS guard for Jest tests.
 */
(function () {
    'use strict';

    // ========================================================================
    // Constants + pure helpers (Jest-tested)
    // ========================================================================

    var DEFAULT_BASE_URLS = {
        openai_compatible: 'https://api.openai.com/v1',
        anthropic: 'https://api.anthropic.com/v1',
        google_gemini: 'https://generativelanguage.googleapis.com/v1beta',
    };

    var PROTOCOL_LABELS = {
        openai_compatible: 'OpenAI-compatible',
        anthropic: 'Anthropic',
        google_gemini: 'Google Gemini',
    };

    function defaultBaseUrl(protocol) {
        return DEFAULT_BASE_URLS[protocol] || DEFAULT_BASE_URLS.openai_compatible;
    }

    /**
     * Parse complete SSE frames out of a (possibly partial) buffer.
     *
     * Returns { frames: [{event, data}], rest } where `rest` holds the
     * trailing incomplete frame (no blank-line terminator yet). Handles
     * "\n\n" and "\r\n\r\n" separators and CR/LF line endings. `event`
     * defaults to 'message'; `data` is JSON-parsed when possible, otherwise
     * the raw string is kept.
     */
    function parseSSEFrames(bufferText) {
        var text = String(bufferText == null ? '' : bufferText);
        var frames = [];

        // Find the end of the LAST complete frame (blank line separator).
        var separator = /\r?\n\r?\n/g;
        var lastEnd = -1;
        var match;
        while ((match = separator.exec(text)) !== null) {
            lastEnd = match.index + match[0].length;
        }
        if (lastEnd === -1) {
            return { frames: frames, rest: text };
        }

        var processable = text.slice(0, lastEnd);
        var rest = text.slice(lastEnd);

        var rawFrames = processable.split(/\r?\n\r?\n/);
        for (var i = 0; i < rawFrames.length; i++) {
            var frame = parseSSEFrameBody(rawFrames[i]);
            if (frame) frames.push(frame);
        }
        return { frames: frames, rest: rest };
    }

    function parseSSEFrameBody(raw) {
        if (!raw) return null;
        var lines = raw.split(/\r?\n/);
        var event = 'message';
        var dataLines = [];
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line === '' || line.charAt(0) === ':') continue; // comment / keep-alive
            if (line.indexOf('event:') === 0) {
                event = line.slice(6).trim();
            } else if (line.indexOf('data:') === 0) {
                dataLines.push(line.slice(5).replace(/^ /, ''));
            }
            // Other SSE fields (id:, retry:) are ignored.
        }
        if (dataLines.length === 0) return null;
        var dataStr = dataLines.join('\n');
        var data = dataStr;
        try {
            data = JSON.parse(dataStr);
        } catch (e) {
            // Keep the raw string when the payload is not JSON.
        }
        return { event: event, data: data };
    }

    // ========================================================================
    // Chat state (pure transitions)
    // ========================================================================

    /**
     * State shape:
     * {
     *   messages: [ { role: 'user'|'assistant'|'tool', parts: [...] } ],
     *   streaming: bool,
     *   pendingVision: null | { request_id, items: [{id, title}] },
     *   stopReason: null | string
     * }
     *
     * Part shapes:
     *   { type: 'text', text }
     *   { type: 'reasoning', text }
     *   { type: 'context', item_ids: [...] }
     *   { type: 'tool', call_id, name, args, status: 'running'|'done'|'error'|'expired',
     *     summary, result, images }
     *   { type: 'error', text }
     */
    function newChatState() {
        return {
            messages: [],
            streaming: false,
            pendingVision: null,
            pendingToolConfirm: null,
            stopReason: null,
        };
    }

    function cloneState(state) {
        var safe = state || newChatState();
        var messages = [];
        for (var i = 0; i < safe.messages.length; i++) {
            var m = safe.messages[i];
            messages.push({
                role: m.role,
                parts: (m.parts || []).map(function (p) {
                    return Object.assign({}, p);
                }),
                // Tree metadata (history-loaded user messages only).
                id: m.id != null ? m.id : null,
                parentId: m.parentId != null ? m.parentId : null,
                siblingIds: Array.isArray(m.siblingIds) ? m.siblingIds.slice() : [],
                version: m.version != null ? m.version : null,
                versionCount: m.versionCount != null ? m.versionCount : null,
            });
        }
        return {
            messages: messages,
            streaming: !!safe.streaming,
            pendingVision: safe.pendingVision
                ? { request_id: safe.pendingVision.request_id, items: (safe.pendingVision.items || []).slice() }
                : null,
            pendingToolConfirm: safe.pendingToolConfirm
                ? {
                    request_id: safe.pendingToolConfirm.request_id,
                    call_id: safe.pendingToolConfirm.call_id,
                    name: safe.pendingToolConfirm.name,
                    arguments: safe.pendingToolConfirm.arguments,
                }
                : null,
            stopReason: safe.stopReason == null ? null : safe.stopReason,
        };
    }

    function lastMessageOf(messages, role) {
        var last = messages[messages.length - 1];
        if (!role) return last || null;
        return last && last.role === role ? last : null;
    }

    function ensureAssistantMessage(messages) {
        var last = lastMessageOf(messages, 'assistant');
        if (!last) {
            last = { role: 'assistant', parts: [] };
            messages.push(last);
        }
        return last;
    }

    /**
     * Append text to the trailing text part of the current assistant message
     * (creating message/part when missing).
     */
    function appendToTypedPart(message, partType, text) {
        var lastPart = message.parts[message.parts.length - 1];
        if (!lastPart || lastPart.type !== partType) {
            lastPart = { type: partType, text: '' };
            message.parts.push(lastPart);
        }
        lastPart.text = (lastPart.text || '') + (text || '');
    }

    function parseToolArgs(args) {
        if (args == null) return null;
        if (typeof args === 'object') return args;
        try {
            return JSON.parse(args);
        } catch (e) {
            return args; // keep raw string
        }
    }

    function findToolCard(messages, callId) {
        for (var i = 0; i < messages.length; i++) {
            var parts = messages[i].parts || [];
            for (var j = 0; j < parts.length; j++) {
                if (parts[j].type === 'tool' && parts[j].call_id === callId) {
                    return parts[j];
                }
            }
        }
        return null;
    }

    /**
     * Pure event reducer. Returns a NEW state; never mutates the input.
     * Unknown event types are ignored (returns an equal state).
     */
    function applyChatEvent(state, event) {
        var ev = event || {};
        var next;

        switch (ev.type) {
            case 'token': {
                next = cloneState(state);
                var msg = ensureAssistantMessage(next.messages);
                appendToTypedPart(msg, 'text', ev.text);
                next.streaming = true;
                return next;
            }

            case 'reasoning': {
                next = cloneState(state);
                var rmsg = ensureAssistantMessage(next.messages);
                appendToTypedPart(rmsg, 'reasoning', ev.text);
                next.streaming = true;
                return next;
            }

            case 'tool_start': {
                next = cloneState(state);
                // A resumed turn (after an approval) re-announces the
                // confirmed call: update the existing card in place so the
                // pre-pause card does not stay "Running..." as a duplicate.
                var started = ev.call_id ? findToolCard(next.messages, ev.call_id) : null;
                if (started) {
                    started.name = ev.name;
                    started.args = parseToolArgs(ev.arguments);
                    started.status = 'running';
                } else {
                    var tmsg = ensureAssistantMessage(next.messages);
                    tmsg.parts.push({
                        type: 'tool',
                        call_id: ev.call_id,
                        name: ev.name,
                        args: parseToolArgs(ev.arguments),
                        status: 'running',
                        summary: null,
                        result: null,
                        images: [],
                    });
                }
                next.streaming = true;
                return next;
            }

            case 'tool_result': {
                next = cloneState(state);
                var card = ev.call_id ? findToolCard(next.messages, ev.call_id) : null;
                var status = ev.is_error ? 'error' : 'done';
                if (card) {
                    card.status = status;
                    if (ev.summary != null) card.summary = ev.summary;
                    if (ev.content != null) card.result = String(ev.content);
                } else {
                    // Result without a visible start (e.g. reconnect): keep it.
                    var umsg = ensureAssistantMessage(next.messages);
                    umsg.parts.push({
                        type: 'tool',
                        call_id: ev.call_id,
                        name: ev.name,
                        args: null,
                        status: status,
                        summary: ev.summary != null ? ev.summary : null,
                        result: ev.content != null ? String(ev.content) : null,
                        images: [],
                    });
                }
                return next;
            }

            case 'vision_request': {
                next = cloneState(state);
                next.pendingVision = {
                    request_id: ev.request_id,
                    items: Array.isArray(ev.items) ? ev.items.slice() : [],
                };
                return next;
            }

            // Internal pseudo-event fired after the user clicks Allow/Deny.
            case 'vision_decided': {
                next = cloneState(state);
                next.pendingVision = null;
                return next;
            }

            // A mutating tool pauses the turn for the user's approval.
            case 'tool_confirm': {
                next = cloneState(state);
                next.pendingToolConfirm = {
                    request_id: ev.request_id,
                    call_id: ev.call_id,
                    name: ev.name,
                    arguments: parseToolArgs(ev.arguments),
                };
                return next;
            }

            // Internal pseudo-event fired after the user answers a
            // tool confirmation card.
            case 'confirm_decided': {
                next = cloneState(state);
                next.pendingToolConfirm = null;
                return next;
            }

            case 'turn_done': {
                next = cloneState(state);
                next.streaming = false;
                next.stopReason = ev.stop_reason == null ? null : ev.stop_reason;
                return next;
            }

            case 'error': {
                next = cloneState(state);
                var emsg = lastMessageOf(next.messages, 'assistant');
                var part = { type: 'error', text: ev.message != null ? String(ev.message) : 'Unknown error' };
                if (emsg) {
                    emsg.parts.push(part);
                } else {
                    next.messages.push({ role: 'assistant', parts: [part] });
                }
                next.streaming = false;
                return next;
            }

            default:
                return cloneState(state);
        }
    }

    /**
     * Append a locally-echoed user message (pure). The echo has no server
     * id yet, so no edit/retry actions are rendered for it. With
     * ``atIndex`` (edit/retry) the message at that index and everything
     * after it are replaced: the new sibling version becomes the visible
     * tail, so the edit never shows up as a second user turn.
     */
    function appendUserMessage(state, text, itemIds, atIndex) {
        var next = cloneState(state);
        var parts = [{ type: 'text', text: text || '' }];
        if (Array.isArray(itemIds) && itemIds.length > 0) {
            parts.push({ type: 'context', item_ids: itemIds.slice() });
        }
        if (typeof atIndex === 'number' && atIndex >= 0) {
            next.messages = next.messages.slice(0, atIndex);
        }
        next.messages.push({
            role: 'user',
            parts: parts,
            id: null,
            parentId: null,
            siblingIds: [],
            version: null,
            versionCount: null,
        });
        return next;
    }

    /**
     * The context part of a stored/optimistic user message, if any.
     */
    function messageContextIds(message) {
        var parts = message.parts || [];
        for (var i = 0; i < parts.length; i++) {
            if (parts[i].type === 'context' && Array.isArray(parts[i].item_ids)) {
                return parts[i].item_ids.slice();
            }
        }
        return [];
    }

    /**
     * First text part of a message ('' when missing).
     */
    function messageText(message) {
        var parts = message.parts || [];
        for (var i = 0; i < parts.length; i++) {
            if (parts[i].type === 'text') return parts[i].text || '';
        }
        return '';
    }

    function normalizeHistoryPart(part) {
        var p = Object.assign({}, part || {});
        if (p.type === 'tool_call') {
            p.type = 'tool';
            p.args = parseToolArgs(p.arguments != null ? p.arguments : p.args);
            delete p.arguments;
            if (!p.status) p.status = 'running';
            if (p.images == null) p.images = [];
        }
        if (p.type === 'tool') {
            if (p.args === undefined) p.args = parseToolArgs(p.arguments);
            if (p.result === undefined) p.result = p.content != null ? p.content : null;
            if (p.images == null || !Array.isArray(p.images)) p.images = [];
        }
        return p;
    }

    /**
     * Build chat state from GET .../messages history. Accepts either the
     * bare message list or the full endpoint payload — the latter carries
     * the conversation's paused vision/confirmation requests, which are
     * restored so the approval cards survive a reload or chat switch.
     * Tool_call parts without any matching tool_result become 'expired',
     * unless they ARE the pending call (still waiting: 'running').
     */
    function stateFromMessages(list) {
        var payload = (list && !Array.isArray(list) && Array.isArray(list.messages))
            ? list : null;
        var items = payload ? payload.messages : (Array.isArray(list) ? list : []);
        var messages = [];
        var resultCallIds = {};

        var pendingVision = null;
        var pendingToolConfirm = null;
        var pendingCallIds = {};
        if (payload && payload.pending_vision) {
            var pv = payload.pending_vision;
            pendingVision = {
                request_id: pv.request_id,
                call_id: pv.call_id || null,
                items: Array.isArray(pv.items) ? pv.items.slice() : [],
            };
            if (pendingVision.call_id) pendingCallIds[pendingVision.call_id] = true;
        }
        if (payload && payload.pending_tool_confirm) {
            var pc = payload.pending_tool_confirm;
            pendingToolConfirm = {
                request_id: pc.request_id,
                call_id: pc.call_id || null,
                name: pc.name || null,
                arguments: parseToolArgs(pc.arguments),
            };
            if (pendingToolConfirm.call_id) pendingCallIds[pendingToolConfirm.call_id] = true;
        }

        var i, j, m, part;
        for (i = 0; i < items.length; i++) {
            m = items[i];
            var parts = (m.parts || []).map(normalizeHistoryPart);
            for (j = 0; j < parts.length; j++) {
                if (parts[j].type === 'tool_result' && parts[j].call_id) {
                    resultCallIds[parts[j].call_id] = true;
                }
            }
            messages.push({
                role: m.role,
                parts: parts,
                // Tree metadata from the server (user messages carry the
                // version group for the ‹k/n› switcher).
                id: m.id != null ? m.id : null,
                parentId: m.parent_id != null ? m.parent_id : null,
                siblingIds: Array.isArray(m.sibling_ids) ? m.sibling_ids.slice() : [],
                version: m.version != null ? m.version : null,
                versionCount: m.version_count != null ? m.version_count : null,
            });
        }

        // Merge tool_result parts (role 'tool' messages) into their tool cards.
        var merged = [];
        for (i = 0; i < messages.length; i++) {
            m = messages[i];
            if (m.role === 'tool') {
                for (j = 0; j < m.parts.length; j++) {
                    part = m.parts[j];
                    if (part.type === 'tool_result') {
                        var card = part.call_id ? findToolCard(merged, part.call_id) : null;
                        if (card) {
                            card.status = part.is_error ? 'error' : 'done';
                            if (part.summary != null) card.summary = part.summary;
                            if (part.content != null) card.result = part.content;
                            if (Array.isArray(part.images) && part.images.length) {
                                card.images = part.images.slice();
                            }
                        } else {
                            merged.push({
                                role: 'assistant',
                                parts: [{
                                    type: 'tool',
                                    call_id: part.call_id,
                                    name: part.name,
                                    args: null,
                                    status: part.is_error ? 'error' : 'done',
                                    summary: part.summary != null ? part.summary : null,
                                    result: part.content != null ? part.content : null,
                                    images: Array.isArray(part.images) ? part.images.slice() : [],
                                }],
                            });
                        }
                    } else {
                        merged.push({ role: 'tool', parts: [part] });
                    }
                }
            } else {
                for (j = 0; j < m.parts.length; j++) {
                    part = m.parts[j];
                    if (part.type === 'tool' && !(part.call_id && resultCallIds[part.call_id])) {
                        part.status = pendingCallIds[part.call_id] ? 'running' : 'expired';
                    }
                }
                merged.push(m);
            }
        }

        return {
            messages: merged,
            streaming: false,
            pendingVision: pendingVision,
            pendingToolConfirm: pendingToolConfirm,
            stopReason: null,
        };
    }

    // ========================================================================
    // Small runtime helpers
    // ========================================================================

    function esc(text) {
        var fn = typeof window !== 'undefined' && window.escapeHtml ? window.escapeHtml : null;
        if (fn) return fn(text);
        if (!text) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function toast(message, isError) {
        if (typeof window !== 'undefined' && typeof window.showToast === 'function') {
            window.showToast(message, isError);
        }
    }

    function baseUrl() {
        return typeof window !== 'undefined' && window.SYNTH_BASE_URL != null
            ? window.SYNTH_BASE_URL
            : '';
    }

    function protocolLabel(protocol) {
        return PROTOCOL_LABELS[protocol] || protocol || '';
    }

    // ========================================================================
    // Reasoning-effort selector (pure helpers, Jest-covered)
    // ========================================================================

    // Composer choices per effective UI style (server: reasoning_ui).
    // 'default' = provider default, nothing is sent.
    var REASONING_UI_CHOICES = {
        levels: ['default', 'minimal', 'low', 'medium', 'high'],
        toggle: ['default', 'on', 'off'],
        enable_toggle: ['default', 'on', 'off'],
        budget: ['default', 'off', 'low', 'medium', 'high'],
    };

    var REASONING_CHOICE_LABELS = {
        default: 'Default',
        minimal: 'Minimal',
        low: 'Low',
        medium: 'Medium',
        high: 'High',
        on: 'On',
        off: 'Off',
    };

    function reasoningChoices(ui) {
        return REASONING_UI_CHOICES[ui] || [];
    }

    function reasoningLabel(value) {
        return REASONING_CHOICE_LABELS[value] || 'Default';
    }

    function renderMarkdownHtml(text) {
        if (typeof window !== 'undefined' && typeof window.marked !== 'undefined' && window.marked.parse) {
            try {
                var html = window.marked.parse(normalizeMarkdownLists(text));
                if (window.DOMPurify) {
                    html = window.DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
                }
                return html;
            } catch (e) {
                // fall through to plain text
            }
        }
        return '<pre class="ai-chat-plain">' + esc(text) + '</pre>';
    }

    // ========================================================================
    // Module state
    // ========================================================================

    var panel = null;
    var els = {};
    var state = newChatState();
    var conversationId = null;
    var conversations = [];
    var chatSettings = null;
    var abortController = null;
    var attachedSelection = false;
    var stickToBottom = true;
    var listVisible = false;
    var renderScheduled = false;
    var initialized = false;
    var sending = false;
    // Agents (GET /api/user/ai/agents) + the active one: the agent of the
    // open conversation, or the draft selection for new chats.
    var agents = [];
    var currentAgentId = null;
    var agentMenuOpen = false;
    // Reasoning-effort choice for the composer chip; mirrors the
    // server-clamped settings.reasoning_effort (null = provider default).
    var reasoningEffort = null;
    var reasoningMenuOpen = false;
    // User-toggled open state of collapsible parts (tool cards / reasoning),
    // keyed by stable part keys. Survives re-renders.
    var expandedParts = Object.create(null);
    // Incremental message rendering: one DOM node + one content signature
    // per state.messages entry, so unchanged messages keep their DOM
    // (images don't reload, user-toggled state survives) while streaming.
    var msgEls = [];
    var msgSigs = [];
    var lastVisionKey = null;
    var lastConfirmKey = null;
    // Server id of the user message currently being edited inline
    // (null = no edit box open).
    var editingMessageId = null;

    // ========================================================================
    // Panel shell (rendered once from a template string)
    // ========================================================================

    var PANEL_TEMPLATE = [
        '<div class="ai-chat-inner">',
        '  <aside class="ai-chat-list" id="ai-chat-list">',
        '    <div class="ai-chat-list-header">',
        '      <span class="ai-chat-list-title">Chats</span>',
        '      <button type="button" id="ai-chat-list-close" class="ai-chat-icon-btn ai-chat-list-close" title="Close chat list" aria-label="Close chat list">',
        '        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
        '      </button>',
        '      <button type="button" id="ai-chat-new-btn" class="btn btn-small">New chat</button>',
        '    </div>',
        '    <div class="ai-chat-conversations" id="ai-chat-conversations"></div>',
        '  </aside>',
        '  <div class="ai-chat-main">',
        '    <header class="ai-chat-header">',
        '      <button type="button" id="ai-chat-list-toggle" class="ai-chat-icon-btn" title="Toggle chat list" aria-label="Toggle chat list">',
        '        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>',
        '      </button>',
        '      <button type="button" id="ai-chat-agent-btn" class="ai-chat-agent-btn" title="Choose agent" aria-label="Choose agent">',
        '        <span class="ai-agent-chip-icon" id="ai-chat-agent-icon"></span>',
        '        <span class="ai-agent-chip-name" id="ai-chat-agent-name">Assistant</span>',
        '        <svg class="ai-agent-chip-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="6 9 12 15 18 9"/></svg>',
        '      </button>',
        '      <span class="ai-chat-title" id="ai-chat-title">New chat</span>',
        '      <span class="ai-chat-model-badge" id="ai-chat-model-badge" title="Active model"></span>',
        '      <button type="button" id="ai-chat-close-btn" class="ai-chat-icon-btn" title="Close" aria-label="Close AI chat">',
        '        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
        '      </button>',
        '    </header>',
        '    <div class="ai-agent-menu hidden" id="ai-agent-menu"></div>',
        '    <div class="ai-chat-messages" id="ai-chat-messages"></div>',
        '    <div class="ai-chat-vision-slot" id="ai-chat-vision-slot"></div>',
        '    <footer class="ai-chat-composer">',
        '      <button type="button" class="ai-chat-reasoning-btn hidden" id="ai-chat-reasoning-btn" title="Reasoning effort" aria-label="Reasoning effort"></button>',
        '      <div class="ai-agent-menu ai-chat-reasoning-menu hidden" id="ai-chat-reasoning-menu"></div>',
        '      <button type="button" class="ai-chat-attach-chip hidden" id="ai-chat-attach-chip" title="Attach the current gallery selection to the next message"></button>',
        '      <div class="ai-chat-input-row">',
        '        <textarea id="ai-chat-input" class="ai-chat-input" rows="1" placeholder="Ask about your library..."></textarea>',
        '        <button type="button" id="ai-chat-send-btn" class="btn btn-small ai-chat-send-btn">Send</button>',
        '      </div>',
        '    </footer>',
        '  </div>',
        '</div>',
    ].join('\n');

    function buildPanel() {
        if (!panel || panel.dataset.built) return;
        panel.innerHTML = PANEL_TEMPLATE;
        panel.dataset.built = 'true';

        els.list = document.getElementById('ai-chat-list');
        els.listToggle = document.getElementById('ai-chat-list-toggle');
        els.listClose = document.getElementById('ai-chat-list-close');
        els.newBtn = document.getElementById('ai-chat-new-btn');
        els.conversations = document.getElementById('ai-chat-conversations');
        els.agentBtn = document.getElementById('ai-chat-agent-btn');
        els.agentIcon = document.getElementById('ai-chat-agent-icon');
        els.agentName = document.getElementById('ai-chat-agent-name');
        els.agentMenu = document.getElementById('ai-agent-menu');
        els.title = document.getElementById('ai-chat-title');
        els.modelBadge = document.getElementById('ai-chat-model-badge');
        els.closeBtn = document.getElementById('ai-chat-close-btn');
        els.messages = document.getElementById('ai-chat-messages');
        els.visionSlot = document.getElementById('ai-chat-vision-slot');
        els.reasoningBtn = document.getElementById('ai-chat-reasoning-btn');
        els.reasoningMenu = document.getElementById('ai-chat-reasoning-menu');
        els.attachChip = document.getElementById('ai-chat-attach-chip');
        els.input = document.getElementById('ai-chat-input');
        els.sendBtn = document.getElementById('ai-chat-send-btn');

        els.listToggle.addEventListener('click', toggleList);
        els.listClose.addEventListener('click', hideList);
        setupListSwipe();
        setupMainSwipe();
        els.newBtn.addEventListener('click', resetToDraft);
        els.agentBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            toggleAgentMenu();
        });
        els.agentMenu.addEventListener('click', onAgentMenuClick);
        els.closeBtn.addEventListener('click', close);
        els.sendBtn.addEventListener('click', onSendButton);
        els.reasoningBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            toggleReasoningMenu();
        });
        els.reasoningMenu.addEventListener('click', onReasoningMenuClick);
        els.attachChip.addEventListener('click', function () {
            setAttached(!attachedSelection);
            renderComposer();
        });
        els.input.addEventListener('input', function () {
            autoResizeInput();
            renderComposer();
        });
        els.input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        els.messages.addEventListener('scroll', function () {
            stickToBottom = isNearBottom();
        });
        // Inline edit box keyboard: Enter saves, Escape cancels.
        els.messages.addEventListener('keydown', function (e) {
            var ta = e.target.closest ? e.target.closest('[data-edit-input]') : null;
            if (!ta) return;
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                saveEdit();
            } else if (e.key === 'Escape') {
                e.preventDefault();
                cancelEdit();
            }
        });
        // Inline edit box grows with its content (capped, then scrolls).
        els.messages.addEventListener('input', function (e) {
            var ta = e.target.closest ? e.target.closest('[data-edit-input]') : null;
            if (ta) autoSizeEditInput(ta);
        });

        // Wheel over the panel must never reach the gallery behind it:
        // inner scrollable areas consume the delta, everything else
        // (header, composer, an edge-hit container) is blocked instead of
        // chaining into the document scroll. CSS overscroll-behavior on
        // the containers handles touch; this covers the mouse wheel.
        panel.addEventListener('wheel', function (e) {
            var horizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY);
            var delta = horizontal ? e.deltaX : e.deltaY;
            if (!delta) return;
            var node = e.target;
            while (node && node !== panel) {
                if (node.nodeType === 1) {
                    var style = window.getComputedStyle(node);
                    var overflow = horizontal ? style.overflowX : style.overflowY;
                    var content = horizontal ? node.scrollWidth : node.scrollHeight;
                    var viewport = horizontal ? node.clientWidth : node.clientHeight;
                    if (/(auto|scroll)/.test(overflow) && content > viewport) {
                        var pos = horizontal ? node.scrollLeft : node.scrollTop;
                        var atStart = pos <= 0;
                        var atEnd = pos + viewport >= content - 1;
                        if ((delta < 0 && !atStart) || (delta > 0 && !atEnd)) return;
                    }
                }
                node = node.parentNode;
            }
            e.preventDefault();
        }, { passive: false });

        // Tool card expand/collapse + reasoning toggle (event delegation).
        // The open/closed state lives in `expandedParts`, not in the DOM,
        // so re-renders (streaming tokens) keep blocks open. The toggle
        // fires on pointerdown: during streaming the message node is
        // replaced between mousedown and click, which swallowed clicks —
        // pointerdown reacts before any re-render can interfere. The click
        // handler below only serves keyboard activation (e.detail === 0).
        function expandRootFromEvent(e) {
            var head = e.target.closest('.ai-tool-card-head');
            var root = head ? head.closest('.ai-tool-card') : null;
            if (!root) {
                var label = e.target.closest('.ai-reasoning-label');
                root = label ? label.closest('.ai-reasoning') : null;
            }
            return root;
        }
        function toggleExpandRoot(root) {
            var key = root.getAttribute('data-expand-key');
            var body = root.querySelector('.ai-tool-card-body, .ai-reasoning-body');
            if (!key || !body) return;
            var hidden = body.classList.toggle('hidden');
            root.classList.toggle('open', !hidden);
            if (hidden) delete expandedParts[key];
            else expandedParts[key] = true;
        }
        els.messages.addEventListener('pointerdown', function (e) {
            if (e.button !== 0) return;
            var root = expandRootFromEvent(e);
            if (root) toggleExpandRoot(root);
        });
        els.messages.addEventListener('click', function (e) {
            // Message actions: edit / retry / version switch / edit box.
            var actionBtn = e.target.closest('[data-msg-action]');
            if (actionBtn) {
                onMessageAction(actionBtn.dataset.msgAction, actionBtn);
                return;
            }
            var stepBtn = e.target.closest('[data-version-step]');
            if (stepBtn) {
                onVersionStep(stepBtn);
                return;
            }
            if (e.target.closest('[data-edit-save]')) {
                saveEdit();
                return;
            }
            if (e.target.closest('[data-edit-cancel]')) {
                cancelEdit();
                return;
            }
            if (e.detail === 0) {
                var root = expandRootFromEvent(e);
                if (root) toggleExpandRoot(root);
            }
        });
        els.visionSlot.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-vision-action]');
            if (btn) {
                decideVision(btn.dataset.visionAction === 'allow');
                return;
            }
            var confirmBtn = e.target.closest('[data-confirm-action]');
            if (confirmBtn) decideToolConfirm(confirmBtn.dataset.confirmAction);
        });

        // Selection can change while the panel is open (click-driven);
        // also close the agent menu when clicking anywhere else.
        document.addEventListener('click', function (e) {
            if (agentMenuOpen && els.agentMenu &&
                !els.agentMenu.contains(e.target) &&
                !els.agentBtn.contains(e.target)) {
                closeAgentMenu();
            }
            if (reasoningMenuOpen && els.reasoningMenu &&
                !els.reasoningMenu.contains(e.target) &&
                !els.reasoningBtn.contains(e.target)) {
                closeReasoningMenu();
            }
            if (isOpen()) renderComposer();
        });
    }

    function isNearBottom() {
        if (!els.messages) return true;
        var el = els.messages;
        return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    }

    function scrollToBottom() {
        if (!els.messages) return;
        els.messages.scrollTop = els.messages.scrollHeight;
    }

    function autoResizeInput() {
        if (!els.input) return;
        els.input.style.height = 'auto';
        els.input.style.height = Math.min(els.input.scrollHeight, 120) + 'px';
    }

    // The inline edit box must fit the message it edits: a fixed 2-row
    // textarea makes editing long prompts uncomfortable.
    var EDIT_INPUT_MAX_HEIGHT = 220;

    function autoSizeEditInput(ta) {
        if (!ta) return;
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, EDIT_INPUT_MAX_HEIGHT) + 'px';
    }

    function isNarrowViewport() {
        return typeof window !== 'undefined' && !!window.matchMedia &&
            window.matchMedia('(max-width: 900px)').matches;
    }

    function showList() {
        listVisible = true;
        panel.classList.add('with-list');
        syncBodyClasses();
        loadConversations();
    }

    function hideList() {
        listVisible = false;
        panel.classList.remove('with-list');
        syncBodyClasses();
    }

    function toggleList() {
        if (listVisible) hideList();
        else showList();
    }

    // Mobile gestures, mirroring the gallery sidebar: swipe left on the
    // open list closes it, swipe right (from the left edge of the chat
    // area) opens it. Passive listeners — no rubber-band, just detection.
    function setupListSwipe() {
        if (!els.list) return;
        var startX = 0, startY = 0, tracking = false;
        els.list.addEventListener('touchstart', function (e) {
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            tracking = true;
        }, { passive: true });
        els.list.addEventListener('touchend', function (e) {
            if (!tracking) return;
            tracking = false;
            var diffX = e.changedTouches[0].clientX - startX;
            var diffY = e.changedTouches[0].clientY - startY;
            if (diffX < -50 && Math.abs(diffX) > Math.abs(diffY)) hideList();
        }, { passive: true });
    }

    function setupMainSwipe() {
        if (!els.messages) return;
        var startX = 0, startY = 0, tracking = false;
        els.messages.addEventListener('touchstart', function (e) {
            // Edge zone only: the rest of the message area hosts
            // horizontally scrollable tool-card bodies.
            if (e.touches[0].clientX > window.innerWidth * 0.3) return;
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            tracking = true;
        }, { passive: true });
        els.messages.addEventListener('touchend', function (e) {
            if (!tracking) return;
            tracking = false;
            var diffX = e.changedTouches[0].clientX - startX;
            var diffY = e.changedTouches[0].clientY - startY;
            if (diffX > 50 && Math.abs(diffX) > Math.abs(diffY) && !listVisible) {
                showList();
            }
        }, { passive: true });

        // Broken-thumbnail handling via capture-phase delegation (error events do not bubble).
        els.messages.addEventListener('error', function (e) {
            var img = e.target;
            if (!img || img.tagName !== 'IMG') return;
            var cell = img.closest ? img.closest('.ai-msg-thumb') : null;
            if (cell) { img.remove(); cell.classList.add('ai-thumb-broken'); }
        }, true);
    }

    function albumSelectionIds() {
        if (typeof window === 'undefined' || !window.selectedAlbums) return [];
        return Array.from(window.selectedAlbums || []);
    }

    function setAttached(value) {
        attachedSelection = !!value && (selectionIds().length + albumSelectionIds().length) > 0;
    }

    function selectionIds() {
        if (typeof window === 'undefined' || !window.selectedPhotos) return [];
        return Array.from(window.selectedPhotos || []);
    }

    // ========================================================================
    // Rendering
    // ========================================================================

    function scheduleRender() {
        if (renderScheduled) return;
        renderScheduled = true;
        (typeof window !== 'undefined' && window.requestAnimationFrame
            ? window.requestAnimationFrame
            : function (fn) { setTimeout(fn, 16); }
        )(function () {
            renderScheduled = false;
            render();
        });
    }

    function render() {
        if (!panel || !panel.dataset.built) return;
        renderHeader();
        renderMessages();
        renderApprovals();
        renderComposer();
    }

    function currentAgent() {
        for (var i = 0; i < agents.length; i++) {
            if (agents[i].id === currentAgentId) return agents[i];
        }
        return null;
    }

    function renderHeader() {
        var title = 'New chat';
        if (conversationId) {
            for (var i = 0; i < conversations.length; i++) {
                if (conversations[i].id === conversationId) {
                    title = conversations[i].title || title;
                    break;
                }
            }
        }
        els.title.textContent = title;

        var agent = currentAgent();
        var iconFn = typeof window !== 'undefined' ? window.agentIconSvg : null;
        var colorFn = typeof window !== 'undefined' ? window.agentColor : null;
        var name = agent ? agent.name : 'Assistant';
        els.agentName.textContent = name;
        els.agentBtn.title = agent && agent.description
            ? agent.name + ' — ' + agent.description
            : 'Choose agent';
        var color = agent && colorFn ? colorFn(agent.color_index) : '';
        els.agentIcon.style.color = color;
        els.agentIcon.innerHTML = iconFn
            ? iconFn(agent ? agent.icon : 'sparkles', 15)
            : '';
        els.agentBtn.classList.toggle('ai-agent-custom', !!agent);

        var s = chatSettings && chatSettings.settings ? chatSettings.settings : null;
        if (s && (s.model_display_name || s.active_model_id)) {
            var label = (s.model_display_name || s.active_model_id) +
                ' \u00b7 ' + (s.provider_label || protocolLabel(s.protocol) || 'unknown');
            els.modelBadge.textContent = label;
            els.modelBadge.classList.remove('ai-chat-badge-empty');
            els.modelBadge.title = s.supports_vision === false
                ? 'This model may not support images'
                : 'Active model';
        } else {
            els.modelBadge.textContent = 'No model configured';
            els.modelBadge.classList.add('ai-chat-badge-empty');
            els.modelBadge.title = 'Set up a provider in Settings \u2192 AI Assistant';
        }
    }

    function removeMessageElements() {
        for (var i = 0; i < msgEls.length; i++) {
            if (msgEls[i] && msgEls[i].parentNode) {
                msgEls[i].parentNode.removeChild(msgEls[i]);
            }
        }
        msgEls = [];
        msgSigs = [];
    }

    function removeTypingIndicator() {
        if (els.typing && els.typing.parentNode) {
            els.typing.parentNode.removeChild(els.typing);
        }
    }

    function partSignature(p) {
        switch (p.type) {
            case 'text':
            case 'reasoning':
            case 'error':
                return p.type + ':' + (p.text || '').length;
            case 'context':
                return 'context:' + (Array.isArray(p.item_ids) ? p.item_ids.join(',') : '');
            case 'tool':
                return 'tool:' + (p.call_id || '') + ':' + (p.status || '') + ':' +
                    (p.summary == null ? -1 : String(p.summary).length) + ':' +
                    (p.result == null ? -1 : String(p.result).length) + ':' +
                    (Array.isArray(p.images) ? p.images.join(',') : '');
            default:
                return (p.type || 'part') + ':' + JSON.stringify(p).length;
        }
    }

    function messageSignature(m) {
        var parts = m.parts || [];
        var s = m.role + '#' + parts.length;
        for (var i = 0; i < parts.length; i++) s += '|' + partSignature(parts[i]);
        // Action buttons / version chips / edit box depend on these.
        if (m.role === 'user' && m.id != null) {
            s += '|u' + m.id + ':' + m.version + '/' + m.versionCount +
                (m.id === editingMessageId ? ':edit' : ':view') +
                (state.streaming ? ':streaming' : '');
        }
        return s;
    }

    function messageClassName(message) {
        if (message.role === 'user') return 'ai-msg-user';
        if (message.role === 'assistant') return 'ai-msg-assistant';
        return 'ai-msg-tool';
    }

    function messageInnerHtml(message, msgIndex) {
        if (message.role === 'user' && message.id != null &&
            message.id === editingMessageId) {
            return userEditHtml(message);
        }
        var parts = message.parts || [];
        var html = '';
        for (var i = 0; i < parts.length; i++) {
            html += renderPart(message, parts[i], msgIndex, i);
        }
        if (message.role === 'user') {
            html += userMessageExtrasHtml(message, msgIndex);
            if (!html) return '&nbsp;';
        }
        return html;
    }

    /**
     * Inline edit box for a stored user message (edit + retry).
     */
    function userEditHtml(message) {
        var text = messageText(message);
        return '<div class="ai-msg-edit">' +
            '<textarea class="ai-msg-edit-input" data-edit-input rows="2">' +
            esc(text) + '</textarea>' +
            '<div class="ai-msg-edit-buttons">' +
            '<button type="button" class="btn btn-small" data-edit-cancel>Cancel</button>' +
            '<button type="button" class="btn btn-small btn-primary" data-edit-save>Save</button>' +
            '</div>' +
            '</div>';
    }

    /**
     * Version chips (‹k/n›) and Edit/Retry actions under a stored user
     * message. Optimistic messages (id == null) get nothing yet.
     */
    function userMessageExtrasHtml(message, msgIndex) {
        if (message.id == null || state.streaming) return '';
        var html = '';
        if (message.versionCount > 1 && Array.isArray(message.siblingIds)) {
            html += '<div class="ai-msg-versions">' +
                '<button type="button" class="ai-msg-version-btn"' +
                ' data-version-step="-1" data-msg-index="' + msgIndex + '"' +
                (message.version <= 1 ? ' disabled' : '') +
                ' title="Previous version">&lsaquo;</button>' +
                '<span class="ai-msg-version-label">' + message.version +
                '/' + message.versionCount + '</span>' +
                '<button type="button" class="ai-msg-version-btn"' +
                ' data-version-step="1" data-msg-index="' + msgIndex + '"' +
                (message.version >= message.versionCount ? ' disabled' : '') +
                ' title="Next version">&rsaquo;</button>' +
                '</div>';
        }
        html += '<div class="ai-msg-actions" data-msg-index="' + msgIndex + '">' +
            '<button type="button" class="ai-msg-action-btn" data-msg-action="edit"' +
            ' title="Edit and resend">' + PENCIL_SVG + '</button>' +
            '<button type="button" class="ai-msg-action-btn" data-msg-action="retry"' +
            ' title="Retry from this message">' + RETRY_SVG + '</button>' +
            '</div>';
        return html;
    }

    /**
     * Incremental message list rendering: message DOM nodes are cached and
     * only re-rendered when their content signature changes. This keeps
     * images from reloading and user-toggled tool/reasoning state intact
     * while tokens stream in.
     */
    function renderMessages() {
        var container = els.messages;
        var wasNearBottom = stickToBottom;
        var msgs = state.messages;
        var i;

        if (msgs.length === 0) {
            removeMessageElements();
            removeTypingIndicator();
            if (!els.welcome) {
                els.welcome = document.createElement('div');
                els.welcome.className = 'ai-chat-welcome';
                els.welcome.innerHTML =
                    '<div class="ai-chat-welcome-icon">' + SPARKLE_SVG + '</div>' +
                    '<p class="ai-chat-welcome-title">Ask about your library</p>' +
                    '<p class="ai-chat-welcome-hint">Tagging, albums, search \u2014 or anything about your media.</p>';
            }
            // Reflect the selected agent in the empty state.
            var wAgent = currentAgent();
            var wTitle = els.welcome.querySelector('.ai-chat-welcome-title');
            var wHint = els.welcome.querySelector('.ai-chat-welcome-hint');
            if (wTitle) wTitle.textContent = wAgent ? wAgent.name : 'Ask about your library';
            if (wHint) {
                wHint.textContent = wAgent && wAgent.description
                    ? wAgent.description
                    : 'Tagging, albums, search \u2014 or anything about your media.';
            }
            if (els.welcome.parentNode !== container) {
                container.innerHTML = '';
                container.appendChild(els.welcome);
            }
            return;
        }

        if (els.welcome && els.welcome.parentNode) {
            els.welcome.parentNode.removeChild(els.welcome);
        }

        // A shrunk message list means the conversation was replaced
        // (switch / new chat) — rebuild the cached nodes from scratch.
        if (msgEls.length > msgs.length) removeMessageElements();

        for (i = 0; i < msgs.length; i++) {
            var sig = messageSignature(msgs[i]);
            if (!msgEls[i]) {
                msgEls[i] = document.createElement('div');
                if (els.typing && els.typing.parentNode) {
                    container.insertBefore(msgEls[i], els.typing);
                } else {
                    container.appendChild(msgEls[i]);
                }
            }
            if (msgSigs[i] !== sig) {
                msgEls[i].className = messageClassName(msgs[i]);
                msgEls[i].innerHTML = messageInnerHtml(msgs[i], i);
                msgSigs[i] = sig;
            }
        }

        // Keep the caret in the open inline edit box across re-renders.
        var editInput = els.messages.querySelector('[data-edit-input]');
        if (editInput && document.activeElement !== editInput &&
            document.activeElement !== els.input) {
            editInput.focus();
        }

        if (state.streaming) {
            if (!els.typing) {
                els.typing = document.createElement('div');
                els.typing.className = 'ai-chat-typing';
                els.typing.innerHTML = '<span></span><span></span><span></span>';
            }
            if (!els.typing.parentNode) container.appendChild(els.typing);
        } else {
            removeTypingIndicator();
        }

        if (wasNearBottom) scrollToBottom();
    }

    function renderPart(message, part, msgIndex, partIndex) {
        switch (part.type) {
            case 'text':
                if (message.role === 'user') {
                    return '<div class="ai-msg-text">' + esc(part.text) + '</div>';
                }
                return '<div class="ai-chat-md markdown-preview">' + renderMarkdownHtml(part.text || '') + '</div>';
            case 'reasoning': {
                var rkey = 'r:' + msgIndex + ':' + partIndex;
                var ropen = !!expandedParts[rkey];
                return '<div class="ai-reasoning' + (ropen ? ' open' : '') + '" data-expand-key="' + esc(rkey) + '">' +
                    '<span class="ai-reasoning-label">Thinking</span>' +
                    '<div class="ai-reasoning-body' + (ropen ? '' : ' hidden') + '">' + esc(part.text) + '</div>' +
                    '</div>';
            }
            case 'context':
                return '<div class="ai-chat-chips">' + renderContextChip(part.item_ids) + '</div>' + renderContextThumbs(part.item_ids);
            case 'tool':
                return renderToolCard(part);
            case 'error':
                return '<div class="ai-msg-error">' + esc(part.text) + '</div>';
            default:
                return '';
        }
    }

    function renderContextChip(itemIds) {
        var ids = Array.isArray(itemIds) ? itemIds : [];
        if (ids.length === 0) return '';
        return '<span class="ai-chip" title="' + esc(ids.join(', ')) + '">' +
            CONTEXT_SVG + ' ' + ids.length + (ids.length === 1 ? ' item' : ' items') +
            '</span>';
    }

    var CONTEXT_THUMB_CAP = 8;

    function contextThumbIds(itemIds, cap) {
        var ids = Array.isArray(itemIds) ? itemIds : [];
        var limit = typeof cap === 'number' && cap > 0 ? cap : CONTEXT_THUMB_CAP;
        return { shown: ids.slice(0, limit), hidden: Math.max(0, ids.length - limit) };
    }

    function renderContextThumbs(itemIds) {
        var split = contextThumbIds(itemIds);
        if (split.shown.length === 0) return '';
        var html = '<div class="ai-msg-thumbs">';
        for (var i = 0; i < split.shown.length; i++) {
            html += '<span class="ai-msg-thumb" title="' + esc(split.shown[i]) + '">' +
                '<img src="' + esc(baseUrl()) + '/files/' + esc(split.shown[i]) + '/thumbnail" loading="lazy" alt="">' +
                '</span>';
        }
        if (split.hidden > 0) {
            html += '<span class="ai-msg-thumb ai-msg-thumb-more">+' + split.hidden + '</span>';
        }
        html += '</div>';
        return html;
    }

    function toolStatusClass(status) {
        return 'ai-tool-' + (status || 'running');
    }

    function toolStatusText(part) {
        switch (part.status) {
            case 'done': return part.summary || 'Done';
            case 'error': return part.summary || 'Failed';
            case 'expired': return 'Expired';
            default: return 'Running...';
        }
    }

    function renderToolCard(part) {
        var argsText = '';
        if (part.args != null) {
            argsText = typeof part.args === 'string' ? part.args : JSON.stringify(part.args, null, 2);
        }
        var resultText = part.result == null ? '' : String(part.result);

        var body = '';
        if (argsText) body += '<div class="ai-tool-section"><span class="ai-tool-section-label">Arguments</span><pre>' + esc(argsText) + '</pre></div>';
        if (resultText) body += '<div class="ai-tool-section"><span class="ai-tool-section-label">Result</span><pre>' + esc(resultText) + '</pre></div>';
        var images = Array.isArray(part.images) ? part.images : [];
        if (images.length) {
            body += '<div class="ai-tool-section"><span class="ai-tool-section-label">Images</span><div class="ai-vision-grid">';
            for (var i = 0; i < images.length; i++) {
                body += '<img src="' + esc(baseUrl()) + '/files/' + esc(images[i]) + '/thumbnail" loading="lazy" alt="tool result image" title="' + esc(images[i]) + '">';
            }
            body += '</div></div>';
        }

        var tkey = 'tool:' + (part.call_id || '');
        var topen = !!expandedParts[tkey];

        return '<div class="ai-tool-card ' + toolStatusClass(part.status) + (topen ? ' open' : '') + '" data-expand-key="' + esc(tkey) + '">' +
            '<button type="button" class="ai-tool-card-head">' +
            '  <span class="ai-tool-dot" aria-hidden="true"></span>' +
            '  <span class="ai-tool-name">' + esc(part.name || 'tool') + '</span>' +
            '  <span class="ai-tool-summary">' + esc(toolStatusText(part)) + '</span>' +
            '  <svg class="ai-tool-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="6 9 12 15 18 9"/></svg>' +
            '</button>' +
            '<div class="ai-tool-card-body' + (topen ? '' : ' hidden') + '">' + body + '</div>' +
            '</div>';
    }

    function renderApprovals() {
        renderVision();
        renderToolConfirm();
    }

    function renderVision() {
        var pending = state.pendingVision;
        // Rebuild only when the pending request actually changed — rebuilding
        // on every render would reload the preview images each frame.
        var key = pending ? String(pending.request_id) : '';
        if (key === lastVisionKey) return;
        lastVisionKey = key;
        if (!pending) {
            els.visionSlot.innerHTML = '';
            els.visionSlot.classList.add('hidden');
            return;
        }
        var items = Array.isArray(pending.items) ? pending.items : [];
        var grid = '';
        for (var i = 0; i < items.length; i++) {
            var it = items[i] || {};
            grid += '<img src="' + esc(baseUrl()) + '/files/' + esc(it.id) + '/thumbnail" loading="lazy" alt="" title="' + esc(it.title || it.id || '') + '">';
        }
        els.visionSlot.innerHTML =
            '<div class="ai-vision-card">' +
            '  <div class="ai-vision-title">Allow viewing the next ' + items.length +
            (items.length === 1 ? ' image?' : ' images?') + '</div>' +
            '  <div class="ai-vision-grid">' + grid + '</div>' +
            '  <div class="ai-vision-actions">' +
            '    <button type="button" class="btn btn-small" data-vision-action="allow">Allow</button>' +
            '    <button type="button" class="btn btn-small btn-secondary" data-vision-action="deny">Deny</button>' +
            '  </div>' +
            '</div>';
        els.visionSlot.classList.remove('hidden');
    }

    function renderToolConfirm() {
        var pending = state.pendingToolConfirm;
        var key = pending ? String(pending.request_id) : '';
        if (key === lastConfirmKey) return;
        lastConfirmKey = key;
        if (!pending) {
            // Decision made: clear the slot unless the vision card took
            // it over. renderVision cannot do it here — its key-based
            // early-return skips the clear when it was already idle.
            if (!state.pendingVision) {
                els.visionSlot.innerHTML = '';
                els.visionSlot.classList.add('hidden');
            }
            return;
        }

        var argsText = pending.arguments == null
            ? ''
            : (typeof pending.arguments === 'string'
                ? pending.arguments
                : JSON.stringify(pending.arguments, null, 2));

        els.visionSlot.innerHTML =
            '<div class="ai-vision-card ai-toolconfirm-card">' +
            '  <div class="ai-vision-title">Run <code>' + esc(pending.name) + '</code>?</div>' +
            '  <div class="ai-vision-subtitle">This changes your library and needs your approval.</div>' +
            (argsText
                ? '<pre class="ai-confirm-args">' + esc(argsText) + '</pre>'
                : '') +
            '  <div class="ai-vision-actions">' +
            '    <button type="button" class="btn btn-small" data-confirm-action="allow">Approve</button>' +
            '    <button type="button" class="btn btn-small btn-secondary" data-confirm-action="always" title="Run without asking for this tool in this chat">Always allow</button>' +
            '    <button type="button" class="btn btn-small btn-secondary" data-confirm-action="deny">Deny</button>' +
            '  </div>' +
            '</div>';
        els.visionSlot.classList.remove('hidden');
    }

    function attachChipLabel(photoCount, albumCount) {
        var parts = [];
        if (photoCount > 0) parts.push(photoCount + (photoCount === 1 ? ' item' : ' items'));
        if (albumCount > 0) parts.push(albumCount + (albumCount === 1 ? ' album' : ' albums'));
        return 'Attach selection (' + parts.join(', ') + ')';
    }

    function renderComposer() {
        var pending = !!state.pendingVision || !!state.pendingToolConfirm;
        var streaming = !!state.streaming;

        els.input.disabled = pending;
        els.input.placeholder = pending
            ? (state.pendingToolConfirm
                ? 'Approve or deny the action first...'
                : 'Decide on the image request first...')
            : 'Ask about your library...';

        if (streaming) {
            els.sendBtn.textContent = 'Stop';
            els.sendBtn.classList.add('ai-chat-stop');
            els.sendBtn.disabled = false;
            els.sendBtn.title = 'Stop generating';
        } else {
            els.sendBtn.textContent = 'Send';
            els.sendBtn.classList.remove('ai-chat-stop');
            els.sendBtn.disabled = pending || !els.input.value.trim();
            els.sendBtn.title = 'Send message';
        }

        var photoCount = selectionIds().length;
        var albumCount = albumSelectionIds().length;
        if (photoCount + albumCount > 0) {
            els.attachChip.classList.remove('hidden');
            els.attachChip.innerHTML = CONTEXT_SVG + ' ' + attachChipLabel(photoCount, albumCount) + (attachedSelection ? ' \u2713' : '');
            els.attachChip.classList.toggle('ai-chat-attach-on', attachedSelection);
        } else {
            attachedSelection = false;
            els.attachChip.classList.add('hidden');
        }

        renderReasoningControl();
    }

    // ========================================================================
    // Reasoning-effort selector
    // ========================================================================

    var THINKING_SVG =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" aria-hidden="true">' +
        '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/>' +
        '<path d="M9 18h6"/><path d="M10 22h4"/></svg>';

    var REASONING_CHEVRON_SVG =
        '<svg class="ai-agent-chip-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';

    function settingsReasoningUi() {
        var s = chatSettings && chatSettings.settings ? chatSettings.settings : null;
        return s ? s.reasoning_ui : null;
    }

    function renderReasoningControl() {
        var choices = reasoningChoices(settingsReasoningUi());
        if (!choices.length) {
            els.reasoningBtn.classList.add('hidden');
            closeReasoningMenu();
            return;
        }
        els.reasoningBtn.classList.remove('hidden');
        els.reasoningBtn.innerHTML = THINKING_SVG +
            '<span>Thinking \u00b7 ' + esc(reasoningLabel(reasoningEffort)) + '</span>' +
            REASONING_CHEVRON_SVG;
        if (reasoningMenuOpen) renderReasoningMenu();
    }

    function toggleReasoningMenu() {
        if (reasoningMenuOpen) closeReasoningMenu();
        else openReasoningMenu();
    }

    function openReasoningMenu() {
        reasoningMenuOpen = true;
        renderReasoningMenu();
        els.reasoningMenu.classList.remove('hidden');
    }

    function closeReasoningMenu() {
        reasoningMenuOpen = false;
        if (els.reasoningMenu) els.reasoningMenu.classList.add('hidden');
    }

    function renderReasoningMenu() {
        var choices = reasoningChoices(settingsReasoningUi());
        var html = '';
        for (var i = 0; i < choices.length; i++) {
            var value = choices[i];
            var active = (reasoningEffort || 'default') === value;
            html += '<button type="button" class="ai-agent-menu-item' + (active ? ' active' : '') + '"' +
                ' data-reasoning-value="' + value + '">' +
                '<span class="ai-agent-menu-text"><span class="ai-agent-menu-name">' +
                esc(reasoningLabel(value)) + '</span></span>' +
                (active ? '<span class="ai-agent-menu-check">\u2713</span>' : '') +
                '</button>';
        }
        els.reasoningMenu.innerHTML = html;
    }

    function onReasoningMenuClick(e) {
        var item = e.target.closest('[data-reasoning-value]');
        if (!item) return;
        setReasoningEffort(item.dataset.reasoningValue);
    }

    function setReasoningEffort(value) {
        closeReasoningMenu();
        var next = value === 'default' ? null : value;
        if (next === reasoningEffort) return;
        // Optimistic update; the PUT persists it (server re-validates
        // against the active model and clamps on the next load).
        reasoningEffort = next;
        renderReasoningControl();
        persistReasoningEffort(next);
    }

    function persistReasoningEffort(effort) {
        try {
            csrfFetch(baseUrl() + '/api/user/ai/settings/reasoning', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ effort: effort }),
            }).catch(function () { /* best effort */ });
        } catch (e) { /* best effort */ }
    }

    // ========================================================================
    // Agents
    // ========================================================================

    async function loadAgents() {
        try {
            var resp = await fetch(baseUrl() + '/api/user/ai/agents', { credentials: 'include' });
            if (!resp.ok) return;
            var data = await resp.json();
            agents = Array.isArray(data.agents) ? data.agents : [];
        } catch (err) {
            console.warn('[ai-chat] Failed to load agents:', err);
            agents = [];
        }
        // A draft chat preselects the agent chosen last (stored server-side
        // per user); an open conversation keeps its own agent.
        if (!conversationId && currentAgentId == null && chatSettings) {
            var s = chatSettings.settings;
            if (s && s.active_agent_id) currentAgentId = s.active_agent_id;
        }
        // Full render: the restored agent must also update the welcome
        // placeholder, not just the header chip.
        render();
        renderAgentMenu();
    }

    function toggleAgentMenu() {
        if (agentMenuOpen) closeAgentMenu();
        else openAgentMenu();
    }

    function openAgentMenu() {
        agentMenuOpen = true;
        renderAgentMenu();
        els.agentMenu.classList.remove('hidden');
    }

    function closeAgentMenu() {
        agentMenuOpen = false;
        if (els.agentMenu) els.agentMenu.classList.add('hidden');
    }

    function agentMenuItem(agent) {
        var iconFn = typeof window !== 'undefined' ? window.agentIconSvg : null;
        var colorFn = typeof window !== 'undefined' ? window.agentColor : null;
        var active = (agent ? agent.id : null) === currentAgentId;
        var icon = iconFn ? iconFn(agent ? agent.icon : 'sparkles', 15) : '';
        var color = agent && colorFn ? colorFn(agent.color_index) : '';
        return '<button type="button" class="ai-agent-menu-item' + (active ? ' active' : '') + '" data-agent-id="' + esc(agent ? String(agent.id) : '') + '">' +
            '<span class="ai-agent-menu-icon" style="color:' + esc(color) + '">' + icon + '</span>' +
            '<span class="ai-agent-menu-text">' +
            '<span class="ai-agent-menu-name">' + esc(agent ? agent.name : 'Assistant') + '</span>' +
            '<span class="ai-agent-menu-desc">' + esc(agent && agent.description ? agent.description : 'No persona, all tools') + '</span>' +
            '</span>' +
            (active ? '<span class="ai-agent-menu-check">\u2713</span>' : '') +
            '</button>';
    }

    function renderAgentMenu() {
        if (!els.agentMenu) return;
        var html = agentMenuItem(null);
        for (var i = 0; i < agents.length; i++) {
            html += agentMenuItem(agents[i]);
        }
        html += '<div class="ai-agent-menu-separator"></div>' +
            '<a class="ai-agent-menu-manage" href="' + esc(baseUrl()) + '/settings">Manage agents\u2026</a>';
        els.agentMenu.innerHTML = html;
    }

    function onAgentMenuClick(e) {
        var manage = e.target.closest('.ai-agent-menu-manage');
        if (manage) {
            closeAgentMenu();
            return; // plain link: browser navigates
        }
        var item = e.target.closest('.ai-agent-menu-item');
        if (!item) return;
        var raw = item.dataset.agentId;
        selectAgent(raw ? parseInt(raw, 10) : null);
    }

    function selectAgent(agentId) {
        closeAgentMenu();
        if (agentId === currentAgentId) {
            renderHeader();
            return;
        }
        // A persona switch never rewrites an open conversation: it starts
        // a new draft (aborting a running stream like "New chat" does).
        if (conversationId || state.messages.length > 0) {
            if (state.streaming && abortController) abortController.abort();
            conversationId = null;
            state = newChatState();
            removeMessageElements();
        }
        currentAgentId = agentId || null;
        persistActiveAgent();
        renderConversations();
        render();
        if (els.input && !els.input.disabled) els.input.focus();
    }

    function persistActiveAgent() {
        try {
            csrfFetch(baseUrl() + '/api/user/ai/agents/active', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active_agent_id: currentAgentId }),
            }).catch(function () { /* best effort */ });
        } catch (e) { /* best effort */ }
    }

    // ========================================================================
    // Conversation list
    // ========================================================================

    function renderConversations() {
        if (!els.conversations) return;
        if (conversations.length === 0) {
            els.conversations.innerHTML = '<p class="ai-chat-list-empty">No chats yet</p>';
            return;
        }
        var html = '';
        for (var i = 0; i < conversations.length; i++) {
            var c = conversations[i];
            var active = c.id === conversationId;
            html += '<div class="ai-chat-conversation-item' + (active ? ' active' : '') + '" data-conversation-id="' + esc(c.id) + '">' +
                '<span class="ai-chat-conversation-title" title="' + esc(c.title || 'Untitled chat') + '">' + esc(c.title || 'Untitled chat') + '</span>' +
                '<button type="button" class="ai-chat-conversation-delete" data-delete-id="' + esc(c.id) + '" title="Delete chat" aria-label="Delete chat">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
                '</button>' +
                '</div>';
        }
        els.conversations.innerHTML = html;
    }

    function onConversationListClick(e) {
        var del = e.target.closest('.ai-chat-conversation-delete');
        if (del) {
            deleteConversation(del.dataset.deleteId);
            return;
        }
        var item = e.target.closest('.ai-chat-conversation-item');
        if (item) selectConversation(item.dataset.conversationId);
    }

    async function loadConversations() {
        try {
            var resp = await fetch(baseUrl() + '/api/ai/chat/conversations', { credentials: 'include' });
            if (!resp.ok) return;
            var data = await resp.json();
            conversations = Array.isArray(data.conversations) ? data.conversations : [];
            renderConversations();
            renderHeader();
        } catch (err) {
            console.warn('[ai-chat] Failed to load conversations:', err);
        }
    }

    async function selectConversation(id) {
        if (!id) return;
        if (state.streaming && abortController) abortController.abort();
        conversationId = id;
        closeAgentMenu();
        try {
            var resp = await fetch(baseUrl() + '/api/ai/chat/conversations/' + encodeURIComponent(id) + '/messages', { credentials: 'include' });
            if (!resp.ok) {
                toast('Failed to load chat (' + resp.status + ')', true);
                return;
            }
            var data = await resp.json();
            state = stateFromMessages(data);
            editingMessageId = null;
            removeMessageElements(); // full state replacement: rebuild all nodes
            // The conversation keeps the agent it was created with.
            currentAgentId = null;
            for (var i = 0; i < conversations.length; i++) {
                if (conversations[i].id === id) {
                    currentAgentId = conversations[i].agent_id || null;
                    break;
                }
            }
            renderConversations();
            render();
            // On phones the list overlays the conversation — close it,
            // like the folder sidebar does after picking a folder.
            if (isNarrowViewport()) hideList();
        } catch (err) {
            console.error('[ai-chat] Failed to load messages:', err);
            toast('Failed to load chat', true);
        }
    }

    async function deleteConversation(id) {
        if (!id) return;
        if (!window.confirm('Delete this chat?')) return;
        try {
            var resp = await csrfFetch(baseUrl() + '/api/ai/chat/conversations/' + encodeURIComponent(id), { method: 'DELETE' });
            if (!resp.ok) {
                var detail = await resp.json().then(function (d) { return d && d.detail; }).catch(function () { return null; });
                toast(detail || 'Failed to delete chat', true);
                return;
            }
            if (id === conversationId) resetToDraft();
            await loadConversations();
        } catch (err) {
            console.error('[ai-chat] Failed to delete conversation:', err);
            toast('Failed to delete chat', true);
        }
    }

    function resetToDraft() {
        if (state.streaming && abortController) abortController.abort();
        conversationId = null;
        editingMessageId = null;
        state = newChatState();
        removeMessageElements(); // full state replacement: rebuild all nodes
        if (isNarrowViewport()) hideList();
        renderConversations();
        render();
        if (els.input) els.input.focus();
    }

    // ========================================================================
    // Settings (model badge)
    // ========================================================================

    async function loadSettings() {
        try {
            var resp = await fetch(baseUrl() + '/api/user/ai/settings', { credentials: 'include' });
            chatSettings = resp.ok ? await resp.json() : null;
        } catch (err) {
            console.warn('[ai-chat] Failed to load AI settings:', err);
            chatSettings = null;
        }
        // The server clamps the stored effort to the active model's style
        // (an incompatible choice degrades to the provider default).
        reasoningEffort = chatSettings && chatSettings.settings
            ? (chatSettings.settings.reasoning_effort || null)
            : null;
        renderHeader();
        if (panel && panel.dataset.built) renderComposer();
    }

    // ========================================================================
    // SSE streaming
    // ========================================================================

    function handleFrame(frame) {
        if (!frame || typeof frame.data !== 'object' || frame.data === null) return;
        var event = Object.assign({}, frame.data, { type: frame.event });
        state = applyChatEvent(state, event);
        scheduleRender();
        if (frame.event === 'turn_done') loadConversations();
    }

    async function handleStreamError(resp) {
        var detail = null;
        try {
            var data = await resp.json();
            detail = data ? data.detail : null;
        } catch (e) {
            detail = null;
        }
        if (typeof detail !== 'string' || !detail) {
            detail = 'Chat request failed (' + resp.status + ')';
        }
        toast(detail, true);
        if (resp.status === 403 && /encryption key/i.test(detail)) {
            toast('Your encryption key is not available. Reload the page and log in again; if it persists, check Settings.', true);
        }
    }

    /**
     * POST a chat endpoint and consume its SSE stream into state.
     */
    async function streamChat(path, body) {
        var controller = new AbortController();
        abortController = controller;
        renderComposer();
        try {
            var resp = await csrfFetch(baseUrl() + path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
                body: JSON.stringify(body || {}),
                signal: controller.signal,
                credentials: 'include',
            });
            if (!resp.ok) {
                await handleStreamError(resp);
                return;
            }
            if (!resp.body || typeof resp.body.getReader !== 'function') {
                toast('Streaming is not supported by this browser', true);
                return;
            }
            var reader = resp.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';
            for (;;) {
                var chunk = await reader.read();
                if (chunk.done) break;
                buffer += decoder.decode(chunk.value, { stream: true });
                var parsed = parseSSEFrames(buffer);
                buffer = parsed.rest;
                for (var i = 0; i < parsed.frames.length; i++) {
                    handleFrame(parsed.frames[i]);
                }
            }
        } catch (err) {
            if (!err || err.name !== 'AbortError') {
                console.error('[ai-chat] Stream failed:', err);
                toast('Chat request failed', true);
            }
        } finally {
            abortController = null;
            if (state.streaming) {
                state = applyChatEvent(state, { type: 'turn_done', stop_reason: 'stopped' });
            }
            render();
            renderComposer();
            loadConversations();
            // Bind server message ids / show the final branch state
            // (edit & retry replace the visible tail on the server). A
            // paused turn (vision / tool approval pending) is not finished:
            // the pending call has no stored result yet, so a refetch would
            // drop the approval card and mark the call expired.
            if (conversationId && !state.pendingVision && !state.pendingToolConfirm) {
                refetchHistory();
            }
        }
    }

    // ========================================================================
    // Sending
    // ========================================================================

    function onSendButton() {
        if (state.streaming && abortController) {
            abortController.abort();
            return;
        }
        sendMessage();
    }

    async function ensureConversation() {
        if (conversationId) return conversationId;
        var createBody = {};
        if (currentAgentId) createBody.agent_id = currentAgentId;
        var resp = await csrfFetch(baseUrl() + '/api/ai/chat/conversations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(createBody),
        });
        if (!resp.ok) {
            var detail = null;
            try {
                var data = await resp.json();
                detail = data ? data.detail : null;
            } catch (e) { /* ignore */ }
            toast(typeof detail === 'string' && detail ? detail : 'Failed to create chat', true);
            throw new Error('conversation create failed');
        }
        var payload = await resp.json();
        conversationId = payload.conversation && payload.conversation.id ? payload.conversation.id : null;
        if (!conversationId) throw new Error('conversation id missing');
        renderConversations();
        return conversationId;
    }

    async function sendMessage() {
        if (!panel || !panel.dataset.built) return;
        if (state.pendingVision || state.pendingToolConfirm) return;
        if (state.streaming || sending) return;

        var text = els.input.value.trim();
        if (!text) return;

        sending = true;
        try {
            var convId;
            try {
                convId = await ensureConversation();
            } catch (e) {
                return;
            }

            var itemIds = attachedSelection ? selectionIds() : [];
            // Album ids are expanded server-side; optimistic echo stays photo-only.
            var albumIds = attachedSelection ? albumSelectionIds() : [];
            var body = { text: text };
            if (itemIds.length > 0) body.item_ids = itemIds;
            if (albumIds.length > 0) body.album_ids = albumIds;

            state = appendUserMessage(state, text, itemIds);
            els.input.value = '';
            autoResizeInput();
            setAttached(false);
            stickToBottom = true;
            render();

            await streamChat('/api/ai/chat/conversations/' + encodeURIComponent(convId) + '/messages', body);
        } finally {
            sending = false;
        }
    }

    function decideVision(approved) {
        var pending = state.pendingVision;
        if (!pending || !conversationId) return;
        // The confirmation stream (if still open) is replaced by the
        // decision's continuation stream.
        if (state.streaming && abortController) abortController.abort();
        state = applyChatEvent(state, { type: 'vision_decided' });
        render();
        streamChat(
            '/api/ai/chat/conversations/' + encodeURIComponent(conversationId) +
            '/vision/' + encodeURIComponent(pending.request_id) + '/decision',
            { approved: !!approved }
        );
    }

    function decideToolConfirm(action) {
        var pending = state.pendingToolConfirm;
        if (!pending || !conversationId) return;
        // The paused stream (if still open) is replaced by the decision's
        // continuation stream.
        if (state.streaming && abortController) abortController.abort();
        state = applyChatEvent(state, { type: 'confirm_decided' });
        render();
        var approved = action !== 'deny';
        var alwaysAllow = action === 'always';
        streamChat(
            '/api/ai/chat/conversations/' + encodeURIComponent(conversationId) +
            '/tools/' + encodeURIComponent(pending.request_id) + '/decision',
            { approved: approved, always_allow: alwaysAllow }
        );
    }

    // ========================================================================
    // Message tree: edit / retry / version switch
    // ========================================================================

    function messageByIndex(btn) {
        var host = btn.closest('[data-msg-index]');
        var index = host ? parseInt(host.getAttribute('data-msg-index'), 10) : -1;
        return index >= 0 ? state.messages[index] : null;
    }

    function onMessageAction(action, btn) {
        var message = messageByIndex(btn);
        if (!message || message.id == null) return;
        if (action === 'edit') startEdit(message);
        else if (action === 'retry') retryMessage(message);
    }

    function startEdit(message) {
        if (state.streaming || sending) return;
        if (state.pendingVision || state.pendingToolConfirm) return;
        editingMessageId = message.id;
        render();
        var ta = els.messages.querySelector('[data-edit-input]');
        if (ta) {
            autoSizeEditInput(ta);
            ta.focus();
            ta.setSelectionRange(ta.value.length, ta.value.length);
        }
    }

    function cancelEdit() {
        if (editingMessageId == null) return;
        editingMessageId = null;
        render();
    }

    function saveEdit() {
        if (editingMessageId == null) return;
        var ta = els.messages.querySelector('[data-edit-input]');
        var message = null;
        for (var i = 0; i < state.messages.length; i++) {
            if (state.messages[i].id === editingMessageId) {
                message = state.messages[i];
                break;
            }
        }
        if (!ta || !message) {
            cancelEdit();
            return;
        }
        var text = ta.value.trim();
        if (!text) return;
        editingMessageId = null;
        sendEditedMessage(message, text);
    }

    function retryMessage(message) {
        if (state.streaming || sending) return;
        if (state.pendingVision || state.pendingToolConfirm) return;
        sendEditedMessage(message, messageText(message));
    }

    /**
     * Edit/retry: send the new text as a sibling version of an earlier
     * user message and stream the replacement turn.
     */
    async function sendEditedMessage(message, text) {
        if (!conversationId || state.streaming || sending) return;
        sending = true;
        try {
            var itemIds = messageContextIds(message);
            var body = { text: text, edit_of_message_id: message.id };
            if (itemIds.length > 0) body.item_ids = itemIds;

            // Optimistic echo; the post-turn history refetch binds the
            // server ids and shows the real tree with version chips. The
            // echo replaces the original message and the old branch's tail
            // (that is what the new sibling version hides on the server).
            var echoIndex = state.messages.indexOf(message);
            state = appendUserMessage(
                state, text, itemIds, echoIndex >= 0 ? echoIndex : undefined
            );
            stickToBottom = true;
            render();

            await streamChat(
                '/api/ai/chat/conversations/' + encodeURIComponent(conversationId) +
                '/messages',
                body
            );
        } finally {
            sending = false;
        }
    }

    /**
     * Switch the conversation to the previous/next version of a user
     * message, then reload the (branch-filtered) history.
     */
    async function onVersionStep(btn) {
        var message = messageByIndex(btn);
        if (!message || !Array.isArray(message.siblingIds) ||
            message.id == null || state.streaming || sending) {
            return;
        }
        var step = parseInt(btn.dataset.versionStep, 10) || 0;
        var current = message.siblingIds.indexOf(message.id);
        var target = message.siblingIds[current + step];
        if (target == null || target === message.id) return;
        try {
            var resp = await csrfFetch(
                baseUrl() + '/api/ai/chat/conversations/' +
                encodeURIComponent(conversationId) + '/branch',
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message_id: target }),
                }
            );
            if (!resp.ok) {
                var detail = null;
                try {
                    var data = await resp.json();
                    detail = data ? data.detail : null;
                } catch (e) { /* ignore */ }
                toast(detail || 'Failed to switch version (' + resp.status + ')', true);
                return;
            }
            await refetchHistory();
        } catch (err) {
            console.error('[ai-chat] Failed to switch version:', err);
            toast('Failed to switch version', true);
        }
    }

    /**
     * Reload the conversation history from the server (binds message ids
     * after a turn, shows the active branch after a version switch).
     */
    async function refetchHistory() {
        if (!conversationId) return;
        try {
            var resp = await fetch(
                baseUrl() + '/api/ai/chat/conversations/' +
                encodeURIComponent(conversationId) + '/messages',
                { credentials: 'include' }
            );
            if (!resp.ok) return;
            var data = await resp.json();
            state = stateFromMessages(data);
            removeMessageElements(); // full state replacement
            render();
        } catch (err) {
            console.warn('[ai-chat] Failed to refresh history:', err);
        }
    }

    // ========================================================================
    // Open / close
    // ========================================================================

    function isOpen() {
        return !!panel && !panel.classList.contains('hidden');
    }

    /**
     * Mirror the panel state onto <body> so CSS can push the page content
     * aside, then nudge width-dependent layouts (masonry listens for resize).
     */
    function syncBodyClasses() {
        var open = isOpen();
        document.body.classList.toggle('ai-chat-open', open);
        document.body.classList.toggle('ai-chat-list-open', open && listVisible);
        if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
            window.dispatchEvent(new Event('resize'));
        }
    }

    function open() {
        if (!panel) return;
        if (isOpen()) return;
        buildPanel();
        panel.classList.remove('hidden');
        syncSidebarButton();
        syncBodyClasses();
        if (typeof window !== 'undefined' && window.BackButtonManager) {
            window.BackButtonManager.register('ai-chat-panel', close);
        }
        loadSettings().then(loadAgents);
        loadConversations();
        render();
        renderComposer();
        els.input.focus();
    }

    function close() {
        if (!panel || !isOpen()) return;
        // Closing the panel must NOT stop a running turn: generation keeps
        // streaming in the background and reopens with its current state.
        // Only the Stop button (and switching chats) aborts the stream.
        panel.classList.add('hidden');
        closeAgentMenu();
        syncSidebarButton();
        syncBodyClasses();
        if (typeof window !== 'undefined' && window.BackButtonManager) {
            if (window.BackButtonManager.isRegistered('ai-chat-panel')) {
                window.BackButtonManager.unregister('ai-chat-panel');
            }
        }
    }

    function toggle() {
        if (isOpen()) close();
        else open();
    }

    function openWithSelection() {
        setAttached(true);
        open();
        renderComposer();
    }

    function syncSidebarButton() {
        var btn = document.getElementById('ai-chat-toggle');
        if (btn) btn.classList.toggle('active', isOpen());
    }

    // ========================================================================
    // Icons
    // ========================================================================

    var SPARKLE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="28" height="28">' +
        '<path d="M12 3l1.9 5.7a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-2a2 2 0 0 0 1.3-1.3z"/>' +
        '<path d="M19 3v4"/><path d="M17 5h4"/>' +
        '</svg>';

    var CONTEXT_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13">' +
        '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>' +
        '</svg>';

    var PENCIL_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13">' +
        '<path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>' +
        '</svg>';

    var RETRY_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13">' +
        '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>' +
        '</svg>';

    // ========================================================================
    // Init
    // ========================================================================

    function init() {
        if (initialized) return;
        panel = document.getElementById('ai-chat-panel');
        if (!panel) return; // standalone pages (settings, login) have no panel
        initialized = true;
        buildPanel();

        var toggleBtn = document.getElementById('ai-chat-toggle');
        if (toggleBtn) toggleBtn.addEventListener('click', toggle);

        var askBtn = document.getElementById('selection-ask-ai-btn');
        if (askBtn) askBtn.addEventListener('click', openWithSelection);

        els.conversations.addEventListener('click', onConversationListClick);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // ========================================================================
    // Public API
    // ========================================================================

    window.AiChat = {
        open: open,
        close: close,
        toggle: toggle,
        isOpen: isOpen,
        openWithSelection: openWithSelection,
    };

    // CommonJS guard for Jest
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            parseSSEFrames: parseSSEFrames,
            applyChatEvent: applyChatEvent,
            appendUserMessage: appendUserMessage,
            stateFromMessages: stateFromMessages,
            newChatState: newChatState,
            defaultBaseUrl: defaultBaseUrl,
            reasoningChoices: reasoningChoices,
            reasoningLabel: reasoningLabel,
            messageContextIds: messageContextIds,
            messageText: messageText,
            contextThumbIds: contextThumbIds,
            attachChipLabel: attachChipLabel,
        };
    }
})();
