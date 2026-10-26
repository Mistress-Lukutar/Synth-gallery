/**
 * AI agents settings - CRUD for per-user chat agents.
 *
 * Talks to:
 *   GET    /api/user/ai/agents                  (agents + tools catalogue)
 *   POST   /api/user/ai/agents
 *   PUT    /api/user/ai/agents/{id}             (custom only)
 *   DELETE /api/user/ai/agents/{id}             (custom only)
 *   POST   /api/user/ai/agents/{id}/duplicate
 *
 * The agents themselves are edited here; picking one for a chat happens
 * in the chat panel header. Icon/color presets come from
 * ai-agents-icons.js (window.AGENT_ICONS / window.agentIconSvg /
 * window.agentColor).
 */
(function () {
    'use strict';

    var CATEGORY_LABELS = {
        library: 'Library (read-only)',
        tags: 'Tags',
        items: 'Item metadata',
        notes: 'Notes',
        structure: 'Folders & albums',
        vision: 'Vision',
        web: 'Web (external sites, confirmed)',
    };
    var CATEGORY_ORDER = ['library', 'tags', 'items', 'notes', 'structure', 'vision', 'web'];

    var agents = [];
    var toolCatalogue = [];
    // null | 'new' | agent id currently being edited inline.
    var editingAgentId = null;

    function getEl(id) {
        return document.getElementById(id);
    }

    function esc(text) {
        if (typeof window !== 'undefined' && window.escapeHtml) {
            return window.escapeHtml(text);
        }
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
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

    async function errorDetail(resp, fallback) {
        var detail = null;
        try {
            var data = await resp.json();
            if (data && typeof data.detail === 'string') detail = data.detail;
            else if (data && Array.isArray(data.detail) && data.detail.length && data.detail[0].msg) {
                detail = data.detail[0].msg;
            }
        } catch (e) { /* ignore */ }
        return detail || fallback;
    }

    function agentById(id) {
        for (var i = 0; i < agents.length; i++) {
            if (agents[i].id === id) return agents[i];
        }
        return null;
    }

    function avatarHtml(agent, size) {
        var iconFn = typeof window !== 'undefined' ? window.agentIconSvg : null;
        var colorFn = typeof window !== 'undefined' ? window.agentColor : null;
        var name = agent ? agent.icon : 'sparkles';
        var color = agent ? (colorFn ? colorFn(agent.color_index) : '#3b82f6') : '#3b82f6';
        var s = size || 34;
        return '<span class="ai-agent-avatar" style="width:' + s + 'px;height:' + s + 'px;' +
            'color:' + esc(color) + ';border-color:' + esc(color) + '55;background:' + esc(color) + '1a">' +
            (iconFn ? iconFn(name, Math.round(s * 0.55)) : '') + '</span>';
    }

    // ========================================================================
    // Data
    // ========================================================================

    async function loadAgents() {
        var list = getEl('ai-agents-list');
        try {
            var resp = await fetch(baseUrl() + '/api/user/ai/agents', { credentials: 'include' });
            if (!resp.ok) {
                toast(await errorDetail(resp, 'Failed to load agents'), true);
                return;
            }
            var data = await resp.json();
            agents = Array.isArray(data.agents) ? data.agents : [];
            toolCatalogue = Array.isArray(data.tools) ? data.tools : [];
            renderAgents();
        } catch (err) {
            console.error('[settings-ai-agents] load failed:', err);
            if (list) list.innerHTML = '<p class="ai-agents-empty">Failed to load agents</p>';
        }
    }

    async function saveAgent(agentId, payload) {
        var isNew = agentId === 'new';
        var path = isNew
            ? baseUrl() + '/api/user/ai/agents'
            : baseUrl() + '/api/user/ai/agents/' + encodeURIComponent(agentId);
        try {
            var resp = await csrfFetch(path, {
                method: isNew ? 'POST' : 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                toast(await errorDetail(resp, 'Failed to save agent'), true);
                return false;
            }
            toast(isNew ? 'Agent created' : 'Agent saved');
            editingAgentId = null;
            await loadAgents();
            return true;
        } catch (err) {
            console.error('[settings-ai-agents] save failed:', err);
            toast('Failed to save agent', true);
            return false;
        }
    }

    async function deleteAgent(id) {
        var agent = agentById(id);
        if (!agent) return;
        if (!window.confirm('Delete agent "' + agent.name + '"? Existing chats keep their history.')) return;
        try {
            var resp = await csrfFetch(baseUrl() + '/api/user/ai/agents/' + encodeURIComponent(id), {
                method: 'DELETE',
            });
            if (!resp.ok) {
                toast(await errorDetail(resp, 'Failed to delete agent'), true);
                return;
            }
            toast('Agent deleted');
            await loadAgents();
        } catch (err) {
            console.error('[settings-ai-agents] delete failed:', err);
            toast('Failed to delete agent', true);
        }
    }

    async function duplicateAgent(id) {
        try {
            var resp = await csrfFetch(baseUrl() + '/api/user/ai/agents/' + encodeURIComponent(id) + '/duplicate', {
                method: 'POST',
            });
            if (!resp.ok) {
                toast(await errorDetail(resp, 'Failed to duplicate agent'), true);
                return;
            }
            var data = await resp.json();
            toast('Agent duplicated');
            editingAgentId = data.agent ? data.agent.id : null;
            await loadAgents();
        } catch (err) {
            console.error('[settings-ai-agents] duplicate failed:', err);
            toast('Failed to duplicate agent', true);
        }
    }

    // ========================================================================
    // Rendering
    // ========================================================================

    function agentCard(agent) {
        var isBuiltin = !!agent.is_builtin;
        var editing = editingAgentId === agent.id;
        var toolCount = (agent.tools || []).length;
        var actions = '';
        if (editing) {
            return '<div class="ai-agent-card" data-agent-id="' + esc(agent.id) + '">' +
                '<div data-form-slot></div></div>';
        }
        if (isBuiltin) {
            actions = '<button type="button" class="btn btn-small btn-secondary" data-action="duplicate" data-id="' + esc(agent.id) + '">Duplicate</button>';
        } else {
            actions =
                '<button type="button" class="btn btn-small btn-secondary" data-action="edit" data-id="' + esc(agent.id) + '">Edit</button>' +
                '<button type="button" class="btn btn-small btn-secondary" data-action="duplicate" data-id="' + esc(agent.id) + '">Duplicate</button>' +
                '<button type="button" class="btn btn-small btn-danger" data-action="delete" data-id="' + esc(agent.id) + '">Delete</button>';
        }
        return '<div class="ai-agent-card" data-agent-id="' + esc(agent.id) + '">' +
            '<div class="ai-agent-row">' +
            avatarHtml(agent) +
            '<div class="ai-agent-info">' +
            '<div class="ai-agent-name">' + esc(agent.name) +
            (isBuiltin ? ' <span class="ai-agent-badge">Built-in</span>' : '') +
            '</div>' +
            (agent.description ? '<div class="ai-agent-desc">' + esc(agent.description) + '</div>' : '') +
            '<div class="ai-agent-meta">' + toolCount + ' tool' + (toolCount === 1 ? '' : 's') + '</div>' +
            '</div>' +
            '<div class="credential-actions">' + actions + '</div>' +
            '</div>' +
            '</div>';
    }

    function renderAgents() {
        var list = getEl('ai-agents-list');
        if (!list) return;
        var html = '';
        if (editingAgentId === 'new') {
            html += '<div class="ai-agent-card"><div data-form-slot></div></div>';
        }
        for (var i = 0; i < agents.length; i++) {
            html += agentCard(agents[i]);
        }
        if (!html) {
            html = '<p class="ai-agents-empty">No agents yet</p>';
        }
        list.innerHTML = html;
        var slot = list.querySelector('[data-form-slot]');
        if (slot) {
            var agent = editingAgentId === 'new' ? null : agentById(editingAgentId);
            renderAgentForm(slot, agent);
        }
    }

    function renderAgentForm(container, agent) {
        var name = agent ? agent.name : '';
        var description = agent ? agent.description || '' : '';
        var prompt = agent ? agent.system_prompt || '' : '';
        var icon = agent ? agent.icon || 'sparkles' : 'sparkles';
        var colorIndex = agent ? agent.color_index || 0 : 0;
        var selectedTools = {};
        (agent ? agent.tools || [] : []).forEach(function (t) { selectedTools[t] = true; });

        var icons = (typeof window !== 'undefined' && window.AGENT_ICONS) || ['sparkles'];
        var colors = (typeof window !== 'undefined' && window.AGENT_COLORS) || ['#3b82f6'];
        var iconFn = typeof window !== 'undefined' ? window.agentIconSvg : null;

        var iconGrid = '<div class="ai-icon-grid">';
        icons.forEach(function (name_) {
            iconGrid += '<button type="button" class="ai-icon-option' + (name_ === icon ? ' selected' : '') + '"' +
                ' data-icon="' + esc(name_) + '" title="' + esc(name_) + '">' +
                (iconFn ? iconFn(name_, 16) : esc(name_)) + '</button>';
        });
        iconGrid += '</div>';

        var colorGrid = '<div class="ai-color-grid">';
        colors.forEach(function (color, index) {
            colorGrid += '<button type="button" class="ai-color-option' + (index === colorIndex ? ' selected' : '') + '"' +
                ' data-color-index="' + index + '" style="background:' + esc(color) + '" aria-label="Color ' + (index + 1) + '"></button>';
        });
        colorGrid += '</div>';

        var toolsHtml = '';
        CATEGORY_ORDER.forEach(function (category) {
            var tools = toolCatalogue.filter(function (t) { return t.category === category; });
            if (tools.length === 0) return;
            var allSelected = tools.every(function (t) { return selectedTools[t.name]; });
            toolsHtml += '<div class="ai-tool-group" data-category="' + esc(category) + '">' +
                '<label class="ai-tool-group-header">' +
                '<input type="checkbox" data-group-toggle="' + esc(category) + '"' + (allSelected ? ' checked' : '') + '> ' +
                '<span>' + esc(CATEGORY_LABELS[category] || category) + '</span>' +
                '</label>';
            tools.forEach(function (tool) {
                var mutating = tool.confirmation === 'always';
                toolsHtml += '<label class="ai-tool-row" title="' + esc(tool.description) + '">' +
                    '<input type="checkbox" data-tool="' + esc(tool.name) + '"' + (selectedTools[tool.name] ? ' checked' : '') + '> ' +
                    '<span class="ai-tool-name">' + esc(tool.name) + '</span>' +
                    (mutating ? '<span class="ai-tool-confirm-badge" title="Asks for your approval before running">confirm</span>' : '') +
                    '</label>';
            });
            toolsHtml += '</div>';
        });

        container.innerHTML =
            '<div class="form-stack ai-agent-form">' +
            '<input type="text" data-field="name" placeholder="Agent name" maxlength="64" value="' + esc(name) + '">' +
            '<input type="text" data-field="description" placeholder="Short description (shown in the picker)" maxlength="200" value="' + esc(description) + '">' +
            '<div class="ai-agent-form-label">Icon</div>' + iconGrid +
            '<div class="ai-agent-form-label">Color</div>' + colorGrid +
            '<div class="ai-agent-form-label">System prompt</div>' +
            '<textarea data-field="system_prompt" rows="7" spellcheck="false" placeholder="You are a meticulous tagging assistant...">' + esc(prompt) + '</textarea>' +
            '<div class="ai-agent-form-label">Tools the agent may use</div>' +
            '<div class="ai-tool-groups">' + (toolsHtml || '<p class="ai-agents-empty">No tools available</p>') + '</div>' +
            '<div class="ai-agent-form-actions">' +
            '<button type="button" class="btn btn-small" data-form-action="save">Save</button>' +
            '<button type="button" class="btn btn-small btn-secondary" data-form-action="cancel">Cancel</button>' +
            '</div>' +
            '</div>';
    }

    function collectFormPayload(container) {
        var nameField = container.querySelector('[data-field="name"]');
        var descField = container.querySelector('[data-field="description"]');
        var promptField = container.querySelector('[data-field="system_prompt"]');
        var iconOption = container.querySelector('.ai-icon-option.selected');
        var colorOption = container.querySelector('.ai-color-option.selected');
        var tools = [];
        container.querySelectorAll('[data-tool]').forEach(function (box) {
            if (box.checked) tools.push(box.dataset.tool);
        });
        return {
            name: nameField ? nameField.value.trim() : '',
            description: descField ? descField.value.trim() : '',
            system_prompt: promptField ? promptField.value : '',
            icon: iconOption ? iconOption.dataset.icon : 'sparkles',
            color_index: colorOption ? parseInt(colorOption.dataset.colorIndex, 10) : 0,
            tools: tools,
        };
    }

    // ========================================================================
    // Wiring
    // ========================================================================

    function onListClick(e) {
        var iconOption = e.target.closest('.ai-icon-option');
        if (iconOption) {
            var grid = iconOption.closest('.ai-icon-grid');
            grid.querySelectorAll('.ai-icon-option').forEach(function (el) {
                el.classList.toggle('selected', el === iconOption);
            });
            return;
        }
        var colorOption = e.target.closest('.ai-color-option');
        if (colorOption) {
            var colors = colorOption.closest('.ai-color-grid');
            colors.querySelectorAll('.ai-color-option').forEach(function (el) {
                el.classList.toggle('selected', el === colorOption);
            });
            return;
        }
        var groupToggle = e.target.closest('[data-group-toggle]');
        if (groupToggle) {
            // The checkbox state already flipped; sync the group rows to it.
            var group = groupToggle.closest('.ai-tool-group');
            group.querySelectorAll('[data-tool]').forEach(function (box) {
                box.checked = groupToggle.checked;
            });
            return;
        }
        var actionBtn = e.target.closest('[data-action]');
        if (actionBtn) {
            var id = actionBtn.dataset.id;
            var action = actionBtn.dataset.action;
            if (action === 'edit') {
                editingAgentId = parseInt(id, 10);
                renderAgents();
            } else if (action === 'delete') {
                deleteAgent(parseInt(id, 10));
            } else if (action === 'duplicate') {
                duplicateAgent(parseInt(id, 10));
            }
            return;
        }
        var formBtn = e.target.closest('[data-form-action]');
        if (formBtn) {
            var card = formBtn.closest('.ai-agent-card');
            var form = card.querySelector('.ai-agent-form');
            if (formBtn.dataset.formAction === 'cancel') {
                editingAgentId = null;
                renderAgents();
                return;
            }
            if (!form) return;
            var payload = collectFormPayload(form);
            saveAgent(editingAgentId, payload);
        }
    }

    function init() {
        var list = getEl('ai-agents-list');
        if (!list) return; // not on the settings page

        list.addEventListener('click', onListClick);
        var addBtn = getEl('ai-add-agent-btn');
        if (addBtn) {
            addBtn.addEventListener('click', function () {
                editingAgentId = 'new';
                renderAgents();
                var field = list.querySelector('[data-field="name"]');
                if (field) field.focus();
            });
        }
        loadAgents();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
