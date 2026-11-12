"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let snapshot, draft, selected = 0, health, overview, noticeTimer;
let detailLoad = 0, currentDetailId = null, pendingLinks = [], sessionAction;
let caseStudies=[];
let setupSlots = [], selectedSlot, slotDirty = false, slotBusy = false;
let previewSlot = null, previewBusy = false, copyDraft = null;
let assistantAttachment = null, setupChatId = null, setupChatBusy = false, setupChatLoading = false;
let setupChatLoad = 0, setupChatHistoryLoad = 0, setupChatTurns = [], setupPendingTurn = null;
let newSetup = false;
const slotStorageKey = "research_selected_setup_slot";
const modeName=task=>task==='negotiation' ? 'Buyer–supplier negotiation' : 'Supplier selection';
const engineNumbers=['spend_cap_usd','session_spend_usd','max_input_tokens','max_output_tokens','input_usd_per_million','output_usd_per_million'];
const negotiationFields=['title','item','quantity','currency','participant_role','price_min','price_max','opening_price','concession_step','instructions','knowledge'];
// Native selects keep the browser's keyboard and assistive technology behavior.
// Browsers with customizable select support also show icons and choice descriptions.
const canStyleSetupChoices = CSS.supports("appearance", "base-select") &&
  CSS.supports("selector(::picker(select))");
