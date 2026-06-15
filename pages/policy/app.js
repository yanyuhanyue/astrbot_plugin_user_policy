(() => {
  "use strict";

  let bridge = null;
  const state = {
    revision: 0,
    personas: [],
    providers: [],
    plugins: [],
    users: [],
    groups: [],
    schedules: [],
    memeIsolation: { enabled: false, copy_default_descriptions: true },
    giteeAiimgEffects: { enabled: false, effects: {} },
    privateCompanionProactive: {
      enabled: true,
      image_fast_mode: true,
      image_debounce_seconds: 1.5,
      image_vision_timeout_seconds: 15,
    },
    autoPersonaSelector: {
      provider_id: "",
      model: "",
      timeout_seconds: 8,
      extra_prompt: "",
    },
    proactivePrompts: { prompts: {} },
    lifeLibraries: {},
    lifePersonaMap: {},
    currentLifeLibraryId: "",
    lifePoolUi: {},
    lifePoolImportKey: "",
    lifePoolImportLibraryId: "",
    integrations: { items: [], diagnostics: [] },
    currentGroupId: "",
    pendingDelete: null,
    memeLibrary: {
      targets: [],
      library: null,
      uploadCategory: "",
      selectedImages: new Set(),
      previewCache: new Map(),
      libraries: {},
      defaultLibrary: null,
      collapsedCategories: new Set(),
      operationBusy: false,
      previewRunId: 0,
      personaLibraryMap: {},
      personas: [],
      revision: 0,
    },
    smartImage: {
      isolation: { enabled: false, inherit_auto_tags: true },
      libraries: {},
      globalTags: [],
      policyGlobalTags: [],
      smartGlobalTags: [],
      personaLibraryMap: {},
      targets: [],
      personas: [],
      status: {},
      revision: 0,
      currentLibraryId: "",
      library: null,
      pending: { images: [] },
      selectedPending: new Set(),
      selectedImages: new Set(),
      selectedImageTargets: new Set(),
      previewCache: new Map(),
      pendingPreviewCache: new Map(),
      nameAction: null,
      tagEditorHash: "",
    },
    sessionImportPreview: null,
  };
  const weekdays = [
    { value: 0, label: "周一" },
    { value: 1, label: "周二" },
    { value: 2, label: "周三" },
    { value: 3, label: "周四" },
    { value: 4, label: "周五" },
    { value: 5, label: "周六" },
    { value: 6, label: "周日" },
  ];
  const PAGE_META = {
    users: {
      title: "自由切换每个会话的人格",
      subtitle: "为私聊用户、群聊和群成员分配不同人格，无需编辑配置文件。",
    },
    groups: {
      title: "群聊管理",
      subtitle: "设置群默认人格、成员名单、策略管理员和插件范围。",
    },
    schedules: {
      title: "人格计划",
      subtitle: "每天、每周定时切换固定人格，或按间隔从指定范围随机切换。",
    },
    integrations: {
      title: "兼容状态",
      subtitle: "检测常见插件状态，并配置少量安全兼容能力。",
    },
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];

  function syncPageHeader(page) {
    const meta = PAGE_META[page] || PAGE_META.users;
    $("#pageTitle").textContent = meta.title;
    $("#pageSubtitle").textContent = meta.subtitle;
    $$("[data-page-action]").forEach((item) => {
      const pages = String(item.dataset.pageAction || "")
        .split(/\s+/)
        .filter(Boolean);
      item.classList.toggle("hidden", !pages.includes(page));
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function unwrap(response) {
    if (response == null) return {};
    if (response.status === "error" || response.ok === false) {
      const error = new Error(
        response.message
        || response.data?.message
        || "接口返回了未知错误。"
      );
      error.code = response.code;
      throw error;
    }
    if (
      (response.status === "ok" || response.ok === true)
      && Object.prototype.hasOwnProperty.call(response, "data")
    ) {
      return response.data ?? {};
    }
    return response;
  }

  async function apiGet(endpoint) {
    if (!bridge?.apiGet) {
      throw new Error("未检测到 AstrBot 插件页面桥接器。");
    }
    return unwrap(await bridge.apiGet(endpoint));
  }

  async function apiPost(endpoint, body) {
    if (!bridge?.apiPost) {
      throw new Error("未检测到 AstrBot 插件页面桥接器。");
    }
    return unwrap(await bridge.apiPost(endpoint, body));
  }

  function toast(message, type = "success") {
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.textContent = message;
    $("#toastStack").append(item);
    setTimeout(() => item.remove(), 3600);
  }

  function setButtonBusy(button, busy, text) {
    if (!button) return;
    if (busy) {
      if (!button.dataset.idleText) button.dataset.idleText = button.textContent;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.textContent = text;
      return;
    }
    button.disabled = false;
    button.removeAttribute("aria-busy");
    if (button.dataset.idleText) {
      button.textContent = button.dataset.idleText;
      delete button.dataset.idleText;
    }
  }

  function openModal(id) {
    $(`#${id}`).classList.remove("hidden");
  }

  function closeModal(id) {
    $(`#${id}`).classList.add("hidden");
    if (id === "smartImageNameModal") {
      state.smartImage.nameAction = null;
    }
    if (id === "smartImageTagModal") {
      state.smartImage.tagEditorHash = "";
    }
  }

  function clearPendingDelete() {
    state.pendingDelete = null;
    $("#confirmDeleteButton").disabled = false;
    $("#confirmDeleteButton").textContent = "确认删除";
    closeModal("deleteModal");
  }

  function requestDelete({ title, message, endpoint, body, successMessage, afterSuccess, action }) {
    state.pendingDelete = { endpoint, body, successMessage, afterSuccess, action };
    $("#deleteModalTitle").textContent = title;
    $("#deleteModalMessage").textContent = message;
    $("#confirmDeleteButton").disabled = false;
    $("#confirmDeleteButton").textContent = "确认删除";
    openModal("deleteModal");
  }

  async function confirmPendingDelete() {
    const pending = state.pendingDelete;
    if (!pending) return;
    const button = $("#confirmDeleteButton");
    button.disabled = true;
    button.textContent = "正在删除...";
    try {
      const success = pending.action
        ? await pending.action()
        : await mutate(
          pending.endpoint,
          pending.body,
          pending.successMessage
        );
      if (success) {
        const afterSuccess = pending.afterSuccess;
        clearPendingDelete();
        if (typeof afterSuccess === "function") afterSuccess();
        return;
      }
    } catch (error) {
      toast(`删除失败：${error.message || String(error)}`, "error");
    }
    button.disabled = false;
    button.textContent = "确认删除";
  }

  function pluginModeLabel(access) {
    const mode = access?.mode || "all";
    const count = access?.plugins?.length || 0;
    if (mode === "inherit") return "沿用群聊";
    if (mode === "allowlist") return `仅允许 ${count} 个`;
    if (mode === "denylist") return `禁用 ${count} 个`;
    return "允许全部";
  }

  function memberAccessLabel(access) {
    const mode = access?.mode || "all";
    const count = access?.users?.length || 0;
    if (mode === "allowlist") return `仅名单内 ${count} 人`;
    if (mode === "denylist") return `禁止名单内 ${count} 人`;
    return "所有成员";
  }

  function personaLabel(personaId, inheritLabel) {
    if (!personaId) return inheritLabel;
    const persona = state.personas.find((item) => item.persona_id === personaId);
    if (!persona) return `${personaId}（已失效）`;
    const name = persona.name || persona.persona_id;
    return name === persona.persona_id
      ? name
      : `${name}（${persona.persona_id}）`;
  }

  function personaRuleLabel(rule, inheritLabel) {
    if (rule?.persona_mode === "auto") return "自动切换人格";
    return personaLabel(rule?.persona_id || "", inheritLabel);
  }

  function memoryIsolationLabel(value, inherit = false) {
    if (inherit && value == null) return "沿用群聊";
    return value === false ? "关闭" : "开启";
  }

  function fillPersonaSelect(select, value, inheritLabel, mode = "", allowAuto = true) {
    const selectedValue = mode === "auto"
      ? "__auto__"
      : (mode === "fixed" || (!mode && value) ? value : "");
    const options = [
      `<option value="">${escapeHtml(inheritLabel)}</option>`,
      ...(allowAuto ? ['<option value="__auto__">自动切换人格</option>'] : []),
      ...state.personas.map((persona) => {
        const label = personaLabel(persona.persona_id, inheritLabel);
        return `<option value="${escapeHtml(persona.persona_id)}">${escapeHtml(label)}</option>`;
      }),
    ];
    if (selectedValue && selectedValue !== "__auto__" && !state.personas.some((item) => item.persona_id === selectedValue)) {
      options.push(
        `<option value="${escapeHtml(selectedValue)}">${escapeHtml(selectedValue)}（已失效）</option>`
      );
    }
    select.innerHTML = options.join("");
    select.value = selectedValue;
  }

  function providerOptions(selected = "", inheritLabel = "跟随当前会话模型") {
    const options = [
      `<option value="">${escapeHtml(inheritLabel)}</option>`,
      ...state.providers.map((provider) => {
        const id = provider.provider_id;
        const model = provider.model || provider.name || id;
        const label = model === id ? model : `${model}（${id}）`;
        return `<option value="${escapeHtml(id)}" ${id === selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
      }),
    ];
    if (selected && !state.providers.some((item) => item.provider_id === selected)) {
      options.push(`<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}（当前不可用）</option>`);
    }
    return options.join("");
  }

  function renderAutoPersonaEditor(prefix, autoPersona = {}) {
    const selected = new Set(autoPersona.persona_ids || []);
    const settings = autoPersona.persona_settings || {};
    $(`#${prefix}SelectorProvider`).innerHTML = providerOptions(
      autoPersona.selector_provider_id || "",
      "沿用全局选择模型"
    );
    $(`#${prefix}AutoScenario`).value = autoPersona.scenario || "";
    $(`#${prefix}AutoPersonaRules`).innerHTML = state.personas.map((persona) => {
      const id = persona.persona_id;
      const setting = settings[id] || {};
      return `
        <article class="auto-persona-rule ${selected.has(id) ? "active" : ""}" data-auto-rule="${escapeHtml(id)}">
          <label class="check-field compact">
            <input type="checkbox" data-auto-persona="${escapeHtml(id)}" ${selected.has(id) ? "checked" : ""}>
            <span><strong>${escapeHtml(persona.name || id)}</strong><small>${escapeHtml(id)}</small></span>
          </label>
          <label class="field">
            <span>回复模型</span>
            <select data-auto-provider="${escapeHtml(id)}">${providerOptions(setting.provider_id || "")}</select>
          </label>
          <label class="field">
            <span>人格描述</span>
            <textarea rows="2" maxlength="2000" data-auto-desc="${escapeHtml(id)}">${escapeHtml(setting.persona_desc || "")}</textarea>
          </label>
          <label class="field">
            <span>适用场景</span>
            <textarea rows="2" maxlength="2000" data-auto-scene="${escapeHtml(id)}">${escapeHtml(setting.scenario_desc || "")}</textarea>
          </label>
        </article>`;
    }).join("") || '<span class="muted">当前没有可选择的 AstrBot 人格。</span>';
  }

  function collectAutoPersona(prefix) {
    const root = $(`#${prefix}AutoPersonaRules`);
    const personaIds = [...root.querySelectorAll("[data-auto-persona]:checked")]
      .map((input) => input.dataset.autoPersona);
    const personaSettings = {};
    personaIds.forEach((personaId) => {
      const provider = [...root.querySelectorAll("[data-auto-provider]")]
        .find((item) => item.dataset.autoProvider === personaId)?.value || "";
      const personaDesc = [...root.querySelectorAll("[data-auto-desc]")]
        .find((item) => item.dataset.autoDesc === personaId)?.value.trim() || "";
      const scenarioDesc = [...root.querySelectorAll("[data-auto-scene]")]
        .find((item) => item.dataset.autoScene === personaId)?.value.trim() || "";
      personaSettings[personaId] = {
        provider_id: provider,
        persona_desc: personaDesc,
        scenario_desc: scenarioDesc,
      };
    });
    return {
      persona_ids: personaIds,
      scenario: $(`#${prefix}AutoScenario`).value.trim(),
      selector_provider_id: $(`#${prefix}SelectorProvider`).value,
      persona_settings: personaSettings,
    };
  }

  function personaSelection(prefix, inheritMode) {
    const value = $(`#${prefix}Persona`).value;
    if (value === "__auto__") {
      const autoPersona = collectAutoPersona(prefix);
      if (autoPersona.persona_ids.length < 2) {
        throw new Error("自动切换人格至少选择两个人格。");
      }
      return { persona_mode: "auto", persona_id: "", auto_persona: autoPersona };
    }
    if (value) {
      return { persona_mode: "fixed", persona_id: value, auto_persona: collectAutoPersona(prefix) };
    }
    return { persona_mode: inheritMode, persona_id: "", auto_persona: collectAutoPersona(prefix) };
  }

  function syncAutoPersonaFields(prefix) {
    const automatic = $(`#${prefix}Persona`).value === "__auto__";
    $(`#${prefix}AutoPersonaFields`).classList.toggle("hidden", !automatic);
  }

  function personaSelectOptions(value = "", placeholder = "请选择人格") {
    const options = [
      `<option value="" ${value ? "" : "selected"}>${escapeHtml(placeholder)}</option>`,
      ...state.personas.map((persona) => {
        const label = personaLabel(persona.persona_id, placeholder);
        const selected = persona.persona_id === value ? "selected" : "";
        return `<option value="${escapeHtml(persona.persona_id)}" ${selected}>${escapeHtml(label)}</option>`;
      }),
    ];
    if (value && !state.personas.some((item) => item.persona_id === value)) {
      options.push(
        `<option value="${escapeHtml(value)}" selected>${escapeHtml(value)}（已失效）</option>`
      );
    }
    return options.join("");
  }

  function renderPersonaPicker(picker, selectedIds) {
    const selected = new Set(selectedIds || []);
    picker.innerHTML = state.personas.map((persona) => `
      <label class="persona-choice">
        <input type="checkbox" data-persona="${escapeHtml(persona.persona_id)}" ${selected.has(persona.persona_id) ? "checked" : ""}>
        <span class="persona-choice-check">✓</span>
        <span>
          <strong>${escapeHtml(persona.name || persona.persona_id)}</strong>
          <small>${escapeHtml(persona.persona_id)}</small>
        </span>
      </label>
    `).join("") || '<span class="muted">当前没有可选择的 AstrBot 人格。</span>';
  }

  function selectedPersonas(picker) {
    return [...picker.querySelectorAll("input[data-persona]:checked")]
      .map((input) => input.dataset.persona);
  }

  function renderPluginPicker(picker, selectedNames) {
    const selected = new Set(selectedNames || []);
    picker.innerHTML = state.plugins.map((plugin) => {
      const stateLabel = !plugin.installed
        ? "当前未安装"
        : (!plugin.activated ? "当前未启用" : plugin.name);
      return `
        <label class="plugin-option">
          <input type="checkbox" data-plugin="${escapeHtml(plugin.name)}" ${selected.has(plugin.name) ? "checked" : ""}>
          <span>
            <strong>${escapeHtml(plugin.display_name || plugin.name)}</strong>
            <small>${escapeHtml(stateLabel)}</small>
          </span>
        </label>`;
    }).join("") || '<span class="muted">当前没有可选择的第三方插件。</span>';
  }

  function selectedPlugins(picker) {
    return [...picker.querySelectorAll("input[data-plugin]:checked")]
      .map((input) => input.dataset.plugin);
  }

  function syncPluginPicker(modeSelect, picker) {
    const hidden = ["all", "inherit"].includes(modeSelect.value);
    picker.classList.toggle("hidden", hidden);
  }

  function parseIds(value) {
    return [...new Set(
      String(value || "")
        .split(/[\n,，]+/)
        .map((item) => item.trim())
        .filter(Boolean)
    )];
  }

  function parseTagText(value) {
    return [...new Set(
      String(value || "")
        .replace(/，/g, ",")
        .split(/[\n,]+/)
        .map((item) => item.trim())
        .filter(Boolean)
    )];
  }

  function statusPill(label, tone = "success") {
    return `<span class="state-pill ${tone}"><span></span>${escapeHtml(label)}</span>`;
  }

  function sessionIdMarkup(value) {
    const raw = String(value || "");
    const index = raw.indexOf(":");
    if (index > 0 && index < raw.length - 1) {
      return `
        <span class="session-id">
          <span class="muted mono">${escapeHtml(raw.slice(0, index + 1))}</span>
          <strong>${escapeHtml(raw.slice(index + 1))}</strong>
        </span>
      `;
    }
    return `<span class="session-id"><strong>${escapeHtml(raw)}</strong></span>`;
  }

  function featureRow(icon, label, value) {
    return `
      <div class="detail-row feature-row">
        <span class="detail-icon detail-icon-${escapeHtml(icon)}" aria-hidden="true"></span>
        <span class="detail-label">${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </div>
    `;
  }

  function renderUsers() {
    $("#usersBody").innerHTML = state.users.map((user) => `
      <tr>
        <td>${sessionIdMarkup(user.user_id)}</td>
        <td class="text-nowrap">${escapeHtml(personaRuleLabel(user, "跟随会话默认"))}</td>
        <td class="text-nowrap">${statusPill(user.blocked ? "禁止使用" : "允许使用", user.blocked ? "danger" : "success")}</td>
        <td class="text-nowrap">${statusPill(user.allow_persona_switch ? "已授权" : "未授权", user.allow_persona_switch ? "success" : "neutral")}</td>
        <td class="text-nowrap">${statusPill(memoryIsolationLabel(user.memory_isolation), user.memory_isolation === false ? "neutral" : "success")}</td>
        <td class="text-nowrap">${statusPill(memoryIsolationLabel(user.livingmemory_isolation), user.livingmemory_isolation === false ? "neutral" : "success")}</td>
        <td class="text-nowrap">${statusPill(user.private_companion_proactive !== false ? "允许" : "关闭", user.private_companion_proactive !== false ? "success" : "neutral")}</td>
        <td class="text-nowrap">${statusPill(pluginModeLabel(user.plugin_access), "success")}</td>
        <td>
          <div class="row-actions">
            <button class="button icon-button edit" type="button" data-edit-user="${escapeHtml(user.user_id)}" aria-label="编辑用户">编辑</button>
            <button class="button icon-button danger" type="button" data-delete-user="${escapeHtml(user.user_id)}" aria-label="删除用户">删除</button>
          </div>
        </td>
      </tr>
    `).join("");
    $("#usersEmpty").classList.toggle("hidden", state.users.length > 0);
  }

  function renderGroups() {
    $("#groupsGrid").innerHTML = state.groups.map((group) => {
      const memberCount = Object.keys(group.users || {}).length;
      return `
        <article class="card group-card">
          <div class="group-card-header">
            <div>
              <h3>${sessionIdMarkup(group.group_id)}</h3>
              <p>用户策略：${escapeHtml(personaRuleLabel(group, "AstrBot 默认人格"))}</p>
            </div>
            <span class="badge neutral">${memberCount} 个成员设置</span>
          </div>
          <div class="detail-panels">
            <div class="detail-panel">
              ${featureRow("persona", "默认人格", personaRuleLabel(group, "跟随会话默认"))}
              ${featureRow("chat", "对话记忆", memoryIsolationLabel(group.memory_isolation))}
              ${featureRow("memory", "长期记忆", memoryIsolationLabel(group.livingmemory_isolation))}
            </div>
            <div class="detail-panel">
              ${featureRow("members", "成员访问", memberAccessLabel(group.member_access))}
              ${featureRow("shield", "策略管理员", `${group.policy_admins?.length || 0} 人`)}
              ${featureRow("plugin", "插件权限", pluginModeLabel(group.plugin_access))}
            </div>
          </div>
          <div class="group-actions">
            <button class="button small" type="button" data-members-group="${escapeHtml(group.group_id)}">成员设置</button>
            <button class="button small" type="button" data-edit-group="${escapeHtml(group.group_id)}">编辑</button>
            <button class="button small danger" type="button" data-delete-group="${escapeHtml(group.group_id)}">删除</button>
          </div>
        </article>`;
    }).join("");
    $("#groupsEmpty").classList.toggle("hidden", state.groups.length > 0);
  }

  function encodeMemberTarget(groupId, userId) {
    return `${encodeURIComponent(groupId)}|${encodeURIComponent(userId)}`;
  }

  function decodeMemberTarget(value) {
    const [groupId = "", userId = ""] = String(value || "").split("|");
    return {
      groupId: decodeURIComponent(groupId),
      userId: decodeURIComponent(userId),
    };
  }

  function scheduleTargetValue(schedule) {
    if (schedule.target_type === "private") return schedule.user_id || "";
    if (schedule.target_type === "group") return schedule.group_id || "";
    return encodeMemberTarget(schedule.group_id || "", schedule.user_id || "");
  }

  function scheduleTargetLabel(schedule) {
    if (schedule.target_type === "private") {
      return `私聊用户 ${schedule.user_id}`;
    }
    const group = state.groups.find((item) => item.group_id === schedule.group_id);
    const groupLabel = group?.description
      ? `${group.description}（${schedule.group_id}）`
      : schedule.group_id;
    if (schedule.target_type === "group") return `群默认人格 ${groupLabel}`;
    return `群成员 ${schedule.user_id} · ${groupLabel}`;
  }

  function scheduleTargetMarkup(schedule) {
    if (schedule.target_type === "private") {
      return `私聊用户 ${sessionIdMarkup(schedule.user_id)}`;
    }
    return escapeHtml(scheduleTargetLabel(schedule));
  }

  function formatRunTime(timestamp) {
    if (!timestamp) return "等待计算";
    return new Date(timestamp * 1000).toLocaleString("zh-CN", {
      hour12: false,
    });
  }

  function scheduleRuleLabel(schedule) {
    if (schedule.mode === "daily") {
      return `每天 ${schedule.daily_time} 切换到 ${personaLabel(schedule.persona_id, "未选择")}`;
    }
    if (schedule.mode === "weekly") {
      const rules = schedule.weekly_rules || [];
      const enabled = rules
        .filter((rule) => rule.enabled !== false && rule.persona_id)
        .map((rule) => {
          const label = weekdays.find((item) => item.value === Number(rule.weekday))?.label
            || `周${Number(rule.weekday) + 1}`;
          return `${label} ${personaLabel(rule.persona_id, "未选择")}`;
        });
      return `每周 ${schedule.weekly_time || "09:00"}：${enabled.join("；") || "未设置星期"}`;
    }
    return `每 ${schedule.interval_minutes} 分钟，从 ${schedule.persona_ids?.length || 0} 个人格中随机切换`;
  }

  function renderSchedules() {
    $("#schedulesGrid").innerHTML = state.schedules.map((schedule) => {
      const runtime = schedule.runtime || {};
      const statusClass = schedule.enabled ? "" : "neutral";
      const statusLabel = schedule.enabled ? "已启用" : "已停用";
      const lastResult = runtime.last_status === "error"
        ? `失败：${runtime.last_message || "未知错误"}`
        : (runtime.last_status === "success"
          ? `已切换为 ${personaLabel(runtime.last_persona_id, "未知人格")}`
          : "尚未执行");
      return `
        <article class="card schedule-card">
          <div class="group-card-header schedule-card-header">
            <div class="schedule-title">
              <span class="content-card-icon icon-calendar" aria-hidden="true"></span>
              <div>
                <h3>${escapeHtml(schedule.name || "未命名计划")}</h3>
                <p>${scheduleTargetMarkup(schedule)}</p>
              </div>
            </div>
            <span class="badge ${statusClass}">${statusLabel}</span>
          </div>
          <div class="detail-list schedule-timeline">
            ${featureRow("clock", "执行规则", scheduleRuleLabel(schedule))}
            ${featureRow("calendar", "下次执行", schedule.enabled ? formatRunTime(runtime.next_run_at) : "已停用")}
            ${featureRow(runtime.last_status === "error" ? "warning" : "pulse", "上次结果", lastResult)}
          </div>
          <div class="group-actions">
            <button class="button small" type="button" data-edit-schedule="${escapeHtml(schedule.schedule_id)}">编辑</button>
            <button class="button small danger" type="button" data-delete-schedule="${escapeHtml(schedule.schedule_id)}">删除</button>
          </div>
        </article>`;
    }).join("");
    $("#schedulesEmpty").classList.toggle("hidden", state.schedules.length > 0);
  }

  function findIntegration(names) {
    return (state.integrations?.items || []).find((item) =>
      names.includes(item.name)
    );
  }

  function renderIntegrations() {
    const report = state.integrations || {};
    const memeItem = findIntegration(["meme_manager", "astrbot_plugin_meme_manager"]);
    const smartImageItem = findIntegration(["astrbot_plugin_smart_imagechat_hub"]);
    const giteeAiimgItem = findIntegration(["astrbot_plugin_gitee_aiimg", "gitee_aiimg"]);
    const proactiveItem = findIntegration(["astrbot_plugin_proactive_chat"]);
    const privateCompanionItem = findIntegration(["astrbot_plugin_private_companion"]);
    const lifeItem = findIntegration(["astrbot_plugin_life_scheduler"]);
    const livingItem = findIntegration(["LivingMemory", "astrbot_plugin_livingmemory"]);
    const memeStatus = memeItem?.meme_isolation;
    const smartImageStatus = smartImageItem?.smart_image_isolation;
    const giteeAiimgStatus = giteeAiimgItem?.gitee_aiimg_effects;
    const memeMappedCount = Object.keys(state.memeLibrary.personaLibraryMap || {}).length;
    const memeWrappedCount = (memeStatus?.wrapped_method_count || 0)
      + (memeStatus?.wrapped_handler_count || 0);
    const giteeWrappedCount = (giteeAiimgStatus?.wrapped_method_count || 0)
      + (giteeAiimgStatus?.wrapped_handler_count || 0);
    const configuredGiteeEffects = Object.values(
      state.giteeAiimgEffects.effects || {}
    ).filter((value) => String(value || "").trim()).length;
    renderGiteeAiimgEffects(giteeAiimgStatus);
    $("#diagnostics").innerHTML = (report.diagnostics || []).map((item) => `
      <div class="diagnostic ${escapeHtml(item.level || "")}">
        <strong>${escapeHtml(item.title)}</strong>
        <span>${escapeHtml(item.message)}</span>
      </div>
    `).join("");
    $("#integrationGrid").innerHTML = (report.items || []).map((item) => {
      const isMemeManager = ["meme_manager", "astrbot_plugin_meme_manager"].includes(item.name);
      const isSmartImage = item.name === "astrbot_plugin_smart_imagechat_hub";
      const isGiteeAiimg = ["astrbot_plugin_gitee_aiimg", "gitee_aiimg"].includes(item.name);
      const isProactive = item.label === "Proactive Chat";
      const isPrivateCompanion = item.label === "Private Companion";
      const isLife = item.label === "Life Scheduler";
      const isLiving = item.label === "LivingMemory";
      const privateCompanionGlobalEnabled = state.privateCompanionProactive.enabled !== false;
      const privateCompanionScope = privateCompanionItem?.private_companion_proactive?.scope || (
        privateCompanionGlobalEnabled
          ? "全局总闸开启，逐用户开关可继续关闭。"
          : "全局总闸关闭，逐用户开关无法单独放行。"
      );
      const privateCompanionImageStatus = privateCompanionItem?.private_companion_proactive?.image_latency || {};
      const privateCompanionImageFast = state.privateCompanionProactive.image_fast_mode !== false;
      const privateCompanionImageDebounce = Number(
        state.privateCompanionProactive.image_debounce_seconds ?? 1.5
      );
      const privateCompanionImageTimeout = Number(
        state.privateCompanionProactive.image_vision_timeout_seconds ?? 15
      );
      return `
        <article class="card integration-card">
          <div class="integration-state">
            <span class="badge ${item.activated ? "" : "neutral"}">${item.activated ? "已启用" : (item.detected ? "未启用" : "未安装")}</span>
          </div>
          <h3 class="integration-title">
            <span>${escapeHtml(item.label)}</span>
            ${isMemeManager ? `<span class="badge warning">暂停维护</span>` : ""}
          </h3>
          <code>${escapeHtml(item.name)}</code>
          <p>${escapeHtml(item.role)}</p>
          <small>${escapeHtml(item.boundary)}</small>
          ${isPrivateCompanion ? `
            <div class="integration-feature">
              <div>
                <strong>私聊主动对话权限</strong>
                <span>${privateCompanionGlobalEnabled ? "全局总闸开启" : "全局总闸关闭"} · ${escapeHtml(privateCompanionScope)}</span>
                <small>${escapeHtml(privateCompanionItem?.private_companion_proactive?.message || "")}</small>
              </div>
              <label class="check-field compact">
                <input type="checkbox" data-private-companion-card-enabled ${privateCompanionGlobalEnabled ? "checked" : ""}>
                <span>开启 Private Companion 私聊主动对话全局总闸</span>
              </label>
              <div class="group-actions integration-actions">
                <button class="button small primary" type="button" data-save-private-companion-card>保存设置</button>
              </div>
            </div>
            <div class="integration-feature">
              <div>
                <strong>私聊图片快速识别保护</strong>
                <span>${privateCompanionImageFast ? "已开启" : "已关闭"} · ${privateCompanionImageStatus.applied ? "运行时适配成功" : "等待运行时适配"}</span>
                <small>${escapeHtml(privateCompanionImageStatus.message || "限制图片防抖和单次视觉任务，并阻止同一图片超时后重复识图。")}</small>
              </div>
              <label class="check-field compact">
                <input type="checkbox" data-private-companion-image-fast ${privateCompanionImageFast ? "checked" : ""}>
                <span>启用快速识图保护</span>
              </label>
              <div class="integration-input-grid">
                <label class="field">
                  <span>图片防抖上限（秒）</span>
                  <input type="number" min="0" max="10" step="0.5" value="${privateCompanionImageDebounce}" data-private-companion-image-debounce>
                  <small>只限制图片消息；文字和群聊防抖不变。</small>
                </label>
                <label class="field">
                  <span>单次视觉任务上限（秒）</span>
                  <input type="number" min="3" max="30" step="1" value="${privateCompanionImageTimeout}" data-private-companion-image-timeout>
                  <small>超时后进入主回复，并阻止同图再次完整识别。</small>
                </label>
              </div>
            </div>
          ` : ""}
          ${isMemeManager ? `
            <div class="integration-feature">
              <div>
                <strong>人格表情包库隔离</strong>
                <span>自动适配 · ${escapeHtml(memeStatus?.message || "等待 Meme Manager 运行时适配。")}</span>
                <small>已映射 ${memeMappedCount} 个人格 · 已包裹 ${memeWrappedCount} 个 Meme Manager 入口。编辑命名图库后，还需要在“人格使用的图库”里把人格映射到该图库。</small>
              </div>
              <label class="check-field compact">
                <input type="checkbox" data-meme-card-copy-descriptions ${state.memeIsolation.copy_default_descriptions !== false ? "checked" : ""}>
                <span>新人格库复制默认分类描述</span>
              </label>
              <div class="group-actions integration-actions">
                <button class="button small" type="button" data-open-meme-library>管理人格图库</button>
                <button class="button small primary" type="button" data-save-meme-card>保存设置</button>
              </div>
            </div>
          ` : ""}
          ${isSmartImage ? `
            <div class="integration-feature">
              <div>
                <strong>智能图片人格图库</strong>
                <span>${state.smartImage.isolation.enabled ? "已启用" : "未启用"} · ${Object.keys(state.smartImage.libraries || {}).length} 个图库 · ${Object.keys(state.smartImage.personaLibraryMap || {}).length} 个人格映射</span>
                <small>${escapeHtml(smartImageStatus?.message || "未启用时完全沿用 Smart ImageChat Hub 原图库。")}</small>
              </div>
              <label class="check-field compact">
                <input type="checkbox" data-smart-image-enabled ${state.smartImage.isolation.enabled ? "checked" : ""}>
                <span>启用按人格隔离 Smart ImageChat Hub 发图候选</span>
              </label>
              <label class="check-field compact">
                <input type="checkbox" data-smart-image-inherit-tags ${state.smartImage.isolation.inherit_auto_tags !== false ? "checked" : ""}>
                <span>缓冲池分发时继承已有标签</span>
              </label>
              <div class="group-actions integration-actions">
                <button class="button small" type="button" data-open-smart-image-library>管理智能图片人格图库</button>
                <button class="button small primary" type="button" data-save-smart-image-card>保存设置</button>
              </div>
            </div>
          ` : ""}
          ${isGiteeAiimg ? `
            <div class="integration-feature">
              <div>
                <strong>人格生图效果</strong>
                <span>${state.giteeAiimgEffects.enabled ? "已启用" : "未启用"} · 已配置 ${configuredGiteeEffects} 个人格</span>
                <small>${escapeHtml(giteeAiimgStatus?.message || "未启用人格生图效果。")} 已包裹 ${giteeWrappedCount} 个入口；只有当前聊天最终命中的人格配置了效果时才会追加。</small>
              </div>
              <label class="check-field compact">
                <input type="checkbox" data-gitee-aiimg-card-enabled ${state.giteeAiimgEffects.enabled ? "checked" : ""}>
                <span>启用按人格追加生图效果</span>
              </label>
              <div class="group-actions integration-actions">
                <button class="button small" type="button" data-open-gitee-aiimg-effects>配置人格效果</button>
                <button class="button small primary" type="button" data-save-gitee-aiimg-card>保存开关</button>
              </div>
            </div>
          ` : ""}
          ${isProactive ? `
            <div class="integration-feature">
              <div>
                <strong>人格主动消息补充要求</strong>
                <span>已配置 ${Object.keys(state.proactivePrompts.prompts || {}).length} 条人格补充要求</span>
                <small>${escapeHtml(proactiveItem?.proactive_persona?.message || "")}</small>
              </div>
              <div class="group-actions integration-actions">
                <button class="button small" type="button" data-open-proactive-prompts>管理人格要求</button>
              </div>
            </div>
          ` : ""}
          ${isLife ? `
            <div class="integration-feature">
              <div>
                <strong>人格日程库</strong>
                <span>${Object.keys(state.lifeLibraries).length} 个日程库 · ${Object.keys(state.lifePersonaMap).length} 个人格映射</span>
                <small>${escapeHtml(lifeItem?.life_schedule_persona?.message || "")}</small>
              </div>
              <div class="group-actions integration-actions">
                <button class="button small" type="button" data-open-life-libraries>管理人格日程库</button>
              </div>
            </div>
          ` : ""}
          ${isLiving ? `
            <div class="integration-feature">
              <div>
                <strong>按规则隔离长期记忆</strong>
                <span>在私聊用户、群聊和成员表单中分别设置，无全局开关。</span>
                <small>${escapeHtml(livingItem?.livingmemory_persona_adapter?.message || "")}</small>
              </div>
            </div>
          ` : ""}
        </article>`;
    }).join("");
  }

  function importPreviewCounts(preview) {
    const privateUsers = preview?.private_users || [];
    const groups = preview?.groups || [];
    return {
      importablePrivate: privateUsers.length,
      importableGroups: groups.length,
      importableTotal: privateUsers.length + groups.length,
      discoveredPrivate: Number(preview?.discovered_private_users || 0),
      discoveredGroups: Number(preview?.discovered_groups || 0),
      existingPrivate: Number(preview?.existing_private_users || 0),
      existingGroups: Number(preview?.existing_groups || 0),
    };
  }

  function diagnosticStatusLabel(status) {
    return {
      found: "发现",
      empty: "空",
      error: "错误",
      skipped: "跳过",
      missing: "缺失",
    }[status] || status || "未知";
  }

  function renderSessionImportPreview(preview) {
    const counts = importPreviewCounts(preview);
    let summary = "";
    if (counts.discoveredPrivate + counts.discoveredGroups === 0) {
      summary = "未从 AstrBot 会话管理器扫描到会话。请查看下方扫描来源。";
    } else if (counts.importableTotal === 0) {
      summary = `发现 ${counts.discoveredPrivate} 个私聊、${counts.discoveredGroups} 个群聊，但都已在策略中，无需重复导入。`;
    } else {
      summary = `发现 ${counts.discoveredPrivate} 个私聊、${counts.discoveredGroups} 个群聊；可新增 ${counts.importablePrivate} 个私聊用户、${counts.importableGroups} 个群聊。`;
    }
    $("#sessionImportSummary").textContent = summary;
    $("#sessionImportCounts").innerHTML = `
      <div><strong>${counts.importablePrivate}</strong><span>可导入私聊</span></div>
      <div><strong>${counts.importableGroups}</strong><span>可导入群聊</span></div>
      <div><strong>${counts.existingPrivate}</strong><span>已存在私聊</span></div>
      <div><strong>${counts.existingGroups}</strong><span>已存在群聊</span></div>
    `;
    const candidates = [
      ...(preview?.private_users || []).map((item) => ({
        type: "私聊",
        id: item.user_id,
        label: item.label,
      })),
      ...(preview?.groups || []).map((item) => ({
        type: "群聊",
        id: item.group_id,
        label: item.label,
      })),
    ];
    $("#sessionImportCandidates").innerHTML = candidates.map((item) => `
      <div class="import-candidate">
        <span>${escapeHtml(item.type)}</span>
        <strong class="mono">${escapeHtml(item.id)}</strong>
        <small>${escapeHtml(item.label || item.id)}</small>
      </div>
    `).join("") || '<div class="empty compact"><strong>没有新增会话</strong><span>扫描到的会话已经存在，或已被识别为内部临时会话。</span></div>';
    $("#sessionImportDiagnostics").innerHTML = (preview?.diagnostics || []).map((item) => `
      <div class="diagnostic compact ${escapeHtml(item.status || "")}">
        <strong>${escapeHtml(item.source || "未知来源")} · ${escapeHtml(diagnosticStatusLabel(item.status))}</strong>
        <span>${escapeHtml(item.message || "")}${Number(item.count || 0) ? `（${Number(item.count)} 个）` : ""}</span>
      </div>
    `).join("") || '<div class="diagnostic compact"><strong>没有扫描诊断</strong><span>AstrBot 当前运行时没有返回会话来源信息。</span></div>';
    const confirmButton = $("#confirmImportSessionsButton");
    confirmButton.disabled = counts.importableTotal === 0;
    confirmButton.textContent = counts.importableTotal
      ? `确认导入 ${counts.importableTotal} 项`
      : "无需导入";
  }

  async function openSessionImportPreview(button) {
    const originalText = button?.textContent || "";
    if (button) {
      button.disabled = true;
      button.textContent = "正在扫描...";
    }
    try {
      const preview = await apiGet("sessions/import-preview");
      state.sessionImportPreview = preview;
      renderSessionImportPreview(preview);
      openModal("sessionImportModal");
    } catch (error) {
      toast(error.message || String(error), "error");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = originalText;
      }
    }
  }

  async function confirmSessionImport() {
    const counts = importPreviewCounts(state.sessionImportPreview);
    if (!counts.importableTotal) {
      toast("没有新的可导入会话。", "error");
      return;
    }
    const button = $("#confirmImportSessionsButton");
    button.disabled = true;
    button.textContent = "正在导入...";
    try {
      const result = await apiPost("sessions/import", {
        revision: state.revision,
      });
      closeModal("sessionImportModal");
      await loadData();
      toast(result.message || "已有会话已导入。");
    } catch (error) {
      if (error.code === 409) await loadData();
      toast(error.message || String(error), "error");
    } finally {
      button.disabled = false;
      renderSessionImportPreview(state.sessionImportPreview || {});
    }
  }

  function applyMemeLibrary(data) {
    if (data.targets) state.memeLibrary.targets = data.targets;
    if (data.library) state.memeLibrary.library = data.library;
    if (data.library?.readonly) state.memeLibrary.defaultLibrary = data.library;
    if (data.libraries) state.memeLibrary.libraries = data.libraries;
    if (data.persona_library_map) state.memeLibrary.personaLibraryMap = data.persona_library_map;
    if (data.personas) state.memeLibrary.personas = data.personas;
    if (typeof data.revision === "number") state.memeLibrary.revision = data.revision;
    pruneMemeSelection();
    renderMemeLibrary();
    renderMemePersonaMap();
  }

  function memeLibraryStats(library) {
    const categories = library?.categories || [];
    const imageCount = categories.reduce(
      (total, category) => total + (category.images?.length || 0),
      0
    );
    return { categoryCount: categories.length, imageCount };
  }

  function memeSelectionKey(category, filename) {
    return `${category}\n${filename}`;
  }

  function allMemeKeys() {
    const categories = state.memeLibrary.library?.categories || [];
    const keys = [];
    for (const category of categories) {
      for (const image of category.images || []) {
        keys.push(memeSelectionKey(category.name, image.filename));
      }
    }
    return keys;
  }

  function pruneMemeSelection() {
    const valid = new Set(allMemeKeys());
    for (const key of [...state.memeLibrary.selectedImages]) {
      if (!valid.has(key)) state.memeLibrary.selectedImages.delete(key);
    }
    for (const key of [...state.memeLibrary.previewCache.keys()]) {
      if (!valid.has(key)) state.memeLibrary.previewCache.delete(key);
    }
  }

  function renderMemeLibraryTargets() {
    const select = $("#memeLibraryTarget");
    const targets = state.memeLibrary.targets || [];
    select.innerHTML = targets.map((target) => `
      <option value="${escapeHtml(target.target_id)}">${escapeHtml(target.label)}</option>
    `).join("");
    const current = state.memeLibrary.library?.target?.target_id || "default";
    if (targets.some((target) => target.target_id === current)) {
      select.value = current;
    }
  }

  function renderMemeLibrary() {
    const library = state.memeLibrary.library;
    const readonly = Boolean(library?.readonly);
    const operationBusy = Boolean(state.memeLibrary.operationBusy);
    const mappedCount = Object.keys(state.memeLibrary.personaLibraryMap || {}).length;
    renderMemeLibraryTargets();
    renderMemeLibraryControls(readonly);
    renderMemeDefaultPreview();
    $("#memeLibraryStatus").textContent = library?.target?.label || "尚未读取";
    $("#memeLibraryPath").textContent = [
      library?.root_dir || "",
      `已映射 ${mappedCount} 个人格；QQ 指令会按当前消息最终人格临时切库，未映射或未命中人格时显示 Meme Manager 默认图库。`,
    ].filter(Boolean).join("\n");
    const categories = library?.categories || [];
    const gallery = $("#memeGallery");
    if (!categories.length) {
      gallery.innerHTML = readonly
        ? `<div class="empty"><strong>默认图库为空</strong><span>${escapeHtml(library?.message || "未检测到 Meme Manager 表情包。")}</span></div>`
        : '<div class="empty"><strong>还没有分类</strong><span>先在上方新增一个分类，再拖入或点击上传表情。</span></div>';
      renderMemeBatchBar();
      return;
    }
    const selected = state.memeLibrary.selectedImages;
    const cache = state.memeLibrary.previewCache;
    gallery.innerHTML = categories.map((category) => {
      const name = escapeHtml(category.name);
      const images = category.images || [];
      const collapsed = state.memeLibrary.collapsedCategories.has(category.name);
      const imageCards = collapsed ? "" : images.map((image) => {
        const key = memeSelectionKey(category.name, image.filename);
        const isSelected = selected.has(key);
        const cached = cache.get(key);
        const imgAttrs = cached
          ? `src="${escapeHtml(cached)}"`
          : `data-meme-preview-cat="${escapeHtml(category.name)}" data-meme-preview-file="${escapeHtml(image.filename)}" data-meme-preview-key="${escapeHtml(key)}"`;
        const removeBtn = readonly
          ? ""
          : `<button class="meme-image-remove" type="button" data-meme-del-image="${escapeHtml(image.filename)}" data-meme-category="${name}" aria-label="删除图片">×</button>`;
        return `
        <div class="meme-image-card ${isSelected ? "is-selected" : ""}" title="${escapeHtml(image.filename)}" data-meme-select="${escapeHtml(key)}">
          ${removeBtn}
          <img loading="lazy" alt="${escapeHtml(image.filename)}" ${imgAttrs}>
          <span>${escapeHtml(image.filename)}</span>
          <small>${Math.ceil((image.size || 0) / 1024)} KB</small>
        </div>`;
      }).join("");
      const headActions = readonly
        ? ""
        : `<div class="meme-category-head-actions">
              <button class="button small" type="button" data-meme-select-cat="${name}">全选本类</button>
              <button class="button small" type="button" data-meme-clear-cat="${name}">清空本类</button>
              <button class="button small danger ghost meme-category-delete" type="button" data-meme-del-category="${name}">删除分类</button>
            </div>`;
      const nameField = readonly
        ? `<span class="meme-category-title-text">${name}</span>`
        : `<input class="meme-category-name" maxlength="80" value="${name}" data-meme-rename="${name}" aria-label="分类名称">`;
      const dropzone = readonly
        ? ""
        : `<button class="meme-dropzone" type="button" data-meme-dropzone="${name}">
              <span class="meme-dropzone-plus">＋</span>
              <span>拖入或点击上传图片 / ZIP</span>
              <small>${images.length} 张 · 单图≤10MB · ZIP≤50MB</small>
            </button>`;
      const descField = readonly
        ? `<textarea class="meme-category-desc" rows="2" readonly>${escapeHtml(category.description || "")}</textarea>`
        : `<textarea class="meme-category-desc" rows="2" maxlength="500" placeholder="Meme Manager 用于描述这一类表情的文本" data-meme-desc="${name}">${escapeHtml(category.description || "")}</textarea>`;
      return `
        <section class="meme-category-card ${collapsed ? "is-collapsed" : ""}" data-meme-card="${name}">
          <header class="meme-category-card-head">
            <button class="meme-category-toggle" type="button" data-meme-toggle-category="${name}" aria-expanded="${collapsed ? "false" : "true"}" aria-label="${collapsed ? "展开" : "折叠"}分类 ${name}">
              <span class="meme-category-arrow" aria-hidden="true">▶</span>
            </button>
            <div class="meme-category-title" data-meme-toggle-category="${name}">
              ${nameField}
              <small>${images.length} 张${collapsed ? " · 已折叠" : ""}</small>
            </div>
            ${collapsed ? "" : headActions}
          </header>
          <div class="meme-category-body">
            ${descField}
            <div class="meme-image-grid">
              ${imageCards}
              ${dropzone}
            </div>
          </div>
        </section>`;
    }).join("");
    renderMemeBatchBar();
    if (!operationBusy) lazyLoadMemePreviews();
  }

  function renderMemeLibraryControls(readonly) {
    $("#memeReadonlyNotice").classList.toggle("hidden", !readonly);
    const addCategory = document.querySelector(".meme-add-category");
    if (addCategory) addCategory.classList.toggle("hidden", readonly);
    const targetId = state.memeLibrary.library?.target?.target_id || "";
    const isNamed = !readonly && targetId && targetId !== "__default__";
    const rename = $("#memeLibRenameButton");
    const del = $("#memeLibDeleteButton");
    if (rename) rename.classList.toggle("hidden", !isNamed);
    if (del) del.classList.toggle("hidden", !isNamed);
    // 复制按钮对默认(MM 只读)库也可用——复制成一个新命名库。
  }

  function renderMemeDefaultPreview() {
    const panel = $("#memeDefaultPreview");
    if (!panel) return;
    const current = state.memeLibrary.library;
    const defaultLibrary = current?.readonly
      ? current
      : state.memeLibrary.defaultLibrary;
    if (!defaultLibrary) {
      panel.innerHTML = `
        <div>
          <strong>默认图库</strong>
          <span>尚未读取 Meme Manager 默认图库。</span>
        </div>
        <button class="button small" type="button" data-open-default-meme-library>读取默认图库</button>`;
      return;
    }
    const stats = memeLibraryStats(defaultLibrary);
    const active = current?.target?.target_id === defaultLibrary.target?.target_id;
    const message = stats.categoryCount
      ? `${stats.categoryCount} 个分类 · ${stats.imageCount} 张图片`
      : (defaultLibrary.message || "默认图库为空。");
    panel.innerHTML = `
      <div>
        <strong>默认图库（Meme Manager）</strong>
        <span>${escapeHtml(message)}</span>
        <small class="mono">${escapeHtml(defaultLibrary.memes_dir || defaultLibrary.root_dir || "")}</small>
      </div>
      <div class="row-actions">
        <button class="button small ${active ? "primary" : ""}" type="button" data-open-default-meme-library>${active ? "正在查看默认图库" : "查看默认图库"}</button>
        <button class="button small" type="button" data-copy-default-meme-library>复制默认图库</button>
      </div>`;
  }

  function renderMemePersonaMap() {
    const list = $("#memePersonaMapList");
    if (!list) return;
    const personas = state.memeLibrary.personas || [];
    const libraries = state.memeLibrary.libraries || {};
    const map = state.memeLibrary.personaLibraryMap || {};
    if (!personas.length) {
      list.innerHTML = '<div class="empty compact"><strong>没有可配置的人格</strong><span>先在 AstrBot 人格设定中添加人格。</span></div>';
      return;
    }
    const libOptions = Object.entries(libraries)
      .map(([id, meta]) => ({ id, name: String(meta?.name || id) }))
      .sort((a, b) => a.name.localeCompare(b.name));
    list.innerHTML = personas.map((persona) => {
      const pid = persona.persona_id;
      const current = map[pid] || "__default__";
      const options = [
        `<option value="__default__" ${current === "__default__" ? "selected" : ""}>默认图库（Meme Manager 只读）</option>`,
        ...libOptions.map((lib) => `<option value="${escapeHtml(lib.id)}" ${current === lib.id ? "selected" : ""}>${escapeHtml(lib.name)}</option>`),
      ].join("");
      return `
        <label class="persona-map-item">
          <span>${escapeHtml(persona.name || pid)}</span>
          <select data-meme-persona-map="${escapeHtml(pid)}">${options}</select>
          <small>只有当前聊天最终命中该人格时，此映射才会影响 QQ 里的表情发送和图库指令。</small>
        </label>`;
    }).join("");
  }

  function lazyLoadMemePreviews() {
    const pending = [...document.querySelectorAll("#memeGallery img[data-meme-preview-file]")];
    if (!pending.length) return;
    const runId = ++state.memeLibrary.previewRunId;
    const cache = state.memeLibrary.previewCache;
    let index = 0;
    const CONCURRENCY = 6;
    async function worker() {
      while (index < pending.length) {
        if (state.memeLibrary.operationBusy || runId !== state.memeLibrary.previewRunId) return;
        const img = pending[index++];
        const cat = img.dataset.memePreviewCat;
        const file = img.dataset.memePreviewFile;
        const key = img.dataset.memePreviewKey;
        delete img.dataset.memePreviewFile;
        try {
          const result = await apiPost("meme-library/images/preview", {
            target_id: memeTargetId(),
            category: cat,
            filename: file,
          });
          if (result?.preview) {
            if (state.memeLibrary.operationBusy || runId !== state.memeLibrary.previewRunId) return;
            cache.set(key, result.preview);
            img.src = result.preview;
          }
        } catch (error) {
          img.classList.add("meme-image-failed");
        }
      }
    }
    for (let i = 0; i < Math.min(CONCURRENCY, pending.length); i += 1) {
      worker();
    }
  }

  function renderMemeBatchBar() {
    const bar = $("#memeBatchBar");
    const count = state.memeLibrary.selectedImages.size;
    bar.classList.toggle("hidden", count === 0);
    $("#memeBatchCount").textContent = `已选 ${count} 张`;
    if (!count) return;
    const busy = Boolean(state.memeLibrary.operationBusy);
    const readonly = Boolean(state.memeLibrary.library?.readonly);
    // 只读默认库:隐藏移动/删除,仅保留「复制到命名库」。
    $("#memeBatchMove").classList.toggle("hidden", readonly);
    $("#memeBatchMoveTarget").classList.toggle("hidden", readonly);
    $("#memeBatchDelete").classList.toggle("hidden", readonly);
    ["memeBatchMove", "memeBatchCopy", "memeBatchDelete", "memeBatchDownload"].forEach((id) => {
      const button = $(`#${id}`);
      if (button && !button.dataset.idleText) button.disabled = busy;
    });
    const categories = state.memeLibrary.library?.categories || [];
    $("#memeBatchMoveTarget").innerHTML = categories.length
      ? categories.map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`).join("")
      : '<option value="">没有可用分类</option>';
    const currentTarget = state.memeLibrary.library?.target?.target_id || "__default__";
    // 复制目标只能是可写的命名库。
    const copyTargets = (state.memeLibrary.targets || []).filter(
      (item) => item.target_id !== currentTarget && !item.readonly
    );
    $("#memeBatchCopyTarget").innerHTML = copyTargets.length
      ? copyTargets.map((item) => `<option value="${escapeHtml(item.target_id)}">${escapeHtml(item.label)}</option>`).join("")
      : '<option value="">没有可写的命名图库</option>';
  }

  async function openMemeLibraryManager() {
    try {
      const data = await apiGet("meme-library/bootstrap");
      state.memeLibrary.collapsedCategories.clear();
      applyMemeLibrary(data);
      openModal("memeLibraryModal");
    } catch (error) {
      toast(error.message || String(error), "error");
    }
  }

  async function loadMemeLibrary(targetId) {
    try {
      const data = await apiPost("meme-library/load", { target_id: targetId });
      state.memeLibrary.collapsedCategories.clear();
      applyMemeLibrary(data);
    } catch (error) {
      toast(error.message || String(error), "error");
    }
  }

  function currentMemeTargetId() {
    return state.memeLibrary.library?.target?.target_id || "__default__";
  }

  async function createMemeLibrary() {
    const input = $("#memeLibCreateName");
    const name = input.value.trim();
    if (!name) {
      toast("请填写图库名称。", "error");
      return;
    }
    try {
      const result = await apiPost("meme-library/library/create", {
        revision: state.memeLibrary.revision,
        name,
      });
      input.value = "";
      state.memeLibrary.revision = result.revision;
      state.memeLibrary.libraries = result.libraries || state.memeLibrary.libraries;
      state.memeLibrary.targets = result.targets || state.memeLibrary.targets;
      toast(result.message || "图库已创建。");
      await loadMemeLibrary(result.lib_id);
    } catch (error) {
      toast(error.message || String(error), "error");
    }
  }

  async function renameMemeLibrary() {
    const libId = currentMemeTargetId();
    if (!libId || libId === "__default__") {
      toast("默认图库不能重命名。", "error");
      return;
    }
    const currentName = state.memeLibrary.libraries?.[libId]?.name || "";
    const name = (window.prompt("输入新的图库名称：", currentName) || "").trim();
    if (!name || name === currentName) return;
    try {
      const result = await apiPost("meme-library/library/rename", {
        revision: state.memeLibrary.revision,
        lib_id: libId,
        name,
      });
      state.memeLibrary.revision = result.revision;
      state.memeLibrary.libraries = result.libraries || state.memeLibrary.libraries;
      state.memeLibrary.targets = result.targets || state.memeLibrary.targets;
      toast(result.message || "图库已重命名。");
      await loadMemeLibrary(libId);
    } catch (error) {
      toast(error.message || String(error), "error");
    }
  }

  async function copyMemeLibrary(sourceTargetId = currentMemeTargetId(), button = null) {
    if (state.memeLibrary.operationBusy) return;
    const sourceId = sourceTargetId || "__default__";
    const sourceName = sourceId === "__default__"
      ? "默认图库"
      : (state.memeLibrary.libraries?.[sourceId]?.name || sourceId);
    // 自动命名：原名(1)，重复则 (2)、(3)…，去重。
    const existingNames = new Set(
      Object.values(state.memeLibrary.libraries || {}).map((lib) => lib.name)
    );
    const baseName = sourceName.replace(/\(\d+\)$/, "");
    let name = "";
    for (let i = 1; i < 1000; i += 1) {
      const candidate = `${baseName}(${i})`;
      if (!existingNames.has(candidate)) {
        name = candidate;
        break;
      }
    }
    if (!name) {
      toast("无法生成不重复的图库名称。", "error");
      return;
    }
    state.memeLibrary.operationBusy = true;
    state.memeLibrary.previewRunId += 1;
    setButtonBusy(button || $("#memeLibCopyButton"), true, "正在复制...");
    $$("[data-copy-default-meme-library]").forEach((item) =>
      setButtonBusy(item, true, "正在复制...")
    );
    try {
      const result = await apiPost("meme-library/library/copy", {
        revision: state.memeLibrary.revision,
        source_target_id: sourceId,
        name,
      });
      state.memeLibrary.revision = result.revision;
      state.memeLibrary.libraries = result.libraries || state.memeLibrary.libraries;
      state.memeLibrary.targets = result.targets || state.memeLibrary.targets;
      toast(result.message || "图库已复制。");
      state.memeLibrary.operationBusy = false;
      await loadMemeLibrary(result.lib_id);
    } catch (error) {
      toast(error.message || String(error), "error");
    } finally {
      state.memeLibrary.operationBusy = false;
      setButtonBusy(button || $("#memeLibCopyButton"), false);
      $$("[data-copy-default-meme-library]").forEach((item) =>
        setButtonBusy(item, false)
      );
    }
  }

  function deleteMemeLibrary() {
    const libId = currentMemeTargetId();
    if (!libId || libId === "__default__") {
      toast("默认图库不能删除。", "error");
      return;
    }
    const name = state.memeLibrary.libraries?.[libId]?.name || libId;
    requestDelete({
      title: "删除命名图库",
      message: `确定删除图库「${name}」及其中全部表情吗？映射到它的人格会自动回退默认图库。`,
      successMessage: "图库已删除。",
      action: async () => {
        try {
          const result = await apiPost("meme-library/library/delete", {
            revision: state.memeLibrary.revision,
            lib_id: libId,
          });
          state.memeLibrary.revision = result.revision;
          state.memeLibrary.libraries = result.libraries || {};
          state.memeLibrary.targets = result.targets || state.memeLibrary.targets;
          state.memeLibrary.personaLibraryMap = result.persona_library_map || {};
          toast(result.message || "图库已删除。");
          await loadMemeLibrary("__default__");
          renderMemePersonaMap();
          return true;
        } catch (error) {
          toast(error.message || String(error), "error");
          return false;
        }
      },
    });
  }

  async function saveMemePersonaMap(personaId, libId) {
    try {
      const result = await apiPost("meme-library/persona-map/save", {
        revision: state.memeLibrary.revision,
        persona_id: personaId,
        lib_id: libId,
      });
      state.memeLibrary.revision = result.revision;
      state.memeLibrary.personaLibraryMap = result.persona_library_map || {};
      renderIntegrations();
      toast(result.message || "已保存。");
    } catch (error) {
      toast(error.message || String(error), "error");
      renderMemePersonaMap();
    }
  }

  async function updateMemeLibrary(endpoint, body, successMessage) {
    try {
      const result = await apiPost(endpoint, body);
      applyMemeLibrary({ library: result.library });
      toast(result.message || successMessage);
      return true;
    } catch (error) {
      toast(error.message || String(error), "error");
      return false;
    }
  }

  function memeTargetId() {
    return $("#memeLibraryTarget").value || "default";
  }

  async function uploadMemeFiles(category, fileList) {
    const files = [...(fileList || [])];
    if (!category || !files.length) return;
    try {
      const encoded = [];
      for (const file of files) {
        encoded.push({ name: file.name, data: await readFileAsDataUrl(file) });
      }
      await updateMemeLibrary("meme-library/images/upload", {
        target_id: memeTargetId(),
        category,
        files: encoded,
      }, "文件已上传。");
    } catch (error) {
      toast(error.message || String(error), "error");
    }
  }

  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error || new Error("读取文件失败。"));
      reader.readAsDataURL(file);
    });
  }

  function memeSelectionByCategory() {
    const groups = new Map();
    for (const key of state.memeLibrary.selectedImages) {
      const index = key.indexOf("\n");
      const category = key.slice(0, index);
      const filename = key.slice(index + 1);
      if (!groups.has(category)) groups.set(category, []);
      groups.get(category).push(filename);
    }
    return groups;
  }

  async function runMemeBatch(groups, perGroup) {
    const errors = [];
    state.memeLibrary.operationBusy = true;
    state.memeLibrary.previewRunId += 1;
    renderMemeBatchBar();
    try {
      for (const [category, filenames] of groups) {
        try {
          await perGroup(category, filenames);
        } catch (error) {
          errors.push(error.message || String(error));
        }
      }
      state.memeLibrary.selectedImages.clear();
      state.memeLibrary.operationBusy = false;
      await loadMemeLibrary(memeTargetId());
    } finally {
      state.memeLibrary.operationBusy = false;
    }
    return errors;
  }

  async function memeBatchDelete() {
    const groups = memeSelectionByCategory();
    if (!groups.size) return;
    const total = state.memeLibrary.selectedImages.size;
    requestDelete({
      title: "批量删除表情图片",
      message: `确定删除选中的 ${total} 张图片吗？`,
      successMessage: "图片已删除。",
      action: async () => {
        const errors = await runMemeBatch(groups, (category, filenames) =>
          apiPost("meme-library/images/delete", {
            target_id: memeTargetId(),
            category,
            filenames,
          })
        );
        toast(errors.length ? `部分删除失败：${errors[0]}` : `已删除 ${total} 张图片。`, errors.length ? "error" : "success");
        return !errors.length;
      },
    });
  }

  async function memeBatchMove() {
    if (state.memeLibrary.operationBusy) return;
    const target = $("#memeBatchMoveTarget").value;
    if (!target) {
      toast("请选择目标分类。", "error");
      return;
    }
    const groups = memeSelectionByCategory();
    let moved = 0;
    let skipped = 0;
    const filtered = new Map();
    for (const [category, filenames] of groups) {
      if (category === target) {
        skipped += filenames.length;
        continue;
      }
      filtered.set(category, filenames);
      moved += filenames.length;
    }
    if (!filtered.size) {
      toast("选中的图片已在目标分类内。", "error");
      return;
    }
    const button = $("#memeBatchMove");
    setButtonBusy(button, true, "正在移动...");
    try {
      const errors = await runMemeBatch(filtered, (category, filenames) =>
        apiPost("meme-library/images/move", {
          target_id: memeTargetId(),
          source_category: category,
          target_category: target,
          filenames,
          include_library: false,
        })
      );
      const suffix = skipped ? `，跳过 ${skipped} 张同分类图片` : "";
      toast(errors.length ? `部分移动失败：${errors[0]}` : `已移动 ${moved} 张图片${suffix}。`, errors.length ? "error" : "success");
    } finally {
      setButtonBusy(button, false);
    }
  }

  async function memeBatchCopy() {
    if (state.memeLibrary.operationBusy) return;
    const dest = $("#memeBatchCopyTarget").value;
    if (!dest) {
      toast("请选择目标人格库。", "error");
      return;
    }
    const groups = memeSelectionByCategory();
    const total = state.memeLibrary.selectedImages.size;
    const button = $("#memeBatchCopy");
    setButtonBusy(button, true, "正在复制...");
    try {
      const errors = await runMemeBatch(groups, (category, filenames) =>
        apiPost("meme-library/images/copy", {
          target_id: memeTargetId(),
          dest_target_id: dest,
          category,
          filenames,
          include_library: false,
        })
      );
      toast(errors.length ? `部分复制失败：${errors[0]}` : `已复制 ${total} 张图片到目标人格库。`, errors.length ? "error" : "success");
    } finally {
      setButtonBusy(button, false);
    }
  }

  async function memeBatchDownload() {
    const keys = [...state.memeLibrary.selectedImages];
    if (!keys.length) {
      toast("没有可下载的图片。", "error");
      return;
    }
    const cache = state.memeLibrary.previewCache;
    toast(`开始下载 ${keys.length} 张图片。`);
    for (let i = 0; i < keys.length; i += 1) {
      const key = keys[i];
      const sep = key.indexOf("\n");
      const category = key.slice(0, sep);
      const filename = key.slice(sep + 1);
      let preview = cache.get(key);
      if (!preview) {
        try {
          const result = await apiPost("meme-library/images/preview", {
            target_id: memeTargetId(),
            category,
            filename,
          });
          preview = result?.preview;
          if (preview) cache.set(key, preview);
        } catch (error) {
          continue;
        }
      }
      if (!preview) continue;
      const link = document.createElement("a");
      link.href = preview;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      await new Promise((resolve) => setTimeout(resolve, 120));
    }
  }

  function applySmartImageData(data) {
    if (typeof data.revision === "number") {
      state.smartImage.revision = data.revision;
      state.revision = data.revision;
    }
    if (data.isolation) state.smartImage.isolation = data.isolation;
    if (data.libraries) state.smartImage.libraries = data.libraries;
    if (Array.isArray(data.global_tags)) {
      state.smartImage.globalTags = data.global_tags;
    }
    if (Array.isArray(data.policy_global_tags)) {
      state.smartImage.policyGlobalTags = data.policy_global_tags;
    }
    if (Array.isArray(data.smart_global_tags)) {
      state.smartImage.smartGlobalTags = data.smart_global_tags;
    }
    if (data.persona_library_map) {
      state.smartImage.personaLibraryMap = data.persona_library_map;
    }
    if (data.targets) state.smartImage.targets = data.targets;
    if (data.personas) state.smartImage.personas = data.personas;
    if (data.status) state.smartImage.status = data.status;
    if (data.library) {
      state.smartImage.library = data.library;
      state.smartImage.currentLibraryId = data.library.library_id || "";
    }
    if (data.pending) state.smartImage.pending = data.pending;
    renderSmartImageManager();
    renderIntegrations();
    $("#runtimeStatus").textContent = `已连接 · 配置修订 #${state.revision}`;
  }

  function currentSmartImageLibraryId() {
    const current = state.smartImage.currentLibraryId;
    if (
      current
      && state.smartImage.targets?.some((item) => item.library_id === current)
    ) {
      return current;
    }
    return state.smartImage.targets?.[0]?.library_id || "";
  }

  function currentSmartImageTarget() {
    const currentId = currentSmartImageLibraryId();
    return (state.smartImage.targets || []).find(
      (item) => item.library_id === currentId
    ) || null;
  }

  function smartImageSelectionKey(item) {
    return `${item.image_id || ""}::${item.hash || ""}`;
  }

  function selectedSmartImages() {
    const selected = state.smartImage.selectedImages;
    return (state.smartImage.library?.images || []).filter(
      (item) => selected.has(smartImageSelectionKey(item))
    );
  }

  function renderSmartImageBatchBar() {
    const selected = selectedSmartImages();
    const readonly = Boolean(state.smartImage.library?.readonly);
    const currentId = currentSmartImageLibraryId();
    const targets = (state.smartImage.targets || []).filter(
      (item) => !item.readonly && item.library_id !== currentId
    );
    const targetIds = new Set(targets.map((item) => item.library_id));
    for (const targetId of [...state.smartImage.selectedImageTargets]) {
      if (!targetIds.has(targetId)) {
        state.smartImage.selectedImageTargets.delete(targetId);
      }
    }
    const visible = selected.length > 0;
    $("#smartImageBatchBar").classList.toggle("hidden", !visible);
    $("#smartImageBatchTargetList").classList.toggle("hidden", !visible);
    $("#smartImageSelectedCount").textContent = `已选 ${selected.length} 张`;
    $("#smartImageBatchMoveButton").classList.toggle("hidden", readonly);
    $("#smartImageBatchDeleteButton").classList.toggle("hidden", readonly);
    $("#smartImageBatchTargetList").innerHTML = targets.map((item) => `
      <label class="smart-target-choice">
        <input type="checkbox" data-smart-image-batch-target value="${escapeHtml(item.library_id)}"
          ${state.smartImage.selectedImageTargets.has(item.library_id) ? "checked" : ""}>
        <span>${escapeHtml(item.name)}</span>
      </label>
    `).join("") || (
      visible
        ? '<span class="muted">没有其他可写的人格图库。</span>'
        : ""
    );
  }

  function renderSmartImageManager() {
    const smart = state.smartImage;
    const currentId = currentSmartImageLibraryId();
    const currentTarget = currentSmartImageTarget();
    const readonly = Boolean(
      currentTarget?.readonly
      || (
        smart.library?.library_id === currentId
        && smart.library?.readonly
      )
    );
    $("#smartImageLibrarySelect").innerHTML = smart.targets.length
      ? smart.targets.map((item) => `
        <option value="${escapeHtml(item.library_id)}" ${item.library_id === currentId ? "selected" : ""}>
          ${escapeHtml(item.name)} · ${Number(item.image_count || 0)} 张
        </option>`).join("")
      : '<option value="">尚未创建人格图库</option>';
    $("#smartImageLibraryStatus").textContent = smart.library?.name || "尚未选择图库";
    $("#smartImageRuntimeStatus").textContent = smart.status?.message || "等待运行时适配";
    $("#smartImageCopyButton").disabled = !currentId;
    ["smartImageRenameButton", "smartImageDeleteButton", "smartImageUploadButton"]
      .forEach((id) => { $(`#${id}`).disabled = !currentId || readonly; });
    $("#smartImageDeleteButton").classList.toggle("hidden", readonly);
    $("#smartImageLibraryHint").textContent = readonly
      ? "当前显示 Smart ImageChat Hub 只读来源图库；可点击“复制”创建独立人格图库。"
      : "标签仅属于当前人格图库，不会写入 Smart ImageChat Hub 原索引。";
    renderSmartImagePersonaMap();
    renderSmartImageGlobalTags();
    renderSmartPendingTargets();
    renderSmartPendingGrid();
    renderSmartImageGrid();
  }

  function renderSmartImageGlobalTags() {
    const tags = state.smartImage.globalTags || [];
    const policyTags = state.smartImage.policyGlobalTags || [];
    const smartTags = state.smartImage.smartGlobalTags || [];
    const input = $("#smartImageGlobalTagsInput");
    if (input && document.activeElement !== input) {
      input.value = policyTags.join("\n");
    }
    $("#smartImageGlobalTagsPreview").innerHTML = tags.map(
      (tag) => `<span>${escapeHtml(tag)}</span>`
    ).join("") || '<small>暂无公用标签</small>';
    const smartPreview = $("#smartImageSmartGlobalTagsPreview");
    if (smartPreview) {
      smartPreview.innerHTML = smartTags.map(
        (tag) => `<span>${escapeHtml(tag)}</span>`
      ).join("") || '<small>Smart ImageChat Hub 暂无公用标签</small>';
    }
  }

  function renderSmartImagePersonaMap() {
    const libraries = (state.smartImage.targets || []).filter(
      (library) => !library.readonly
    );
    const mapping = state.smartImage.personaLibraryMap || {};
    $("#smartImagePersonaMapList").innerHTML = state.personas.map((persona) => `
      <label class="persona-map-item">
        <span>${escapeHtml(persona.name || persona.persona_id)}</span>
        <select data-smart-image-persona-map="${escapeHtml(persona.persona_id)}">
          <option value="">沿用 Smart ImageChat Hub 原图库</option>
          ${libraries.map((library) => `
            <option value="${escapeHtml(library.library_id)}" ${mapping[persona.persona_id] === library.library_id ? "selected" : ""}>
              ${escapeHtml(library.name)}
            </option>`).join("")}
        </select>
      </label>
    `).join("") || '<span class="muted">当前没有可映射的人格。</span>';
  }

  function renderSmartPendingTargets() {
    const targets = (state.smartImage.targets || []).filter(
      (item) => !item.readonly
    );
    $("#smartPendingTargetList").innerHTML = targets.map((item) => `
      <label class="smart-target-choice">
        <input type="checkbox" data-smart-pending-target value="${escapeHtml(item.library_id)}">
        <span>${escapeHtml(item.name)}</span>
      </label>
    `).join("") || '<span class="muted">请先创建至少一个人格图库。</span>';
  }

  function renderSmartPendingGrid() {
    const items = state.smartImage.pending?.images || [];
    const selected = state.smartImage.selectedPending;
    $("#smartPendingGrid").innerHTML = items.map((item) => `
      <article class="smart-image-card ${selected.has(item.id) ? "is-selected" : ""}" data-smart-pending-select="${escapeHtml(item.id)}">
        <img alt="${escapeHtml(item.filename || item.id)}" data-smart-pending-preview="${escapeHtml(item.id)}">
        <strong>${escapeHtml(item.filename || item.id)}</strong>
        <small>${escapeHtml((item.tags || []).join("、") || "暂无标签")}</small>
      </article>
    `).join("") || '<div class="empty compact"><strong>缓冲池为空</strong><span>Smart ImageChat Hub 自动偷图后会出现在这里。</span></div>';
    hydrateSmartPendingPreviews();
  }

  function renderSmartImageGrid() {
    const library = state.smartImage.library;
    const images = library?.images || [];
    const readonly = Boolean(library?.readonly);
    const selected = state.smartImage.selectedImages;
    const validKeys = new Set(images.map(smartImageSelectionKey));
    for (const key of [...selected]) {
      if (!validKeys.has(key)) selected.delete(key);
    }
    $("#smartImageGrid").innerHTML = images.map((item) => `
      <article class="smart-image-card ${selected.has(smartImageSelectionKey(item)) ? "is-selected" : ""}"
        data-smart-image-select="${escapeHtml(smartImageSelectionKey(item))}">
        <img alt="${escapeHtml(item.filename)}"
          data-smart-image-preview="${escapeHtml(item.hash)}"
          data-smart-image-id="${escapeHtml(item.image_id || "")}"
          data-smart-image-library="${escapeHtml(library?.library_id || "")}">
        <strong>${escapeHtml(item.filename)}</strong>
        <small class="mono">${escapeHtml(item.hash.slice(0, 12))}</small>
        ${readonly ? `
          <div class="smart-tag-summary">
            <span>${escapeHtml(item.source_label || "Smart 原图库")}</span>
            <span>${escapeHtml(item.caption_status_label || "未标记状态")}</span>
          </div>
        ` : ""}
        <div class="smart-tag-summary">
          ${(item.tags || []).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")
            || '<small>暂无标签</small>'}
        </div>
        <div class="smart-image-actions">
          ${readonly
            ? '<span class="badge neutral">来源图库只读</span>'
            : `
              <button class="button small primary" type="button" data-smart-edit-tags="${escapeHtml(item.hash)}">编辑标签</button>
              <button class="button small" type="button" data-smart-caption-image="${escapeHtml(item.hash)}">智能打标</button>
              <button class="button small danger" type="button" data-smart-delete-image="${escapeHtml(item.hash)}">移除</button>
            `}
        </div>
      </article>
    `).join("") || `<div class="empty compact"><strong>当前图库没有图片</strong><span>${
      readonly
        ? "Smart ImageChat Hub 当前来源没有可读取的本地图片。"
        : "可上传图片/ZIP，或从自动偷图缓冲池分发。"
    }</span></div>`;
    renderSmartImageBatchBar();
    hydrateSmartImagePreviews();
  }

  async function hydrateSmartImagePreviews() {
    for (const image of $$("img[data-smart-image-preview]")) {
      const hash = image.dataset.smartImagePreview;
      const libraryId = image.dataset.smartImageLibrary || "";
      const imageId = image.dataset.smartImageId || "";
      const cacheKey = `${libraryId}:${imageId || hash}`;
      let preview = state.smartImage.previewCache.get(cacheKey);
      if (!preview) {
        try {
          preview = (await apiPost("smart-image/image/preview", {
            hash,
            library_id: libraryId,
            image_id: imageId,
          })).preview;
          if (preview) state.smartImage.previewCache.set(cacheKey, preview);
        } catch (_) {
          continue;
        }
      }
      if (image.isConnected) image.src = preview;
    }
  }

  async function hydrateSmartPendingPreviews() {
    for (const image of $$("img[data-smart-pending-preview]")) {
      const imageId = image.dataset.smartPendingPreview;
      let preview = state.smartImage.pendingPreviewCache.get(imageId);
      if (!preview) {
        try {
          preview = (await apiPost("smart-image/pending/preview", {
            image_id: imageId,
          })).preview;
          if (preview) state.smartImage.pendingPreviewCache.set(imageId, preview);
        } catch (_) {
          continue;
        }
      }
      if (image.isConnected) image.src = preview;
    }
  }

  async function openSmartImageManager() {
    openModal("smartImageLibraryModal");
    $("#smartImageRuntimeStatus").textContent = "正在读取 Smart ImageChat Hub 数据...";
    try {
      const data = await apiGet("smart-image/bootstrap");
      applySmartImageData(data);
      const first = currentSmartImageLibraryId();
      if (first) await loadSmartImageLibrary(first);
      await refreshSmartPending();
    } catch (error) {
      $("#smartImageRuntimeStatus").textContent = "读取失败";
      toast(error.message || String(error), "error");
    }
  }

  async function loadSmartImageLibrary(libraryId) {
    if (!libraryId) {
      state.smartImage.library = null;
      state.smartImage.currentLibraryId = "";
      state.smartImage.selectedImages.clear();
      state.smartImage.selectedImageTargets.clear();
      renderSmartImageManager();
      return;
    }
    try {
      const library = await apiPost("smart-image/library/load", {
        library_id: libraryId,
      });
      state.smartImage.library = library;
      state.smartImage.currentLibraryId = libraryId;
      state.smartImage.selectedImages.clear();
      state.smartImage.selectedImageTargets.clear();
      renderSmartImageManager();
    } catch (error) {
      toast(error.message || String(error), "error");
    }
  }

  async function smartImageMutate(endpoint, body, successMessage) {
    try {
      const result = await apiPost(endpoint, {
        revision: state.smartImage.revision || state.revision,
        ...body,
      });
      applySmartImageData(result);
      toast(result.message || successMessage);
      return result;
    } catch (error) {
      if (error.code === 409) {
        await loadData();
        state.smartImage.revision = state.revision;
        if (!$("#smartImageLibraryModal").classList.contains("hidden")) {
          closeModal("smartImageNameModal");
          closeModal("smartImageTagModal");
          await openSmartImageManager();
        }
      }
      toast(error.message || String(error), "error");
      return null;
    }
  }

  async function refreshSmartPending() {
    try {
      state.smartImage.pending = await apiGet("smart-image/pending/snapshot");
      state.smartImage.selectedPending.clear();
      state.smartImage.pendingPreviewCache.clear();
      renderSmartPendingGrid();
    } catch (error) {
      toast(error.message || String(error), "error");
    }
  }

  async function uploadSmartImageFiles(fileList) {
    const libraryId = currentSmartImageLibraryId();
    if (currentSmartImageTarget()?.readonly) {
      toast("Smart ImageChat Hub 原图库为只读，请先复制或创建人格图库。", "error");
      return;
    }
    const files = [...(fileList || [])];
    if (!libraryId || !files.length) return;
    const encoded = [];
    for (const file of files) {
      encoded.push({ name: file.name, data: await readFileAsDataUrl(file) });
    }
    const result = await smartImageMutate("smart-image/images/upload", {
      library_id: libraryId,
      files: encoded,
    }, "智能图片已上传。");
    if (result?.library) {
      state.smartImage.library = result.library;
      renderSmartImageGrid();
    }
  }

  async function distributeSmartPending(removeFromPending) {
    const imageIds = [...state.smartImage.selectedPending];
    const libraryIds = $$("[data-smart-pending-target]:checked").map((item) => item.value);
    if (!imageIds.length) return toast("请先选择缓冲池图片。", "error");
    if (!libraryIds.length) return toast("请至少选择一个目标人格图库。", "error");
    const result = await smartImageMutate("smart-image/pending/distribute", {
      image_ids: imageIds,
      library_ids: libraryIds,
      inherit_auto_tags: $("#smartPendingInheritTags").checked,
      remove_from_pending: Boolean(removeFromPending),
    }, removeFromPending ? "缓冲图片已移动。" : "缓冲图片已复制。");
    if (result) {
      state.smartImage.selectedPending.clear();
      if (result.pending) state.smartImage.pending = result.pending;
      await loadSmartImageLibrary(currentSmartImageLibraryId());
      renderSmartPendingGrid();
    }
  }

  async function distributeSelectedSmartImages(move) {
    const images = selectedSmartImages().map((item) => ({
      hash: item.hash,
      image_id: item.image_id || "",
    }));
    const targetIds = $$("[data-smart-image-batch-target]:checked").map(
      (item) => item.value
    );
    if (!images.length) {
      toast("请先选择当前图库图片。", "error");
      return;
    }
    if (!targetIds.length) {
      toast("请至少选择一个目标人格图库。", "error");
      return;
    }
    if (move && state.smartImage.library?.readonly) {
      toast("Smart ImageChat Hub 来源图库为只读，只能复制图片。", "error");
      return;
    }
    const result = await smartImageMutate("smart-image/images/distribute", {
      source_library_id: currentSmartImageLibraryId(),
      target_library_ids: targetIds,
      images,
      move: Boolean(move),
    }, move ? "选中图片已移动。" : "选中图片已复制。");
    if (!result) return;
    state.smartImage.selectedImages.clear();
    state.smartImage.selectedImageTargets.clear();
    if (result.library) state.smartImage.library = result.library;
    renderSmartImageGrid();
  }

  function deleteSelectedSmartImages() {
    if (state.smartImage.library?.readonly) {
      toast("Smart ImageChat Hub 来源图库不能删除图片。", "error");
      return;
    }
    const hashes = selectedSmartImages().map((item) => item.hash);
    if (!hashes.length) {
      toast("请先选择要删除的图片。", "error");
      return;
    }
    requestDelete({
      title: "删除选中图片",
      message: `确定从当前人格图库删除选中的 ${hashes.length} 张图片吗？`,
      successMessage: "选中图片已删除。",
      action: async () => {
        const result = await smartImageMutate("smart-image/images/delete", {
          library_id: currentSmartImageLibraryId(),
          hashes,
        }, "选中图片已删除。");
        if (!result) return false;
        state.smartImage.selectedImages.clear();
        state.smartImage.selectedImageTargets.clear();
        if (result.library) state.smartImage.library = result.library;
        renderSmartImageGrid();
        return true;
      },
    });
  }

  function deleteSmartPending() {
    const imageIds = [...state.smartImage.selectedPending];
    if (!imageIds.length) {
      toast("请先选择要删除的缓冲图片。", "error");
      return;
    }
    requestDelete({
      title: "删除缓冲图片",
      message: `确定从 Smart ImageChat Hub 缓冲池删除选中的 ${imageIds.length} 张图片吗？`,
      successMessage: "缓冲图片已删除。",
      action: async () => {
        const result = await smartImageMutate("smart-image/pending/delete", {
          image_ids: imageIds,
        }, "缓冲图片已删除。");
        if (!result) return false;
        state.smartImage.selectedPending.clear();
        if (result.pending) state.smartImage.pending = result.pending;
        renderSmartPendingGrid();
        return true;
      },
    });
  }

  async function backupSmartImages() {
    try {
      const result = await apiPost("smart-image/backup", { library_ids: [] });
      const link = document.createElement("a");
      link.href = result.data;
      link.download = result.filename || "smart-image-libraries.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast("智能图片人格图库备份已生成。");
    } catch (error) {
      toast(error.message || String(error), "error");
    }
  }

  async function createSmartImageLibrary() {
    const input = $("#smartImageCreateName");
    const name = input.value.trim();
    if (!name) return toast("请填写智能图片图库名称。", "error");
    const result = await smartImageMutate("smart-image/library/create", {
      name,
    }, "智能图片图库已创建。");
    if (!result) return;
    input.value = "";
    await loadSmartImageLibrary(result.library_id);
  }

  function suggestedSmartImageCopyName(sourceName) {
    const existingNames = new Set(
      Object.values(state.smartImage.libraries || {}).map((item) => item.name)
    );
    for (let index = 1; index < 1000; index += 1) {
      const candidate = `${sourceName}(${index})`;
      if (!existingNames.has(candidate)) return candidate;
    }
    return `${sourceName}副本`;
  }

  function openSmartImageNameModal(mode) {
    const libraryId = currentSmartImageLibraryId();
    if (!libraryId) return;
    if (mode !== "copy" && currentSmartImageTarget()?.readonly) {
      toast("Smart ImageChat Hub 原图库不能重命名。", "error");
      return;
    }
    const currentName = state.smartImage.libraries?.[libraryId]?.name
      || state.smartImage.library?.name
      || libraryId;
    const copying = mode === "copy";
    state.smartImage.nameAction = { mode, libraryId };
    $("#smartImageNameTitle").textContent = copying ? "复制人格图库" : "重命名人格图库";
    $("#smartImageNameHint").textContent = copying
      ? "复制会创建独立的图库成员和标签副本，之后修改互不影响。"
      : "重命名只改变显示名称，不影响人格映射和图片。";
    $("#smartImageNameInput").value = copying
      ? suggestedSmartImageCopyName(currentName)
      : currentName;
    openModal("smartImageNameModal");
    setTimeout(() => {
      $("#smartImageNameInput").focus();
      $("#smartImageNameInput").select();
    }, 0);
  }

  async function confirmSmartImageName() {
    const action = state.smartImage.nameAction;
    const name = $("#smartImageNameInput").value.trim();
    if (!action || !name) {
      toast("请填写图库名称。", "error");
      return;
    }
    const copying = action.mode === "copy";
    const endpoint = copying
      ? "smart-image/library/copy"
      : "smart-image/library/rename";
    const body = copying
      ? { source_library_id: action.libraryId, name }
      : { library_id: action.libraryId, name };
    const result = await smartImageMutate(endpoint, body, copying
      ? "智能图片图库已复制。"
      : "智能图片图库已重命名。");
    if (!result) return;
    closeModal("smartImageNameModal");
    await loadSmartImageLibrary(
      copying ? result.library_id : action.libraryId
    );
  }

  async function openSmartImageTagEditor(imageHash) {
    const image = state.smartImage.library?.images?.find(
      (item) => item.hash === imageHash
    );
    if (!image) return;
    state.smartImage.tagEditorHash = imageHash;
    $("#smartImageTagTitle").textContent = image.filename || "图片标签";
    $("#smartImageTagFilename").textContent = image.filename || imageHash;
    $("#smartImageTagHash").textContent = imageHash;
    $("#smartImageTagText").value = (image.tags || []).join("\n");
    const imageTags = new Set(image.tags || []);
    const globalTags = state.smartImage.globalTags || [];
    $("#smartImageCommonTagList").innerHTML = globalTags.map((tag) => `
      <label class="smart-common-tag-choice">
        <input type="checkbox" data-smart-common-tag value="${escapeHtml(tag)}" ${imageTags.has(tag) ? "checked" : ""}>
        <span>${escapeHtml(tag)}</span>
      </label>
    `).join("") || '<span class="muted">尚未配置公用特征标签。</span>';
    const currentId = currentSmartImageLibraryId();
    const matchingTargets = (state.smartImage.targets || []).filter((target) => (
      target.library_id !== currentId
      && Boolean(
        state.smartImage.libraries?.[target.library_id]?.images?.[imageHash]
      )
    ));
    $("#smartImageTagTargetList").innerHTML = matchingTargets.map((target) => `
      <label class="smart-target-choice">
        <input type="checkbox" data-smart-tag-target value="${escapeHtml(target.library_id)}">
        <span>${escapeHtml(target.name)}</span>
      </label>
    `).join("") || '<span class="muted">其他图库中没有这张相同图片。</span>';
    $("#smartImageTagApplyButton").disabled = matchingTargets.length === 0;
    const currentLibraryId = currentSmartImageLibraryId();
    const previewCacheKey = `${currentLibraryId}:${image.image_id || imageHash}`;
    const preview = state.smartImage.previewCache.get(previewCacheKey);
    $("#smartImageTagPreview").removeAttribute("src");
    if (preview) {
      $("#smartImageTagPreview").src = preview;
    }
    openModal("smartImageTagModal");
    if (!preview) {
      try {
        const payload = await apiPost("smart-image/image/preview", {
          hash: imageHash,
          library_id: currentLibraryId,
          image_id: image.image_id || "",
        });
        if (payload.preview) {
          state.smartImage.previewCache.set(
            previewCacheKey,
            payload.preview
          );
          if (state.smartImage.tagEditorHash === imageHash) {
            $("#smartImageTagPreview").src = payload.preview;
          }
        }
      } catch (error) {
        toast(error.message || String(error), "error");
      }
    }
  }

  async function saveSmartImageTags(imageHash, tags) {
    const result = await smartImageMutate("smart-image/images/tags/save", {
      library_id: currentSmartImageLibraryId(),
      hash: imageHash,
      tags,
    }, "图片标签已保存。");
    if (result?.library) {
      state.smartImage.library = result.library;
      renderSmartImageGrid();
    }
    return result;
  }

  async function saveSmartImageGlobalTags() {
    const result = await smartImageMutate("smart-image/global-tags/save", {
      tags: $("#smartImageGlobalTagsInput").value,
    }, "公用特征标签已保存。");
    if (result) renderSmartImageGlobalTags();
  }

  async function saveSmartImageTagEditor(applyToTargets) {
    const imageHash = state.smartImage.tagEditorHash;
    if (!imageHash) return;
    const targetIds = $$("[data-smart-tag-target]:checked").map(
      (item) => item.value
    );
    if (applyToTargets && !targetIds.length) {
      toast("请至少选择一个包含相同图片的目标图库。", "error");
      return;
    }
    const selectedCommonTags = $$("[data-smart-common-tag]:checked").map(
      (item) => item.value
    );
    const tags = [
      ...parseTagText($("#smartImageTagText").value),
      ...selectedCommonTags,
    ];
    const saved = await saveSmartImageTags(imageHash, tags);
    if (!saved) return;
    if (applyToTargets) {
      const applied = await smartImageMutate("smart-image/images/tags/apply", {
        source_library_id: currentSmartImageLibraryId(),
        hash: imageHash,
        target_library_ids: targetIds,
      }, "图片标签已套用。");
      if (!applied) return;
    }
    closeModal("smartImageTagModal");
  }

  async function copySmartImageLibrary() {
    openSmartImageNameModal("copy");
  }

  async function renameSmartImageLibrary() {
    openSmartImageNameModal("rename");
  }

  function deleteSmartImageLibrary() {
    const libraryId = currentSmartImageLibraryId();
    if (!libraryId) return;
    if (currentSmartImageTarget()?.readonly) {
      toast("Smart ImageChat Hub 原图库不能删除。", "error");
      return;
    }
    const name = state.smartImage.libraries?.[libraryId]?.name
      || state.smartImage.library?.name
      || libraryId;
    requestDelete({
      title: "删除智能图片人格图库",
      message: `确定删除图库「${name}」吗？映射到它的人格将回退 Smart ImageChat Hub 原图库。`,
      successMessage: "智能图片图库已删除。",
      action: async () => {
        const result = await smartImageMutate("smart-image/library/delete", {
          library_id: libraryId,
        }, "智能图片图库已删除。");
        if (!result) return false;
        const nextId = state.smartImage.targets?.[0]?.library_id || "";
        await loadSmartImageLibrary(nextId);
        return true;
      },
    });
  }

  async function captionSmartImage(imageHash, button) {
    setButtonBusy(button, true, "打标中...");
    try {
      const result = await smartImageMutate("smart-image/images/caption", {
        library_id: currentSmartImageLibraryId(),
        hash: imageHash,
      }, "Smart 视觉智能标签已生成。");
      if (result?.library) {
        state.smartImage.library = result.library;
        renderSmartImageGrid();
      }
    } finally {
      setButtonBusy(button, false);
    }
  }

  function deleteSmartImage(imageHash) {
    const item = state.smartImage.library?.images?.find(
      (image) => image.hash === imageHash
    );
    requestDelete({
      title: "移除智能图片",
      message: `确定从当前图库移除「${item?.filename || imageHash}」吗？`,
      successMessage: "图片已移除。",
      action: async () => {
        const result = await smartImageMutate("smart-image/images/delete", {
          library_id: currentSmartImageLibraryId(),
          hashes: [imageHash],
        }, "图片已移除。");
        if (!result) return false;
        if (result.library) state.smartImage.library = result.library;
        renderSmartImageGrid();
        return true;
      },
    });
  }

  async function saveSmartImagePersonaMap(personaId, libraryId) {
    await smartImageMutate("smart-image/persona-map/save", {
      persona_id: personaId,
      library_id: libraryId,
    }, "智能图片人格图库映射已保存。");
  }

  function renderGiteeAiimgEffects(status) {
    $("#giteeAiimgEffectsEnabled").checked = Boolean(state.giteeAiimgEffects.enabled);
    const wrappedCount = (status?.wrapped_method_count || 0)
      + (status?.wrapped_handler_count || 0);
    $("#giteeAiimgEffectsStatus").textContent = `${
      status?.message || "未启用人格生图效果。"
    } 已包裹 ${wrappedCount} 个入口；效果只会追加到当前最终人格对应的生图请求。`;
    const effects = state.giteeAiimgEffects.effects || {};
    $("#giteeAiimgEffectsList").innerHTML = state.personas.map((persona) => `
      <label class="field persona-effect-item">
        <span>${escapeHtml(persona.name || persona.persona_id)}</span>
        <textarea rows="2" maxlength="1000" data-gitee-aiimg-effect="${escapeHtml(persona.persona_id)}" placeholder="例如：柔和手绘风、暖色灯光、猫耳少女、表情更活泼">${escapeHtml(effects[persona.persona_id] || "")}</textarea>
        <small class="mono">${escapeHtml(persona.persona_id)}</small>
      </label>
    `).join("") || '<span class="muted">当前没有可配置的 AstrBot 人格。</span>';
  }

  function openProactivePrompts() {
    const prompts = state.proactivePrompts.prompts || {};
    $("#proactivePromptsList").innerHTML = state.personas.map((persona) => `
      <label class="field persona-effect-item">
        <span>${escapeHtml(persona.name || persona.persona_id)}</span>
        <textarea rows="3" maxlength="2000" data-proactive-prompt="${escapeHtml(persona.persona_id)}" placeholder="话题、语气和禁忌等补充要求">${escapeHtml(prompts[persona.persona_id] || "")}</textarea>
        <small class="mono">${escapeHtml(persona.persona_id)}</small>
      </label>
    `).join("") || '<span class="muted">当前没有可配置的 AstrBot 人格。</span>';
    openModal("proactivePromptsModal");
  }

  function lifeLibraryEntries() {
    return Object.entries(state.lifeLibraries || {});
  }

  function currentLifeLibrary() {
    return state.lifeLibraries[state.currentLifeLibraryId] || null;
  }

  function lifePoolUi(libraryId = state.currentLifeLibraryId) {
    const id = String(libraryId || "");
    if (!id) {
      return { expanded: new Set(), selected: {} };
    }
    const existing = state.lifePoolUi[id];
    if (existing) return existing;
    const created = { expanded: new Set(), selected: {} };
    state.lifePoolUi[id] = created;
    return created;
  }

  function resetLifePoolUi(libraryId) {
    if (libraryId) delete state.lifePoolUi[libraryId];
  }

  function renderLifeLibraries() {
    const entries = lifeLibraryEntries();
    if (!state.currentLifeLibraryId || !state.lifeLibraries[state.currentLifeLibraryId]) {
      state.currentLifeLibraryId = entries[0]?.[0] || "";
    }
    $("#lifeLibrarySelect").innerHTML = entries.length
      ? entries.map(([id, library]) => `<option value="${escapeHtml(id)}" ${id === state.currentLifeLibraryId ? "selected" : ""}>${escapeHtml(library.name || id)}</option>`).join("")
      : '<option value="">尚未创建日程库</option>';
    $("#renameLifeLibraryButton").disabled = !state.currentLifeLibraryId;
    $("#deleteLifeLibraryButton").disabled = !state.currentLifeLibraryId;
    $("#addLifeRecordButton").disabled = !state.currentLifeLibraryId;
    $("#lifePersonaMapList").innerHTML = state.personas.map((persona) => `
      <label class="persona-map-item">
        <span>${escapeHtml(persona.name || persona.persona_id)}</span>
        <select data-life-persona-map="${escapeHtml(persona.persona_id)}">
          <option value="">沿用 Life Scheduler 全局日程</option>
          ${entries.map(([id, library]) => `<option value="${escapeHtml(id)}" ${state.lifePersonaMap[persona.persona_id] === id ? "selected" : ""}>${escapeHtml(library.name || id)}</option>`).join("")}
        </select>
      </label>
    `).join("") || '<span class="muted">当前没有可映射的人格。</span>';
    const records = Object.values(currentLifeLibrary()?.records || {})
      .sort((a, b) => String(b.date).localeCompare(String(a.date)));
    $("#lifeRecordList").innerHTML = records.map((record) => `
      <article class="life-record-card">
        <div>
          <strong>${escapeHtml(record.date)}</strong>
          <span>${escapeHtml(record.outfit_style || "未设置穿搭风格")}</span>
        </div>
        <p>${escapeHtml(record.schedule || "未设置日程")}</p>
        <div class="row-actions">
          <button class="button small" type="button" data-edit-life-record="${escapeHtml(record.date)}">编辑</button>
          <button class="button small danger" type="button" data-delete-life-record="${escapeHtml(record.date)}">删除</button>
        </div>
      </article>
    `).join("") || '<div class="empty compact"><strong>当前日程库没有记录</strong></div>';
    renderLifePool();
  }

  const LIFE_POOL_GROUPS = [
    { key: "daily_themes", label: "今日主题池" },
    { key: "mood_colors", label: "心情色彩池" },
    { key: "outfit_styles", label: "穿搭风格池" },
    { key: "schedule_types", label: "日程类型池" },
  ];

  function renderLifePool() {
    const grid = $("#lifePoolGrid");
    if (!grid) return;
    const library = currentLifeLibrary();
    if (!library) {
      grid.innerHTML = '<div class="empty compact"><strong>请选择或创建日程库</strong></div>';
      return;
    }
    const pool = library.pool || {};
    const ui = lifePoolUi();
    const expanded = ui.expanded;
    grid.innerHTML = LIFE_POOL_GROUPS.map((group) => {
      const items = Array.isArray(pool[group.key]) ? pool[group.key] : [];
      const isOpen = expanded.has(group.key);
      let body;
      if (isOpen) {
        const selected = ui.selected[group.key] || new Set();
        const chips = items.map((item) => {
          const isSel = selected.has(item);
          return `
          <label class="life-pool-chip selectable ${isSel ? "is-selected" : ""}">
            <input type="checkbox" data-life-pool-select="${group.key}" value="${escapeHtml(item)}" ${isSel ? "checked" : ""}>
            <span>${escapeHtml(item)}</span>
          </label>`;
        }).join("");
        const selCount = [...selected].filter((v) => items.includes(v)).length;
        body = `
          <div class="life-pool-chips">${chips || '<span class="muted">暂无条目</span>'}</div>
          <div class="input-with-button">
            <input class="life-pool-input" maxlength="100" data-life-pool-add="${group.key}" placeholder="添加${group.label.replace(/池$/, "")}">
            <button class="button small primary" type="button" data-life-pool-add-btn="${group.key}">添加</button>
          </div>
          <div class="life-pool-actions">
            <button class="button small" type="button" data-life-pool-import="${group.key}">批量导入</button>
            <button class="button small" type="button" data-life-pool-select-all="${group.key}">全选</button>
            <button class="button small" type="button" data-life-pool-select-none="${group.key}">取消选择</button>
            <button class="button small danger" type="button" data-life-pool-delete-selected="${group.key}">删除选中${selCount ? `（${selCount}）` : ""}</button>
            <button class="text-button life-pool-collapse" type="button" data-life-pool-collapse="${group.key}">收起</button>
          </div>`;
      } else {
        const first = items.length
          ? `<span class="life-pool-chip readonly">${escapeHtml(items[0])}</span>`
          : '<span class="muted">暂无条目</span>';
        const more = items.length > 1
          ? `<button class="life-pool-more" type="button" data-life-pool-expand="${group.key}">＋${items.length - 1}</button>`
          : "";
        body = `
          <div class="life-pool-chips">${first}${more}</div>
          <button class="text-button life-pool-collapse" type="button" data-life-pool-expand="${group.key}">添加更多</button>`;
      }
      return `
        <div class="life-pool-group">
          <div class="life-pool-head"><strong>${group.label}</strong><span class="muted">${items.length} 项</span></div>
          ${body}
        </div>`;
    }).join("");
  }

  function currentLifePool() {
    const library = currentLifeLibrary();
    const pool = (library && library.pool) || {};
    const result = {};
    for (const group of LIFE_POOL_GROUPS) {
      result[group.key] = Array.isArray(pool[group.key]) ? [...pool[group.key]] : [];
    }
    return result;
  }

  async function saveLifePool(pool, libraryId = state.currentLifeLibraryId) {
    if (!libraryId) return;
    await lifeMutate("life-schedule/pool/save", {
      library_id: libraryId,
      pool,
    }, "创意池已保存。");
  }

  async function confirmLifePoolImport() {
    const key = state.lifePoolImportKey;
    const libraryId = state.lifePoolImportLibraryId || state.currentLifeLibraryId;
    if (!key || !libraryId || libraryId !== state.currentLifeLibraryId) return;
    const incoming = ($("#lifePoolImportText").value || "")
      .split(/[\n,，]/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!incoming.length) {
      toast("没有可导入的条目。", "error");
      return;
    }
    const pool = currentLifePool();
    const existing = new Set(pool[key]);
    let added = 0;
    for (const item of incoming) {
      const value = item.slice(0, 100);
      if (!existing.has(value)) {
        pool[key].push(value);
        existing.add(value);
        added += 1;
      }
    }
    lifePoolUi(libraryId).expanded.add(key);
    closeModal("lifePoolImportModal");
    state.lifePoolImportKey = "";
    state.lifePoolImportLibraryId = "";
    if (!added) {
      toast("没有新增条目（均已存在）。");
      return;
    }
    await saveLifePool(pool);
  }

  function openLifeLibraries() {
    renderLifeLibraries();
    $("#lifeRecordForm").classList.add("hidden");
    openModal("lifeScheduleLibraryModal");
  }

  function openLifeRecord(date = "") {
    const record = currentLifeLibrary()?.records?.[date] || {};
    $("#lifeRecordDate").value = record.date || new Date().toISOString().slice(0, 10);
    $("#lifeRecordStatus").value = record.status || "ok";
    $("#lifeRecordStyle").value = record.outfit_style || "";
    $("#lifeRecordOutfit").value = record.outfit || "";
    $("#lifeRecordSchedule").value = record.schedule || "";
    $("#lifeRecordForm").classList.remove("hidden");
  }

  async function lifeMutate(endpoint, body, message) {
    try {
      const result = await apiPost(endpoint, {
        revision: state.revision,
        ...body,
      });
      state.revision = result.revision || state.revision;
      state.lifeLibraries = result.life_schedule_libraries || {};
      state.lifePersonaMap = result.life_schedule_persona_map || {};
      if (result.library_id) state.currentLifeLibraryId = result.library_id;
      if (!state.lifeLibraries[state.currentLifeLibraryId]) {
        state.currentLifeLibraryId = Object.keys(state.lifeLibraries)[0] || "";
      }
      renderLifeLibraries();
      renderIntegrations();
      toast(result.message || message);
      return true;
    } catch (error) {
      if (error.code === 409) await loadData();
      toast(error.message || String(error), "error");
      return false;
    }
  }

  function renderAll() {
    renderUsers();
    renderGroups();
    renderSchedules();
    renderIntegrations();
    $("#runtimeStatus").textContent = `已连接 · 配置修订 #${state.revision}`;
    $("#runtimeStatus").classList.remove("error");
  }

  async function loadData(showToast = false) {
    try {
      const data = await apiGet("bootstrap");
      state.revision = data.revision || 0;
      state.personas = data.personas || [];
      state.providers = data.providers || [];
      state.plugins = data.plugins || [];
      state.users = data.private_users || [];
      state.groups = data.groups || [];
      state.schedules = data.schedules || [];
      state.memeIsolation = data.meme_manager_isolation || {
        enabled: false,
        copy_default_descriptions: true,
      };
      state.memeLibrary.libraries = data.meme_libraries || {};
      state.memeLibrary.personaLibraryMap = data.meme_persona_library_map || {};
      state.smartImage.isolation = data.smart_image_isolation || {
        enabled: false,
        inherit_auto_tags: true,
      };
      state.smartImage.libraries = data.smart_image_libraries || {};
      state.smartImage.personaLibraryMap = data.smart_image_persona_library_map || {};
      state.smartImage.globalTags = data.smart_image_global_tags || [];
      state.smartImage.policyGlobalTags = data.smart_image_global_tags || [];
      state.smartImage.smartGlobalTags = [];
      state.giteeAiimgEffects = data.gitee_aiimg_persona_effects || {
        enabled: false,
        effects: {},
      };
      state.privateCompanionProactive = data.private_companion_proactive || {
        enabled: true,
        image_fast_mode: true,
        image_debounce_seconds: 1.5,
        image_vision_timeout_seconds: 15,
      };
      state.autoPersonaSelector = data.auto_persona_selector || {
        provider_id: "",
        model: "",
        timeout_seconds: 8,
        extra_prompt: "",
      };
      state.proactivePrompts = data.proactive_chat_persona_prompts || { prompts: {} };
      state.lifeLibraries = data.life_schedule_libraries || {};
      state.lifePersonaMap = data.life_schedule_persona_map || {};
      state.integrations = data.integrations || { items: [], diagnostics: [] };
      renderAll();
      (data.warnings || []).forEach((warning) => toast(warning, "error"));
      if (showToast) toast("数据已刷新。");
    } catch (error) {
      $("#runtimeStatus").textContent = "连接失败";
      $("#runtimeStatus").classList.add("error");
      toast(error.message || String(error), "error");
    }
  }

  function openUserEditor(userId = "") {
    const user = state.users.find((item) => item.user_id === userId) || {
      user_id: "",
      persona_mode: "default",
      persona_id: "",
      provider_id: "",
      auto_persona: {},
      blocked: false,
      allow_persona_switch: false,
      memory_isolation: true,
      livingmemory_isolation: true,
      private_companion_proactive: true,
      plugin_access: { mode: "all", plugins: [] },
    };
    const editing = Boolean(userId);
    $("#userModalTitle").textContent = editing ? "编辑用户" : "添加用户";
    $("#userId").value = user.user_id;
    $("#userId").disabled = editing;
    $("#userStatus").value = user.blocked ? "blocked" : "allowed";
    $("#userCanSwitch").checked = Boolean(user.allow_persona_switch);
    $("#userMemoryIsolation").checked = user.memory_isolation !== false;
    $("#userLivingMemoryIsolation").checked = user.livingmemory_isolation !== false;
    $("#userPrivateCompanionProactive").checked = user.private_companion_proactive !== false;
    $("#userResponseProvider").innerHTML = providerOptions(user.provider_id || "");
    fillPersonaSelect(
      $("#userPersona"),
      user.persona_id,
      "跟随会话默认人格",
      user.persona_mode
    );
    renderAutoPersonaEditor("user", user.auto_persona || {});
    syncAutoPersonaFields("user");
    $("#userPluginMode").value = user.plugin_access?.mode || "all";
    renderPluginPicker($("#userPluginPicker"), user.plugin_access?.plugins);
    syncPluginPicker($("#userPluginMode"), $("#userPluginPicker"));
    openModal("userModal");
  }

  function openGroupEditor(groupId = "") {
    const group = state.groups.find((item) => item.group_id === groupId) || {
      group_id: "",
      description: "",
      persona_mode: "default",
      persona_id: "",
      provider_id: "",
      auto_persona: {},
      memory_isolation: true,
      livingmemory_isolation: true,
      plugin_access: { mode: "all", plugins: [] },
      member_access: { mode: "all", users: [] },
      policy_admins: [],
      users: {},
    };
    const editing = Boolean(groupId);
    $("#groupModalTitle").textContent = editing ? "编辑群聊" : "添加群聊";
    $("#groupId").value = group.group_id;
    $("#groupId").disabled = editing;
    $("#groupDescription").value = group.description || "";
    fillPersonaSelect(
      $("#groupPersona"),
      group.persona_id,
      "跟随会话默认人格",
      group.persona_mode
    );
    renderAutoPersonaEditor("group", group.auto_persona || {});
    syncAutoPersonaFields("group");
    $("#groupMemoryIsolation").checked = group.memory_isolation !== false;
    $("#groupLivingMemoryIsolation").checked = group.livingmemory_isolation !== false;
    $("#groupResponseProvider").innerHTML = providerOptions(group.provider_id || "");
    $("#memberAccessMode").value = group.member_access?.mode || "all";
    $("#memberAccessUsers").value = (group.member_access?.users || []).join("\n");
    $("#groupPolicyAdmins").value = (group.policy_admins || []).join("\n");
    $("#groupPluginMode").value = group.plugin_access?.mode || "all";
    renderPluginPicker($("#groupPluginPicker"), group.plugin_access?.plugins);
    syncPluginPicker($("#groupPluginMode"), $("#groupPluginPicker"));
    syncMemberAccessField();
    openModal("groupModal");
  }

  function syncMemberAccessField() {
    $("#memberAccessUsersField").classList.toggle(
      "hidden",
      $("#memberAccessMode").value === "all"
    );
  }

  function currentGroup() {
    return state.groups.find((item) => item.group_id === state.currentGroupId);
  }

  function openMembers(groupId) {
    state.currentGroupId = groupId;
    const group = currentGroup();
    if (!group) return;
    $("#membersModalTitle").textContent = `群 ${groupId}`;
    renderMembers();
    openModal("membersModal");
  }

  function renderMembers() {
    const group = currentGroup();
    const members = Object.entries(group?.users || {});
    $("#membersBody").innerHTML = members.map(([userId, member]) => `
      <tr>
        <td class="mono">${escapeHtml(userId)}</td>
        <td class="text-nowrap">${escapeHtml(personaRuleLabel(member, "沿用群默认人格"))}</td>
        <td class="text-nowrap">${member.allow_persona_switch ? "已授权" : '<span class="muted">未授权</span>'}</td>
        <td class="text-nowrap">${escapeHtml(memoryIsolationLabel(member.memory_isolation, true))}</td>
        <td class="text-nowrap">${escapeHtml(memoryIsolationLabel(member.livingmemory_isolation, true))}</td>
        <td class="text-nowrap">${escapeHtml(pluginModeLabel(member.plugin_access))}</td>
        <td>
          <div class="row-actions">
            <button class="button small" type="button" data-edit-member="${escapeHtml(userId)}">编辑</button>
            <button class="button small danger" type="button" data-delete-member="${escapeHtml(userId)}">删除</button>
          </div>
        </td>
      </tr>
    `).join("");
    $("#membersEmpty").classList.toggle("hidden", members.length > 0);
  }

  function openMemberEditor(userId = "") {
    const group = currentGroup();
    if (!group) return;
    const member = group.users?.[userId] || {
      persona_mode: "inherit",
      persona_id: "",
      provider_id: "",
      auto_persona: {},
      allow_persona_switch: false,
      memory_isolation: null,
      livingmemory_isolation: null,
      plugin_access: { mode: "inherit", plugins: [] },
    };
    const editing = Boolean(userId);
    $("#memberModalTitle").textContent = editing ? "编辑成员设置" : "添加成员设置";
    $("#memberId").value = userId;
    $("#memberId").disabled = editing;
    fillPersonaSelect(
      $("#memberPersona"),
      member.persona_id,
      "沿用群默认人格",
      member.persona_mode
    );
    renderAutoPersonaEditor("member", member.auto_persona || {});
    syncAutoPersonaFields("member");
    $("#memberCanSwitch").checked = Boolean(member.allow_persona_switch);
    $("#memberMemoryIsolation").value = member.memory_isolation == null
      ? "inherit"
      : (member.memory_isolation ? "enabled" : "disabled");
    $("#memberLivingMemoryIsolation").value = member.livingmemory_isolation == null
      ? "inherit"
      : (member.livingmemory_isolation ? "enabled" : "disabled");
    $("#memberResponseProvider").innerHTML = providerOptions(member.provider_id || "");
    $("#memberPluginMode").value = member.plugin_access?.mode || "inherit";
    renderPluginPicker($("#memberPluginPicker"), member.plugin_access?.plugins);
    syncPluginPicker($("#memberPluginMode"), $("#memberPluginPicker"));
    openModal("memberModal");
  }

  function syncScheduleTargets(selectedValue = "") {
    const type = $("#scheduleTargetType").value;
    let options = [];
    let hint = "";
    if (type === "private") {
      options = state.users.map((user) => ({
        value: user.user_id,
        label: `用户 ${user.user_id}`,
      }));
      hint = "目标来自“私聊用户”页面。";
    } else if (type === "group") {
      options = state.groups.map((group) => ({
        value: group.group_id,
        label: group.description
          ? `${group.description}（${group.group_id}）`
          : `群 ${group.group_id}`,
      }));
      hint = "切换该群的默认人格。";
    } else {
      options = state.groups.flatMap((group) =>
        Object.keys(group.users || {}).map((userId) => ({
          value: encodeMemberTarget(group.group_id, userId),
          label: `${userId} · ${group.description || group.group_id}`,
        }))
      );
      hint = "目标来自群聊中的“成员设置”。";
    }

    const target = $("#scheduleTarget");
    target.innerHTML = options.length
      ? options.map((item) =>
        `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`
      ).join("")
      : '<option value="">当前没有可选择的目标</option>';
    if (
      selectedValue
      && !options.some((item) => item.value === selectedValue)
    ) {
      target.insertAdjacentHTML(
        "beforeend",
        `<option value="${escapeHtml(selectedValue)}">${escapeHtml(selectedValue)}（已失效）</option>`
      );
    }
    target.value = selectedValue || options[0]?.value || "";
    $("#scheduleTargetHint").textContent = hint;
  }

  function renderWeeklyRules(rules) {
    const byDay = new Map(
      (rules || []).map((rule) => [Number(rule.weekday), rule])
    );
    $("#scheduleWeeklyRules").innerHTML = weekdays.map((day) => {
      const rule = byDay.get(day.value) || {};
      const personaId = rule.persona_id || "";
      const enabled = rule.enabled !== false && Boolean(personaId);
      const choices = [...state.personas];
      if (personaId && !choices.some((item) => item.persona_id === personaId)) {
        choices.push({ persona_id: personaId, name: `${personaId}（已失效）` });
      }
      return `
        <article class="weekly-rule-card ${enabled ? "active" : ""}" data-weekly-card="${day.value}">
          <div class="weekly-rule-header">
            <div>
              <strong>${escapeHtml(day.label)}</strong>
              <span data-weekly-summary="${day.value}">${escapeHtml(enabled ? personaLabel(personaId, "不切换") : "不切换")}</span>
            </div>
            <button class="button small ${enabled ? "" : "hidden"}" type="button" data-clear-weekly-persona="${day.value}">清除</button>
          </div>
          <div class="persona-picker weekly-persona-picker">
            ${choices.map((persona) => `
              <label class="persona-choice weekly-persona-choice">
                <input type="radio" name="weekly-persona-${day.value}" data-weekly-persona="${day.value}" value="${escapeHtml(persona.persona_id)}" ${persona.persona_id === personaId ? "checked" : ""}>
                <span class="persona-choice-check">✓</span>
                <span>
                  <strong>${escapeHtml(persona.name || persona.persona_id)}</strong>
                  <small>${escapeHtml(persona.persona_id)}</small>
                </span>
              </label>
            `).join("") || '<span class="muted">当前没有可选择的 AstrBot 人格。</span>'}
          </div>
        </article>`;
    }).join("");
  }

  function selectedWeeklyRules() {
    return weekdays.map((day) => {
      const selected = $(`input[data-weekly-persona="${day.value}"]:checked`);
      const personaId = selected?.value || "";
      return {
        weekday: day.value,
        enabled: Boolean(personaId),
        persona_id: personaId,
      };
    }).filter((rule) => rule.enabled);
  }

  function syncWeeklyRuleCard(dayValue) {
    const card = $(`[data-weekly-card="${dayValue}"]`);
    if (!card) return;
    const selected = card.querySelector(`input[data-weekly-persona="${dayValue}"]:checked`);
    const personaId = selected?.value || "";
    card.classList.toggle("active", Boolean(personaId));
    const summary = card.querySelector(`[data-weekly-summary="${dayValue}"]`);
    if (summary) summary.textContent = personaId ? personaLabel(personaId, "不切换") : "不切换";
    const clearButton = card.querySelector(`[data-clear-weekly-persona="${dayValue}"]`);
    if (clearButton) clearButton.classList.toggle("hidden", !personaId);
  }

  function syncScheduleMode() {
    const mode = $("#scheduleMode").value;
    const daily = mode === "daily";
    const random = mode === "random_interval";
    const weekly = mode === "weekly";
    $("#scheduleDailyFields").classList.toggle("hidden", !daily);
    $("#scheduleRandomFields").classList.toggle("hidden", !random);
    $("#scheduleWeeklyFields").classList.toggle("hidden", !weekly);
    $("#scheduleDailyTime").required = daily;
    $("#schedulePersona").required = daily;
    $("#scheduleInterval").required = random;
    $("#scheduleWeeklyTime").required = weekly;
  }

  function openScheduleEditor(scheduleId = "") {
    const schedule = state.schedules.find(
      (item) => item.schedule_id === scheduleId
    ) || {
      schedule_id: "",
      name: "",
      enabled: true,
      target_type: "private",
      user_id: "",
      group_id: "",
      mode: "daily",
      daily_time: "09:00",
      weekly_time: "09:00",
      interval_minutes: 60,
      persona_id: "",
      persona_ids: [],
      weekly_rules: [],
    };
    $("#scheduleModalTitle").textContent = scheduleId ? "编辑计划" : "添加计划";
    $("#scheduleId").value = schedule.schedule_id || "";
    $("#scheduleName").value = schedule.name || "";
    $("#scheduleEnabled").checked = schedule.enabled !== false;
    $("#scheduleTargetType").value = schedule.target_type || "private";
    syncScheduleTargets(scheduleTargetValue(schedule));
    $("#scheduleMode").value = schedule.mode || "daily";
    $("#scheduleDailyTime").value = schedule.daily_time || "09:00";
    $("#scheduleWeeklyTime").value = schedule.weekly_time || "09:00";
    fillPersonaSelect(
      $("#schedulePersona"),
      schedule.persona_id,
      "请选择人格",
      "fixed",
      false
    );
    $("#scheduleInterval").value = schedule.interval_minutes || 60;
    renderPersonaPicker(
      $("#schedulePersonaPicker"),
      schedule.persona_ids || []
    );
    renderWeeklyRules(schedule.weekly_rules || []);
    syncScheduleMode();
    openModal("scheduleModal");
  }

  async function mutate(endpoint, body, successMessage) {
    try {
      const result = await apiPost(endpoint, {
        revision: state.revision,
        ...body,
      });
      await loadData();
      toast(result.message || successMessage);
      return true;
    } catch (error) {
      if (error.code === 409) await loadData();
      toast(error.message || String(error), "error");
      return false;
    }
  }

  $("#userForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = $("#userPluginMode").value;
    let persona;
    try {
      persona = personaSelection("user", "default");
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    const success = await mutate("users/save", {
      user_id: $("#userId").value,
      rule: {
        ...persona,
        provider_id: $("#userResponseProvider").value,
        blocked: $("#userStatus").value === "blocked",
        allow_persona_switch: $("#userCanSwitch").checked,
        memory_isolation: $("#userMemoryIsolation").checked,
        livingmemory_isolation: $("#userLivingMemoryIsolation").checked,
        private_companion_proactive: $("#userPrivateCompanionProactive").checked,
        plugin_access: {
          mode,
          plugins: ["all", "inherit"].includes(mode)
            ? []
            : selectedPlugins($("#userPluginPicker")),
        },
      },
    }, "私聊用户已保存。");
    if (success) closeModal("userModal");
  });

  $("#groupForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const groupId = $("#groupId").value;
    const existing = state.groups.find((item) => item.group_id === groupId);
    const mode = $("#groupPluginMode").value;
    let persona;
    try {
      persona = personaSelection("group", "default");
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    const success = await mutate("groups/save", {
      group_id: groupId,
      rule: {
        description: $("#groupDescription").value,
        ...persona,
        provider_id: $("#groupResponseProvider").value,
        memory_isolation: $("#groupMemoryIsolation").checked,
        livingmemory_isolation: $("#groupLivingMemoryIsolation").checked,
        member_access: {
          mode: $("#memberAccessMode").value,
          users: $("#memberAccessMode").value === "all"
            ? []
            : parseIds($("#memberAccessUsers").value),
        },
        policy_admins: parseIds($("#groupPolicyAdmins").value),
        plugin_access: {
          mode,
          plugins: mode === "all"
            ? []
            : selectedPlugins($("#groupPluginPicker")),
        },
        users: existing?.users || {},
      },
    }, "群聊设置已保存。");
    if (success) closeModal("groupModal");
  });

  $("#memberForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = $("#memberPluginMode").value;
    let persona;
    try {
      persona = personaSelection("member", "inherit");
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    const success = await mutate("groups/users/save", {
      group_id: state.currentGroupId,
      user_id: $("#memberId").value,
      rule: {
        ...persona,
        provider_id: $("#memberResponseProvider").value,
        allow_persona_switch: $("#memberCanSwitch").checked,
        memory_isolation: $("#memberMemoryIsolation").value === "inherit"
          ? null
          : $("#memberMemoryIsolation").value === "enabled",
        livingmemory_isolation: $("#memberLivingMemoryIsolation").value === "inherit"
          ? null
          : $("#memberLivingMemoryIsolation").value === "enabled",
        plugin_access: {
          mode,
          plugins: ["all", "inherit"].includes(mode)
            ? []
            : selectedPlugins($("#memberPluginPicker")),
        },
      },
    }, "群成员个性设置已保存。");
    if (success) {
      closeModal("memberModal");
      renderMembers();
    }
  });

  $("#scheduleForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const targetType = $("#scheduleTargetType").value;
    const targetValue = $("#scheduleTarget").value;
    if (!targetValue) {
      toast("请先在对应页面添加可用目标。", "error");
      return;
    }
    const memberTarget = targetType === "member"
      ? decodeMemberTarget(targetValue)
      : { groupId: "", userId: "" };
    const mode = $("#scheduleMode").value;
    const personaIds = selectedPersonas($("#schedulePersonaPicker"));
    const weeklyRules = selectedWeeklyRules();
    if (mode === "random_interval" && personaIds.length < 2) {
      toast("随机人格范围至少选择两个人格。", "error");
      return;
    }
    if (mode === "weekly" && weeklyRules.length < 1) {
      toast("每周切换至少启用一个星期并选择人格。", "error");
      return;
    }
    const success = await mutate("schedules/save", {
      schedule_id: $("#scheduleId").value,
      rule: {
        name: $("#scheduleName").value,
        enabled: $("#scheduleEnabled").checked,
        target_type: targetType,
        user_id: targetType === "private"
          ? targetValue
          : memberTarget.userId,
        group_id: targetType === "group"
          ? targetValue
          : memberTarget.groupId,
        mode,
        daily_time: $("#scheduleDailyTime").value,
        weekly_time: $("#scheduleWeeklyTime").value,
        interval_minutes: Number($("#scheduleInterval").value),
        persona_id: mode === "daily" ? $("#schedulePersona").value : "",
        persona_ids: mode === "random_interval" ? personaIds : [],
        weekly_rules: mode === "weekly" ? weeklyRules : [],
      },
    }, "人格计划已保存。");
    if (success) closeModal("scheduleModal");
  });

  $("#giteeAiimgEffectsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const effects = {};
    $$("[data-gitee-aiimg-effect]").forEach((input) => {
      const value = input.value.trim();
      if (value) effects[input.dataset.giteeAiimgEffect] = value;
    });
    await mutate("integrations/gitee-aiimg-effects/save", {
      rule: {
        enabled: $("#giteeAiimgEffectsEnabled").checked,
        effects,
      },
    }, "Gitee AI Image 人格效果已保存。");
  });

  $("#proactivePromptsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const prompts = {};
    $$("[data-proactive-prompt]").forEach((input) => {
      const value = input.value.trim();
      if (value) prompts[input.dataset.proactivePrompt] = value;
    });
    const success = await mutate("integrations/proactive-chat/save", {
      rule: { prompts },
    }, "主动消息人格补充要求已保存。");
    if (success) closeModal("proactivePromptsModal");
  });

  $("#lifeRecordForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.currentLifeLibraryId) return;
    const success = await lifeMutate("life-schedule/record/save", {
      library_id: state.currentLifeLibraryId,
      record: {
        date: $("#lifeRecordDate").value,
        status: $("#lifeRecordStatus").value,
        outfit_style: $("#lifeRecordStyle").value,
        outfit: $("#lifeRecordOutfit").value,
        schedule: $("#lifeRecordSchedule").value,
      },
    }, "日程记录已保存。");
    if (success) $("#lifeRecordForm").classList.add("hidden");
  });

  document.addEventListener("click", async (event) => {
    // 图片选中卡片是 <div>，不会被下面的 closest("button") 命中，
    // 所以先单独处理；点到右上角 ✕（button）时交给后面的删除分支。
    const removeButton = event.target.closest(".meme-image-remove");
    if (!removeButton) {
      const selectCardHit = event.target.closest("[data-meme-select]");
      if (selectCardHit) {
        const key = selectCardHit.dataset.memeSelect;
        if (state.memeLibrary.selectedImages.has(key)) {
          state.memeLibrary.selectedImages.delete(key);
        } else {
          state.memeLibrary.selectedImages.add(key);
        }
        selectCardHit.classList.toggle("is-selected");
        renderMemeBatchBar();
        return;
      }
    }
    const smartImageCard = event.target.closest("[data-smart-image-select]");
    if (
      smartImageCard
      && !event.target.closest("button, input, select, textarea")
    ) {
      const key = smartImageCard.dataset.smartImageSelect;
      if (state.smartImage.selectedImages.has(key)) {
        state.smartImage.selectedImages.delete(key);
      } else {
        state.smartImage.selectedImages.add(key);
      }
      smartImageCard.classList.toggle(
        "is-selected",
        state.smartImage.selectedImages.has(key)
      );
      renderSmartImageBatchBar();
      return;
    }
    const smartPendingCard = event.target.closest("[data-smart-pending-select]");
    if (smartPendingCard && !event.target.closest("button, input, select, textarea")) {
      const imageId = smartPendingCard.dataset.smartPendingSelect;
      if (state.smartImage.selectedPending.has(imageId)) {
        state.smartImage.selectedPending.delete(imageId);
      } else {
        state.smartImage.selectedPending.add(imageId);
      }
      smartPendingCard.classList.toggle(
        "is-selected",
        state.smartImage.selectedPending.has(imageId)
      );
      return;
    }
    const titleToggle = event.target.closest(".meme-category-title[data-meme-toggle-category]");
    if (titleToggle && !event.target.closest("input, textarea, select, button")) {
      const category = titleToggle.dataset.memeToggleCategory;
      if (state.memeLibrary.collapsedCategories.has(category)) {
        state.memeLibrary.collapsedCategories.delete(category);
      } else {
        state.memeLibrary.collapsedCategories.add(category);
      }
      renderMemeLibrary();
      return;
    }
    const target = event.target.closest("button");
    if (!target) return;
    if (target.dataset.close) {
      closeModal(target.dataset.close);
      return;
    }
    if (target.dataset.cancelDelete !== undefined) {
      clearPendingDelete();
      return;
    }
    if (target.id === "confirmDeleteButton") {
      await confirmPendingDelete();
      return;
    }
    if (target.matches(".tab")) {
      $$(".tab").forEach((item) => item.classList.toggle("active", item === target));
      $$(".page").forEach((page) =>
        page.classList.toggle("active", page.id === `page-${target.dataset.page}`)
      );
      syncPageHeader(target.dataset.page);
      return;
    }
    if (target.id === "addUserButton" || target.matches("[data-add-user-inline]")) return openUserEditor();
    if (target.id === "addGroupButton") return openGroupEditor();
    if (target.id === "addMemberButton") return openMemberEditor();
    if (target.id === "addScheduleButton") return openScheduleEditor();
    if (target.dataset.importSessions !== undefined) {
      await openSessionImportPreview(target);
      return;
    }
    if (target.id === "confirmImportSessionsButton") {
      await confirmSessionImport();
      return;
    }
    if (target.dataset.openMemeLibrary !== undefined) {
      await openMemeLibraryManager();
      return;
    }
    if (target.dataset.openSmartImageLibrary !== undefined) {
      await openSmartImageManager();
      return;
    }
    if (target.dataset.saveSmartImageCard !== undefined) {
      await smartImageMutate("smart-image/isolation/save", {
        rule: {
          enabled: Boolean($("[data-smart-image-enabled]")?.checked),
          inherit_auto_tags: Boolean(
            $("[data-smart-image-inherit-tags]")?.checked
          ),
        },
      }, "智能图片人格图库隔离设置已保存。");
      return;
    }
    if (target.id === "smartImageCreateButton") {
      await createSmartImageLibrary();
      return;
    }
    if (target.id === "smartImageRenameButton") {
      await renameSmartImageLibrary();
      return;
    }
    if (target.id === "smartImageCopyButton") {
      await copySmartImageLibrary();
      return;
    }
    if (target.id === "smartImageNameConfirmButton") {
      await confirmSmartImageName();
      return;
    }
    if (target.id === "smartImageTagSaveButton") {
      await saveSmartImageTagEditor(false);
      return;
    }
    if (target.id === "smartImageTagApplyButton") {
      await saveSmartImageTagEditor(true);
      return;
    }
    if (target.id === "smartImageDeleteButton") {
      deleteSmartImageLibrary();
      return;
    }
    if (target.id === "smartImageUploadButton") {
      const input = $("#smartImageUploadInput");
      input.value = "";
      input.click();
      return;
    }
    if (target.id === "smartImageBackupButton") {
      await backupSmartImages();
      return;
    }
    if (target.id === "smartImageGlobalTagsSaveButton") {
      await saveSmartImageGlobalTags();
      return;
    }
    if (target.id === "smartPendingRefreshButton") {
      await refreshSmartPending();
      return;
    }
    if (target.id === "smartPendingCopyButton") {
      await distributeSmartPending(false);
      return;
    }
    if (target.id === "smartPendingMoveButton") {
      await distributeSmartPending(true);
      return;
    }
    if (target.id === "smartPendingDeleteButton") {
      deleteSmartPending();
      return;
    }
    if (target.id === "smartImageBatchCopyButton") {
      await distributeSelectedSmartImages(false);
      return;
    }
    if (target.id === "smartImageBatchMoveButton") {
      await distributeSelectedSmartImages(true);
      return;
    }
    if (target.id === "smartImageBatchDeleteButton") {
      deleteSelectedSmartImages();
      return;
    }
    if (target.dataset.smartEditTags !== undefined) {
      await openSmartImageTagEditor(target.dataset.smartEditTags);
      return;
    }
    if (target.dataset.smartCaptionImage !== undefined) {
      await captionSmartImage(target.dataset.smartCaptionImage, target);
      return;
    }
    if (target.dataset.smartDeleteImage !== undefined) {
      deleteSmartImage(target.dataset.smartDeleteImage);
      return;
    }
    if (target.dataset.openDefaultMemeLibrary !== undefined) {
      state.memeLibrary.selectedImages.clear();
      await loadMemeLibrary("__default__");
      return;
    }
    if (target.dataset.copyDefaultMemeLibrary !== undefined) {
      await copyMemeLibrary("__default__", target);
      return;
    }
    if (target.dataset.saveMemeCard !== undefined) {
      await mutate("integrations/meme-isolation/save", {
        rule: {
          enabled: true,
          copy_default_descriptions: Boolean(
            $("[data-meme-card-copy-descriptions]")?.checked
          ),
        },
      }, "表情包库隔离设置已保存。");
      return;
    }
    if (target.dataset.openGiteeAiimgEffects !== undefined) {
      const giteeItem = findIntegration(["astrbot_plugin_gitee_aiimg", "gitee_aiimg"]);
      renderGiteeAiimgEffects(giteeItem?.gitee_aiimg_effects);
      openModal("giteeAiimgEffectsModal");
      return;
    }
    if (target.dataset.openProactivePrompts !== undefined) {
      openProactivePrompts();
      return;
    }
    if (target.dataset.openLifeLibraries !== undefined) {
      openLifeLibraries();
      return;
    }
    if (target.id === "createLifeLibraryButton") {
      const name = $("#lifeLibraryName").value.trim();
      if (!name) return toast("请填写日程库名称。", "error");
      const success = await lifeMutate("life-schedule/library/create", { name }, "日程库已创建。");
      if (success) $("#lifeLibraryName").value = "";
      return;
    }
    if (target.id === "renameLifeLibraryButton") {
      const library = currentLifeLibrary();
      if (!library) return;
      const name = $("#lifeLibraryName").value.trim();
      if (!name) {
        $("#lifeLibraryName").value = library.name || "";
        $("#lifeLibraryName").focus();
        toast("请在名称输入框修改后再次点击重命名。", "error");
        return;
      }
      await lifeMutate("life-schedule/library/rename", {
        library_id: state.currentLifeLibraryId,
        name,
      }, "日程库已重命名。");
      return;
    }
    if (target.id === "deleteLifeLibraryButton") {
      const library = currentLifeLibrary();
      if (!library) return;
      const libraryId = state.currentLifeLibraryId;
      requestDelete({
        title: "删除人格日程库",
        message: `确定删除日程库「${library.name || libraryId}」及全部记录吗？`,
        action: () => {
          resetLifePoolUi(libraryId);
          return lifeMutate("life-schedule/library/delete", {
            library_id: libraryId,
          }, "日程库已删除。");
        },
      });
      return;
    }
    if (target.id === "addLifeRecordButton") {
      openLifeRecord();
      return;
    }
    if (target.dataset.lifePoolExpand) {
      lifePoolUi().expanded.add(target.dataset.lifePoolExpand);
      renderLifePool();
      return;
    }
    if (target.dataset.lifePoolCollapse) {
      lifePoolUi().expanded.delete(target.dataset.lifePoolCollapse);
      renderLifePool();
      return;
    }
    if (target.dataset.lifePoolImport) {
      const key = target.dataset.lifePoolImport;
      const group = LIFE_POOL_GROUPS.find((g) => g.key === key);
      state.lifePoolImportKey = key;
      state.lifePoolImportLibraryId = state.currentLifeLibraryId;
      $("#lifePoolImportTitle").textContent = `批量导入${group?.label || ""}`;
      $("#lifePoolImportText").value = "";
      openModal("lifePoolImportModal");
      setTimeout(() => $("#lifePoolImportText").focus(), 0);
      return;
    }
    if (target.dataset.lifePoolSelectAll) {
      const key = target.dataset.lifePoolSelectAll;
      const items = currentLifePool()[key] || [];
      lifePoolUi().selected[key] = new Set(items);
      renderLifePool();
      return;
    }
    if (target.dataset.lifePoolSelectNone) {
      const key = target.dataset.lifePoolSelectNone;
      lifePoolUi().selected[key] = new Set();
      renderLifePool();
      return;
    }
    if (target.dataset.lifePoolDeleteSelected) {
      const key = target.dataset.lifePoolDeleteSelected;
      const group = LIFE_POOL_GROUPS.find((g) => g.key === key);
      const libraryId = state.currentLifeLibraryId;
      const pool = currentLifePool();
      const selected = lifePoolUi().selected[key] || new Set();
      const count = [...selected].filter((v) => (pool[key] || []).includes(v)).length;
      if (!count) {
        toast("请先勾选要删除的条目。", "error");
        return;
      }
      requestDelete({
        title: "删除选中条目",
        message: `确定从「${group?.label || key}」删除选中的 ${count} 个条目吗？`,
        successMessage: "已删除。",
        action: async () => {
          pool[key] = pool[key].filter((item) => !selected.has(item));
          const ui = lifePoolUi();
          ui.selected[key] = new Set();
          ui.expanded.add(key);
          await saveLifePool(pool, libraryId);
          return true;
        },
      });
      return;
    }
    if (target.dataset.lifePoolAddBtn) {
      const key = target.dataset.lifePoolAddBtn;
      const libraryId = state.currentLifeLibraryId;
      const input = $(`[data-life-pool-add="${key}"]`);
      const value = (input?.value || "").trim();
      if (!value) {
        toast("请填写要添加的条目。", "error");
        return;
      }
      const pool = currentLifePool();
      if (pool[key].includes(value)) {
        toast("该条目已存在。", "error");
        return;
      }
      pool[key].push(value);
      await saveLifePool(pool, libraryId);
      return;
    }
    if (target.id === "cancelLifeRecordButton") {
      $("#lifeRecordForm").classList.add("hidden");
      return;
    }
    if (target.dataset.editLifeRecord) {
      openLifeRecord(target.dataset.editLifeRecord);
      return;
    }
    if (target.dataset.deleteLifeRecord) {
      const date = target.dataset.deleteLifeRecord;
      const libraryId = state.currentLifeLibraryId;
      requestDelete({
        title: "删除日程记录",
        message: `确定删除 ${date} 的日程记录吗？`,
        action: () => lifeMutate("life-schedule/record/delete", {
          library_id: libraryId,
          date,
        }, "日程记录已删除。"),
      });
      return;
    }
    if (target.dataset.saveGiteeAiimgCard !== undefined) {
      await mutate("integrations/gitee-aiimg-effects/save", {
        rule: {
          enabled: Boolean($("[data-gitee-aiimg-card-enabled]")?.checked),
          effects: state.giteeAiimgEffects.effects || {},
        },
      }, "Gitee AI Image 人格效果开关已保存。");
      return;
    }
    if (target.dataset.savePrivateCompanionCard !== undefined) {
      await mutate("integrations/private-companion-proactive/save", {
        rule: {
          enabled: Boolean($("[data-private-companion-card-enabled]")?.checked),
          image_fast_mode: Boolean($("[data-private-companion-image-fast]")?.checked),
          image_debounce_seconds: Number($("[data-private-companion-image-debounce]")?.value ?? 1.5),
          image_vision_timeout_seconds: Number($("[data-private-companion-image-timeout]")?.value ?? 15),
        },
      }, "Private Companion 主动对话与识图等待设置已保存。");
      return;
    }
    if (target.dataset.clearWeeklyPersona !== undefined) {
      const dayValue = target.dataset.clearWeeklyPersona;
      const checked = $(`input[data-weekly-persona="${dayValue}"]:checked`);
      if (checked) checked.checked = false;
      syncWeeklyRuleCard(dayValue);
      return;
    }
    if (target.dataset.editUser) return openUserEditor(target.dataset.editUser);
    if (target.dataset.editGroup) return openGroupEditor(target.dataset.editGroup);
    if (target.dataset.membersGroup) return openMembers(target.dataset.membersGroup);
    if (target.dataset.editMember) return openMemberEditor(target.dataset.editMember);
    if (target.dataset.editSchedule) return openScheduleEditor(target.dataset.editSchedule);
    if (target.id === "memeAddCategoryButton") {
      const name = $("#memeNewCategoryName").value.trim();
      if (!name) {
        toast("请填写分类名称。", "error");
        return;
      }
      const success = await updateMemeLibrary("meme-library/category/save", {
        target_id: memeTargetId(),
        category: name,
        description: "",
      }, "分类已创建。");
      if (success) {
        $("#memeNewCategoryName").value = "";
      }
      return;
    }
    const deleteCategory = target.dataset.memeDelCategory;
    if (deleteCategory !== undefined) {
      requestDelete({
        title: "删除表情分类",
        message: `确定删除分类 ${deleteCategory} 及其中全部图片吗？`,
        successMessage: "分类已删除。",
        action: () => updateMemeLibrary("meme-library/category/delete", {
          target_id: memeTargetId(),
          category: deleteCategory,
        }, "分类已删除。"),
      });
      return;
    }
    const delImage = target.dataset.memeDelImage;
    if (delImage !== undefined) {
      const category = target.dataset.memeCategory;
      requestDelete({
        title: "删除表情图片",
        message: `确定删除图片 ${delImage} 吗？`,
        successMessage: "图片已删除。",
        action: () => updateMemeLibrary("meme-library/images/delete", {
          target_id: memeTargetId(),
          category,
          filenames: [delImage],
        }, "图片已删除。"),
      });
      return;
    }
    const dropzone = target.closest("[data-meme-dropzone]");
    if (dropzone) {
      state.memeLibrary.uploadCategory = dropzone.dataset.memeDropzone;
      const input = $("#memeUploadInput");
      input.value = "";
      input.click();
      return;
    }
    const toggleCategory = target.dataset.memeToggleCategory;
    if (toggleCategory !== undefined) {
      if (state.memeLibrary.collapsedCategories.has(toggleCategory)) {
        state.memeLibrary.collapsedCategories.delete(toggleCategory);
      } else {
        state.memeLibrary.collapsedCategories.add(toggleCategory);
      }
      renderMemeLibrary();
      return;
    }
    if (target.id === "memeBatchSelectAll") {
      state.memeLibrary.selectedImages = new Set(allMemeKeys());
      renderMemeLibrary();
      return;
    }
    if (target.id === "memeBatchInvert") {
      const next = new Set();
      for (const key of allMemeKeys()) {
        if (!state.memeLibrary.selectedImages.has(key)) next.add(key);
      }
      state.memeLibrary.selectedImages = next;
      renderMemeLibrary();
      return;
    }
    if (target.id === "memeBatchClear") {
      state.memeLibrary.selectedImages.clear();
      renderMemeLibrary();
      return;
    }
    if (target.id === "memeBatchDelete") {
      await memeBatchDelete();
      return;
    }
    if (target.id === "memeBatchMove") {
      await memeBatchMove();
      return;
    }
    if (target.id === "memeBatchCopy") {
      await memeBatchCopy();
      return;
    }
    if (target.id === "memeBatchDownload") {
      await memeBatchDownload();
      return;
    }
    const selectCatName = target.dataset.memeSelectCat;
    if (selectCatName !== undefined) {
      const category = (state.memeLibrary.library?.categories || [])
        .find((item) => item.name === selectCatName);
      for (const image of category?.images || []) {
        state.memeLibrary.selectedImages.add(memeSelectionKey(selectCatName, image.filename));
      }
      renderMemeLibrary();
      return;
    }
    const clearCatName = target.dataset.memeClearCat;
    if (clearCatName !== undefined) {
      const category = (state.memeLibrary.library?.categories || [])
        .find((item) => item.name === clearCatName);
      for (const image of category?.images || []) {
        state.memeLibrary.selectedImages.delete(memeSelectionKey(clearCatName, image.filename));
      }
      renderMemeLibrary();
      return;
    }

    if (target.dataset.deleteUser) {
      requestDelete({
        title: "删除私聊用户",
        message: `确定删除用户 ${target.dataset.deleteUser} 吗？`,
        endpoint: "users/delete",
        body: { user_id: target.dataset.deleteUser },
        successMessage: "用户已删除。",
      });
      return;
    }
    if (target.dataset.deleteGroup) {
      requestDelete({
        title: "删除群聊设置",
        message: `确定删除群 ${target.dataset.deleteGroup} 的全部设置吗？成员设置和相关计划可能一并失效。`,
        endpoint: "groups/delete",
        body: { group_id: target.dataset.deleteGroup },
        successMessage: "群聊已删除。",
      });
      return;
    }
    if (target.dataset.deleteMember) {
      requestDelete({
        title: "删除成员设置",
        message: `确定删除成员 ${target.dataset.deleteMember} 的个性设置吗？`,
        endpoint: "groups/users/delete",
        body: {
          group_id: state.currentGroupId,
          user_id: target.dataset.deleteMember,
        },
        successMessage: "成员设置已删除。",
        afterSuccess: renderMembers,
      });
      return;
    }
    if (target.dataset.deleteSchedule) {
      requestDelete({
        title: "删除人格计划",
        message: "确定删除这个人格计划吗？",
        endpoint: "schedules/delete",
        body: {
          schedule_id: target.dataset.deleteSchedule,
        },
        successMessage: "人格计划已删除。",
      });
      return;
    }
  });

  $("#userPluginMode").addEventListener("change", () =>
    syncPluginPicker($("#userPluginMode"), $("#userPluginPicker"))
  );
  $("#groupPluginMode").addEventListener("change", () =>
    syncPluginPicker($("#groupPluginMode"), $("#groupPluginPicker"))
  );
  $("#memberPluginMode").addEventListener("change", () =>
    syncPluginPicker($("#memberPluginMode"), $("#memberPluginPicker"))
  );
  $("#memberAccessMode").addEventListener("change", syncMemberAccessField);
  $("#userPersona").addEventListener("change", () => syncAutoPersonaFields("user"));
  $("#groupPersona").addEventListener("change", () => syncAutoPersonaFields("group"));
  $("#memberPersona").addEventListener("change", () => syncAutoPersonaFields("member"));
  $("#scheduleTargetType").addEventListener("change", () =>
    syncScheduleTargets()
  );
  $("#scheduleMode").addEventListener("change", syncScheduleMode);
  $("#memeLibraryTarget").addEventListener("change", (event) => {
    state.memeLibrary.selectedImages.clear();
    loadMemeLibrary(event.target.value);
  });
  $("#smartImageLibrarySelect").addEventListener("change", (event) => {
    loadSmartImageLibrary(event.target.value);
  });
  $("#lifeLibrarySelect").addEventListener("change", (event) => {
    state.currentLifeLibraryId = event.target.value;
    state.lifePoolImportKey = "";
    state.lifePoolImportLibraryId = "";
    $("#lifeRecordForm").classList.add("hidden");
    closeModal("lifePoolImportModal");
    renderLifeLibraries();
  });
  $("#memeLibCreateButton").addEventListener("click", createMemeLibrary);
  $("#memeLibRenameButton").addEventListener("click", renameMemeLibrary);
  $("#memeLibDeleteButton").addEventListener("click", deleteMemeLibrary);
  $("#memeLibCopyButton").addEventListener("click", copyMemeLibrary);
  $("#lifePoolImportConfirm").addEventListener("click", confirmLifePoolImport);
  $("#memeUploadInput").addEventListener("change", async (event) => {
    const files = [...event.target.files];
    const category = state.memeLibrary.uploadCategory;
    state.memeLibrary.uploadCategory = "";
    event.target.value = "";
    await uploadMemeFiles(category, files);
  });
  $("#smartImageUploadInput").addEventListener("change", async (event) => {
    const files = [...event.target.files];
    event.target.value = "";
    await uploadSmartImageFiles(files);
  });
  $("#smartImageNameInput").addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    await confirmSmartImageName();
  });
  document.addEventListener("change", (event) => {
    const smartImageBatchTarget = event.target.closest(
      "input[data-smart-image-batch-target]"
    );
    if (smartImageBatchTarget) {
      if (smartImageBatchTarget.checked) {
        state.smartImage.selectedImageTargets.add(
          smartImageBatchTarget.value
        );
      } else {
        state.smartImage.selectedImageTargets.delete(
          smartImageBatchTarget.value
        );
      }
      return;
    }
    const poolSelect = event.target.closest("input[data-life-pool-select]");
    if (poolSelect) {
      const key = poolSelect.dataset.lifePoolSelect;
      const ui = lifePoolUi();
      const set = ui.selected[key] || (ui.selected[key] = new Set());
      if (poolSelect.checked) {
        set.add(poolSelect.value);
      } else {
        set.delete(poolSelect.value);
      }
      poolSelect.closest(".life-pool-chip")?.classList.toggle("is-selected", poolSelect.checked);
      // 更新「删除选中（N）」计数。
      renderLifePool();
      return;
    }
    const personaMap = event.target.closest("select[data-meme-persona-map]");
    if (personaMap) {
      saveMemePersonaMap(personaMap.dataset.memePersonaMap, personaMap.value);
      return;
    }
    const smartImagePersonaMap = event.target.closest(
      "select[data-smart-image-persona-map]"
    );
    if (smartImagePersonaMap) {
      saveSmartImagePersonaMap(
        smartImagePersonaMap.dataset.smartImagePersonaMap,
        smartImagePersonaMap.value
      );
      return;
    }
    const lifePersonaMap = event.target.closest("select[data-life-persona-map]");
    if (lifePersonaMap) {
      lifeMutate("life-schedule/persona-map/save", {
        persona_id: lifePersonaMap.dataset.lifePersonaMap,
        library_id: lifePersonaMap.value,
      }, "人格日程库映射已保存。");
      return;
    }
    const autoPersona = event.target.closest("input[data-auto-persona]");
    if (autoPersona) {
      autoPersona.closest(".auto-persona-rule")?.classList.toggle(
        "active",
        autoPersona.checked
      );
      return;
    }
    const weeklyPersona = event.target.closest("input[data-weekly-persona]");
    if (weeklyPersona) {
      syncWeeklyRuleCard(weeklyPersona.dataset.weeklyPersona);
    }
  });
  document.addEventListener("focusout", async (event) => {
    const renameInput = event.target.closest("input[data-meme-rename]");
    if (renameInput) {
      const original = renameInput.dataset.memeRename;
      const next = renameInput.value.trim();
      if (!next || next === original) {
        renameInput.value = original;
        return;
      }
      const card = renameInput.closest("[data-meme-card]");
      const description = card?.querySelector("[data-meme-desc]")?.value || "";
      await updateMemeLibrary("meme-library/category/save", {
        target_id: memeTargetId(),
        category: original,
        new_name: next,
        description,
      }, "分类已重命名。");
      return;
    }
    const descInput = event.target.closest("textarea[data-meme-desc]");
    if (descInput) {
      const category = descInput.dataset.memeDesc;
      const current = state.memeLibrary.library?.categories?.find(
        (item) => item.name === category
      );
      if (!current || (current.description || "") === descInput.value) return;
      await updateMemeLibrary("meme-library/category/save", {
        target_id: memeTargetId(),
        category,
        description: descInput.value,
      }, "分类描述已保存。");
    }
  });
  const memeGallery = $("#memeGallery");
  memeGallery.addEventListener("dragover", (event) => {
    const dropzone = event.target.closest("[data-meme-dropzone]");
    if (!dropzone) return;
    event.preventDefault();
    dropzone.classList.add("is-dragover");
  });
  memeGallery.addEventListener("dragleave", (event) => {
    const dropzone = event.target.closest("[data-meme-dropzone]");
    if (dropzone) dropzone.classList.remove("is-dragover");
  });
  memeGallery.addEventListener("drop", async (event) => {
    const dropzone = event.target.closest("[data-meme-dropzone]");
    if (!dropzone) return;
    event.preventDefault();
    dropzone.classList.remove("is-dragover");
    await uploadMemeFiles(dropzone.dataset.memeDropzone, event.dataTransfer?.files);
  });
  memeGallery.addEventListener("keydown", (event) => {
    const renameInput = event.target.closest("input[data-meme-rename]");
    if (renameInput && event.key === "Enter") {
      event.preventDefault();
      renameInput.blur();
    }
  });

  async function waitForBridge() {
    for (let attempt = 0; attempt < 50; attempt += 1) {
      if (window.AstrBotPluginPage?.ready) {
        return window.AstrBotPluginPage;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("AstrBot 插件页面桥接器加载超时，请刷新页面。");
  }

  async function initialize() {
    try {
      bridge = await waitForBridge();
      await bridge.ready();
      syncPageHeader("users");
      await loadData();
    } catch (error) {
      $("#runtimeStatus").textContent = "连接失败";
      $("#runtimeStatus").classList.add("error");
      toast(error.message || String(error), "error");
    }
  }

  initialize();
})();