const setupTones = {study:"blue", task:"violet", conditions:"mint", engine:"amber", advanced:"blue"};
const caseIcons = {
  supplier_selection:"box", negotiation:"chat", demand_forecasting:"chart",
  supplier_risk:"shield", disruption_response:"bolt", ozer_game:"activity",
};
const setupChoices = {
  "config-status": {
    draft:["edit","Only test sessions can start."], active:["door","Accept new participants."],
    closed:["lock","Stop new participant sessions."],
  },
  "config-task": {
    supplier_selection:["box","Compare and choose between suppliers."],
    negotiation:["chat","Agree on a unit price within a set range."],
  },
  "config-assignment": {
    balanced:["groups","Favor groups with fewer assignments."],
    random:["shuffle","Give each group the same odds."],
    external:["link","Let a survey or launch link choose the group."],
  },
  "config-provider": {
    demo:["bolt","Built-in responses for testing."], openai:["spark","Use the server’s OpenAI key."],
  },
  "config-memory": {
    session:["memory","Include earlier messages from this session."],
    none:["shield","Start each response without earlier messages."],
  },
  "cond-agent_enabled": {true:["spark"], false:["person"]},
  "cond-agent-mode": {
    analytical:["chart","Show analysis without choosing."], chatbot:["chat","Talk through the options."],
    recommender:["target","Suggest a choice for the participant to accept or reject."],
    autonomous:["bolt","Record the simulated decision automatically."],
  },
  "cond-authority": {
    recommendation:["target"], provisional_decision:["bolt"], chat:["chat"],
  },
  "cond-role": {buyer:["cart"], supplier:["box"], genai_tool:["spark"], retailer:["cart"]},
  "neg-participant_role": {buyer:["cart"], supplier:["box"]},
  "cond-reasoning": {direct:["bolt"], role:["person"], few_shot:["study"], explanation:["chat"]},
  "cond-output": {recommendation:["target"], explanation:["text"], number:["hash"]},
  "cond-selection": {weighted:["chart"], model:["spark"]},
  "cond-advice_quality": {standard:["check"], flawed:["activity"]},
  "config-reasoning_effort": {"":["chip"], none:["bolt"], minimal:["sliders"], low:["sliders"], medium:["sliders"], high:["chart"], xhigh:["chart"]},
};
const setupFieldIcons = {
  "slot-label":"tag", "config-name":"study", "config-description":"text", "config-status":"door",
  "scenario-consent_text":"shield", "config-telemetry":"activity", "config-task":"scenario",
  "case-study-select":"study", "scenario-title":"tag", "neg-title":"tag",
  "scenario-instructions":"scenario", "neg-instructions":"scenario",
  "scenario-knowledge":"study", "neg-knowledge":"study", "neg-item":"box", "neg-quantity":"hash",
  "neg-currency":"coin", "neg-participant_role":"person", "neg-price_min":"coin", "neg-price_max":"coin",
  "neg-opening_price":"coin", "neg-concession_step":"coin", "config-assignment":"branch",
  "cond-label":"tag", "cond-agent_enabled":"spark", "cond-role":"person", "cond-agent-mode":"target",
  "cond-authority":"target", "cond-objective":"target", "cond-id":"hash", "cond-prompt":"chat",
  "cond-output":"text", "cond-private":"lock", "cond-price-min":"lock", "cond-price-max":"lock",
  "agent-tool_timeout_seconds":"clock", "agent-turn_timeout_seconds":"clock", "agent-data-access":"lock",
  "config-provider":"bolt", "config-model":"chip", "config-memory":"memory", "config-knowledge":"tag",
  "config-spend_cap_usd":"coin", "config-session_spend_usd":"coin",
  "config-input_usd_per_million":"coin", "config-output_usd_per_million":"coin",
};
function setupIcon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><use href="#setup-icon-${name}"/></svg>`;
}
function setupChoiceText(select) {
  const option = select.selectedOptions[0];
  return option ? option.dataset.choiceLabel || option.text : "";
}
function enhanceSetupChoice(select) {
  if (!canStyleSetupChoices) return;
  select.classList.add("setup-choice");
  if (!select.firstElementChild?.matches("button")) {
    // Replacing the option list also removes the optional selected-content button.
    const button = document.createElement("button");
    button.type = "button";
    button.append(document.createElement("selectedcontent"));
    select.prepend(button);
  }
  for (const option of select.options) {
    if (option.dataset.choiceLabel) continue;
    const value = option.value;
    const title = option.text;
    let [glyph, detail] = setupChoices[select.id]?.[value] ||
      [select.matches("[data-criterion-direction]") ? "chart" : "sliders"];
    if (select.id === "case-study-select") {
      const pack = value === "" ? null : caseStudies[Number(value)];
      glyph = pack ? caseIcons[pack.id] || "scenario" : "edit";
      if (pack) detail = "Start with this case’s scenario and groups.";
    }
    option.value = value;
    option.dataset.choiceLabel = title;
    option.innerHTML = `<span class="setup-choice-icon" aria-hidden="true">${setupIcon(glyph)}</span><span class="setup-choice-copy"><span class="setup-choice-title">${esc(title)}</span>${detail ? `<small aria-hidden="true">${esc(detail)}</small>` : ""}</span>`;
  }
}
function enhanceSetupFields(root) {
  for (const label of root.querySelectorAll("label")) {
    const control = label.control;
    if (!control || label.dataset.hasFieldIcon) continue;
    const text = [...label.childNodes].find(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
    if (!text) continue;
    const name = setupFieldIcons[control.id] ||
      (control.matches("textarea") ? "text" : control.matches("input[type=number]") ? "hash" :
        control.matches("input[type=checkbox]") ? "check" : control.matches("select") ? "sliders" : "tag");
    const line = document.createElement("span");
    line.className = "setup-label-line";
    line.innerHTML = setupIcon(name);
    text.replaceWith(line);
    line.append(text);
    label.dataset.hasFieldIcon = "true";
  }
  for (const select of root.querySelectorAll("select")) enhanceSetupChoice(select);
}

const defaultSetupModel = "gpt-4.1-mini-2025-04-14";
const negotiationNumbers=new Set(['quantity','price_min','price_max','opening_price','concession_step']);
function readNumber(id){const input=$(id), raw=input.value.trim();if(!raw||!Number.isFinite(Number(raw)))throw new Error('Enter a finite number for '+input.labels[0].textContent.trim());return Number(raw);}
function renderTaskFields(){
  const packed=Boolean(draft?.case_study);
  $("task-workflow-description").textContent = draft?.case_study?.id === "ozer_game" ?
    "Two counterpart roles. Each session has 3 practice rounds and 20 paid rounds." :
    (packed ? "The case provides the task and data. Preview the participant brief below." : "Set the task, instructions, and information participants will see.");
  $('config-task').disabled=packed;
  $('config-task').closest('label').classList.toggle('hidden',packed);
  $("case-study-status").innerHTML = packed ? setupIcon("scenario") + " Case template: " + esc(slotCaseTitle(draft)) : "";
  $("case-study-status").classList.toggle("hidden", !packed);
  $('selection-settings').classList.toggle('hidden',packed||$('config-task').value!=='supplier_selection');
  $('negotiation-settings').classList.toggle('hidden',packed||$('config-task').value!=='negotiation');
  $('private-price-settings').classList.toggle('hidden',$('config-task').value!=='negotiation'&&!$('cond-price-min').value&&!$('cond-price-max').value);
  renderScenarioExample();
}
function renderScenarioExample() {
  const pack = caseStudies.find(item => item.id === draft?.case_study?.id && item.version === draft.case_study.version);
  const prefix = $("config-task").value === "negotiation" ? "neg" : "scenario";
  $("scenario-example-title").textContent = (pack ? pack.title : $(prefix+"-title").value.trim()) || "Untitled scenario";
  $("scenario-example-text").textContent = (pack ? pack.instructions : $(prefix+"-instructions").value.trim()) || "Add participant instructions above.";
}
for (const id of ["neg-title", "neg-instructions", "scenario-title", "scenario-instructions"]) {
  $(id).addEventListener("input", renderScenarioExample);
}
function renderBudget(){if(!$('budget-status'))return;const budget=overview?.budget||{};$('budget-status').textContent=(budget.warning_80_percent?'Warning: at least 80% of the global allowance is allocated. ':'')+'$'+((budget.committed_micros||0)/1e6).toFixed(4)+' spent/accounted · $'+((budget.reserved_session_micros||0)/1e6).toFixed(4)+' reserved for active sessions.';}


async function api(path, body) {
  let response;
  try {
    response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers: {"Content-Type":"application/json","X-Research-Request":"1"}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  } catch {
    throw new Error("Could not reach the workspace. Check the connection and try again.");
  }
  let result;
  try { result = await response.json(); }
  catch { throw Object.assign(new Error("The server returned an unreadable response. Try again shortly."), {status:response.status}); }
  if (!response.ok) throw Object.assign(new Error(result.error || "Request failed"), {status:response.status});
  return result;
}
function notice(message, error = false) {
  clearTimeout(noticeTimer);
  $("notice").textContent = message;
  $("notice").className = "notice" + (error ? " error" : "");
  $("notice").scrollIntoView({block: "nearest", behavior:"instant"});
  if (!error) noticeTimer = setTimeout(() => $("notice").classList.add("hidden"),6000);
}
function guarded(fn) { return async event => { try { await fn(event); } catch (error) { notice(error.message, true); } }; }
const pageNames = {overview:"Overview",conditions:"Agent setup",sessions:"Sessions","session-chat":"Session chat","setup-chat":"Setup chat",integration:"Share a session",settings:"Settings"};
const isSessionChat = tab => tab === "session-chat" || tab.startsWith("session-chat/");
const isSetupChat = tab => tab === "setup-chat" || tab.startsWith("setup-chat/");
let activeTab = "overview", navigationPending = false;
const narrowWorkspace = matchMedia("(max-width: 850px)");
let navigationPreference = "open";

function setNavigationOpen(open) {
  const toggle = $("toggle-sidebar");
  const label = open ? "Hide navigation" : "Show navigation";
  const panel = $("workspace-sidebar");
  panel.classList.toggle("is-open", open);
  panel.inert = !open;
  $("nav-scrim").hidden = !open || !narrowWorkspace.matches;
  $("workspace").classList.toggle("navigation-closed", !open);
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", label);
  toggle.title = label;
}

function applyNavigationPreference() {
  setNavigationOpen(!narrowWorkspace.matches && navigationPreference !== "closed");
}
applyNavigationPreference();
narrowWorkspace.addEventListener("change", applyNavigationPreference);
$("toggle-sidebar").onclick = async () => {
  const open = !$("workspace-sidebar").classList.contains("is-open");
  if (narrowWorkspace.matches) {
    setNavigationOpen(open);
    return;
  }
  const button = $("toggle-sidebar");
  const field = $("account-sidebar");
  button.disabled = field.disabled = true;
  try {
    await saveAccountPreference("sidebar", open ? "open" : "closed");
    field.value = researcher.sidebar;
  } catch (err) { notice(err.message, true); }
  finally { button.disabled = field.disabled = false; }
};
$("nav-scrim").onclick = () => {
  setNavigationOpen(false);
  $("toggle-sidebar").focus();
};
const setupLibraryDrawer = $("setup-library-drawer");
$("toggle-save-slots").onclick = () => {
  setupLibraryDrawer.append($("setup-library"));
  setupLibraryDrawer.showModal();
  $("setup-search").focus();
};
setupLibraryDrawer.addEventListener("close", () => {
  if (!setupLibraryDrawer.open) $("setup-library-home").append($("setup-library"));
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape" || event.defaultPrevented || document.querySelector("dialog[open]")) return;
  if (narrowWorkspace.matches && $("workspace-sidebar").classList.contains("is-open")) {
    event.preventDefault();
    setNavigationOpen(false);
    $("toggle-sidebar").focus();
  }
});

async function switchTab(tab, updateHistory = true) {
  if (!tab.startsWith("sessions/") && !isSessionChat(tab) && !isSetupChat(tab) && !(tab in pageNames)) tab = "overview";
  if (tab === activeTab) return true;
  const restoreAddress = () => {
    if (!updateHistory) history.replaceState(null, "", "#" + activeTab);
  };
  if (slotBusy || setupCheckBusy || navigationPending || sessionChatBusy || setupChatBusy || $("leave-draft-dialog").open) {
    restoreAddress();
    return false;
  }
  navigationPending = true;
  try {
    if (activeTab === "conditions") {
      if (!await leaveEditor()) {
        restoreAddress();
        return false;
      }
      if (builderOpen()) setBuilderOpen(false, false, false);
    }
    // A history change can be interrupted by another navigation while the dialog is open.
    if (!updateHistory && location.hash.slice(1) !== tab && (location.hash || tab !== "overview")) {
      history.replaceState(null, "", "#" + tab);
    }
    renderTab(tab, updateHistory);
    return true;
  } finally {
    navigationPending = false;
  }
}

function renderTab(tab, updateHistory = true) {
  const isDetail = tab.startsWith("sessions/");
  const sessionChatOpen = isSessionChat(tab);
  const setupChatOpen = isSetupChat(tab);
  if (!isDetail && !sessionChatOpen && !setupChatOpen && !(tab in pageNames)) tab = "overview";
  activeTab = tab;
  if (updateHistory && location.hash !== "#" + tab) history.pushState(null,"","#"+tab);
  const panel = isDetail ? "session-detail" : sessionChatOpen ? "session-chat" : setupChatOpen ? "setup-chat" : tab;
  $("workspace").classList.toggle("session-chat-open", sessionChatOpen || setupChatOpen);
  $("clear-session-chat").classList.toggle("hidden", !sessionChatOpen);
  $("new-setup-chat").classList.toggle("hidden", !setupChatOpen);
  $("chat-history").classList.toggle("hidden", !sessionChatOpen);
  $("setup-chat-history").classList.toggle("hidden", !setupChatOpen);
  $("session-chat-actions").classList.add("hidden");
  $("session-chat-actions").open = false;
  $("toggle-save-slots").classList.toggle("hidden", panel !== "conditions" || !$("setup-home").classList.contains("hidden"));
  if (narrowWorkspace.matches && updateHistory) {
    if ($("workspace-sidebar").contains(document.activeElement)) $("toggle-sidebar").focus();
    setNavigationOpen(false);
  }
  const previousFocusPanel = document.activeElement.closest(".tab-panel");
  document.querySelectorAll(".tab-panel").forEach(el => el.classList.toggle("hidden", el.id !== "tab-" + panel));
  if (previousFocusPanel?.classList.contains("hidden")) {
    const heading = [...$("tab-" + panel).querySelectorAll("h1")].find(el => !el.closest(".hidden"));
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({preventScroll:true});
    }
  }
  document.querySelectorAll("[data-tab]").forEach(el => {const active=el.dataset.tab===(isDetail ? "sessions" : panel);el.classList.toggle("active",active);if(active)el.setAttribute("aria-current","page");else el.removeAttribute("aria-current");});
  const title = isDetail ? "Session review" : pageNames[panel];
  document.title=title+" · Research Agent";
  $("breadcrumb").textContent = title;
  window.scrollTo({top:0,behavior:"instant"});
  if (isDetail) { currentDetailId = tab.slice(9); loadSessionDetail(currentDetailId); }
  else { currentDetailId=null; detailLoad++; }
  if (panel === "sessions" && updateHistory) guarded(refresh)();
  if (sessionChatOpen) {
    loadChat(tab === "session-chat" ? null : tab.slice("session-chat/".length));
    refreshChatHistory();
  }
  if (setupChatOpen) {
    loadSetupChat(tab === "setup-chat" ? null : tab.slice("setup-chat/".length));
    refreshSetupChatHistory();
  } else setupChatLoad++;
  if (panel === "settings") {
    refreshSettings();
    refreshResearchers();
  }
}
const sessionStatusLabels = {
  active:"In progress", completed:"Completed", withdrawn:"Stopped by participant",
  revoked:"Access revoked", ended_system_limit:"System limit",
};
const sessionCaseTones = {
  supplier_selection:"blue", negotiation:"plum", demand_forecasting:"mint",
  supplier_risk:"rose", disruption_response:"amber", ozer_game:"aqua",
};
function sessionMenu(session) {
  const id = esc(session.id);
  const item = (action, label, danger = false) =>
    `<button type="button" data-session-action="${action}" data-session-id="${id}"${danger ? ' class="danger"' : ""}>${label}</button>`;
  return `<details class="chat-actions session-options">
    <summary class="quiet-button" aria-label="Session actions for ${esc(session.label || session.participant_id)}" title="Session actions">
      <span class="session-options-more" aria-hidden="true">•••</span><span class="session-options-close" aria-hidden="true">×</span>
    </summary>
    <div>${item("edit", "Edit label &amp; note")}
    <a href="/api/admin/session?session_id=${encodeURIComponent(session.id)}&amp;download=1" download>Download session</a>
    ${session.status === "active" ? item("revoke-session", "Revoke access", true) : item("delete-session", "Delete session", true)}</div>
  </details>`;
}
const actionNames = {ozer_round:"Round result recorded",autonomous:"Agent committed automatically",accept:"Accepted the agent’s choice",override:"Overrode the agent’s choice",reject:"Finished without a choice",independent:"Chose independently"};
function dateTime(value) { return value == null ? "Not recorded" : new Date(value*1000).toLocaleString(undefined,{month:"short",day:"numeric",year:"numeric",hour:"numeric",minute:"2-digit"}); }
function duration(ms) { if(ms==null)return "Unavailable"; if(ms<1000)return Math.round(ms)+" ms";if(ms<60000)return (ms/1000).toFixed(1)+" sec";const secs=Math.round(ms/1000);return Math.floor(secs/60)+" min "+secs%60+" sec"; }
function sessionRows(rows, compact = false) {
  if (!rows.length) return '<div class="empty-state"><div class="empty-symbol">○</div><h3>No sessions yet.</h3><p>Run a preview to see the conversation, decisions, and activity here.</p></div>';
  return '<div class="session-list'+(compact ? ' compact-session-list' : '')+'">'+rows.map(s=>{
    const status = sessionStatusLabels[s.status] || s.status;
    const outcome = s.task === "ozer_game" ? (s.status === "active" ? "Round " + s.round_id + " of 23" : "") :
      s.final_price != null ? s.currency + " " + s.final_price.toFixed(2) + " / unit" :
      s.final_option_label || (s.action === "reject" ? "No choice recorded" : "");
    return `<div class="session-list-item"><a class="session-row" data-status="${esc(s.status)}" href="#sessions/${encodeURIComponent(s.id)}"><span class="session-avatar glyph-${sessionCaseTones[s.task] || "blue"}" aria-hidden="true">${setupIcon(caseIcons[s.task] || "study")}</span><span class="session-identity"><strong>${esc(s.label || s.participant_id)}</strong><span>${s.label ? esc(s.participant_id) + " · " : ""}${esc(s.condition_label || s.condition_id)}${s.preview ? '<em>Preview</em>' : ''}${s.edit_id ? '<em class="session-edited">Edited</em>' : ''}</span></span><span class="session-result"><span class="session-row-status">${esc(status)}</span>${outcome ? `<span class="session-row-outcome">${esc(outcome)}</span>` : ""}</span><span class="session-date">${esc(dateTime(s.created_at))}</span><span class="session-open" aria-hidden="true">↗</span></a>${sessionMenu(s)}</div>`;
  }).join("")+'</div>';
}

let sessionChatBusy = false, chatLoading = false, currentChat = null, chatLoadVersion = 0;
let chatHistoryVersion = 0, savedChats = [], pendingTurn = null, chatAction = null;
const chatScopes = {study:"Study sessions", preview:"Preview sessions", all:"All recorded sessions"};
const chatStatuses = {...sessionStatusLabels, not_completed:"Not completed"};

function updateSessionChatControls() {
  const configured = health?.openai_configured;
  const field = $("session-chat-question");
  const hasMessages = Boolean($("session-chat-log").childElementCount);
  const unavailable = activeTab.startsWith("session-chat/") && !currentChat;
  field.disabled = sessionChatBusy || chatLoading || unavailable || !configured;
  $("send-session-question").disabled = field.disabled || !field.value.trim();
  $("session-chat-scope").disabled = sessionChatBusy || chatLoading;
  $("clear-session-chat").disabled = sessionChatBusy ||
    (!chatLoading && activeTab === "session-chat" && !hasMessages && !field.value && $("session-chat-error").classList.contains("hidden"));
  $("session-chat-empty").classList.toggle("hidden", hasMessages || chatLoading);
  $("session-chat-actions").classList.toggle("hidden", !currentChat || !isSessionChat(activeTab) || sessionChatBusy);
  const menu = $("session-chat-quick-menu");
  const useMenu = hasMessages && configured;
  menu.classList.toggle("hidden", !useMenu);
  const suggestions = $("session-chat-suggestions");
  const suggestionsParent = useMenu ? menu : $("session-chat-starters");
  if (suggestions.parentElement !== suggestionsParent) {
    menu.open = false;
    suggestionsParent.append(suggestions);
  }
  suggestions.classList.toggle("hidden", chatLoading);
  document.querySelectorAll("[data-chat-query]").forEach(button => button.disabled = sessionChatBusy || chatLoading || unavailable);
  $("session-chat-help").textContent = configured ?
    "OpenAI interprets typed questions. Session records are queried locally." :
    "Use the suggested queries now, or configure OpenAI on the server to type your own questions.";
}

function renderSessionAnswer(answer, result, chatId, requestId) {
  const {query, total, rows, groups, next_offset:nextOffset} = result;
  if (query.view === "unsupported") {
    answer.textContent = "I can look up session status, completion counts, study groups, and participant codes. Open a session to review its conversation or decisions.";
    return;
  }
  const filters = [chatScopes[query.scope], chatStatuses[query.status], query.study && "Study: " + query.study,
    query.condition && "Group: " + query.condition, query.participant && "Participant: " + query.participant].filter(Boolean);
  const headline = total === 0 ? "No matching sessions." : total === 1 ? "1 matching session" : total + " matching sessions";
  answer.innerHTML = `<p class="session-chat-result-title"><strong>${headline}</strong></p>
    <p class="help">${esc(filters.join(" · "))}</p>` +
    (rows.length ? `<ul class="session-chat-results">${rows.map(row => `<li>
      <a href="#sessions/${encodeURIComponent(row.id)}">
        <span><strong>${esc(row.participant_id)}</strong><small>${esc(row.condition_label || row.condition_id)}${row.preview ? " · Preview" : ""}</small></span>
        <span class="session-chat-result-status">${esc(sessionStatusLabels[row.status] || row.status)}<span aria-hidden="true">↗</span></span>
      </a>
    </li>`).join("")}</ul>` : "") +
    (groups.length ? `<ul class="session-chat-results session-chat-groups">${groups.map(group => `<li>
      <strong>${esc(query.view === "by_status" ? sessionStatusLabels[group.label] || group.label : group.label)}</strong>
      <span>${query.view === "by_status" ? esc(group.count) : esc(group.completed + " of " + group.count + " completed")}</span>
    </li>`).join("")}</ul>` : "");
  if (nextOffset === null) return;
  const more = document.createElement("button");
  more.type = "button";
  more.className = "secondary";
  more.textContent = "Show more";
  more.onclick = async () => {
    more.disabled = true;
    try {
      const page = await api("/api/admin/chats/page", {chat_id: chatId, request_id: requestId, offset: nextOffset});
      if (!answer.isConnected) return;
      const thread = $("session-chat-thread");
      const scrollTop = thread.scrollTop;
      renderSessionAnswer(answer, {...page, rows: [...rows, ...page.rows], groups: [...groups, ...page.groups]}, chatId, requestId);
      thread.scrollTop = scrollTop;
      answer.focus({preventScroll:true});
    } catch (err) {
      if (!answer.isConnected) return;
      $("session-chat-error").textContent = err.message;
      $("session-chat-error").classList.remove("hidden");
    } finally { more.disabled = false; }
  };
  answer.append(more);
}

function chatPrompt(question) {
  const prompt = document.createElement("p");
  prompt.className = "session-chat-prompt";
  prompt.textContent = question;
  return prompt;
}

function chatAnswer(turn, chatId) {
  const answer = document.createElement("div");
  answer.className = "session-chat-answer";
  answer.tabIndex = -1;
  renderSessionAnswer(answer, turn.result, chatId, turn.request_id);
  return answer;
}

function renderChatHistory() {
  const list = $("chat-history-list");
  const links = savedChats.map(chat => {
    const link = document.createElement("a");
    link.href = "#session-chat/" + encodeURIComponent(chat.id);
    link.textContent = chat.title;
    link.title = chat.title;
    if (chat.id === currentChat?.id) link.setAttribute("aria-current", "page");
    return link;
  });
  list.replaceChildren(...links);
  $("chat-history-status").textContent = links.length ? "" : "Your chats will appear here.";
}

async function refreshChatHistory() {
  const request = ++chatHistoryVersion;
  try {
    const chats = await api("/api/admin/chats");
    if (request !== chatHistoryVersion) return;
    savedChats = chats;
    renderChatHistory();
  } catch (err) {
    if (request === chatHistoryVersion) $("chat-history-status").textContent = err.message;
  }
}

async function loadChat(chatId) {
  const request = ++chatLoadVersion;
  const thread = $("session-chat-thread");
  const status = $("session-chat-status");
  const log = $("session-chat-log");
  const error = $("session-chat-error");
  currentChat = null;
  pendingTurn = null;
  chatLoading = Boolean(chatId);
  log.replaceChildren();
  thread.scrollTop = 0;
  $("session-chat-question").value = "";
  $("session-chat-scope").value = researcher.chat_scope;
  error.classList.add("hidden");
  status.textContent = "Opening chat…";
  status.classList.toggle("hidden", !chatLoading);
  updateSessionChatControls();
  renderChatHistory();
  if (!chatId) return;
  try {
    const {chat, turns} = await api("/api/admin/chats?id=" + encodeURIComponent(chatId));
    if (request !== chatLoadVersion || activeTab !== "session-chat/" + chatId) return;
    currentChat = chat;
    log.replaceChildren(...turns.flatMap(turn => [chatPrompt(turn.question), chatAnswer(turn, chat.id)]));
    if (turns.length) $("session-chat-scope").value = turns.at(-1).scope;
    renderChatHistory();
  } catch (err) {
    if (request !== chatLoadVersion || activeTab !== "session-chat/" + chatId) return;
    error.textContent = err.message;
    error.classList.remove("hidden");
  } finally {
    if (request === chatLoadVersion) {
      chatLoading = false;
      status.classList.add("hidden");
      updateSessionChatControls();
      thread.scrollTop = thread.scrollHeight;
    }
  }
}

async function askAboutSessions(question, query) {
  if (sessionChatBusy || chatLoading || !question || (activeTab.startsWith("session-chat/") && !currentChat)) return;
  const error = $("session-chat-error");
  const log = $("session-chat-log");
  const thread = $("session-chat-thread");
  const field = $("session-chat-question");
  const scope = $("session-chat-scope").value;
  $("session-chat-quick-menu").open = false;
  const prompt = chatPrompt(question);
  log.append(prompt);
  const draftQuestion = field.value;
  if (!query) field.value = "";
  error.classList.add("hidden");
  sessionChatBusy = true;
  updateSessionChatControls();
  const status = $("session-chat-status");
  status.textContent = "Looking up sessions…";
  status.classList.remove("hidden");
  if (isSessionChat(activeTab)) thread.scrollTop = thread.scrollHeight;
  try {
    const body = {chat_id: currentChat?.id ?? null, question, scope,
      ...(query ? {query: {...query, scope, condition:"", study:"", participant:""}} : {})};
    const signature = JSON.stringify(body);
    if (pendingTurn?.signature !== signature) pendingTurn = {signature, id: crypto.randomUUID()};
    const {chat, turn} = await api("/api/admin/chats/ask", {...body, request_id: pendingTurn.id});
    pendingTurn = null;
    currentChat = chat;
    if (activeTab === "session-chat") {
      activeTab = "session-chat/" + chat.id;
      history.replaceState(null, "", "#" + activeTab);
    }
    log.append(chatAnswer(turn, chat.id));
    refreshChatHistory();
    status.classList.add("hidden");
    if (isSessionChat(activeTab)) {
      thread.scrollTop += prompt.getBoundingClientRect().top - thread.getBoundingClientRect().top - 20;
    }
  } catch (err) {
    prompt.remove();
    if (!query) field.value = draftQuestion;
    error.textContent = err.message;
    error.classList.remove("hidden");
  } finally {
    sessionChatBusy = false;
    status.classList.add("hidden");
    updateSessionChatControls();
    if (isSessionChat(activeTab) && !field.disabled) field.focus({preventScroll:true});
  }
}

$("session-chat-form").onsubmit = event => {
  event.preventDefault();
  askAboutSessions($("session-chat-question").value.trim());
};
$("session-chat-question").oninput = updateSessionChatControls;
$("session-chat-question").onkeydown = event => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (!$("send-session-question").disabled) $("session-chat-form").requestSubmit();
};
document.querySelectorAll("[data-chat-query]").forEach(button => {
  button.onclick = () => askAboutSessions(button.textContent, {view: button.dataset.chatQuery, status: button.dataset.chatStatus});
});
$("clear-session-chat").onclick = async () => {
  if (activeTab === "session-chat") loadChat(null);
  else if (!await switchTab("session-chat")) return;
  const field = $("session-chat-question");
  (field.disabled ? $("session-chat-scope") : field).focus();
};

function openChatAction(action) {
  if (!currentChat) return;
  chatAction = {action, chat: currentChat};
  const menu = $("session-chat-actions");
  menu.open = false;
  menu.querySelector("summary").focus({preventScroll:true});
  const rename = action === "rename";
  $("manage-chat-title").textContent = rename ? "Rename chat" : "Delete chat?";
  $("manage-chat-description").textContent = rename ? "" : "Delete “" + currentChat.title + "” and its conversation history?";
  $("chat-title-field").classList.toggle("hidden", !rename);
  $("chat-title-input").required = rename;
  $("chat-title-input").value = rename ? currentChat.title : "";
  $("manage-chat-submit").textContent = rename ? "Save title" : "Delete chat";
  $("manage-chat-error").classList.add("hidden");
  $("manage-chat-dialog").showModal();
  if (rename) $("chat-title-input").select();
}
$("rename-chat").onclick = () => openChatAction("rename");
$("delete-chat").onclick = () => openChatAction("delete");
$("manage-chat-form").onsubmit = async event => {
  event.preventDefault();
  const {action, chat} = chatAction;
  const submit = $("manage-chat-submit");
  const dialog = $("manage-chat-dialog");
  submit.disabled = true;
  dialog.dataset.busy = "true";
  try {
    await api("/api/admin/chats/" + action, {chat_id: chat.id, title: $("chat-title-input").value});
    dialog.close();
    if (action === "delete") {
      history.replaceState(null, "", "#session-chat");
      renderTab("session-chat", false);
    } else {
      currentChat.title = $("chat-title-input").value.trim();
      refreshChatHistory();
    }
  } catch (err) {
    $("manage-chat-error").textContent = err.message;
    $("manage-chat-error").classList.remove("hidden");
  } finally {
    submit.disabled = false;
    delete dialog.dataset.busy;
  }
};

async function refresh() {
  [overview, pendingLinks] = await Promise.all([api("/api/admin/overview"), api("/api/admin/pending-launches")]);
  $("metric-sessions").textContent = overview.totals.sessions || 0;
  $("metric-completed").textContent = overview.totals.completed || 0;
  $("recent-sessions").innerHTML = sessionRows(overview.sessions.slice(0, 4),true);
  renderSessionTable(); renderPendingLinks(); renderBudget();
}
function renderSessionTable() {
  if(!overview) return;
  const q=$("session-search").value.trim().toLowerCase();
  const filter=$("session-filter").value;
  const rows=overview.sessions.filter(s=>[s.participant_id,s.label,s.condition_id,s.condition_label,s.action,s.status,s.final_option,s.final_option_label,s.final_price,s.task].some(v=>String(v||"").toLowerCase().includes(q))).filter(s=>filter==="all"||filter==="preview"&&s.preview||filter==="study"&&!s.preview||s.status===filter);
  $("session-result-count").textContent=rows.length+" shown" + (overview.totals.sessions > overview.sessions.length ? " · latest 1,000 sessions" : "");
  $("all-sessions").innerHTML=rows.length || (!q && $("session-filter").value==="all") ? sessionRows(rows) : '<div class="empty-state"><h3>No matching sessions.</h3><p>Try another search or session filter.</p></div>';
}
function renderPendingLinks() {
  $("unused-link-count").textContent = pendingLinks.length || "";
  $("unused-links").innerHTML = pendingLinks.length ? `<p class="help">Links disappear here after they are used, revoked, or expire.</p><ul class="pending-links">${pendingLinks.map(link =>
    `<li><div><strong>${esc(link.participant_id)}</strong><small>${link.preview ? "Preview · " : ""}Expires ${esc(dateTime(link.expires_at))}</small></div><button type="button" class="secondary" data-revoke-link="${esc(link.id)}">Revoke link</button></li>`).join("")}</ul>` :
    '<div class="empty-state"><h3>No unused links.</h3><p>New participant links appear here until they are used or expire.</p></div>';
}
document.querySelectorAll("[data-session-view]").forEach(button => button.onclick = guarded(async () => {
  const history = button.dataset.sessionView === "history";
  for (const item of document.querySelectorAll("[data-session-view]")) {
    item.classList.toggle("active", item === button);
    item.setAttribute("aria-pressed", String(item === button));
  }
  $("session-history-view").classList.toggle("hidden", !history);
  $("session-links-view").classList.toggle("hidden", history);
  await refresh();
}));
function renderSummary() {
  const c = snapshot.config;
  $("study-name").textContent = c.name;
  $("study-description").textContent = c.description;
  $("study-state").textContent = c.status;
  $("launch-assignment").value=c.assignment.method;updateAssignment();
  $("metric-conditions").textContent = c.conditions.length;
  $("launch-condition").innerHTML = c.conditions.map(cond => `<option value="${esc(cond.id)}">${esc(cond.label)} (${esc(cond.id)})</option>`).join("");
}
function providerLabel(config) {
  const provider = config.architecture.provider;
  if (provider === "relay") return "Provider setup needed";
  if (provider === "demo") return "Demo · no key needed";
  return health.openai_configured ? "OpenAI · key configured" : "OpenAI · key needed";
}
function conditionDescription(cond, config) {
  if (!cond.agent_enabled) return "Complete the task without an agent.";
  if (config.case_study?.id === "ozer_game") return cond.role === "retailer" ?
    "Choose production after seeing the AI retailer’s forecast." : "Report a forecast to the AI supplier.";
  if (cond.agent) return {
    analytical: "Get analysis and numbers without a suggested action.",
    chatbot: "Discuss ideas with a read-only assistant.",
    recommender: "Accept or reject the AI’s suggestion.",
    autonomous: "See the simulated decision the AI makes.",
  }[cond.agent.mode];
  return {
    recommendation: "Get a suggestion, then make your own choice.",
    provisional_decision: "Review the AI’s choice and change it if needed.",
    chat: "Discuss the task without an AI decision.",
  }[cond.authority];
}

function updatePreviewMode() {
  const config = previewSlot?.config || snapshot.config;
  const conditionId = $("preview-form").elements["preview-condition"].value;
  const group = config.conditions.find(item => item.id === conditionId);
  const usesPrivatePolicy = !config.case_study && group && (
    Object.keys(group.private_information).length > 0 ||
    Object.values(group.private_rules).some(bounds => Object.keys(bounds).length > 0)
  );
  const live = $("preview-live");
  const provider = config.architecture.provider;
  const ozer = config.case_study?.id === "ozer_game";
  const supportsSupplierLive = !config.case_study && config.task === "supplier_selection" &&
    group?.agent_enabled && !usesPrivatePolicy && group.output !== "number" &&
    group.advice_quality === "standard" &&
    (provider !== "openai" || group.selection !== "model");
  const supportsLive = health.openai_configured && Boolean(group) &&
    (supportsSupplierLive || (ozer && provider === "openai"));
  live.disabled = !supportsLive;
  $("preview-live-setting").classList.toggle("hidden", !supportsLive);
  const useLive = supportsLive && live.checked;
  $("start-preview").disabled = previewBusy || !group || (!useLive && (provider === "relay" ||
    (provider === "openai" && group.agent_enabled && !usesPrivatePolicy && !ozer && !health.openai_configured)));
  $("preview-provider").textContent = useLive ? (ozer ? `Live OpenAI · ${config.architecture.model} · uses this setup’s role instructions and API budget` :
    "Live OpenAI · uses this setup’s supplier data and your question") :
    ozer ? "Built-in Ozer counterpart · no API calls" :
    provider === "relay" ? "Choose live OpenAI or update this setup’s response provider." :
    !group?.agent_enabled ? "Human-only · assistant off" :
    usesPrivatePolicy ? "Private policy · fixed responses" :
    ozer || provider === "demo" ? "Fixed responses · no language model" : providerLabel(config);
}

function showPreview(slot = null, preferredCondition = null) {
  const c = slot?.config || snapshot.config;
  previewSlot = slot;
  const conditionId = preferredCondition ?? (slot ? c.conditions[selected]?.id : null) ?? c.conditions[0].id;
  $("preview-conditions").innerHTML = c.conditions.map((cond,i) => `<label class="condition-choice"><input type="radio" name="preview-condition" value="${esc(cond.id)}" ${cond.id===conditionId ? "checked" : ""}><span><strong>${esc(cond.label)}</strong><small>${esc(conditionDescription(cond, c))}</small></span><span class="condition-choice-check" aria-hidden="true">✓</span></label>`).join("");
  // Setup tests start with the saved provider; live preview remains an explicit override.
  $("preview-live").checked = !slot && c.architecture.provider !== "openai";
  $("preview-error").classList.add("hidden");
  updatePreviewMode();
  $("preview-setup-description").textContent = slot ? "Preview “" + slot.label + "”." :
    "Preview the setup currently used for the study.";
  $("preview-dialog").showModal();
}
$("preview-form").onchange = () => {
  $("preview-error").classList.add("hidden");
  updatePreviewMode();
};
function conditionMode(cond, config = draft) {
  if (!cond.agent_enabled) return "Human-only";
  if (config.case_study?.id === "ozer_game") return "AI " + cond.role;
  return conditionModeLabels[cond.agent?.mode || cond.authority];
}
const conditionFields = ["id","label","role","authority","reasoning","output","selection","objective","prompt","advice_quality"];
function renderEditor() {
  const reorderable = draft.case_study?.id !== "ozer_game" && draft.conditions.length > 1;
  $("condition-tabs").innerHTML = draft.conditions.map((cond, i) => `<button class="condition-tab ${selected === i ? "active" : ""}" data-condition="${i}" aria-pressed="${selected===i}" ${reorderable ? 'draggable="true"' : ''}><strong>${esc(cond.label)}</strong><small ${cond.label.toLowerCase() === conditionMode(cond)?.toLowerCase() ? 'class="hidden"' : ""}>${esc(conditionMode(cond))}</small></button>`).join("");
  const cond = draft.conditions[selected];
  const roleSelect=$('cond-role');
  [...roleSelect.querySelectorAll('[data-custom-role]')].forEach(o=>o.remove());
  if(!['buyer','supplier','genai_tool'].includes(cond.role)){const option=document.createElement('option');option.value=cond.role;option.textContent=cond.role;option.dataset.customRole='true';roleSelect.append(option);}
  for (const k of conditionFields) $("cond-" + k).value = cond[k];
  const ozer = draft.case_study?.id === "ozer_game";
  for (const key of ["id", "role", "reasoning", "output", "agent_enabled"]) $("cond-"+key).disabled = ozer;
  $('agent-layer-settings').classList.toggle('hidden', !draft.case_study || ozer);
  $('cond-authority').closest('label').classList.toggle('hidden',Boolean(draft.case_study));
  $('cond-selection').closest('label').classList.toggle('hidden',Boolean(draft.case_study));
  if(cond.agent){
    $('cond-agent-mode').value=cond.agent.mode;
    for(const key of ['max_tool_calls','tool_timeout_seconds','turn_timeout_seconds'])$('agent-'+key).value=cond.agent[key];
    $('agent-data-access').value=JSON.stringify(cond.agent.data_access,null,2);
    $('agent-tool-fields').innerHTML=(caseStudies.find(p=>p.id===draft.case_study.id&&p.version===draft.case_study.version)?.allowed_tools || cond.agent.allowed_tools).map(name=>`<label class="checkbox-label"><input type="checkbox" data-agent-tool="${esc(name)}" ${cond.agent.allowed_tools.includes(name)?'checked':''}>${esc(name.replaceAll('_',' '))}</label>`).join('');
  }
  $('cond-agent_enabled').value=String(cond.agent_enabled);
  $('cond-price-min').value=cond.private_rules.min.price ?? '';
  $('cond-price-max').value=cond.private_rules.max.price ?? '';
  $("cond-private").value = JSON.stringify(cond.private_information, null, 2);
  $("cond-rules").value = JSON.stringify(cond.private_rules, null, 2);
  $("cond-examples").value = JSON.stringify(cond.examples, null, 2);
  renderTaskFields();
  $("remove-condition").disabled = draft.conditions.length < 2;
  $("remove-condition").closest(".condition-actions").classList.toggle("hidden", ozer);
  $("add-condition").classList.toggle("hidden", ozer);
  $("fixed-conditions-note").classList.toggle("hidden", !ozer);
  $("add-condition").disabled = ozer;
  $("cond-agent_enabled").closest(".setup-field").classList.toggle("hidden", ozer);
  enhanceSetupChoice(roleSelect);
  enhanceSetupFields($("agent-tool-fields"));
  renderConditionPresentation();
  invalidateSetupCheck();
  document.querySelectorAll("[data-condition]").forEach(el => el.onclick = guarded(() => {
    const next = Number(el.dataset.condition);
    if (selected === next) return;
    collect();
    selected = next;
    renderEditor();
    $("condition-tabs").querySelector(`[data-condition="${selected}"]`).focus();
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const editor = document.querySelector(".condition-editor");
      editor.getAnimations().find(animation => animation.id === "group-change")?.cancel();
      editor.animate([{opacity:.55, transform:"translateY(4px)"}, {opacity:1, transform:"none"}],
        {id:"group-change", duration:180, easing:"cubic-bezier(.2,.75,.25,1)"});
    }
  }));
  $("condition-reorder").classList.toggle("hidden", ozer || draft.conditions.length < 2);
  $("move-condition-up").disabled = selected === 0;
  $("move-condition-down").disabled = selected === draft.conditions.length - 1;
}
const conditionModeDescriptions = {
  analytical: "Participants get analysis and numbers and choose their own action.",
  chatbot: "Participants can discuss the task with the agent. They make the final decision.",
  recommender: "The agent suggests a choice. Participants accept or reject it.",
  autonomous: "The agent records a simulated decision without asking for approval.",
  recommendation: "The agent suggests a choice. Participants decide what to submit.",
  provisional_decision: "The agent makes a provisional choice. Participants can change it.",
  chat: "Participants can discuss the task with the agent. They make the final decision.",
};
const conditionModeLabels = {
  analytical:"Analysis only", chatbot:"Discussion only", recommender:"Recommendation", autonomous:"Automatic decision",
  recommendation:"Recommendation", provisional_decision:"Participant can override", chat:"Discussion only",
};
const moduleExamples = {
  supplier_selection: ["Compare the sample suppliers.", "Northstar delivers in 4 days. Meridian takes 7.", "4 vs. 7 days", "Northstar", "Choose a supplier", ["Northstar", "Meridian"]],
  supplier_risk: ["What risk stands out?", "Northstar has had 3 late shipments this month. Meridian had 1.", "3 vs. 1 late shipments", "Review Northstar", "Choose a supplier to review", ["Northstar", "Meridian"]],
  demand_forecasting: ["What does recent demand suggest?", "The last three weeks average 120 units.", "120 units", "120 units for next week", "Record a forecast", ["110 units", "120 units"]],
  disruption_response: ["How long would the delay last?", "The disruption adds 5 days. A backup route cuts that to 2.", "5 vs. 2 days", "Use the backup route", "Choose a response", ["Wait for the supplier", "Use the backup route"]],
  negotiation: ["My offer is $12 per unit.", "I can offer $13 per unit.", "$13 per unit", "$13 per unit", "Record a unit price", ["$12 per unit", "$13 per unit"]],
};
function renderModuleExample(enabled, mode, ozer) {
  const pack = draft.case_study?.id;
  const role = $("cond-role").value;
  const task = pack || $("config-task").value;
  const numeric = !pack && $("cond-output").value === "number";
  const key = [selected, task, enabled, mode, role, numeric].join("|");
  const content = $("module-example-content");
  if (content.dataset.key === key) return;
  content.dataset.key = key;
  const generic = ["What does the sample data show?", "The two sample options have different costs and delivery times.", "4 vs. 7 days", "Option A", "Record a choice", ["Option A", "Option B"]];
  const [question, reply, number, proposal, label, choices] = moduleExamples[task] || generic;
  const buyer = role === "buyer";
  const negotiation = task === "negotiation" && role !== "genai_tool";
  const prompt = negotiation && buyer ? "I’m asking $13 per unit." : question;
  const response = negotiation && buyer ? "I can offer $12 per unit." :
    task === "negotiation" && !negotiation ? "A $1 change in unit price changes the total by $1 for every unit." : reply;
  const exampleProposal = negotiation && buyer ? "$12 per unit" : proposal;
  const heading = $("module-example-heading");
  const button = (text, action, primary = false) =>
    `<button type="button" class="${primary ? "primary" : "secondary"}" data-module-action="${action}">${esc(text)}</button>`;
  const choice = (title, options) =>
    `<label>${esc(title)}<select data-example-choice><option value="">Choose…</option>${options.map(value => `<option>${esc(value)}</option>`).join("")}</select></label>`;
  let example;
  if (ozer) {
    const retailer = role === "supplier";
    heading.textContent = retailer ? "Retailer’s forecast" : "Supplier’s production";
    example = `<p class="module-observation">${retailer ? "Your private demand signal" : "The retailer reported"} <strong>120 units</strong></p>
      <label>${retailer ? "Report a forecast" : "Set production"}<input type="number" data-example-choice min="0" max="475" step="1" required placeholder="Units"></label>
      ${button("Submit example", "submit", true)}`;
  } else if (!enabled) {
    heading.textContent = "Independent decision";
    example = choice(label, choices) + button("Record example", "submit", true);
  } else if (mode === "chat" || mode === "chatbot") {
    heading.textContent = negotiation ? (buyer ? "Talk to the buyer" : "Talk to the supplier") : "Chat with the assistant";
    example = `<div class="module-thread"><p class="module-message">${esc(prompt)}</p>
      <p class="module-message module-reply hidden" data-example-result>${esc(numeric ? number : response)}</p></div>
      ${button(negotiation ? "Send example offer" : "Send example message", "reveal", true)}`;
  } else if (mode === "analytical") {
    heading.textContent = "Analysis panel";
    example = `<p class="module-observation">${esc(question)}</p><output data-example-result class="module-number hidden">${esc(number)}</output>
      ${button("Run sample analysis", "reveal", true)}`;
  } else if (mode === "autonomous") {
    heading.textContent = "Automatic decision";
    example = `<p class="module-observation">The agent runs the task and records the result.</p>
      <p class="module-message module-reply hidden" data-example-result>Recorded · ${esc(exampleProposal)}</p>
      ${button("Run sample decision", "reveal", true)}`;
  } else {
    const provisional = mode === "provisional_decision";
    heading.textContent = provisional ? "Provisional choice" : "Agent recommendation";
    const alternative = !pack ? choices.find(value => value !== exampleProposal) : null;
    example = `<p class="module-message module-reply">${esc(exampleProposal)}</p><div class="button-row">
      ${button("Accept", "accept", true)}${button(pack ? "Reject" : "Choose another", alternative ? "choose" : "reject")}</div>
      ${alternative ? `<div data-example-alternatives class="hidden">${choice(label, [alternative])}${button("Record example", "submit", true)}</div>` : ""}`;
  }
  content.innerHTML = example + '<p class="module-feedback" role="status"></p>';
  const select = content.querySelector("[data-example-choice]");
  const submit = content.querySelector('[data-module-action="submit"]');
  if (select && submit) {
    submit.disabled = true;
    select.oninput = () => { submit.disabled = !select.value || !select.validity.valid; };
  }
}
$("module-example-content").onclick = event => {
  const button = event.target.closest("[data-module-action]");
  if (!button) return;
  const content = $("module-example-content");
  const feedback = content.querySelector(".module-feedback");
  const action = button.dataset.moduleAction;
  if (action === "reveal") {
    const result = content.querySelector("[data-example-result]");
    const show = result.classList.toggle("hidden") === false;
    if (!button.dataset.originalLabel) button.dataset.originalLabel = button.textContent;
    button.textContent = show ? "Reset example" : button.dataset.originalLabel;
    feedback.textContent = show ? "Example result shown." : "";
  } else if (action === "choose") {
    content.querySelector("[data-example-alternatives]").classList.remove("hidden");
    content.querySelector("[data-example-choice]").focus();
    feedback.textContent = "";
  } else {
    const alternative = content.querySelector("[data-example-alternatives]");
    if (action !== "submit") alternative?.classList.add("hidden");
    feedback.textContent = action === "submit" ? "Example recorded · " + content.querySelector("[data-example-choice]").value :
      action === "accept" ? "Example accepted." : "Example rejected.";
  }
};
function renderConditionPresentation() {
  const enabled = $("cond-agent_enabled").value === "true";
  const ozer = draft.case_study?.id === "ozer_game";
  const mode = draft.case_study ? $("cond-agent-mode").value : $("cond-authority").value;
  const name = $("cond-label").value.trim() || "Untitled group";
  const label = ozer ? conditionMode(draft.conditions[selected]) : enabled ? conditionModeLabels[mode] : "No agent";
  $("condition-editor-title").textContent = name;
  $("condition-agent-settings").classList.toggle("hidden", !enabled || ozer);
  $("condition-independent-note").classList.toggle("hidden", enabled);
  $("condition-fixed-summary").classList.toggle("hidden", !ozer);
  $("condition-fixed-summary").textContent = ozer ? conditionDescription(draft.conditions[selected], draft) : "";
  $("condition-mode-description").textContent = conditionModeDescriptions[mode] || "";
  $("cond-id").closest("details").classList.toggle("hidden", ozer);
  $("designer-agent-instructions").querySelector("p").textContent = ozer ?
    "The live counterpart uses this goal and these instructions. Keyless previews use fixed decisions." :
    "Describe the goal, the information it may use, and how it should respond.";
  $("inspect-condition").classList.toggle("hidden", !enabled || ozer);
  const group = $("condition-tabs").querySelector(`[data-condition="${selected}"]`);
  group.querySelector("strong").textContent = name;
  group.querySelector("small").textContent = label;
  group.querySelector("small").classList.toggle("hidden", name.toLowerCase() === label.toLowerCase());
  $("add-human-condition").classList.toggle("hidden", ozer || !enabled ||
    draft.conditions.some((condition, index) => index !== selected && !condition.agent_enabled));
  $("designer-agent-instructions").classList.toggle("hidden", !enabled);
  renderModuleExample(enabled, mode, ozer);
  renderDesignerSummary();
}
for (const id of ["cond-label", "cond-agent_enabled", "cond-agent-mode", "cond-authority", "cond-role", "cond-output"]) {
  $(id).addEventListener("input", renderConditionPresentation);
}
let draggedCondition = null;
function moveCondition(from, to, focusId) {
  if (draft.case_study?.id === "ozer_game" || from === to || to < 0 || to >= draft.conditions.length) return;
  collect();
  const [condition] = draft.conditions.splice(from, 1);
  draft.conditions.splice(to, 0, condition);
  selected = to;
  renderEditor();
  markDirty();
  const focus = focusId && $(focusId);
  if (focus && !focus.disabled) focus.focus();
  else $("condition-tabs").querySelector(`[data-condition="${selected}"]`).focus();
}
$("move-condition-up").onclick = guarded(() => moveCondition(selected, selected - 1, "move-condition-up"));
$("move-condition-down").onclick = guarded(() => moveCondition(selected, selected + 1, "move-condition-down"));
$("condition-tabs").addEventListener("dragstart", event => {
  const button = event.target.closest('[data-condition][draggable="true"]');
  if (!button) return;
  draggedCondition = Number(button.dataset.condition);
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", button.textContent);
});
$("condition-tabs").addEventListener("dragover", event => {
  const button = event.target.closest("[data-condition]");
  if (draggedCondition === null || !button) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  $("condition-tabs").querySelector(".drop-target")?.classList.remove("drop-target");
  if (Number(button.dataset.condition) !== draggedCondition) button.classList.add("drop-target");
});
function clearConditionDrag() {
  draggedCondition = null;
  $("condition-tabs").querySelector(".drop-target")?.classList.remove("drop-target");
}
$("condition-tabs").addEventListener("dragend", clearConditionDrag);
$("condition-tabs").addEventListener("drop", guarded(event => {
  const button = event.target.closest("[data-condition]");
  const from = draggedCondition;
  if (from === null || !button) return;
  event.preventDefault();
  clearConditionDrag();
  moveCondition(from, Number(button.dataset.condition));
}));
function renderForm() {
  delete $("module-example-content").dataset.key;
  $("config-name").value = draft.name;
  $("config-description").value = draft.description;
  $("config-status").value = draft.status;
  $("config-telemetry").checked = draft.telemetry.enabled;
  $("config-clipboard-text").checked = Boolean(draft.telemetry.clipboard_text);
  $("config-outside-ai-question").checked = Boolean(draft.telemetry.outside_ai_question);
  $("config-reasoning_effort").value = draft.architecture.reasoning_effort ?? "";
  for (const k of ["model", "memory"]) $("config-" + k).value = draft.architecture[k];
  $("config-provider").value = draft.architecture.provider === "relay" ? "" : draft.architecture.provider;
  if (!$("config-provider").value) $("config-model").value = "";
  $("config-knowledge").value = draft.architecture.knowledge_version;
  $('config-task').value=draft.task;$('config-assignment').value=draft.assignment.method;
  $('config-temperature').value=draft.architecture.temperature ?? 0;
  for(const key of engineNumbers)$('config-'+key).value=draft.architecture[key];
  for(const key of ['title','instructions','knowledge','consent_text'])$('scenario-'+key).value=draft.scenario[key];
  for(const key of negotiationFields)$('neg-'+key).value=draft.negotiation[key];
  renderScenarioEditors();
  $("case-study-select").value = "";
  $("load-case-study").disabled = true;
  renderTaskFields();renderBudget();
  document.querySelector(".scenario-example").open = Boolean(draft.case_study);
  $("config-memory").disabled = draft.case_study?.id === "ozer_game";
  $("model-memory-note").classList.toggle("hidden", !$("config-memory").disabled);
  selected = Math.min(selected, draft.conditions.length - 1);
  renderEditor();
  providerHelp();
}
function renderScenarioEditors() {
  $('criteria-fields').innerHTML=draft.scenario.criteria.map((c,i)=>`<fieldset class="criterion-fields"><legend>${esc(c.id)}</legend><label>Criterion<input data-criterion-label="${i}" value="${esc(c.label)}"></label><label>Unit<input data-criterion-unit="${i}" value="${esc(c.unit)}"></label><label>Preferred direction<select data-criterion-direction="${i}"><option value="min" ${c.direction==='min' ? 'selected' : ''}>Lower</option><option value="max" ${c.direction==='max' ? 'selected' : ''}>Higher</option></select></label><label>Weight<input data-criterion-weight="${i}" type="number" min="0" step=".01" value="${esc(c.weight)}"></label><button class="text-button danger" type="button" data-remove-criterion="${i}" ${draft.scenario.criteria.length <= 1 ? 'disabled' : ''}>Remove criterion</button></fieldset>`).join('');
  $('supplier-fields').innerHTML=draft.scenario.options.map((o,i)=>`<fieldset class="supplier-fields"><legend>${esc(o.id)}</legend><label>Supplier name<input data-supplier-label="${i}" value="${esc(o.label)}"></label>${draft.scenario.criteria.map((c,j)=>`<label>${esc(c.label)}<input type="number" step="any" data-supplier-value="${i}" data-criterion="${j}" value="${esc(o.values[c.id])}"></label>`).join('')}<button class="text-button danger" type="button" data-remove-supplier="${i}" ${draft.scenario.options.length <= 2 ? 'disabled' : ''}>Remove supplier</button></fieldset>`).join('');
  enhanceSetupFields($("criteria-fields"));
  enhanceSetupFields($("supplier-fields"));
  $("add-scenario-criterion").disabled = draft.scenario.criteria.length >= 20;
  $("add-scenario-supplier").disabled = draft.scenario.options.length >= 30;
}
function editScenarioRows(kind, removeIndex = null) {
  if (draft.case_study || $("config-task").value === "negotiation") return;
  const config = collectSlotDraft();
  const {criteria, options} = config.scenario;
  const isCriterion = kind === "criterion";
  const rows = isCriterion ? criteria : options;
  if (removeIndex != null) {
    if (rows.length <= (isCriterion ? 1 : 2)) return;
    const removed = rows[removeIndex];
    if (isCriterion) {
      const bounds = config.conditions.flatMap(condition => Object.values(condition.private_rules));
      if (bounds.some(bound => Object.hasOwn(bound, removed.id)) &&
          !confirm("Remove this criterion and its condition limits?")) return;
      for (const bound of bounds) delete bound[removed.id];
      for (const option of options) delete option.values[removed.id];
      $("cond-rules").value = JSON.stringify(config.conditions[selected].private_rules, null, 2);
    }
    rows.splice(removeIndex, 1);
  } else {
    if (rows.length >= (isCriterion ? 20 : 30)) return;
    const id = kind + "_" + crypto.randomUUID().slice(0, 8);
    if (isCriterion) {
      criteria.push({id, label:"New criterion", unit:"", direction:"min", weight:1});
      for (const option of options) option.values[id] = 0;
    } else {
      options.push({id, label:"New supplier", values:Object.fromEntries(criteria.map(criterion => [criterion.id, 0]))});
    }
  }
  renderScenarioEditors();
  markDirty();
  const field = isCriterion ? "criterion-label" : "supplier-label";
  if (removeIndex == null) document.querySelector(`[data-${field}="${rows.length - 1}"]`).focus();
  else $(isCriterion ? "add-scenario-criterion" : "add-scenario-supplier").focus();
}
$("add-scenario-criterion").onclick = guarded(() => editScenarioRows("criterion"));
$("add-scenario-supplier").onclick = guarded(() => editScenarioRows("supplier"));
for (const [container, kind] of [["criteria-fields", "criterion"], ["supplier-fields", "supplier"]]) {
  $(container).onclick = guarded(event => {
    const button = event.target.closest(`[data-remove-${kind}]`);
    if (button) editScenarioRows(kind, Number(button.getAttribute(`data-remove-${kind}`)));
  });
}
function providerHelp() {
  const provider=$("config-provider").value;
  $("provider-help").textContent = {
    demo: draft.case_study?.id === "ozer_game" ? "Play with a fixed counterpart. Choose OpenAI to test your role instructions or enroll participants." :
      "Run the case with built-in responses.",
    openai: "Use a model through the server’s OpenAI key.",
    "": "Choose a response provider for this older setup.",
  }[provider];
  $("config-model").readOnly=provider!=="openai";
  $("api-settings").classList.toggle("hidden",provider!=="openai");
  $("model-snapshot-field").classList.toggle("hidden",provider!=="openai");
  updateReasoningControl();
}
function updateReasoningControl() {
  const fixed = $("config-model").value.trim() === defaultSetupModel;
  $("config-reasoning_effort").disabled = fixed;
  $("model-reasoning-note").classList.toggle("hidden", !fixed);
}
$("config-model").addEventListener("input", updateReasoningControl);
function markDirty(dirty = true) {
  slotDirty = dirty;
  invalidateSetupCheck();
  renderDesignerSummary();
  $("save-state").textContent = dirty ? (newSetup ? "Unsaved draft" : "Unsaved changes") : "";
  $("save-state").classList.toggle("hidden", !dirty);
  updateSetupActions();
  $("save-state").classList.toggle("unsaved", dirty);
  $("revert-slot").classList.toggle("hidden", !dirty || newSetup);
  renderSetupReview();
  if (selectedSlot) {
    $("slot-heading").textContent = $("slot-label").value.trim() || selectedSlot.label;
    if (dirty) showSlotCase(draft.case_study ? slotCaseTitle(draft) : modeName($("config-task").value));
    renderStudyAction();
  }
}
function collect(target = draft, preview = false) {
  // Parse first so an invalid JSON field cannot partially update the target.
  let privateInformation, rules, examples;
  try { privateInformation = JSON.parse($("cond-private").value); rules = JSON.parse($("cond-rules").value); examples = JSON.parse($("cond-examples").value); }
  catch { throw new Error("Check the JSON in the selected condition's private fields or examples."); }
  target.task=$('config-task').value;target.assignment.method=$('config-assignment').value;
  for(const key of engineNumbers)target.architecture[key]=readNumber('config-'+key);
  for(const key of ['title','instructions','knowledge','consent_text'])target.scenario[key]=$('scenario-'+key).value;
  for(const key of negotiationFields)target.negotiation[key]=negotiationNumbers.has(key) ? readNumber('neg-'+key) : $('neg-'+key).value;
  target.negotiation.currency=target.negotiation.currency.toUpperCase();
  document.querySelectorAll('[data-criterion-label]').forEach(el=>target.scenario.criteria[Number(el.dataset.criterionLabel)].label=el.value);
  document.querySelectorAll('[data-criterion-unit]').forEach(el=>target.scenario.criteria[Number(el.dataset.criterionUnit)].unit=el.value);
  document.querySelectorAll('[data-criterion-direction]').forEach(el=>target.scenario.criteria[Number(el.dataset.criterionDirection)].direction=el.value);
  document.querySelectorAll('[data-criterion-weight]').forEach(el=>{if(!el.value.trim()||!Number.isFinite(Number(el.value)))throw new Error('Enter valid criterion weights');target.scenario.criteria[Number(el.dataset.criterionWeight)].weight=Number(el.value);});
  document.querySelectorAll('[data-supplier-label]').forEach(el=>target.scenario.options[Number(el.dataset.supplierLabel)].label=el.value);
  document.querySelectorAll('[data-supplier-value]').forEach(el=>{if(!el.value.trim()||!Number.isFinite(Number(el.value)))throw new Error('Enter finite supplier values');target.scenario.options[Number(el.dataset.supplierValue)].values[target.scenario.criteria[Number(el.dataset.criterion)].id]=Number(el.value);});
  target.name = $("config-name").value;
  target.description = $("config-description").value;
  target.status = $("config-status").value;
  target.telemetry.enabled = $("config-telemetry").checked;
  for (const [key, id] of [["clipboard_text", "config-clipboard-text"], ["outside_ai_question", "config-outside-ai-question"]]) {
    if ($(id).checked) target.telemetry[key] = true;
    else delete target.telemetry[key];
  }
  target.architecture.reasoning_effort = $("config-reasoning_effort").value || null;
  if (!$("config-provider").value && !preview) throw new Error("Choose Demo or OpenAI in Study controls.");
  if ($("config-provider").value) {
    for (const k of ["provider","model","memory"]) target.architecture[k] = $("config-" + k).value;
  }
  target.architecture.temperature=target.architecture.provider==="openai" ? readNumber("config-temperature") : null;
  if (target.architecture.provider !== "openai" || target.architecture.model.trim() === defaultSetupModel) {
    target.architecture.reasoning_effort = null;
  }
  target.architecture.knowledge_version = $("config-knowledge").value;
  const cond = target.conditions[selected];
  for (const k of conditionFields) cond[k] = $("cond-" + k).value;
  cond.agent_enabled=$('cond-agent_enabled').value==='true';
  if(cond.agent){cond.agent.mode=$('cond-agent-mode').value;cond.agent.allowed_tools=[...document.querySelectorAll('[data-agent-tool]:checked')].map(el=>el.dataset.agentTool);cond.agent.data_access=JSON.parse($('agent-data-access').value);for(const key of ['max_tool_calls','tool_timeout_seconds','turn_timeout_seconds'])cond.agent[key]=readNumber('agent-'+key);}
  if(target.task==='negotiation'){
    if(!rules||!rules.min||!rules.max||typeof rules.min!=='object'||typeof rules.max!=='object')throw new Error('Private rules need min and max objects.');
    for(const [bound,id] of [['min','cond-price-min'],['max','cond-price-max']]){
      if($(id).value.trim())rules[bound].price=readNumber(id);else delete rules[bound].price;
    }
  }
  cond.private_information = privateInformation; cond.private_rules = rules; cond.examples = examples;
}
async function launch(preview, participant, condition, method = "external", slot = null, demoAgent = null, livePreview = false) {
  const result = await api("/api/admin/launch", {
    preview, participant_id: participant, assignment_method: method,
    ...(method === "external" ? {condition_id: condition} : {}),
    ...(slot ? {slot_id: slot.id, expected_slot_revision: slot.revision} : {}),
    ...(demoAgent ? {demo_agent: demoAgent} : {}),
    ...(livePreview ? {live_preview: true} : {}),
  });
  return {...result,url:location.origin+'/participate#launch='+encodeURIComponent(result.launch_token)};
}
function updateAssignment(){ $('launch-condition-label').classList.toggle('hidden',$('launch-assignment').value!=='external'); }
$('launch-assignment').onchange=updateAssignment;
$('config-task').onchange=renderTaskFields;
$('inspect-condition').onclick=guarded(async()=>{
  collect();const result=await api('/api/admin/condition-preview',{config:draft,condition_id:draft.conditions[selected].id});
  $('inspector-title').textContent=result.condition.label;
  $('inspector-content').innerHTML=`<p class="preview-task-label">${esc(result.public.case_study?.title || modeName(result.public.task))} · round 1</p>${facts([['Agent',result.condition.agent_enabled ? result.condition.role : 'Off · human-only'],['Authority',result.public.case_study?.id==='ozer_game' ? 'Round counterpart' : result.public.case_study?.mode || result.condition.authority],['Output',result.condition.output],['Save slot',selectedSlot.label]])}<h3>Public task</h3><p class="snapshot-text">${esc(result.public.scenario.instructions)}</p><h3>Sample response</h3><p class="help">Illustration using the demo engine. This does not test your model or validate prompt behavior. Use Test this setup to run a participant session.</p><p class="inspector-answer">${esc(result.sample?.content || 'No assistant reply. Tracking and independent outcome submission remain enabled.')}</p>`;
  $('condition-inspector').showModal();
});
async function initialize() {
  health = await api("/api/health");
  try { researcher = await api("/api/admin/account"); }
  catch (err) {
    if (err.status !== 403) throw err;
    if (health.local_mode && !health.accounts_configured) {
      await api("/api/local-login", {});
      researcher = await api("/api/admin/account");
    } else {
      showResearcherLogin();
      return;
    }
  }
  applyResearcherSettings();
  snapshot = await api("/api/admin/config");
  $("login").classList.add("hidden"); $("workspace").classList.remove("hidden");
  $("demo-provider").textContent = health.openai_configured ? "Powered by OpenAI" : "Built-in responses · no key needed";
  [caseStudies, setupSlots] = await Promise.all([api("/api/admin/case-studies"), api("/api/admin/setup-slots")]);
  const caseOptions = caseStudies.map((p,i) => `<option value="${i}">${esc(p.title)}</option>`).join("");
  $("case-study-select").innerHTML = '<option value="">Choose a case…</option>' + caseOptions;
  enhanceSetupChoice($("case-study-select"));
  const remembered = sessionStorage.getItem(slotStorageKey);
  const initial = setupSlots.find(slot => String(slot.id) === remembered) ||
    setupSlots.find(slot => slot.id === snapshot.source_slot_id) || setupSlots[0];
  selectSlot(await fetchSlot(initial.id));
  renderSummary();
  await refresh();
  renderTab(location.hash.slice(1) || researcher.start_page, false);
}

function fetchSlot(id) {
  return api("/api/admin/setup-slots?id=" + encodeURIComponent(id));
}

function slotCaseTitle(slot) {
  const pack = slot.case_study;
  if (!pack) return modeName(slot.task);
  return caseStudies.find(p => p.id === pack.id && p.version === pack.version)?.title ||
    pack.id.replaceAll("_", " ");
}

function slotStudyState(slot) {
  if (slot.id == null || snapshot.source_slot_id !== slot.id) return "";
  return snapshot.config_hash === slot.config_hash ? "Used for study" : "Study update available";
}

function renderSetupLibrary() {
  const query = $("setup-search").value.trim().toLowerCase();
  const matches = setupSlots.filter(slot =>
    [slot.label, slotCaseTitle(slot), slotStudyState(slot), slot.status || ""].some(value => value.toLowerCase().includes(query)));
  matches.sort((first, second) =>
    Number(Boolean(slotStudyState(second))) - Number(Boolean(slotStudyState(first))) ||
    (second.updated_at ?? 0) - (first.updated_at ?? 0) || first.label.localeCompare(second.label));
  $("setup-count").textContent = query ? `${matches.length} of ${setupSlots.length}` : String(setupSlots.length);
  $("setup-slots").innerHTML = matches.length ? matches.map(slot => {
    const status = slotStudyState(slot);
    const badge = status || ({draft:"Draft", active:"Active", closed:"Closed"})[slot.status];
    const tone = status === "Used for study" ? "blue" : status ? "amber" : slot.status === "active" ? "mint" : "neutral";
    const task = slot.case_study?.id || slot.task;
    const title = slotCaseTitle(slot);
    const type = title.toLowerCase() === slot.label.toLowerCase() ? (slot.case_study ? "Case template" : "Custom scenario") : title;
    const groupCount = slot.condition_count == null ? "" : `${slot.condition_count} ${slot.condition_count === 1 ? "group" : "groups"}`;
    const provider = ({demo:"Demo responses", openai:"OpenAI", relay:"Manual relay"})[slot.provider];
    const updated = slot.updated_at == null ? "" :
      new Date(slot.updated_at * 1000).toLocaleDateString(undefined, {month:"short", day:"numeric", year:"numeric"});
    const deleteHint = status ? "Choose another setup for the study before deleting this one" :
      setupSlots.length === 1 ? "Save another setup before deleting this one" : `Delete ${slot.label}`;
    return `<li class="setup-entry-row"><button type="button" class="setup-entry" data-slot-id="${esc(slot.id)}" ${slot.id === selectedSlot?.id ? 'aria-current="true"' : ""}>
      <span class="setup-entry-icon glyph-${sessionCaseTones[task] || "blue"}" aria-hidden="true">${setupIcon(caseIcons[task] || "study")}</span>
      <span class="setup-entry-copy">
        <span class="setup-entry-title"><strong title="${esc(slot.label)}">${esc(slot.label)}</strong></span>
        <span class="setup-entry-meta">${esc(type)}</span>
      </span>
      <span class="setup-entry-facts">
        <span class="setup-entry-details">${esc([groupCount, provider].filter(Boolean).join(" · "))}</span>
        ${badge ? `<span class="setup-entry-status" data-tone="${tone}">${esc(badge)}</span>` : ""}
      </span>
      ${updated ? `<span class="setup-entry-updated">Updated ${esc(updated)}</span>` : ""}
      <span class="setup-entry-arrow" aria-hidden="true">→</span>
    </button>
    <span class="setup-entry-delete" title="${esc(deleteHint)}">
      <button class="setup-delete-button" type="button" aria-label="${esc(deleteHint)}" data-delete-slot="${esc(slot.id)}" ${status || setupSlots.length === 1 ? "disabled" : ""}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"/></svg>
      </button>
    </span></li>`;
  }).join("") : `<li class="setup-catalog-empty">${query ? "No setups match your search." : "Your saved setups will appear here."}</li>`;
}
$("setup-search").oninput = renderSetupLibrary;

function renderSlots() {
  renderSetupLibrary();
  const selectedSummary = setupSlots.find(slot => slot.id === selectedSlot.id);
  const status = slotStudyState(selectedSlot);
  $("slot-study-badge").textContent = status;
  $("slot-study-badge").classList.toggle("hidden", !status);
  showSlotCase(slotCaseTitle(selectedSummary || selectedSlot.config));
  renderStudyAction();
}

function showSlotCase(title) {
  const line = $("slot-description");
  line.textContent = title;
  line.classList.toggle("hidden", title === $("slot-heading").textContent);
}
function renderStudyAction() {
  const action = $("use-slot");
  const status = slotStudyState(selectedSlot);
  action.classList.toggle("hidden", newSetup || status === "Used for study");
  action.disabled = slotDirty;
  action.textContent = status === "Study update available" ? "Update study setup" : "Use for study";
}

function selectSlot(slot) {
  newSetup = false;
  updateSlotList(slot);
  selectedSlot = slot;
  $("slot-menu").open = false;
  selected = 0;
  draft = structuredClone(slot.config);
  $("slot-label").value = slot.label;
  sessionStorage.setItem(slotStorageKey, String(slot.id));
  renderForm();
  markDirty(false);
  renderSlots();
}

async function withSlotOperation(operation) {
  if (slotBusy) return;
  slotBusy = true;
  const library = $("setup-home");
  const editor = document.querySelector(".setup-workbench");
  library.inert = true;
  $("setup-library").inert = true;
  editor.inert = true;
  try { return await operation(); }
  finally {
    library.inert = false;
    $("setup-library").inert = false;
    editor.inert = false;
    slotBusy = false;
    updateSetupActions();
  }
}

function collectSlotDraft() {
  collect();
  return draft;
}

$("setup-slots").onclick = guarded(async event => {
  if (slotBusy) return;
  const deleteButton = event.target.closest("[data-delete-slot]");
  if (deleteButton) {
    if (!deleteButton.disabled) openDeleteSetup(Number(deleteButton.dataset.deleteSlot));
    return;
  }
  const button = event.target.closest("[data-slot-id]");
  if (!button) return;
  if (Number(button.dataset.slotId) === selectedSlot?.id) {
    setBuilderOpen(true, setupLibraryDrawer.open || slotDirty);
    $("slot-heading").focus({preventScroll: true});
    return;
  }
  if (!await leaveEditor()) return;
  await withSlotOperation(async () => selectSlot(await fetchSlot(button.dataset.slotId)));
  setBuilderOpen(true);
  $("slot-heading").focus({preventScroll: true});
});
const deleteSetupDialog = $("delete-setup-dialog");
let setupToDelete;
function openDeleteSetup(id) {
  setupToDelete = setupSlots.find(slot => slot.id === id);
  if (!setupToDelete) return;
  const hasEdits = selectedSlot?.id === id && slotDirty;
  $("delete-setup-description").textContent = `Delete “${setupToDelete.label}” from Saved setups?` +
    (hasEdits ? " Its unsaved edits will also be discarded." : "") + " Past sessions will keep their study settings.";
  $("delete-setup-error").classList.add("hidden");
  deleteSetupDialog.showModal();
}
deleteSetupDialog.addEventListener("cancel", event => {
  if (deleteSetupDialog.dataset.busy) event.preventDefault();
});
$("delete-setup-form").onsubmit = async event => {
  event.preventDefault();
  if (!setupToDelete || slotBusy) return;
  const slot = setupToDelete;
  const buttons = $("delete-setup-form").querySelectorAll("button");
  for (const button of buttons) button.disabled = true;
  deleteSetupDialog.dataset.busy = "true";
  const error = $("delete-setup-error");
  error.classList.add("hidden");
  try {
    await withSlotOperation(async () => {
      await api("/api/admin/delete-setup-slot", {id:slot.id, expected_revision:slot.revision});
      setupSlots = setupSlots.filter(item => item.id !== slot.id);
      if (selectedSlot?.id === slot.id) {
        selectedSlot = null;
        draft = null;
        sessionStorage.removeItem(slotStorageKey);
        markDirty(false);
        setBuilderOpen(false, false, false);
      }
      renderSetupLibrary();
    });
    deleteSetupDialog.close();
    $("setup-search").focus({preventScroll:true});
    notice(`Deleted “${slot.label}”.`);
  } catch (failure) {
    error.textContent = failure.status === 404 ? "Restart the local server, then reload to enable deleting setups." : failure.message;
    error.classList.remove("hidden");
  } finally {
    delete deleteSetupDialog.dataset.busy;
    for (const button of buttons) button.disabled = false;
  }
};

$("revert-slot").onclick = guarded(async () => {
  if (!confirm("Discard unsaved edits to this slot?")) return;
  await withSlotOperation(async () => selectSlot(await fetchSlot(selectedSlot.id)));
});

function builderOpen() {
  return $("setup-home").classList.contains("hidden");
}

function setBuilderOpen(open, resume = false, focus = true) {
  if (setupLibraryDrawer.open) setupLibraryDrawer.close();
  $("setup-home").classList.toggle("hidden", open);
  $("toggle-save-slots").classList.toggle("hidden", !open);
  document.querySelector(".setup-workbench").classList.toggle("hidden", !open);
  if (open) {
    if (!resume) showSetupStep("study");
    return;
  }
  renderSetupLibrary();
  if (!focus) return;
  window.scrollTo({top:0, behavior:"instant"});
  $("setup-search").focus({preventScroll:true});
}
$("back-to-setup-home").onclick = guarded(async () => {
  if (await leaveEditor()) setBuilderOpen(false);
});

function nextDraftName(base) {
  const label = base.slice(0, 80);
  const used = new Set(setupSlots.map(slot => slot.label));
  if (!used.has(label)) return label;
  for (let i = 2; ; i++) {
    const suffix = " · " + i;
    const candidate = label.slice(0, 80 - suffix.length) + suffix;
    if (!used.has(candidate)) return candidate;
  }
}
function beginSetup(config, label = config.name) {
  if (!$("notice").classList.contains("error")) $("notice").classList.add("hidden");
  newSetup = true;
  selected = 0;
  draft = {...structuredClone(config), status:"draft"};
  label = nextDraftName(label);
  selectedSlot = {id:null, label, config:structuredClone(draft)};
  $("slot-label").value = label;
  $("slot-label").dataset.automaticLabel = label;
  $("designer-task").open = false;
  renderForm();
  markDirty();
  renderSlots();
  setBuilderOpen(true);
  $("slot-heading").focus({preventScroll:true});
}
async function startCustomSetup() {
  if (!snapshot || slotBusy || !await switchTab("conditions") || !await leaveEditor()) return;
  const template = caseStudies.find(item => item.id === "supplier_selection");
  if (!template) throw new Error("The custom scenario starter is unavailable.");
  const result = await withSlotOperation(() =>
    api("/api/admin/case-study-config", {id:template.id, version:template.version}));
  if (!result || activeTab !== "conditions") return;
  const {config} = result;
  config.case_study = null;
  config.name = "Untitled study";
  config.description = "";
  config.scenario.title = "Choose a supplier";
  config.conditions = [{
    ...config.conditions.find(condition => condition.id === "recommender"),
    id:"recommend", label:"Agent recommendation", agent:null, role:"buyer",
    objective:"Help the participant compare the available suppliers.", authority:"recommendation",
  }];
  beginSetup(config);
  $("config-name").focus({preventScroll:true});
  $("config-name").select();
}
$("start-custom-setup").onclick = guarded(startCustomSetup);

function openNewSlot(copy = false) {
  copyDraft = copy ? structuredClone(collectSlotDraft()) : null;
  $("slot-menu").open = false;
  $("new-slot-title").textContent = copy ? "Save as copy" : "New save slot";
  $("new-slot-name").value = copy ? ($("slot-label").value.trim() + " copy").slice(0, 80) : "";
  $("new-slot-source-field").classList.toggle("hidden", copy);
  $("new-slot-copy-note").classList.toggle("hidden", !copy);
  $("new-slot-source").innerHTML = '<option value="study">Current study setup</option>' +
    '<option value="slot">Selected slot’s saved settings</option>' +
    caseStudies.map((p, i) => `<option value="pack-${i}">${esc(p.title)}</option>`).join("");
  $("new-slot-error").classList.add("hidden");
  $("new-slot-dialog").showModal();
  $("new-slot-name").focus();
}
$("copy-slot").onclick = guarded(() => openNewSlot(true));

$("new-slot-form").onsubmit = async event => {
  event.preventDefault();
  if (!copyDraft && !await leaveEditor()) return;
  const button = event.submitter;
  button.disabled = true;
  const error = $("new-slot-error");
  error.classList.add("hidden");
  try {
    await withSlotOperation(async () => {
      const source = $("new-slot-source").value;
      let config = copyDraft || (source === "slot" ? selectedSlot.config : snapshot.config);
      if (!copyDraft && source.startsWith("pack-")) {
        const pack = caseStudies[Number(source.slice(5))];
        config = (await api("/api/admin/case-study-config", {id: pack.id, version: pack.version})).config;
      }
      if (config.architecture.provider === "relay") {
        config = structuredClone(config);
        Object.assign(config.architecture, {provider:"demo", model:"deterministic-v1", temperature:null, reasoning_effort:null});
        for (const condition of config.conditions) condition.selection = "weighted";
      }
      const slot = await api("/api/admin/setup-slots", {label: $("new-slot-name").value, config});
      selectSlot(slot);
      $("new-slot-dialog").close();
      $("slot-heading").focus({preventScroll: true});
      setBuilderOpen(true);
      document.querySelector('[data-settings="study"]').click();
      notice("Created save slot “" + slot.label + "”.");
    });
  } catch (err) { error.textContent = err.message; error.classList.remove("hidden"); }
  finally { button.disabled = false; }
};

function attachmentPayload(file) {
  if (file.size > 4_000_000) throw new Error("Attach one document up to 4 MB.");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name: file.name, data: reader.result.split(",", 2)[1]});
    reader.onerror = () => reject(new Error("Could not read the attachment."));
    reader.readAsDataURL(file);
  });
}

const setupDocumentInput = $("setup-document");
const setupBrief = $("setup-brief");

function renderSetupAttachment() {
  $("setup-attachment").classList.toggle("hidden", !assistantAttachment);
  $("setup-attachment-name").textContent = assistantAttachment?.name || "";
  $("choose-setup-document").textContent = assistantAttachment ? "Replace file" : "Attach a file";
}

function setupChatControls() {
  const unavailable = activeTab.startsWith("setup-chat/") && !setupChatId;
  const busy = setupChatBusy || setupChatLoading || unavailable;
  const legacy = /^setup-chat\/generation-\d+$/.test(activeTab);
  $("setup-assistant-form").classList.toggle("hidden", legacy);
  $("setup-chat-empty").classList.toggle("hidden", setupChatTurns.length > 0 || busy || legacy);
  $("new-setup-chat").disabled = setupChatBusy;
  setupBrief.disabled = $("choose-setup-document").disabled = $("remove-setup-document").disabled = busy;
  $("generate-setup").disabled = busy || !health.openai_configured || (!setupBrief.value.trim() && !assistantAttachment);
  $("setup-ai-help").textContent = legacy ? "This draft is from the earlier generation history. Start a new chat to draft another setup." :
    health.openai_configured ? "PDF, DOCX, TXT or Markdown · Up to 4 MB. OpenAI receives chat context and extracted text." :
    "An OpenAI key is needed to draft a setup. You can still build a setup manually in Agent setup.";
}

function setupChatError(message) {
  $("setup-ai-error").textContent = message;
  $("setup-ai-error").classList.toggle("hidden", !message);
}

function setupAnswer(result, index) {
  const config = result.config;
  const answer = document.createElement("div");
  answer.className = "session-chat-answer setup-chat-answer";
  const review = result.review.length ? `<ul class="setup-chat-review">${result.review.map(note => `<li>${esc(note)}</li>`).join("")}</ul>` : "";
  const assignment = config && {balanced: "Balanced random", random: "Random", external: "External"}[config.assignment.method];
  const ozer = config?.case_study?.id === "ozer_game";
  answer.innerHTML = (config ? `<p><strong>Here’s a draft for your study.</strong></p>
    <div class="setup-draft-card">
      <strong>${esc(config.name)}</strong><p>${esc(config.description)}</p>
      <p class="help">${esc(slotCaseTitle(config))} · ${esc(assignment)} assignment · Demo responses</p>
      <p class="help">${config.architecture.memory === "none" ? "No memory" : "Session memory"} · Detailed interaction counts ${config.telemetry.enabled ? "on" : "off"}</p>
      ${ozer ? '<p class="help">Uses the installed Ozer rules for rounds, roles and payoffs. Choose OpenAI in Study controls to test the saved role instructions.</p>' : ''}
      <details><summary>Conditions and prompts (${config.conditions.length})</summary><ul>
        ${config.conditions.map(condition => `<li><strong>${esc(condition.label)}</strong><p>${esc(condition.objective)}</p>
          ${condition.agent_enabled ? `<p>${esc(condition.prompt)}</p>` : ""}</li>`).join("")}</ul></details>
      ${review}
      <button type="button" class="secondary" data-save-setup-turn="${index}">Save draft &amp; edit</button>
    </div>` : `<p><strong>This study needs a different case.</strong></p>${review}`);
  return answer;
}

function setupPrompt(description, filename) {
  const prompt = chatPrompt(description || "Draft a setup from this protocol.");
  if (filename) {
    const file = document.createElement("small");
    file.className = "setup-chat-file";
    file.textContent = filename;
    prompt.append(file);
  }
  return prompt;
}

function appendSetupTurn(turn) {
  const nodes = [setupAnswer(turn.result, setupChatTurns.length)];
  if (turn.description || turn.attachment_name) nodes.unshift(setupPrompt(turn.description, turn.attachment_name));
  setupChatTurns.push(turn);
  $("setup-chat-log").append(...nodes);
}

async function refreshSetupChatHistory() {
  const ticket = ++setupChatHistoryLoad;
  try {
    const [chats, generations] = await Promise.all([api("/api/admin/setup-chats"), api("/api/admin/setup-generations")]);
    if (ticket !== setupChatHistoryLoad || !isSetupChat(activeTab)) return;
    const render = (rows, listId, prefix = "") => {
      const links = rows.map(row => {
        const link = document.createElement("a");
        link.href = "#setup-chat/" + prefix + encodeURIComponent(row.id);
        link.textContent = link.title = row.title;
        if (link.hash === "#" + activeTab) link.setAttribute("aria-current", "page");
        return link;
      });
      $(listId).replaceChildren(...links);
    };
    render(chats, "setup-chat-history-list");
    render(generations, "earlier-setup-list", "generation-");
    $("earlier-setup-history").classList.toggle("hidden", !generations.length);
    if (activeTab.startsWith("setup-chat/generation-")) $("earlier-setup-history").open = true;
    $("setup-chat-history-status").textContent = chats.length ? "" : "Your chats will appear here.";
  } catch (err) {
    if (ticket === setupChatHistoryLoad) $("setup-chat-history-status").textContent = err.message;
  }
}

async function loadSetupChat(id) {
  const ticket = ++setupChatLoad;
  setupChatLoading = Boolean(id);
  setupChatId = null;
  setupPendingTurn = null;
  setupChatTurns = [];
  assistantAttachment = null;
  $("setup-assistant-form").reset();
  $("setup-chat-log").replaceChildren();
  $("setup-chat-thread").scrollTop = 0;
  setupChatError("");
  renderSetupAttachment();
  setupChatControls();
  const status = $("setup-ai-status");
  status.textContent = "Opening conversation…";
  status.classList.toggle("hidden", !id);
  if (!id) return;
  try {
    if (id.startsWith("generation-")) {
      const result = await api("/api/admin/setup-generations?id=" + encodeURIComponent(id.slice(11)));
      if (ticket === setupChatLoad) appendSetupTurn({result});
    } else {
      const data = await api("/api/admin/setup-chats?id=" + encodeURIComponent(id));
      if (ticket !== setupChatLoad) return;
      setupChatId = data.id;
      data.turns.forEach(appendSetupTurn);
    }
  } catch (err) {
    if (ticket === setupChatLoad) setupChatError(err.message);
  } finally {
    if (ticket === setupChatLoad) {
      setupChatLoading = false;
      status.classList.add("hidden");
      setupChatControls();
      const thread = $("setup-chat-thread");
      thread.scrollTop = thread.scrollHeight;
    }
  }
}

$("open-setup-assistant").onclick = guarded(async () => {
  if (await switchTab("setup-chat")) setupBrief.focus();
});
$("new-setup-chat").onclick = guarded(async () => {
  if (activeTab === "setup-chat") loadSetupChat(null);
  else if (!await switchTab("setup-chat")) return;
  setupBrief.focus();
});

$("setup-assistant-form").onsubmit = async event => {
  event.preventDefault();
  if ($("generate-setup").disabled || slotBusy) return;
  const description = setupBrief.value.trim();
  const file = assistantAttachment;
  const prompt = setupPrompt(description, file?.name);
  const log = $("setup-chat-log");
  const thread = $("setup-chat-thread");
  const status = $("setup-ai-status");
  const previousId = setupChatId;
  setupChatError("");
  setupChatBusy = true;
  setupChatControls();
  status.textContent = previousId ? "Updating your draft…" : "Drafting your setup…";
  status.classList.remove("hidden");
  log.append(prompt);
  thread.scrollTop = thread.scrollHeight;
  try {
    const document = file ? await attachmentPayload(file) : null;
    if (setupPendingTurn?.description !== description || setupPendingTurn?.file !== file) {
      setupPendingTurn = {id: crypto.randomUUID(), description, file};
    }
    const {id, turn} = await api("/api/admin/setup-chats/ask", {
      chat_id: previousId, request_id: setupPendingTurn.id, description, document,
    });
    setupChatId = id;
    setupPendingTurn = null;
    activeTab = "setup-chat/" + id;
    history.replaceState(null, "", "#" + activeTab);
    log.append(setupAnswer(turn.result, setupChatTurns.length));
    setupChatTurns.push(turn);
    $("setup-assistant-form").reset();
    assistantAttachment = null;
    renderSetupAttachment();
    refreshSetupChatHistory();
    thread.scrollTop += prompt.getBoundingClientRect().top - thread.getBoundingClientRect().top - 20;
  } catch (err) {
    prompt.remove();
    setupChatError(err.message);
  } finally {
    setupChatBusy = false;
    status.classList.add("hidden");
    setupChatControls();
    setupBrief.focus({preventScroll:true});
  }
};
setupBrief.oninput = () => { setupChatError(""); setupChatControls(); };
setupBrief.onkeydown = event => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (!$("generate-setup").disabled) $("setup-assistant-form").requestSubmit();
};

$("choose-setup-document").onclick = () => {
  setupDocumentInput.value = "";
  setupDocumentInput.click();
};
setupDocumentInput.onchange = () => {
  const file = setupDocumentInput.files[0];
  if (!file) return;
  if (!/\.(pdf|docx|txt|md)$/i.test(file.name) || !file.size || file.size > 4_000_000) {
    setupChatError("Choose a PDF, DOCX, TXT, or Markdown file up to 4 MB.");
    return;
  }
  assistantAttachment = file;
  renderSetupAttachment();
  setupChatError("");
  setupChatControls();
};
$("remove-setup-document").onclick = () => {
  assistantAttachment = null;
  setupDocumentInput.value = "";
  renderSetupAttachment();
  setupChatError("");
  setupChatControls();
  $("choose-setup-document").focus();
};

$("setup-chat-log").onclick = async event => {
  const button = event.target.closest("[data-save-setup-turn]");
  if (!button || slotBusy || setupChatBusy) return;
  const config = setupChatTurns[Number(button.dataset.saveSetupTurn)]?.result.config;
  if (!config) return;
  button.disabled = true;
  setupChatError("");
  try {
    const slot = await withSlotOperation(async () => {
      const saved = await api("/api/admin/setup-slots", {label: nextDraftName(config.name), config});
      selectSlot(saved);
      return saved;
    });
    if (await switchTab("conditions")) {
      setBuilderOpen(true);
      showSetupStep("study", true);
      notice("Saved “" + slot.label + "”.");
    }
  } catch (err) { setupChatError(err.message); }
  finally { button.disabled = false; }
};

function updateSlotList(slot) {
  const config = slot.config;
  const summary = {
    id: slot.id, label: slot.label, revision: slot.revision, config_hash: slot.config_hash, updated_at:slot.updated_at,
    task: config.task, case_study: config.case_study, provider: config.architecture.provider,
    model: config.architecture.model, condition_count: config.conditions.length, status: config.status,
  };
  const index = setupSlots.findIndex(item => item.id === slot.id);
  if (index === -1) setupSlots.push(summary);
  else setupSlots[index] = summary;
}

function saveEditorDraft() {
  return withSlotOperation(async () => {
    const config = structuredClone(draft);
    collect(config);
    const slot = await api("/api/admin/setup-slots", {
      ...(newSetup ? {} : {id:selectedSlot.id, expected_revision:selectedSlot.revision}),
      label: $("slot-label").value, config,
    });
    const conditionIndex = selected;
    selectSlot(slot);
    selected = Math.min(conditionIndex, draft.conditions.length - 1);
    renderEditor();
    renderSetupReview();
    return slot;
  });
}

const saveSetup = guarded(async () => {
  if (slotBusy || !slotDirty) return;
  try {
    const slot = await saveEditorDraft();
    notice("Saved “" + slot.label + "”.");
    $("slot-heading").focus({preventScroll: true});
  } catch (error) { showDesignerError(error); }
});
$("save-draft").onclick = saveSetup;

function confirmDraftExit(isNew, save, discard) {
  const dialog = $("leave-draft-dialog");
  if (dialog.open || slotBusy) return Promise.resolve(false);
  const form = dialog.querySelector("form");
  const error = $("leave-draft-error");
  const controls = [...form.elements];
  $("leave-draft-title").textContent = isNew ? "Save your draft?" : "Save your changes?";
  $("leave-draft-description").textContent = isNew ?
    "Save this draft to reopen it from Saved setups, or delete it and leave." :
    "Save your edits to this setup, or discard the edits and keep the last saved version.";
  $("leave-draft-save").textContent = isNew ? "Save draft" : "Save changes";
  $("leave-draft-discard").textContent = isNew ? "Delete draft" : "Discard changes";
  error.classList.add("hidden");
  dialog.returnValue = "keep";
  return new Promise(resolve => {
    dialog.addEventListener("close", () => {
      form.onsubmit = null;
      resolve(dialog.returnValue === "leave");
    }, {once:true});
    form.onsubmit = async event => {
      event.preventDefault();
      const choice = event.submitter?.value;
      if (choice !== "save" && choice !== "discard") return dialog.close("keep");
      dialog.dataset.busy = "true";
      controls.forEach(button => { button.disabled = true; });
      error.classList.add("hidden");
      try {
        if (choice === "save") {
          const slot = await save();
          notice("Saved “" + slot.label + "”.");
        } else {
          discard();
        }
        dialog.close("leave");
      } catch (failure) {
        error.textContent = failure.message;
        error.classList.remove("hidden");
      } finally {
        delete dialog.dataset.busy;
        controls.forEach(button => { button.disabled = false; });
      }
    };
    dialog.showModal();
  });
}
$("leave-draft-dialog").addEventListener("cancel", event => {
  if (event.currentTarget.dataset.busy) event.preventDefault();
});

async function leaveEditor() {
  if (slotBusy) return false;
  if (!slotDirty) return true;
  return confirmDraftExit(newSetup, saveEditorDraft, () => {
    if (!newSetup) return selectSlot(selectedSlot);
    newSetup = false;
    selectedSlot = draft = null;
    markDirty(false);
  });
}

$("use-slot").onclick = guarded(() => {
  if (slotDirty) throw new Error("Save your changes before using this slot for the study.");
  if (selectedSlot.config.architecture.provider === "relay") throw new Error("Choose Demo or OpenAI for this setup first.");
  const enrollment = {draft: "Draft · previews only", active: "Open to participants", closed: "Closed"}[selectedSlot.config.status];
  $("use-slot-description").textContent = "Use “" + selectedSlot.label + "” for new study links. Enrollment: " + enrollment + ".";
  $("use-slot-error").classList.add("hidden");
  $("use-slot-dialog").showModal();
});
$("use-slot-form").onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter;
  const error = $("use-slot-error");
  button.disabled = true;
  error.classList.add("hidden");
  try {
    await withSlotOperation(async () => {
      snapshot = await api("/api/admin/use-setup-slot", {
        id: selectedSlot.id, expected_revision: selectedSlot.revision,
        expected_study_revision: snapshot.revision,
      });
      renderSummary();
      renderSlots();
      $("use-slot-dialog").close();
      notice("The study now uses “" + selectedSlot.label + "”.");
    });
  } catch (err) { error.textContent = err.message; error.classList.remove("hidden"); }
  finally { button.disabled = false; }
};

$("load-case-study").onclick = guarded(async () => {
  const value = $("case-study-select").value;
  if (value === "") throw new Error("Select a case study.");
  const pack = caseStudies[Number(value)];
  if (!confirm("Replace the scenario, consent text, and groups with this template? Your study name, enrollment, and provider will be kept.")) return;
  const previous = collectSlotDraft();
  await withSlotOperation(async () => {
    const {config} = await api("/api/admin/case-study-config", {id:pack.id, version:pack.version});
    for (const key of ["name","description","status","assignment","telemetry","architecture"]) config[key] = previous[key];
    if (pack.id === "ozer_game") config.architecture.memory = "none";
    draft = config;
    selected = 0;
    renderForm();
    markDirty();
    $("case-template-details").open = false;
  });
});
$("case-study-select").onchange = () => { $("load-case-study").disabled = !$("case-study-select").value; };

document.querySelectorAll("[data-tab]").forEach(el => el.onclick = guarded(() => switchTab(el.dataset.tab)));
document.querySelectorAll("[data-go]").forEach(el => el.onclick = guarded(() => switchTab(el.dataset.go)));
document.querySelector(".sidebar .brand").onclick = guarded(event => {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  return switchTab("overview");
});
$("add-condition").onclick = guarded(() => {
  collect();
  if (draft.conditions.length >= 20) throw new Error("At most 20 conditions are supported.");
  const cond = structuredClone(draft.conditions[selected]);
  cond.id = "condition_" + crypto.randomUUID().slice(0, 8); cond.label += " copy";
  draft.conditions.push(cond); selected = draft.conditions.length - 1; renderEditor();
  markDirty();
});
$("add-human-condition").onclick = guarded(() => {
  collect();
  if (draft.conditions.length >= 20) throw new Error("At most 20 conditions are supported.");
  if (draft.case_study?.id === "ozer_game" || draft.conditions.some(group => !group.agent_enabled)) return;
  const group = structuredClone(draft.conditions[selected]);
  group.id = "human_" + crypto.randomUUID().slice(0, 8);
  group.label = "No agent";
  group.agent_enabled = false;
  group.private_information = {};
  group.private_rules = {min:{}, max:{}};
  draft.conditions.push(group);
  selected = draft.conditions.length - 1;
  renderEditor();
  markDirty();
  $("cond-label").focus();
});
$("remove-condition").onclick = guarded(() => {
  if (draft.conditions.length < 2) return;
  collect();
  draft.conditions.splice(selected, 1); selected = 0; renderEditor();
  markDirty();
});
$("config-provider").onchange = () => {
  if ($("config-provider").value === "demo") $("config-model").value = "deterministic-v1";
  else if (["","deterministic-v1"].includes($("config-model").value)) $("config-model").value = defaultSetupModel;
  providerHelp();
};
function reloadSetupJson() {
  const current = structuredClone(draft);
  collect(current, true);
  $("raw-config").value = JSON.stringify(current, null, 2);
}
$("open-setup-json").onclick = guarded(() => {
  reloadSetupJson();
  $("slot-menu").open = false;
  $("setup-json-error").classList.add("hidden");
  $("slot-menu").querySelector("summary").focus({preventScroll:true});
  $("setup-json-dialog").showModal();
});
$("refresh-json").onclick = () => {
  const error = $("setup-json-error");
  try { reloadSetupJson(); error.classList.add("hidden"); }
  catch (failure) { error.textContent = failure.message; error.classList.remove("hidden"); }
};
$("apply-json").onclick = async () => {
  const error = $("setup-json-error");
  const button = $("apply-json");
  if (slotBusy || button.disabled) return;
  error.classList.add("hidden");
  button.disabled = true;
  try {
    const candidate = JSON.parse($("raw-config").value);
    if (!candidate?.architecture || !Array.isArray(candidate.conditions) || !candidate.conditions.length) {
      throw new Error("The configuration needs architecture and at least one group.");
    }
    const {config} = await api("/api/admin/validate-config", {config:candidate});
    if (!$("setup-json-dialog").open) return;
    draft = config;
    renderForm();
    markDirty();
    $("setup-json-dialog").close();
    $("slot-heading").focus({preventScroll:true});
    notice("Configuration applied. Review and save your changes when ready.");
  } catch (failure) {
    error.textContent = failure.message;
    error.classList.remove("hidden");
  } finally { button.disabled = false; }
};
$("refresh-data").onclick = guarded(refresh);
async function previewSelectedSlot() {
  if (slotBusy || setupCheckBusy) return;
  if (!await checkDesignerDraft()) return;
  try {
    if (slotDirty) await saveEditorDraft();
    showPreview(selectedSlot);
  } catch (error) { showDesignerError(error); }
}
function previewFromPage() {
  if (activeTab === "conditions" && builderOpen()) return previewSelectedSlot();
  else showPreview();
}
$("preview-slot").onclick = guarded(previewSelectedSlot);
document.querySelectorAll(".preview-button").forEach(button => button.onclick = guarded(previewFromPage));
$("preview-form").onsubmit = guarded(async e => {
  e.preventDefault();
  const button = $("start-preview");
  if (button.disabled) return;
  previewBusy = true;
  button.disabled = true;
  const errorMessage = $("preview-error");
  errorMessage.classList.add("hidden");
  try {
    if (!await leaveEditor()) return;
    const condition = document.querySelector('input[name="preview-condition"]:checked').value;
    const live = $("preview-live");
    const result = await launch(true, "preview-" + crypto.randomUUID().slice(0,8), condition, "external", previewSlot, null, live.checked && !live.disabled);
    location.assign(result.url);
  } catch (error) {
    errorMessage.textContent = error.message;
    errorMessage.classList.remove("hidden");
  } finally {
    previewBusy = false;
    updatePreviewMode();
  }
});
for (const button of document.querySelectorAll("[data-demo-agent]")) {
  button.onclick = guarded(async () => {
    const buttons = document.querySelectorAll("[data-demo-agent]");
    buttons.forEach(control => { control.disabled = true; });
    try {
      const result = await launch(true, "demo-" + crypto.randomUUID().slice(0, 8), null, "external", null, button.dataset.demoAgent);
      location.assign(result.url);
    } finally {
      buttons.forEach(control => { control.disabled = false; });
    }
  });
}
window.addEventListener("beforeunload", event => {
  if (!slotDirty) return;
  event.preventDefault();
  event.returnValue = "";
});
$("launch-form").onsubmit = guarded(async e => {
  e.preventDefault();
  const result = await launch($("launch-preview").checked,$("launch-participant").value,$("launch-condition").value,$("launch-assignment").value);
  const url=result.url;
  $("launch-assigned").textContent="Assigned "+(snapshot.config.conditions.find(c=>c.id===result.condition_id)?.label || result.condition_id)+" · config v"+result.config_version+" · "+result.assignment_method;
  $("launch-url").value = url; $("open-link").href = url;
  $("launch-empty").classList.add("hidden");
  $("launch-result").classList.remove("hidden");
  $("launch-result").scrollIntoView({behavior:"instant",block:"nearest"});
});
$("copy-link").onclick = guarded(async () => { await navigator.clipboard.writeText($("launch-url").value); notice("Launch link copied."); });
let researcher;

function showResearcherLogin() {
  const configured = health.accounts_configured;
  $("workspace").classList.add("hidden");
  $("login").classList.remove("hidden");
  $("login-username-field").classList.toggle("hidden", !configured);
  $("login-username").required = configured;
  $("login-password-label").textContent = configured ? "Password" : "Researcher access key";
  $("login-instructions").textContent = configured ? "Sign in with your researcher account." :
    "Use the workspace access key to set up the owner account.";
}

function applyResearcherSettings() {
  const configured = Boolean(researcher.username);
  const canManage = configured && researcher.role === "owner";
  const role = researcher.role === "owner" ? "Owner" : "Researcher";
  const identity = researcher.display_name ? researcher.display_name + " · @" + researcher.username : researcher.username;
  $("account-summary").textContent = configured ?
    identity + " · " + role :
    "Create your owner account to sign in with a username and password.";
  $("workspace-account-label").textContent = researcher.display_name || researcher.username || "Local workspace";
  $("workspace-account-label").title = $("workspace-account-label").textContent;
  $("claim-account-form").classList.toggle("hidden", configured);
  $("account-controls").classList.toggle("hidden", !configured);
  $("researcher-management").classList.toggle("hidden", !canManage);
  $("researcher-settings-link").classList.toggle("hidden", !canManage);
  document.querySelectorAll("[data-account-preference]").forEach(field => {
    field.value = researcher[field.dataset.accountPreference];
  });
  $("account-display-name").defaultValue = researcher.display_name;
  $("account-display-name").value = researcher.display_name;
  navigationPreference = researcher.sidebar;
  applyNavigationPreference();
  window.applyResearcherTheme(researcher.theme);
}

async function saveAccountPreference(key, value) {
  const result = await api("/api/admin/account/preferences", {[key]: value});
  researcher[key] = result[key];
  if (key === "theme") window.applyResearcherTheme(result.theme);
  if (key === "sidebar") {
    navigationPreference = result.sidebar;
    applyNavigationPreference();
  }
}

async function refreshSettings() {
  const status = $("settings-load-status");
  status.textContent = "";
  try {
    const {login_count: logins, chat_count: chats, workspace} = await api("/api/admin/account/settings");
    $("settings-chat-count").textContent = chats + (chats === 1 ? " saved conversation" : " saved conversations");
    $("settings-login-count").textContent = logins === 1 ? "This browser" : logins + " active sign-ins including this browser";
    $("sign-out-others").disabled = logins < 2;
    const enrollment = {active:"Enrolling", draft:"Draft", closed:"Closed"}[workspace.status] || workspace.status;
    $("settings-study").textContent = workspace.study + " · " + enrollment;
    const provider = {demo:"Demo", openai:"OpenAI", relay:"Local relay"}[workspace.provider] || workspace.provider;
    const keyStatus = workspace.openai_configured ? "OpenAI key configured" : "OpenAI key absent";
    $("settings-provider").textContent = [provider, workspace.model, keyStatus].filter(Boolean).join(" · ");
    const dollars = new Intl.NumberFormat("en-US", {style:"currency", currency:"USD", maximumFractionDigits:4});
    const budget = workspace.budget;
    $("settings-budget").textContent = dollars.format(budget.committed_micros/1e6) + " accounted for · " +
      dollars.format(budget.reserved_session_micros/1e6) + " reserved · " + dollars.format(budget.cap_micros/1e6) + " limit";
  } catch (err) { status.textContent = err.message; }
}

async function refreshResearchers() {
  if (!researcher?.username || researcher.role !== "owner") return;
  const status = $("researchers-status");
  try {
    const accounts = await api("/api/admin/account/researchers");
    $("researcher-list").innerHTML = accounts.map(account => `<li><span><strong>${esc(account.display_name || account.username)}</strong>
      <small>${account.display_name ? esc("@" + account.username) + " · " : ""}${account.role === "owner" ? "Owner" : account.disabled ? "Disabled" : "Researcher"}</small></span>
      ${account.role === "owner" ? "" : `<button class="secondary" type="button" data-researcher-id="${account.id}"
        data-researcher-disabled="${!account.disabled}" aria-label="${account.disabled ? "Enable" : "Disable"} ${esc(account.username)}">${account.disabled ? "Enable" : "Disable"}</button>`}</li>`).join("");
  } catch (err) { status.textContent = err.message; }
}

function submitSettings(formId, statusId, submit) {
  $(formId).onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const status = $(statusId);
    button.disabled = true;
    status.textContent = "";
    try {
      await submit(Object.fromEntries(new FormData(form)));
      form.reset();
    } catch (err) { status.textContent = err.message; }
    finally { button.disabled = false; }
  };
}

$("login-form").onsubmit = async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  const error = $("login-error");
  const password = $("access-key").value;
  const data = health.accounts_configured ? {username: $("login-username").value.trim(), password} : {token: password};
  button.disabled = true;
  error.classList.add("hidden");
  try {
    await api("/api/login", data);
    $("access-key").value = "";
    await initialize();
  } catch (err) {
    error.textContent = err.message;
    error.classList.remove("hidden");
  } finally { button.disabled = false; }
};

document.querySelectorAll("[data-settings-jump]").forEach(button => {
  button.onclick = () => $(button.dataset.settingsJump).scrollIntoView({block:"start"});
});
document.querySelectorAll("[data-account-preference]").forEach(field => field.onchange = async event => {
  const field = event.currentTarget;
  const key = field.dataset.accountPreference;
  const status = $(key === "chat_scope" ? "chat-preference-status" : "appearance-status");
  field.disabled = true;
  if (key === "sidebar") $("toggle-sidebar").disabled = true;
  status.textContent = "";
  try {
    await saveAccountPreference(key, field.value);
    status.textContent = "Saved.";
  } catch (err) {
    field.value = researcher[key];
    status.textContent = err.message;
  } finally {
    field.disabled = false;
    if (key === "sidebar") $("toggle-sidebar").disabled = false;
  }
});

submitSettings("claim-account-form", "account-status", async data => {
  await api("/api/admin/account/claim", data);
  researcher = await api("/api/admin/account");
  health.accounts_configured = true;
  applyResearcherSettings();
  $("account-status").textContent = "Owner account created.";
  refreshSettings();
  refreshResearchers();
});
submitSettings("profile-form", "account-status", async data => {
  await saveAccountPreference("display_name", data.display_name);
  applyResearcherSettings();
  $("account-status").textContent = "Display name saved.";
});
submitSettings("change-password-form", "account-status", async data => {
  await api("/api/admin/account/password", data);
  $("account-status").textContent = "Password updated.";
  refreshSettings();
});
submitSettings("add-researcher-form", "researchers-status", async data => {
  await api("/api/admin/account/researchers", data);
  $("researchers-status").textContent = "Researcher added.";
  await refreshResearchers();
});
$("researcher-list").onclick = async event => {
  const button = event.target.closest("[data-researcher-id]");
  if (!button) return;
  button.disabled = true;
  const disabled = button.dataset.researcherDisabled === "true";
  try {
    await api("/api/admin/account/access", {id: Number(button.dataset.researcherId), disabled});
    $("researchers-status").textContent = disabled ? "Researcher disabled." : "Researcher enabled.";
    await refreshResearchers();
  } catch (err) { $("researchers-status").textContent = err.message; }
  finally { button.disabled = false; }
};
$("researcher-sign-out").onclick = async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/logout", {});
    location.reload();
  } catch (err) {
    $("account-status").textContent = err.message;
    button.disabled = false;
  }
};
$("sign-out-others").onclick = async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const {revoked} = await api("/api/admin/account/sign-out-others", {});
    $("account-status").textContent = revoked ? "Other browsers signed out." : "Only this browser is signed in.";
    await refreshSettings();
  } catch (err) {
    $("account-status").textContent = err.message;
    button.disabled = false;
  }
};
$("exports").innerHTML = ["sessions","messages","events","decisions","revisions","provider_spend"].map(t => `<div class="export-card"><h3>${({sessions:"Sessions",messages:"Conversations",events:"Interaction events",decisions:"Final decisions",revisions:"Study configurations",provider_spend:"API spend ledger"})[t]}</h3><a href="/api/admin/export?table=${t}&format=csv">CSV ↓</a><a href="/api/admin/export?table=${t}&format=json">JSON ↓</a></div>`).join("");
$("session-search").oninput=renderSessionTable;
$("session-filter").onchange=renderSessionTable;
let setupCheckVersion = 0, setupCheckBusy = false;
function updateSetupActions() {
  $("save-draft").classList.remove("hidden");
  $("save-draft").disabled = !slotDirty || slotBusy;
  $("save-draft").textContent = newSetup ? "Save draft" : "Save changes";
  $("preview-slot").classList.remove("hidden");
  $("preview-slot").disabled = slotBusy || setupCheckBusy;
  $("preview-slot").textContent = slotDirty ? "Save & test" : "Test this setup";
  $("designer-check").disabled = slotBusy || setupCheckBusy;
}
function setupReviewCard(key, title, icon, rows) {
  return `<article class="setup-review-card">
    <header><span class="feature-icon tone-${setupTones[key]}">${setupIcon(icon)}</span><h3>${title}</h3>
    <button class="text-button" type="button" data-edit-setup="${key}" aria-label="Edit ${title}">${setupIcon("edit")} Edit</button></header>
    <dl>${rows.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value || "Not set")}</dd></div>`).join("")}</dl>
  </article>`;
}
function renderSetupReview() {
  if (!draft) return;
  const provider = $("config-provider").value;
  const packed = Boolean(draft.case_study);
  const negotiation = $("config-task").value === "negotiation";
  const task = packed ? slotCaseTitle(draft) : modeName($("config-task").value);
  const groups = draft.conditions.map((group, index) => {
    const current = index === selected;
    const name = (current ? $("cond-label").value : group.label).trim() || "Untitled group";
    const mode = (current && $("condition-tabs").querySelector(`[data-condition="${index}"] small`)?.textContent) ||
      conditionMode(group);
    return mode && mode.toLowerCase() !== name.toLowerCase() ? `${name} · ${mode}` : name;
  });
  const model = provider === "openai" ? $("config-model").value.trim() :
    provider === "demo" ? "Built-in responses" : "Choose a provider";
  const cards = [
    setupReviewCard("study", "Study", "study", [
      ["Setup name", $("slot-label").value.trim()], ["Study title", $("config-name").value.trim()],
      ["Enrollment", setupChoiceText($("config-status"))],
    ]),
    setupReviewCard("task", "Scenario", "scenario", [
      [packed ? "Case template" : "Task type", task],
      ...(!packed ? [["Participant title", $(negotiation ? "neg-title" : "scenario-title").value.trim()]] : []),
    ]),
    setupReviewCard("conditions", "Groups", "groups", [
      ["Default assignment", setupChoiceText($("config-assignment"))],
      ...groups.map((name, i) => [`Group ${i + 1}`, name]),
    ]),
    setupReviewCard("engine", "AI model", "spark", [
      ["Provider", setupChoiceText($("config-provider"))], ["Model", model],
      ["Memory", setupChoiceText($("config-memory"))],
    ]),
  ];
  $("setup-review-summary").innerHTML = cards.join("");
  $("setup-review-save-note").textContent = slotDirty ?
    (newSetup ? "Save the draft to your library before running a test session." : "Save your changes before running a test session. The saved version is used for testing.") :
    "This version is saved. Run a test session to see what participants will experience.";
}
function showSetupStep(key, focus = false) {
  const page = ["task","conditions"].includes(key) ? "study" : key;
  if (!["study", "engine", "advanced"].includes(page)) return;
  document.querySelectorAll("[data-settings]").forEach(button => {
    const active = button.dataset.settings === page;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  document.querySelectorAll("[data-settings-panel]").forEach(panel => {
    const sectionPage = ["task","conditions"].includes(panel.dataset.settingsPanel) ? "study" : panel.dataset.settingsPanel;
    panel.classList.toggle("hidden", sectionPage !== page);
  });
  $("designer-task").classList.toggle("hidden", page !== "study");
  if (key === "task") $("designer-task").open = true;
  updateSetupActions();
  renderSetupReview();
  renderDesignerSummary();
  if (focus) {
    const heading = key === "task" ? $("designer-task").querySelector("summary") :
      $("setup-panel-" + key).querySelector("h2");
    heading.focus({preventScroll:true});
    heading.scrollIntoView({block:"start",behavior:"instant"});
  }
}
for (const button of document.querySelectorAll("[data-settings]")) {
  button.onclick = () => showSetupStep(button.dataset.settings);
}
$("setup-review-summary").onclick = event => {
  const button = event.target.closest("[data-edit-setup]");
  if (button) showSetupStep(button.dataset.editSetup, true);
};

function renderDesignerSummary() {
  if (!draft) return;
  const enabled = $("cond-agent_enabled").value === "true";
  const group = $("cond-label").value.trim() || "Untitled group";
  const task = draft.case_study ? slotCaseTitle(draft) : modeName($("config-task").value);
  const provider = $("config-provider").value;
  const mode = draft.case_study?.id === "ozer_game" ? conditionDescription(draft.conditions[selected], draft) :
    enabled ? ($("condition-mode-description").textContent || conditionMode(draft.conditions[selected])) :
    "Participants work independently.";
  $("designer-summary").innerHTML = facts([
    ["Selected group", group], ["Task", task],
    ["Provider", enabled ? (provider === "demo" ? "Built-in demo responses" : provider === "openai" ? $("config-model").value : "Provider needed") : "No agent"],
    ["Decision", mode],
  ]);
  $("designer-task-summary").textContent = draft.case_study ? task :
    $(draft.task === "negotiation" ? "neg-title" : "scenario-title").value.trim() || task;
  $("designer-example-note").classList.toggle("hidden", !newSetup || Boolean(draft.case_study));
  $("designer-study-checks").innerHTML = '<h3>Research controls</h3>' + facts([
    ["Consent", $("scenario-consent_text").value.trim() ? "Provided — review the wording in Study controls" : "Missing"],
    ["Interaction counts", $("config-telemetry").checked ? "Enabled" : "Disabled"],
    ["Clipboard text", $("config-clipboard-text").checked ? "Offered with separate participant permission" : "Off"],
    ["Outside AI question", $("config-outside-ai-question").checked ? "Optional, after completion" : "Off"],
    ["API connection", provider === "openai" ? (health.openai_configured ? "Key configured; verify with a test session" : "Server API key is missing") : "Not required for demo responses"],
    ["Study API cap", provider === "openai" ? "$" + $("config-spend_cap_usd").value : "No API spending for demo responses"],
    ["Per-session allowance", provider === "openai" ? "$" + $("config-session_spend_usd").value : "Not used"],
    ["Outcomes", "Decisions, conversations, and timing are recorded for each session"],
  ]);
}
function invalidateSetupCheck() {
  setupCheckVersion++;
  $("designer-public").classList.add("hidden");
  $("designer-errors").classList.add("hidden");
  $("designer-check-status").textContent = "Check the current draft before testing.";
}
function showDesignerError(error) {
  $("designer-check-status").textContent = "This draft needs attention.";
  $("designer-errors").textContent = error.message;
  $("designer-errors").classList.remove("hidden");
  $("designer-errors").scrollIntoView({block:"nearest",behavior:"instant"});
}
async function checkDesignerDraft() {
  if (setupCheckBusy || slotBusy) return false;
  const version = setupCheckVersion;
  setupCheckBusy = true;
  updateSetupActions();
  $("designer-errors").classList.add("hidden");
  $("designer-check-status").textContent = "Checking the draft with the server…";
  try {
    if (!$("slot-label").value.trim()) throw new Error("Enter a study name or set a saved setup label in Design.");
    const config = structuredClone(slotDirty ? draft : selectedSlot.config);
    if (slotDirty) collect(config);
    const group = config.conditions[selected];
    const result = await api("/api/admin/builder-preview", {config, condition_id:group.id});
    if (version !== setupCheckVersion) return false;
    const pub = result.public;
    const task = pub.scenario;
    $("designer-public-title").textContent = task.title || "Participant task";
    $("designer-public-instructions").textContent = task.instructions;
    $("designer-public-knowledge").textContent = task.knowledge || "No additional public information.";
    $("designer-public-mode").textContent = conditionDescription(group, config);
    $("designer-public").classList.remove("hidden");
    $("designer-check-status").textContent = config.architecture.provider === "openai" && !health.openai_configured ?
      "Configuration is valid. Tests that need live responses require the server’s OpenAI key." :
      "Configuration is valid. Review the participant brief, then run a test.";
    return true;
  } catch (error) {
    if (version === setupCheckVersion) showDesignerError(error);
    return false;
  } finally {
    setupCheckBusy = false;
    updateSetupActions();
  }
}
$("designer-check").onclick = checkDesignerDraft;
$("designer-edit-task").onclick = () => showSetupStep("task", true);
$("config-name").addEventListener("input", event => {
  const field = $("slot-label");
  if (newSetup && field.value === field.dataset.automaticLabel) {
    const title = event.target.value.trim();
    field.value = title ? nextDraftName(title) : "";
    field.dataset.automaticLabel = field.value;
  }
});

enhanceSetupFields($("setup-editor"));
// Hover and keyboard focus show help; tapping can keep it open.
const setupHelp = [...document.querySelectorAll(".help-hint")];
for (const hint of setupHelp) {
  const trigger = hint.querySelector(".help-trigger");
  trigger.onclick = () => {
    const open = !hint.classList.contains("help-open");
    setupHelp.forEach(other => other.classList.remove("help-open"));
    hint.classList.toggle("help-open", open);
    hint.classList.toggle("help-dismissed", !open);
  };
  const resetHelp = () => hint.classList.remove("help-dismissed");
  hint.addEventListener("pointerleave", resetHelp);
  trigger.addEventListener("blur", resetHelp);
}
document.addEventListener("click", event => {
  for (const hint of setupHelp) if (!hint.contains(event.target)) hint.classList.remove("help-open");
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  const visible = setupHelp.filter(hint =>
    hint.getClientRects().length && getComputedStyle(hint.querySelector(".setup-tooltip")).visibility === "visible");
  for (const hint of visible) {
    hint.classList.remove("help-open");
    hint.classList.add("help-dismissed");
  }
  if (visible.length) event.preventDefault();
}, true);
document.querySelectorAll("[data-open-dialog]").forEach(b=>b.onclick=()=>$(b.dataset.openDialog).showModal());
function dismissDialog(dialog) {
  if (dialog.dataset.busy !== "true") dialog.close();
}
document.querySelectorAll("[data-close-dialog]").forEach(b=>b.onclick=()=>dismissDialog($(b.dataset.closeDialog)));
document.querySelectorAll("dialog").forEach(dialog=>dialog.addEventListener("click",e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dismissDialog(dialog);}}));
for(const type of ["input","change"]) $("tab-conditions").addEventListener(type,e=>{if(e.target.closest("#setup-editor") && e.target.matches("input,textarea,select") && e.target.id !== "case-study-select")markDirty();});
initialize().catch(error => { $("login").classList.remove("hidden"); alert(error.message); });

window.addEventListener("hashchange", guarded(() => switchTab(location.hash.slice(1) || researcher?.start_page || "overview", false)));
const commands = [
  ...Object.entries(pageNames).map(([page,label])=>({label,keywords:page,detail:"Page",run:guarded(()=>switchTab(page))})),
  {label:"Start a setup",keywords:"new setup custom scenario case study",detail:"Action",run:guarded(startCustomSetup)},
  {label:"Try the agent",keywords:"preview test participant",detail:"Action",run:guarded(previewFromPage)},
  {label:"Export study data",keywords:"download csv json",detail:"Action",run:()=>$("export-dialog").showModal()},
  {label:"Find a session",keywords:"search participant",detail:"Action",run:guarded(async()=>{if(await switchTab("sessions"))$("session-search").focus();})}
];
let commandIndex = 0, visibleCommands = commands;
function renderCommands() {
  const query = $("command-search").value.trim().toLowerCase();
  visibleCommands=commands.filter(c=>(c.label+" "+c.keywords).toLowerCase().includes(query));
  commandIndex=0;
  $("command-results").innerHTML=visibleCommands.length ? visibleCommands.map((c,i)=>`<button class="command-result ${i===0 ? "active" : ""}" data-command="${i}">${esc(c.label)}<span>${esc(c.detail)}</span></button>`).join("") : '<p class="command-empty">No matching pages or actions.</p>';
  document.querySelectorAll("[data-command]").forEach(button=>button.onclick=()=>runCommand(Number(button.dataset.command)));
}
function runCommand(index) { const command=visibleCommands[index];if(!command)return;$("command-dialog").close();command.run(); }
function openCommands() { if($("workspace").classList.contains("hidden"))return;$("command-search").value="";renderCommands();if(!$("command-dialog").open)$("command-dialog").showModal();$("command-search").focus(); }
$("open-command").onclick=openCommands;
$("command-search").addEventListener("input",renderCommands);
$("command-dialog").addEventListener("keydown",e=>{
  if(e.key==="ArrowDown"||e.key==="ArrowUp"){
    e.preventDefault();
    if(!visibleCommands.length)return;
    commandIndex=(commandIndex+(e.key==="ArrowDown"?1:-1)+visibleCommands.length)%visibleCommands.length;
    if(e.target===$("command-search")&&e.key==="ArrowDown")commandIndex=0;
    document.querySelectorAll("[data-command]").forEach((button,i)=>{button.classList.toggle("active",i===commandIndex);if(i===commandIndex)button.focus();});
  }
  if(e.key==="Enter"&&e.target===$("command-search")){e.preventDefault();runCommand(commandIndex);}
});
document.addEventListener("keydown",e=>{
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="k"){
    // Keep the native modal focus boundary when another dialog is open.
    const modal=document.querySelector("dialog[open]");
    if(modal&&modal!==$("command-dialog"))return;
    e.preventDefault();if($("command-dialog").open)$("command-dialog").close();else openCommands();
  }
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==="s"&&snapshot&&!$("tab-conditions").classList.contains("hidden")&&$("setup-home").classList.contains("hidden")&&!document.querySelector("dialog[open]")) {e.preventDefault();saveSetup();}
});

function detailSection(name) {
  document.querySelectorAll('[data-detail-tab]').forEach(button=>{const active=button.dataset.detailTab===name;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
  document.querySelectorAll('[data-detail-panel]').forEach(panel=>panel.classList.toggle('hidden',panel.dataset.detailPanel!==name));
}
document.querySelectorAll('[data-detail-tab]').forEach(button=>button.onclick=()=>detailSection(button.dataset.detailTab));
const sessionActions = {
  "revoke-session": ["Revoke access", "The participant will be returned to the end screen. The recorded session remains available for review."],
  "delete-session": ["Delete session", "Permanently remove the session, conversation, activity and outcomes from this workspace. Previously downloaded exports and provider charges are kept."],
  "revoke-launch": ["Revoke link", "This unused link will stop working. You can create a new link for the same participant."],
  "restore": ["Restore original", "Clear the current label and note. The original participant code will be shown, and the edit and restore will both stay in the history."],
};
let displayedSession = null, editingSession = null;
function showSessionAction(action, id, participant, editId = null) {
  const [label, description] = sessionActions[action];
  sessionAction = {action, id, editId};
  $("session-action-title").textContent = label + " for " + participant + "?";
  $("session-action-description").textContent = description;
  $("confirm-session-action").textContent = label;
  $("confirm-session-action").classList.toggle("danger", action !== "restore");
  $("session-action-error").classList.add("hidden");
  $("session-action-dialog").showModal();
}
$("unused-links").onclick = event => {
  const button = event.target.closest("[data-revoke-link]");
  if (!button) return;
  const link = pendingLinks.find(item => item.id === button.dataset.revokeLink);
  if (link) showSessionAction("revoke-launch", link.id, link.participant_id);
};
const sessionActionDialog = $("session-action-dialog");
sessionActionDialog.addEventListener("cancel", event => {
  if (sessionActionDialog.dataset.busy === "true") event.preventDefault();
});
$("session-action-form").onsubmit = async event => {
  event.preventDefault();
  if (sessionActionDialog.dataset.busy === "true" || !sessionAction) return;
  const {action, id, editId} = sessionAction;
  const buttons = event.currentTarget.querySelectorAll("button");
  const error = $("session-action-error");
  error.classList.add("hidden");
  buttons.forEach(button => { button.disabled = true; });
  sessionActionDialog.dataset.busy = "true";
  try {
    const field = action === "revoke-launch" ? "launch_id" : "session_id";
    if (action === "restore") await api("/api/admin/session/edit", {session_id:id, expected_edit_id:editId, restore:true});
    else await api("/api/admin/" + action, {[field]:id});
  } catch (failure) {
    error.textContent = failure.message;
    error.classList.remove("hidden");
    return;
  } finally {
    buttons.forEach(button => { button.disabled = false; });
    delete sessionActionDialog.dataset.busy;
  }
  sessionActionDialog.close();
  if (action === "delete-session" && currentDetailId === id) { await switchTab("sessions"); $("session-search").focus(); }
  if (["revoke-session", "restore"].includes(action) && currentDetailId === id) await loadSessionDetail(id, false);
  try { await refresh(); } catch (failure) { notice("Saved, but the session list could not refresh. " + failure.message, true); return; }
  if (action === "revoke-launch") document.querySelector('[data-session-view="links"]').focus();
  notice({ "delete-session":"Session deleted.", "revoke-session":"Session access revoked.", "revoke-launch":"Link revoked.",
    restore:"Original session details restored. The history was kept." }[action]);
};
const editSessionDialog = $("edit-session-dialog");
editSessionDialog.addEventListener("cancel", event => {
  if (editSessionDialog.dataset.busy === "true") event.preventDefault();
});
const actionMenuSelector = "details:is(.chat-actions, .setup-menu)";
const openActionMenus = () => document.querySelectorAll(`${actionMenuSelector}[open]`);
function closeActionMenu(menu) {
  menu.open = false;
  menu.querySelector(":scope > div").inert = true;
}
document.addEventListener("toggle", event => {
  const menu = event.target;
  if (!menu.matches?.(actionMenuSelector)) return;
  // A menu can still be painted during its exit, but its actions are already unavailable.
  menu.querySelector(":scope > div").inert = !menu.open;
  if (menu.open) openActionMenus().forEach(other => { if (other !== menu) closeActionMenu(other); });
}, true);
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  const menu = document.activeElement.closest(`${actionMenuSelector}[open]`);
  if (menu) {
    event.preventDefault();
    closeActionMenu(menu);
    menu.querySelector("summary").focus({preventScroll:true});
  }
});
document.addEventListener("click", guarded(async event => {
  const triggerMenu = event.target.closest("summary")?.parentElement;
  // Prepare keyboard focus before the summary's native toggle runs.
  if (triggerMenu?.matches(actionMenuSelector)) triggerMenu.querySelector(":scope > div").inert = triggerMenu.open;
  const button = event.target.closest("[data-session-action]");
  openActionMenus().forEach(menu => {
    if (!menu.contains(event.target) || (button && menu.matches(".session-options"))) closeActionMenu(menu);
  });
  if (!button || button.disabled) return;
  const id = button.dataset.sessionId;
  const action = button.dataset.sessionAction;
  const session = (displayedSession?.id === id ? displayedSession : null) || overview?.sessions.find(item => item.id === id);
  if (!session) return;
  if (action !== "edit") {
    showSessionAction(action, id, session.label || session.participant_id, session.edit_id);
    return;
  }
  const sourceTab = activeTab;
  const result = displayedSession?.id === id ? displayedSession :
    (await api("/api/admin/session?session_id=" + encodeURIComponent(id))).session;
  if (activeTab !== sourceTab || document.querySelector("dialog[open]")) return;
  editingSession = result;
  $("edit-session-title").textContent = "Edit details for " + result.participant_id;
  $("session-edit-label").value = result.label;
  $("session-edit-note").value = result.note;
  $("edit-session-error").classList.add("hidden");
  editSessionDialog.showModal();
  $("session-edit-label").focus();
}));
$("edit-session-form").onsubmit = async event => {
  event.preventDefault();
  if (editSessionDialog.dataset.busy || !editingSession) return;
  const {id, edit_id} = editingSession;
  const error = $("edit-session-error");
  const controls = [...event.currentTarget.elements];
  editSessionDialog.dataset.busy = "true";
  controls.forEach(control => control.disabled = true);
  error.classList.add("hidden");
  try {
    await api("/api/admin/session/edit", {session_id:id, expected_edit_id:edit_id,
      label:$("session-edit-label").value, note:$("session-edit-note").value});
    editSessionDialog.close();
  } catch (err) {
    error.textContent = err.message;
    error.classList.remove("hidden");
    return;
  } finally {
    delete editSessionDialog.dataset.busy;
    controls.forEach(control => control.disabled = false);
  }
  if (currentDetailId === id) await loadSessionDetail(id, false);
  try { await refresh(); } catch (err) { notice("Saved, but the session list could not refresh. " + err.message, true); return; }
  notice("Session details saved.");
};
$('refresh-session-detail').onclick=()=>{if(currentDetailId)loadSessionDetail(currentDetailId,false);};
function facts(items) { return '<dl class="review-facts">'+items.map(([key,value])=>`<div><dt>${esc(key)}</dt><dd>${esc(value ?? 'Not recorded')}</dd></div>`).join('')+'</dl>'; }
function metric(label,value,note) { return `<article class="review-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`; }
function percent(value) {return value==null ? 'Not recorded' : (value*100).toFixed(1)+' / 100';}
function sessionAnnotations(session, history) {
  if (!history.length) return "";
  const restore = session.label || session.note ?
    `<button type="button" class="secondary" data-session-action="restore" data-session-id="${esc(session.id)}">Restore original</button>` : "";
  return `<article class="card session-annotations"><div class="card-title"><h2>Session details</h2>${restore}</div>
    ${facts([["Original participant code", session.participant_id], ["Display label", session.label || "Original"],
      ["Researcher note", session.note || "None"]])}
    <details class="disclosure"><summary>Edit history (${history.length})</summary>
      <ol class="session-edit-history">${history.map(edit => `<li><strong>${edit.action === "restore" ? "Restored original" : "Edited details"}</strong>
      <span>${esc(edit.actor_name)} · ${esc(dateTime(edit.created_at))}</span>
      ${edit.action === "edit" ? facts([["Display label", edit.label || "Original"], ["Researcher note", edit.note || "None"]]) : ""}</li>`).join("")}</ol>
    </details></article>`;
}
async function loadSessionDetail(id, reset = true) {
  const ticket=++detailLoad;
  displayedSession = null;
  $('session-detail-error').classList.add('hidden');$('session-detail-loading').classList.remove('hidden');$('session-detail-content').classList.add('hidden');$('download-session').classList.add('hidden');
  $('manage-session').classList.add('hidden');
  $('session-detail-title').textContent='Session review';$('session-detail-subtitle').textContent='';$('detail-session-code').textContent='';$('refresh-session-detail').disabled=true;
  try {
    const data=await api('/api/admin/session?session_id='+encodeURIComponent(id));
    if(ticket!==detailLoad)return;
    renderSessionDetail(data);
    if(reset)detailSection('overview');
    $('session-detail-content').classList.remove('hidden');
  } catch(error) {
    if(ticket!==detailLoad)return;
    $('session-detail-error').textContent=error.message==='Not found' ? 'Restart the Python server to enable session reviews, then refresh this page.' : error.message;$('session-detail-error').classList.remove('hidden');
  } finally {if(ticket===detailLoad){$('session-detail-loading').classList.add('hidden');$('refresh-session-detail').disabled=false;}}
}
const aiVerdictLabels = {likely_used:"Likely used outside AI", likely_not_used:"Likely did not use outside AI", unclear:"Unclear"};
const clipboardAreas = {prompt:"Chat input", reason:"Decision reason", task_answer:"Task answer", study_task:"Task content",
  agent_reply:"Study agent reply", participant_message:"Participant message"};
function renderAiEvidence(session, evidence) {
  const panel = $("detail-ai-use");
  if (!evidence) {
    panel.innerHTML = '<article class="card"><p>Restart the server to enable AI use review.</p></article>';
    return;
  }
  const clipboard = evidence.clipboard;
  const copies = clipboard.filter(clip => clip.direction === "copy").length;
  const pastes = clipboard.length - copies;
  const clipCount = `${copies} ${copies === 1 ? "copy" : "copies"} · ${pastes} ${pastes === 1 ? "paste" : "pastes"}`;
  const report = {yes:"Reported using outside AI", no:"Reported no outside AI use", prefer_not:"Prefer not to say"}[evidence.outside_ai_answer]
    || (evidence.outside_ai_question ? "No answer recorded" : "Question was off");
  const permission = !evidence.clipboard_offered ? "Not offered" : evidence.clipboard_consent ? "Granted" : "Not granted";
  const latest = evidence.reviews[0];
  panel.innerHTML = `<article class="card ai-evidence-card"><div class="card-title"><h2>AI use evidence</h2></div>
    ${facts([["Study agent outputs", String(evidence.in_app_outputs)], ["Model-generated outputs", String(evidence.in_app_model_outputs)],
      ["Outside AI self-report", report], ["Clipboard permission", permission], ["Text captured", clipCount]])}
    <p class="help">Outputs show what the study agent produced. Clipboard activity cannot identify the source app or prove outside AI use. No recorded paste does not rule it out.</p></article>
    <article class="card ai-evidence-card"><h2>Clipboard text</h2>
    ${clipboard.length ? `<ol class="clipboard-evidence-list">${clipboard.map(clip => `<li><div class="clipboard-evidence-meta">
      <strong>${clip.direction === "copy" ? "Copied" : "Pasted"} · ${esc(clipboardAreas[clip.area] || clip.area)}</strong>
      <span>${esc(dateTime(clip.received_at))} · Round ${esc(clip.round_id)}</span></div>
      <pre>${esc(clip.text)}</pre>${clip.truncated ? '<p class="help">Excerpt · text was truncated.</p>' : ''}
      ${clip.direction === "paste" ? `<p class="help">${clip.matches_study_copy ? "Matches text copied earlier in this study." : "Source unknown."}</p>` : ''}</li>`).join("")}</ol>` :
      '<p class="help">No clipboard text was captured for this session.</p>'}
    ${clipboard.length >= evidence.clipboard_limit ? `<p class="help">This session reached its ${esc(evidence.clipboard_limit)}-action capture limit.</p>` : ''}</article>
    <article class="card ai-evidence-card"><h2>Researcher judgment</h2>
    <p>${latest ? `<strong>${esc(aiVerdictLabels[latest.verdict])}</strong> · ${esc(latest.actor_name)} · ${esc(dateTime(latest.created_at))}` : "No assessment saved."}</p>
    ${latest?.note ? `<p class="ai-review-note">${esc(latest.note)}</p>` : ""}
    <p class="help">Record your interpretation separately from the participant’s report. Choose Unclear when the evidence does not support a judgment.</p>
    ${session.status === "active" ? '<p class="help">You can assess this session after it ends.</p>' : `<form id="ai-evidence-form">
      <label>Assessment<select name="verdict" required><option value="">Choose an assessment</option>
      ${Object.entries(aiVerdictLabels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select></label>
      <label>Evidence or reasoning <small>Optional</small><textarea name="note" maxlength="1000" rows="2"></textarea></label>
      <button class="secondary" type="submit">Save assessment</button><p id="ai-evidence-error" class="help" role="status"></p>
    </form>`}
    ${evidence.reviews.length > 1 ? `<details class="disclosure"><summary>Assessment history (${evidence.reviews.length})</summary><ol class="session-edit-history">${evidence.reviews.map(item =>
      `<li><strong>${esc(aiVerdictLabels[item.verdict])}</strong><span>${esc(item.actor_name)} · ${esc(dateTime(item.created_at))}</span>${item.note ? `<p class="ai-review-note">${esc(item.note)}</p>` : ""}</li>`).join("")}</ol></details>` : ''}</article>`;
  const form = $("ai-evidence-form");
  if (form) form.onsubmit = async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    const button = event.submitter;
    button.disabled = true;
    try {
      await api("/api/admin/session/ai-assessment", {...data, session_id:session.id});
      if (currentDetailId === session.id) {
        await loadSessionDetail(session.id, false);
        notice("Assessment saved.");
      }
    } catch (error) { form.querySelector("#ai-evidence-error").textContent = error.message; }
    finally { button.disabled = false; }
  };
}
function renderSessionDetail(data) {
  const s=data.session, d=data.decision, setup=data.setup, condition=setup.condition;
  displayedSession = s;
  renderAiEvidence(s, data.usage_evidence);
  const isNegotiation=!setup.case_study&&setup.task==='negotiation';
  const packOutcome=setup.case_study&&d ? JSON.parse(d.outcome_json) : null;
  const priceLabel=value=>value==null ? 'No price' : setup.negotiation.currency+' '+value.toFixed(2)+' / unit';
  const option=id=>setup.scenario.options.find(o=>o.id===id)?.label || (id ? 'Unknown option' : 'No choice');
  const assistant=data.messages.filter(m=>m.role==='assistant').at(-1);
  const recommended=setup.case_study ? (assistant?.decision_action_json ? JSON.parse(assistant.decision_action_json) : null) : d ? (isNegotiation ? d.recommended_price : d.recommended_option) : isNegotiation ? assistant?.offer_price : assistant?.option_id;
  const choiceLabel=value=>setup.case_study ? (value ? JSON.stringify(value) : 'No recorded action') : isNegotiation ? priceLabel(value) : option(value);
  const finalChoice=setup.case_study ? packOutcome?.decision_action : d ? (isNegotiation ? d.final_price : d.final_option) : null;
  const decisionSummary=d ? (isNegotiation ? (d.agreement ? 'Simulated agreement reached' : d.action==='accept' ? 'Accepted the agent’s price advice' : d.action==='override' ? 'Recorded a different price · no agreement' : d.action==='reject' ? 'Finished without agreement' : 'Recorded an independent price') : actionNames[d.action]) : '';

  const status = sessionStatusLabels[s.status] || s.status;
  const manage = $("manage-session");
  manage.innerHTML = sessionMenu(s);
  manage.classList.remove("hidden");
  $('session-detail-title').textContent=s.label || s.participant_id;
  document.title=(s.label || s.participant_id)+' · Session review';
  $('detail-session-code').textContent='Session '+s.id.slice(0,8)+' · Round '+s.round_id;
  $('session-detail-subtitle').innerHTML=`${s.label ? esc(s.participant_id) + " · " : ""}${esc(condition.label)} <span class="detail-status" data-status="${esc(s.status)}">${esc(status)}</span>${s.preview ? '<span class="preview-tag">PREVIEW</span>' : ''}${s.edit_id ? '<span class="session-edited">Edited</span>' : ''}<span class="detail-started">Started ${esc(dateTime(s.created_at))}</span>`;
  $('download-session').href='/api/admin/session?session_id='+encodeURIComponent(s.id)+'&download=1';$('download-session').classList.remove('hidden');
  $('detail-message-count').textContent=data.messages.length;
  $('session-metrics').innerHTML=metric('Final choice',d ? choiceLabel(finalChoice) : 'Not submitted',d ? decisionSummary : status)+metric('Session duration',s.duration_ms==null ? 'Not ended' : duration(s.duration_ms),'Elapsed time · includes pauses')+metric('Conversation',data.metrics.prompt_count+(data.metrics.prompt_count===1 ? ' prompt' : ' prompts'),data.metrics.reply_count+(data.metrics.reply_count===1 ? ' assistant reply' : ' assistant replies'))+metric('After advice',d?.client_deliberation_ms!=null ? duration(d.client_deliberation_ms) : 'Unavailable','Browser time from exposure to decision');
  const outcome=d ? `<div class="choice-comparison"><div><span>${setup.case_study ? 'Agent action' : isNegotiation ? 'Agent price' : 'Agent recommended'}</span><strong>${esc(choiceLabel(recommended))}</strong></div><span class="choice-connector" aria-hidden="true">→</span><div><span>${d.action==='autonomous' ? 'Agent committed' : setup.case_study ? 'Participant recorded' : isNegotiation ? 'Participant recorded' : 'Participant selected'}</span><strong>${esc(choiceLabel(finalChoice))}</strong></div></div><div class="decision-outcome"><span class="outcome-tag">${esc(decisionSummary)}</span><span>${esc(dateTime(d.created_at))}</span></div><h3>Participant’s reason</h3><p class="participant-reason">${esc(d.reason || 'No reason supplied.')}</p>` : `<div class="review-empty"><h3>${s.status==='withdrawn' ? 'The participant stopped this session.' : s.status==='revoked' ? 'The researcher revoked access.' : 'No final choice has been submitted.'}</h3><p>${recommended ? 'The latest agent proposal is '+esc(choiceLabel(recommended))+'.' : 'The agent has not issued a current recommendation.'}</p><p>${data.metrics.pending_requests ? 'A response is still pending.' : 'Recorded conversation and activity are available in the sections above.'}</p></div>`;
  $('detail-overview').innerHTML=`<div class="review-grid"><article class="card decision-review"><div class="card-title"><h2>Decision summary</h2><span class="review-kicker">${esc(status)}</span></div>${outcome}${d?.outcome_score!=null ? `<details class="disclosure"><summary>Task benchmark <span class="summary-note">${esc(percent(d.outcome_score))}</span></summary><div class="disclosure-content">${facts([[isNegotiation ? 'Public price position' : 'Public weighted score',percent(d.outcome_score)],[isNegotiation ? 'Gap from favourable public endpoint' : 'Gap from highest public score',(d.score_regret*100).toFixed(1)+' points']])}<p class="help">${isNegotiation ? 'Computed from the public price range and participant role; a lower price favours a buyer and a higher price favours a supplier.' : 'Computed from the saved public criteria and weights.'} This is a public task benchmark, not an observed payoff. Private limits are excluded.</p></div></details>` : ''}</article><article class="card timing-review"><h2>Timing & collection</h2>${facts([['Consent recorded',dateTime(s.consent_at)],['Last recorded activity',dateTime(s.last_activity_at)],['Session ended',s.ended_at==null ? 'Not ended' : dateTime(s.ended_at)],['Browser time after advice',duration(d?.client_deliberation_ms)],['Server time after exposure',duration(d?.server_since_seen_ms)]])}<p class="help">Advice timing includes pauses. Browser timing can be unavailable after a reload. Neither measure proves attention or comprehension.</p></article></div>`;
  $('detail-conversation').innerHTML=`<article class="card transcript-card"><div class="card-title"><h2>Full conversation</h2><span class="review-kicker">${data.metrics.prompt_characters.toLocaleString()} prompt characters</span></div>${data.messages.length ? `<ol class="session-transcript">${data.messages.map(m=>{
    const meta=m.metadata;
    const latency = meta.latency_ms != null ? 'Response time: ' + duration(meta.latency_ms) : '';
    return `<li class="transcript-message ${m.role==='assistant' ? 'assistant' : 'participant'}"><div class="transcript-heading"><strong>${m.role==='assistant' ? 'Study assistant' : 'Participant'}</strong><time>${esc(dateTime(m.created_at))}</time></div><p>${esc(m.content)}</p>${m.role==='assistant' ? `<div class="transcript-meta">${m.offer_price!=null ? `<span>Agent price: ${esc(priceLabel(m.offer_price))}</span>` : m.option_id ? `<span>Agent choice: ${esc(option(m.option_id))}</span>` : ''}${latency ? `<span>${esc(latency)}</span>` : ''}<span>${m.seen_at ? 'Exposure reported '+esc(dateTime(m.seen_at)) : 'No exposure report'}</span></div><details class="disclosure"><summary>Response details</summary><div class="disclosure-content">${facts([['Provider',meta.provider],['Response source',meta.response_source],['Requested model',meta.requested_model],['Returned model',meta.returned_model],['Engine version',meta.engine_version]])}</div></details>` : m.request_status==='pending'||m.request_status==='failed' ? `<span class="request-state">Response ${esc(m.request_status)}</span>` : ''}</li>`;
  }).join('')}</ol>` : '<div class="review-empty"><h3>No messages recorded.</h3><p>This participant has not submitted a prompt.</p></div>'}<p class="help transcript-note">Relay wait is shown separately from model response time. Unsent drafts are not part of this transcript.</p></article>`;
  if (data.ozer_rounds) {
    const rounds = data.ozer_rounds;
    const profit = rounds.filter(r => !r.practice).reduce((total, r) => total + r[r.participant_role+"_profit"], 0);
    const headings = ["Round", "Phase", "Signal X", "Forecast F", "Production Q", "Shock Y", "Demand", "Units sold", "Supplier profit", "Retailer profit", "Decision time"];
    const rows = rounds.map(r => [r.round_id, r.practice ? "Practice" : "Paid", r.signal, r.forecast, r.production, r.shock,
      r.demand, r.sales, r.supplier_profit, r.retailer_profit, duration((r.human_submitted_at-r.decision_opened_at)*1000)]);
    $('session-metrics').innerHTML = metric("Rounds settled", rounds.length+" of 23", condition.label) +
      metric("Participant paid profit", profit.toLocaleString(), "Experimental dollars") +
      metric("Session duration", duration(s.duration_ms), status);
    $('detail-overview').innerHTML = `<article class="card"><div class="card-title"><h2>Round results</h2>
      <span class="review-kicker">Researcher only</span></div>
      ${rows.length ? `<div class="ozer-round-table"><table aria-label="Ozer round results">
        <thead><tr>${headings.map(h=>`<th scope="col">${esc(h)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` :
        '<p>No rounds have settled.</p>'}</article>`;
    $('detail-conversation').innerHTML = '<article class="card"><h2>Counterpart decisions</h2><p>Forecasts and production quantities are recorded in Round results. Per-round decision events are available in the session download.</p></article>';
  }
  if(packOutcome?.metric)$('detail-overview').insertAdjacentHTML('beforeend',`<article class="card"><h2>Recorded outcome metric</h2><pre class="snapshot-text">${esc(JSON.stringify(packOutcome.metric,null,2))}</pre><p class="help">This is a synthetic task benchmark. Forecast reference error is not measured against future actual demand.</p></article>`);
  if(s.status==='ended_system_limit')$('detail-overview').insertAdjacentHTML('afterbegin',`<article class="card"><h2>System-limit flag</h2><p>${esc(s.system_message)}</p>${facts([['Terminal state',s.status],['Reason',s.end_reason]])}</article>`);
  $('detail-overview').insertAdjacentHTML('beforeend', sessionAnnotations(s, data.edit_history));
  const a=data.activity, totals=a.totals;
  const eventLabel=e=>({session_ended:'Session ended by system limit',budget_reserved:'Session budget reserved',budget_warning:'Global budget at 80% allocation',output_replaced:'Unsafe response replaced',tool_call:'Tool execution recorded',decision_committed:'Study decision committed',page_open:'Study page opened',page_exit:'Study page exited',recommendation_visible:'Recommendation exposure reported',focus:e.payload.state==='focused' ? 'Window focused' : 'Window blurred',visibility:e.payload.state==='visible' ? 'Tab became visible' : 'Tab hidden'}[e.type] || e.type);
  $('detail-activity').innerHTML=`<div class="activity-metric-grid">${metric('Clicks',totals.clicks.toLocaleString(),'Within the study page')}${metric('Key presses',totals.keystrokes.toLocaleString(),'Counts only · no raw keys')}${metric('Copy / paste',totals.copies+' / '+totals.pastes,'Clipboard contents are not recorded')}${metric('Typing bursts',duration(totals.typing_ms),'Intervals between reported key presses')}</div><div class="review-grid"><article class="card"><h2>Recorded activity</h2><p class="help" id="activity-shown" role="status"></p><div class="activity-toolbar"><label><span class="visually-hidden">Filter activity reports</span><select id="activity-filter"><option value="all">All activity</option><option value="recommendation_visible">Advice exposures</option><option value="attention">Focus & visibility</option><option value="pages">Page visits</option></select></label></div>${a.timeline.length ? `<ol class="activity-timeline">${a.timeline.map(e=>`<li data-event-type="${esc(e.type)}"><span class="timeline-dot"></span><div><strong>${esc(eventLabel(e))}</strong><time>${esc(dateTime(e.received_at))}</time></div><span>${esc(duration(Math.max(0,(e.received_at-s.created_at)*1000)))} into session</span></li>`).join('')}</ol>` : '<div class="review-empty"><p>No activity reports recorded.</p></div>'}<button id="more-activity" class="secondary activity-more">Show more reports</button></article><article class="card"><h2>Collection notes</h2>${facts([['Detailed counts',setup.telemetry_enabled ? 'Enabled for this session' : 'Disabled for this session'],['Total reports',a.event_count],['Supplier hover time',duration(totals.hover_ms)],['Paste characters',totals.paste_characters],['Cut actions',totals.cut],['Failed responses',data.metrics.failed_requests],['Pending responses',data.metrics.pending_requests]])}<p class="help">Reports are supplied by the browser and can be incomplete. Zero means no count was received; it does not establish that an action never happened. No off-page activity is collected. This view covers the latest 80 lifecycle/exposure reports. Download the session for all raw activity reports, including interaction count batches.</p></article></div>`;
  if(setup.case_study){$('detail-activity').insertAdjacentHTML('beforeend',`<article class="card"><h2>Tool execution records</h2>${a.timeline.filter(e=>e.type==='tool_call').map(e=>`<details class="disclosure"><summary>${esc(e.payload.tool_name)} · ${esc(e.payload.status)} · ${esc(e.payload.latency_ms)} ms</summary><div class="disclosure-content"><pre class="snapshot-text">${esc(JSON.stringify({dataset_version:e.dataset_version,...e.payload},null,2))}</pre></div></details>`).join('') || '<p>No tools executed.</p>'}<p class="help">Download the session for every tool record.</p></article>`);}
  let visibleActivity=12;
  function updateActivity() {
    const filter=$('activity-filter').value;
    const rows=[...document.querySelectorAll('.activity-timeline li')];
    const matching=rows.filter(row=>filter==='all'||row.dataset.eventType===filter||filter==='attention'&&['focus','visibility'].includes(row.dataset.eventType)||filter==='pages'&&['page_open','page_exit'].includes(row.dataset.eventType));
    rows.forEach(row=>row.classList.toggle('hidden',!matching.includes(row)||matching.indexOf(row)>=visibleActivity));
    $('activity-shown').textContent=Math.min(visibleActivity,matching.length)+' shown · '+a.timeline_total+' browser and server events recorded · newest first';
    $('more-activity').classList.toggle('hidden',visibleActivity>=matching.length);
  }
  $('activity-filter').onchange=()=>{visibleActivity=12;updateActivity();};
  $('more-activity').onclick=()=>{visibleActivity+=12;updateActivity();};
  updateActivity();
  $('detail-setup').innerHTML=`<div class="snapshot-banner"><span class="panel-symbol" aria-hidden="true">◈</span><div><strong>Setup at the time of this session</strong><p>Config v${s.config_version} · round ${s.round_id} · Later edits to the study do not change this record.</p></div></div><div class="review-grid"><article class="card"><h2>Study & condition</h2>${facts([['Study',setup.name],['Task',setup.case_study ? setup.case_study.id : modeName(setup.task)],['Scenario',setup.scenario.title],['Condition',condition.label],['Condition ID',condition.id],['Authority',condition.agent?.mode || ({recommendation:'Recommendation',provisional_decision:'Provisional choice with human override',chat:'Conversation only'})[condition.authority]],['Agent availability',condition.agent_enabled ? 'Enabled' : 'Off · human-only baseline'],['Role',condition.role],['Choice policy',condition.selection],['Prompting',condition.reasoning],['Output',condition.output],['Private policy',condition.private_mode ? (setup.case_study ? 'Role-filtered data + output check' : 'Enabled · controlled responses') : 'Not enabled']])}<details class="disclosure"><summary>Objective & instructions</summary><div class="disclosure-content"><h3>Objective</h3><p class="snapshot-text">${esc(condition.objective)}</p><h3>Agent instructions</h3><p class="snapshot-text">${esc(condition.prompt)}</p><h3>Participant task</h3><p class="snapshot-text">${esc(setup.scenario.instructions)}</p><h3>Consent text</h3><p class="snapshot-text">${esc(setup.scenario.consent_text)}</p></div></details></article><article class="card"><h2>Response engine</h2>${facts([['Provider',setup.architecture.provider],['Session model snapshot',s.model_snapshot],['Dataset version',s.dataset_version],['Fixed temperature',setup.architecture.temperature ?? 'Not applicable'],['Memory',setup.architecture.memory==='session' ? 'This participant’s session' : 'No previous messages'],['Knowledge version',setup.architecture.knowledge_version],['API version',setup.architecture.api_version]])}<details class="disclosure"><summary>Record identifiers</summary><div class="disclosure-content">${facts([['Session ID',s.id],['Configuration hash',s.config_hash],['Consent version',s.consent_version]])}</div></details><p class="help">The configured model is the requested model. Actual returned model identifiers, when available, appear with individual responses.</p></article></div>${isNegotiation ? `<article class="card snapshot-options"><h2>Negotiation terms presented</h2>${facts([['Item',setup.negotiation.item],['Quantity',setup.negotiation.quantity],['Currency',setup.negotiation.currency],['Public unit-price range',setup.negotiation.price_min+'–'+setup.negotiation.price_max],['Participant role',setup.negotiation.participant_role]])}</article>` : `<article class="card snapshot-options"><h2>Options presented to the participant</h2><div class="table-wrap"><table><thead><tr><th>Supplier</th>${setup.scenario.criteria.map(c=>`<th>${esc(c.label)} (${esc(c.unit)})</th>`).join('')}</tr></thead><tbody>${setup.scenario.options.map(o=>`<tr><td>${esc(o.label)}</td>${setup.scenario.criteria.map(c=>`<td>${esc(o.values[c.id])}</td>`).join('')}</tr>`).join('')}</tbody></table></div><details class="disclosure"><summary>Scoring criteria</summary><div class="disclosure-content">${facts(setup.scenario.criteria.map(c=>[c.label,(c.direction==='min' ? 'Lower' : 'Higher')+' is preferred · weight '+c.weight]))}<p class="snapshot-text">${esc(setup.scenario.knowledge)}</p></div></details></article>`}`;
  if(setup.case_study){document.querySelector('#detail-setup .snapshot-options')?.remove();$('detail-setup').insertAdjacentHTML('beforeend',`<article class="card"><h2>Case study & agent configuration</h2>${facts([['Pack',setup.case_study.id+' @ '+setup.case_study.version],['Dataset',s.dataset_version],['Mode',condition.agent?.mode],['Tools',condition.agent?.allowed_tools.join(', ')],['Pack hash',setup.case_study.pack_hash],['Dataset hash',setup.case_study.dataset_hash]])}<details class="disclosure"><summary>Condition data access & loop limits</summary><pre class="snapshot-text">${esc(JSON.stringify(condition.agent,null,2))}</pre></details></article>`);}
}

$('cond-rules').addEventListener('input',()=>{try{const rules=JSON.parse($('cond-rules').value);if(rules?.min&&rules?.max){$('cond-price-min').value=rules.min.price ?? '';$('cond-price-max').value=rules.max.price ?? '';}}catch{/* The editor can keep an incomplete JSON draft until preview/save. */}});
