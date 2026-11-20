"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let token, session, launchToken, selected = null, latest = null, busy = false, finishing = false, tracking = false;
let pendingEvents = [], flushing = null, exposed = new Set();
let clipboardActionsQueued = 0;
let relayRequest = null, relayPolling = false, toastTimer;
const sessionParams = new URLSearchParams(location.search);
const builderPreview = sessionParams.get("builder-preview") === "1";
const compactScreen = matchMedia("(max-width:780px)");
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
let replyReveal = null;
let finalPrice = null, participantOffer = null;
const actionLabel=action=>Object.entries(action||{}).map(([key,value])=>key.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase())+': '+(typeof value==='number'?value.toLocaleString():value)).join(' · ');
const pageId = crypto.randomUUID();
let counts = emptyCounts(), lastKey = null, hoverStart = null;
function emptyCounts() { return {keystrokes:0,clicks:0,copies:0,pastes:0,cut:0,typing_ms:0,hover_ms:0,paste_characters:0}; }
function showError(error) { if (session?.status === "revoked") return; $("participant-notice").textContent = error.message || String(error); $("participant-notice").classList.remove("hidden"); }
async function api(path, body, keepalive = false) {
  if (builderPreview) throw new Error("This preview is read-only.");
  const response = await fetch("/api/participant/" + path, {method: body === undefined ? "GET" : "POST", keepalive,
    headers: {"Content-Type":"application/json","X-Research-Request":"1",...(token ? {Authorization:"Bearer " + token} : {})},
    ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const result = await response.json();
  if (!response.ok) throw Object.assign(new Error(result.error || "Request failed"), {status:response.status});
  return result;
}
function event(type, payload = {}) {
  if (!tracking) return;
  pendingEvents.push({id:crypto.randomUUID(),type,client_ms:performance.now(),client_wall_ms:Date.now(),page_id:pageId,round_id:session.round_id,payload});
}
function collectCounts() {
  if (!tracking || !session.public.telemetry_enabled) return;
  if (hoverStart !== null) { counts.hover_ms += performance.now() - hoverStart; hoverStart = performance.now(); }
  if (Object.values(counts).some(Boolean)) { event("interaction_counts", counts); counts = emptyCounts(); }
}
function endHover() {
  if (hoverStart !== null) { counts.hover_ms += performance.now() - hoverStart; hoverStart = null; }
}
async function flush(keepalive = false) {
  collectCounts();
  if (flushing) { await flushing; return pendingEvents.length ? flush(keepalive) : undefined; }
  if (!pendingEvents.length || !token) return;
  const hasClipboard = pendingEvents.some(e => e.type === "clipboard_text");
  const batch = pendingEvents.splice(0, hasClipboard ? (keepalive ? 4 : 16) : 100);
  flushing = api("events", {events:batch}, keepalive).then(result => {
    if (result.status === "revoked" && session?.status === "active") complete(result);
  }).catch(error => { if (tracking) pendingEvents.unshift(...batch); throw error; });
  try { await flushing; } finally { flushing = null; }
  if (pendingEvents.length && !keepalive) await flush();
}
function trackStart() {
  if (builderPreview) return;
  tracking = true;
  event("page_open"); event("visibility", {state:document.visibilityState}); event("focus", {state:document.hasFocus() ? "focused" : "blurred"});
}
function renderMessage(message, {animate = false} = {}) {
  $("chat-empty")?.remove();
  const messages = $("messages"), nearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  const bubble = document.createElement("div"); bubble.className = "message " + message.role;
  const label = document.createElement("span"); label.className = "message-label"; label.textContent = message.role === "assistant" ? $("chat-title").textContent.toUpperCase() : "YOU";
  const content = document.createElement("span"); content.className = "message-content"; content.textContent = message.content;
  bubble.append(label, content);
  if (animate && !reducedMotion.matches) {
    bubble.classList.add("message-entering");
    bubble.addEventListener("animationend", () => bubble.classList.remove("message-entering"), {once:true});
  }
  if (message.id) bubble.dataset.message = message.id;
  messages.append(bubble);
  if (message.role === "user" || nearBottom) messages.scrollTop = messages.scrollHeight;
  else $("jump-latest").classList.remove("hidden");
  return bubble;
}
function thinkingIndicator() {
  const waiting = document.createElement("div");
  waiting.className = "typing";
  waiting.setAttribute("role", "status");
  const dots = document.createElement("span"); dots.className = "thinking-dots"; dots.setAttribute("aria-hidden", "true");
  for (let index = 0; index < 3; index++) dots.append(document.createElement("i"));
  const label = document.createElement("span"); label.textContent = "Thinking…";
  waiting.append(dots, label);
  return waiting;
}
function revealReply(bubble, text) {
  if (reducedMotion.matches || document.hidden || !text) return Promise.resolve();
  const content = bubble.querySelector(".message-content"), messages = $("messages");
  const ends = [...text.matchAll(/\S+\s*/g)].map(match => match.index + match[0].length);
  if (!ends.length) return Promise.resolve();
  content.textContent = "";
  const duration = Math.min(1200, Math.max(240, text.length * 5));
  let start, frame, count = 0;
  let follow = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  return new Promise(resolve => {
    const finish = () => {
      cancelAnimationFrame(frame); content.textContent = text;
      if (replyReveal?.cancel === finish) replyReveal = null;
      resolve();
    };
    replyReveal = {cancel:finish};
    const tick = timestamp => {
      if (document.hidden || reducedMotion.matches || !bubble.isConnected || session.status !== "active") { finish(); return; }
      start ??= timestamp;
      const next = Math.min(ends.length, Math.max(1, Math.ceil((timestamp - start) / duration * ends.length)));
      if (next !== count) {
        follow = follow && messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
        content.textContent = text.slice(0, ends[next - 1]); count = next;
        if (follow) messages.scrollTop = messages.scrollHeight;
      }
      if (count === ends.length) finish();
      else frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  });
}
function expose() {
  if (!latest || exposed.has(latest.id) || document.visibilityState !== "visible" || document.querySelector("dialog[open]")) return;
  const node = [...document.querySelectorAll("[data-message]")].find(el => el.dataset.message === latest.id);
  if (!node) return;
  const rect = node.getBoundingClientRect(), viewport = $("messages").getBoundingClientRect();
  const inChat = rect.width > 0 && rect.height > 0 && rect.bottom > viewport.top && rect.top < viewport.bottom && rect.bottom > 0 && rect.top < innerHeight;
  const panel = $("recommendation");
  const panelRect = panel.getBoundingClientRect();
  const inPanel = panelRect.width > 0 && panelRect.height > 0 && (latest.option_id || latest.offer_price!=null || latest.decision_action) && !panel.classList.contains("hidden") && panelRect.bottom > 0 && panelRect.top < innerHeight;
  if (!inChat && !inPanel) return;
  exposed.add(latest.id); event("recommendation_visible", {message_id:latest.id});
  flush().catch(showError);
}
function updateDecision() {
  if(session?.public.case_study){updateCaseDecision();return;}
  if(session?.public.task==='negotiation'){updateNegotiation();return;}
  $("submit-decision").disabled = !selected || busy || finishing;
  $("reject-decision").disabled = busy || finishing;
  const recommended = latest?.option_id;
  $("recommendation").classList.toggle("hidden", !recommended);
  $("reject-decision").classList.toggle("hidden", !recommended);
  if (recommended) {
    $("recommended-name").textContent = session.public.scenario.options.find(o => o.id === recommended).label;
    $("recommendation-label").textContent = session.public.authority === "provisional_decision" ? "Provisional agent decision" : "Agent recommendation";
  }
  const choice=session.public.scenario.options.find(o=>o.id===selected);
  document.querySelector(".decision-area").classList.toggle("has-selection",!!choice);
  $("selection-status").textContent=choice ? "Ready to submit" : "No supplier selected";
  $("selected-name").textContent=choice ? choice.label : "Select a row above";
  $("selected-attributes").innerHTML=choice ? session.public.scenario.criteria.map(c=>`<span><b>${esc(choice.values[c.id])}${c.unit==="%"?"%":""}</b> ${esc(c.label)}</span>`).join("") : "";
  document.querySelectorAll("[data-option-row]").forEach(row=>{
    row.classList.toggle("is-selected",row.dataset.optionRow===selected);
    row.classList.toggle("is-recommended",row.dataset.optionRow===recommended);
  });
  $("choice-shortcut-label").textContent = choice ? choice.label : "Your choice";
  $("jump-decision").classList.toggle("has-choice",!!choice);
  $("jump-decision").setAttribute("aria-label",choice ? "Review your choice: "+choice.label : "Review your choice");
  $("jump-decision").title="Review your choice (Ctrl/⌘ + Alt + D)";
  saveDraft();
  $("submit-decision").textContent = !selected ? "Select a supplier to continue" : recommended && selected === recommended ? "Accept & submit →" : recommended ? "Override & submit →" : "Submit your choice →";
}
function updateNegotiation(){
  const n=session.public.negotiation, proposed=latest?.offer_price;
  const raw=$('final-price').value;
  finalPrice=raw.trim()&&Number.isFinite(Number(raw)) ? Number(raw) : null;
  const valid=finalPrice!=null&&finalPrice>=n.price_min&&finalPrice<=n.price_max&&Math.abs(finalPrice*100-Math.round(finalPrice*100))<1e-6;
  $('submit-decision').disabled=!valid||busy||finishing;$('reject-decision').disabled=busy||finishing;
  $('reject-decision').classList.remove('hidden');$('reject-decision').textContent='Finish without agreement';
  $('recommendation').classList.toggle('hidden',proposed==null);
  $('use-agent-price').classList.toggle('hidden',proposed==null);
  if(proposed!=null){$('recommendation-label').textContent=session.public.agent_role==='genai_tool' ? 'Agent price advice' : 'Current agent offer';$('recommended-name').textContent=n.currency+' '+proposed.toFixed(2)+' / unit';}
  $('selection-status').textContent=valid ? 'Ready to record' : 'Enter a final price';
  $('choice-shortcut-label').textContent=valid ? n.currency+' '+finalPrice.toFixed(2) : 'Your price';
  $('jump-decision').setAttribute('aria-label','Review your final price');
  $('jump-decision').classList.toggle('has-choice',valid);
  const accepts=proposed!=null&&valid&&finalPrice===proposed;
  $('submit-decision').textContent=!valid ? 'Enter a price to continue' : accepts ? (session.public.agent_role==='genai_tool' ? 'Accept advice & record price →' : 'Accept & record agreement →') : 'Record your own price →';
  const guidance=!session.public.agent_enabled ? 'Record your preferred price. No negotiated agreement is inferred in this baseline.' : session.public.agent_role==='genai_tool' ? 'The tool gives advice; recording a price does not confirm a counterparty’s agreement.' : proposed==null ? 'Record your own price, or exchange offers before deciding.' : accepts ? 'This records a simulated agreement at the current agent offer.' : 'A different recorded price is an override, not an agreement by the other party.';
  $('negotiation-guidance').textContent=guidance;
  $("negotiation-order-total").classList.toggle("hidden", !valid);
  $("negotiation-order-total").textContent = valid ? `Order total · ${negotiationMoney(finalPrice * n.quantity)} for ${n.quantity} units` : "";
  document.querySelector('.decision-area').classList.toggle('has-selection',valid);saveDraft();
}
function renderNegotiation(){
  const n=session.public.negotiation;
  document.body.classList.add("negotiation-mode");
  $('negotiation-card').classList.remove('hidden');$('options').classList.add('hidden');
  $("case-data").classList.add("hidden");
  $('comparison-title').textContent='Order details';$('supplier-count').textContent=n.currency+' / unit';
  $('decision-title').textContent='Your outcome';$('task-mode-label').textContent='Price negotiation';
  $('negotiation-role').textContent='Your role · '+n.participant_role;
  $('negotiation-item').textContent=n.item;
  const counterpart = ["buyer", "supplier"].includes(session.public.agent_role) ?
    "AI " + session.public.agent_role : "Study assistant";
  $("chat-title").textContent = counterpart;
  $("negotiation-task").textContent = session.public.agent_enabled ?
    `Negotiate the unit price with the ${counterpart} for this fixed order.` :
    `Review this order and record your preferred unit price for all ${n.quantity} units.`;
  const paragraphs = (n.knowledge || "").split(/\n\s*\n/);
  $("negotiation-company-overview").textContent = paragraphs[0] || "";
  $("negotiation-company-overview").classList.toggle("hidden", !paragraphs[0]);
  $("negotiation-context-text").textContent = paragraphs.slice(1).join("\n\n");
  $("negotiation-business-context").classList.toggle("hidden", !n.knowledge);
  $('negotiation-facts').innerHTML=`<div><span>Quantity</span><strong>${esc(n.quantity)} units</strong></div><div><span>Allowed unit price</span><strong>${esc(negotiationMoney(n.price_min))}–${esc(negotiationMoney(n.price_max))}</strong></div>`;
  $("final-price").min = n.price_min;
  $("final-price").max = n.price_max;
  $("final-price").placeholder = n.currency + " per unit";
  participantOffer = session.messages.filter(message => message.role === "user" && message.offer_price != null).at(-1)?.offer_price ?? null;
  updateParticipantOffer();
  document.querySelector('[data-study-pane="comparison"]>span').textContent='Terms';
  document.querySelector('.criteria-note summary').textContent='Public task notes';
  if (!session.public.case_study) {
    $('final-price-field').classList.remove('hidden');$('negotiation-guidance').classList.remove('hidden');
  }
  $('message-input').placeholder='Message the supplier…';
  $("message-input").setAttribute("aria-label", "Your message to the " + counterpart);
  document.querySelector('#chat-empty h3').textContent=session.public.agent_role==='genai_tool' ? 'Explore your negotiating options.' : 'Start the negotiation.';
  document.querySelector('#chat-empty p').textContent='Discuss your business needs, challenge the price, or make an offer in the chat.';
  $('initial-recommendation').textContent='Request an opening offer →';
}
function negotiationMoney(value) {
  return new Intl.NumberFormat("en-US", {style: "currency", currency: session.public.negotiation.currency}).format(value);
}
function updateParticipantOffer() {
  if (session?.public.task !== "negotiation") return;
  $("negotiation-last-offer").classList.toggle("hidden", participantOffer == null);
  $("negotiation-last-offer").textContent = participantOffer == null ? "" :
    `Your latest offer · ${negotiationMoney(participantOffer)} per unit`;
}
function showRelayWaiting() {
  busy = true;
  $("send-message").disabled = true;
  let waiting = $("relay-waiting");
  if (!waiting) { waiting = document.createElement("div"); waiting.id = "relay-waiting"; waiting.className = "typing"; $("messages").append(waiting); }
  waiting.textContent = "Waiting for this chat. Tell Codex: ‘Answer the queued web prompt.’";
  $("conversation-status").textContent="Waiting for chat";
  $("messages").scrollTop = $("messages").scrollHeight;
  updateDecision();
}
async function receiveResponse(response) {
  $("relay-waiting")?.remove(); relayRequest = null;
  const bubble = renderMessage(response, {animate:true});
  if (!response.committed_decision) {
    $("conversation-status").textContent = "Replying…";
    await revealReply(bubble, response.content);
    if (session.status !== "active") return;
  }
  latest = response;
  if(response.committed_decision){collectCounts();flush().catch(showError);session.messages.push(response);complete(response.committed_decision);return;}
  if (session.public.authority === "provisional_decision") {if(session.public.task==='negotiation'&&latest.offer_price!=null)$("final-price").value=latest.offer_price;else if(latest.option_id){selected=latest.option_id;selectRadio();}}
  busy = false; $("send-message").disabled = false;
  $("conversation-status").textContent="Reply received";
  if($("workbench").dataset.view==="comparison") $("mobile-reply-dot").classList.remove("hidden");
  updateDecision(); requestAnimationFrame(expose);
}
async function pollRelay() {
  if (!relayRequest || relayPolling || !tracking) return;
  relayPolling = true;
  try {
    const result = await api("request?request_id=" + encodeURIComponent(relayRequest));
    if (!tracking) return;
    if (result.status === "completed") await receiveResponse(result.message);
    else if (result.status === "failed") {
      relayRequest = null; busy = false; $("relay-waiting")?.remove(); $("send-message").disabled = false; updateDecision();
      $("conversation-status").textContent="Ready";
      showError(new Error("This relay request was stopped. You may submit a new prompt."));
    }
  } catch (error) { showError(error); }
  finally { relayPolling = false; }
}
function renderSessionChrome(preview, publicView) {
  const isTest = preview && !builderPreview;
  document.body.classList.toggle("is-test-session", isTest);
  $("participant-badge").textContent = builderPreview ? "Read-only" : isTest ? "Test session" : "Participant session";
  const group = $("participant-test-group");
  group.textContent = isTest ? (publicView?.test_group || "") : "";
  group.classList.toggle("hidden", !group.textContent);
  const back = $("return-workspace");
  back.classList.toggle("hidden", !isTest);
  if (!isTest) {
    window.clearTestTheme();
    return;
  }
  const from = sessionParams.get("from");
  back.href = "/";
  if (!from) return;
  try {
    const target = new URL(from.startsWith("/") ? from : "/#" + from, location.origin);
    if (target.origin === location.origin && target.pathname === "/") {
      back.href = target.pathname + target.search + target.hash;
    }
  } catch { /* The workspace home remains available if the return URL is invalid. */ }
}
function renderSession() {
  $("loading").classList.add("hidden"); $("consent").classList.add("hidden");
  renderSessionChrome(session.preview, session.public);
  if (session.ozer && (session.status === "active" ||
      (session.status === "completed" && session.ozer.stage === "feedback"))) {
    renderOzer();
    return;
  }
  if (session.status !== "active") { complete({...session, ...session.decision, completion_code:session.session_id}); return; }
  $("experiment").classList.remove("hidden");
  const scenario = session.public.scenario;
  $("scenario-title").textContent = scenario.title; $("scenario-instructions").textContent = scenario.instructions;
  $("public-knowledge").textContent = scenario.knowledge;
  $("session-label").textContent = "Session " + session.session_id.slice(0, 8)+" · Round "+session.round_id;
  $("demo-warning").classList.toggle("hidden", session.public.provider_mode !== "demo" || !session.public.agent_enabled);
  $("relay-warning").classList.toggle("hidden", session.public.provider_mode !== "relay" || !session.public.agent_enabled);
  if(session.public.case_study){renderCaseSession();return;}
  document.body.classList.toggle("human-only",!session.public.agent_enabled);
  document.body.classList.toggle("negotiation-mode",session.public.task==='negotiation');
  $("baseline-warning").classList.toggle("hidden",session.public.agent_enabled);
  if(session.public.task==='negotiation')renderNegotiation();
  if(!session.public.agent_enabled){$('chat-title').textContent='Independent condition';$('conversation-status').textContent='Agent off';document.querySelector('#chat-empty h3').textContent='Make your own decision.';document.querySelector('#chat-empty p').textContent='No assistant is available in this condition. All task information and outcome controls remain available.';}
  $("supplier-count").textContent=session.public.task==='negotiation' ? session.public.negotiation.currency+' / unit' : scenario.options.length+" suppliers";
  $("options").innerHTML=`<table class="supplier-table"><caption class="visually-hidden">Supplier comparison. Choose one supplier using the selection controls.</caption><thead><tr><th scope="col">Supplier</th>${scenario.criteria.map(c=>`<th scope="col"><span>${esc(c.label)}</span><small>${esc(c.unit)}</small></th>`).join("")}</tr></thead><tbody>${scenario.options.map((o,i)=>`<tr data-option-row="${esc(o.id)}" data-track-hover="${esc(o.id)}"><th scope="row"><label class="supplier-label"><input type="radio" name="option" value="${esc(o.id)}" aria-label="Select ${esc(o.label)}"><span class="supplier-identifier" aria-hidden="true">${esc(o.label.charAt(0))}</span><span>${esc(o.label)}</span></label></th>${scenario.criteria.map(c=>`<td>${esc(o.values[c.id])}${c.unit==="%"?"%":""}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  document.querySelectorAll('input[name="option"]').forEach(input => input.onchange = () => { selected = input.value; updateDecision(); });
  document.querySelectorAll("[data-option-row]").forEach(row=>row.addEventListener("click",e=>{if(e.target.closest("input,label"))return;const input=row.querySelector("input");input.checked=true;selected=input.value;updateDecision();}));
  for (const m of session.messages) { renderMessage(m); if (m.role === "assistant") latest = m; }
  if (session.public.authority === "provisional_decision") {if(session.public.task==='negotiation'&&latest?.offer_price!=null)$("final-price").value=latest.offer_price;else if(latest?.option_id){selected=latest.option_id;selectRadio();}}
  restoreDraft(); trackStart(); updateDecision(); requestAnimationFrame(expose);
  if (session.pending_request) { relayRequest = session.pending_request.request_id; showRelayWaiting(); pollRelay(); }
}
function selectRadio() { document.querySelectorAll('input[name="option"]').forEach(input => { input.checked = input.value === selected; }); }
async function send(text) {
  if (!session.public.agent_enabled) return;
  if (busy || finishing || !text.trim()) return;
  busy = true; $("send-message").disabled = true; $("initial-recommendation")?.setAttribute("disabled", ""); updateDecision();
  $("conversation-status").textContent="Thinking…";
  $("participant-notice").classList.add("hidden");
  renderMessage({role:"user",content:text}, {animate:true}); $("message-input").value = ""; $("draft-status").textContent="Enter to send · Shift+Enter for a new line"; resizeComposer(); saveDraft();
  const waiting = thinkingIndicator(); $("messages").append(waiting);
  $("messages").scrollTop = $("messages").scrollHeight;
  try {
    expose(); await flush();
    const response = await api("message", {content:text,round_id:session.round_id,request_id:crypto.randomUUID(),
      message_id:latest?.id || null,client_ms:performance.now(),page_id:pageId});
    waiting.remove();
    if (session.status === "revoked") return;
    if (Number.isFinite(response.participant_offer_price)) {
      participantOffer = response.participant_offer_price;
      updateParticipantOffer();
    }
    if (response.status === "pending") { relayRequest = response.request_id; showRelayWaiting(); }
    else if (["completed", "ended_system_limit", "revoked"].includes(response.status)) complete(response);
    else if (session.status === "active") await receiveResponse(response);
  } catch (error) { if (!$("message-input").value) $("message-input").value = text; resizeComposer(); saveDraft(); showError(error); }
  finally { waiting.remove(); if (!relayRequest) { busy = false; $("send-message").disabled = false; if(["Thinking…", "Replying…"].includes($("conversation-status").textContent))$("conversation-status").textContent="Ready"; } updateDecision(); }
}
async function decide(reject = false) {
  if(session.public.case_study){await decideCase(reject);return;}
  if (finishing || busy) return;
  finishing = true; updateDecision(); $("send-message").disabled = true;
  try {
    expose(); await flush();
    const recommended = session.public.task==='negotiation' ? latest?.offer_price : latest?.option_id;
    const currentChoice = session.public.task==='negotiation' ? finalPrice : selected;
    const action = reject ? "reject" : recommended!=null ? (currentChoice === recommended ? "accept" : "override") : "independent";
    const result = await api("decision", {action,option_id:session.public.task==='negotiation' || reject ? null : selected,final_price:session.public.task==='negotiation'&&!reject ? finalPrice : null,round_id:session.round_id,message_id:latest?.id || null,
      client_ms:performance.now(),page_id:pageId,reason:$("decision-reason").value});
    complete(result);
  } catch (error) { showError(error); }
  finally { finishing = false; $("send-message").disabled = false; updateDecision(); }
}
function complete(result) {
  replyReveal?.cancel();
  if (session.status === "revoked" && !$("complete").classList.contains("hidden")) return;
  session.status = result.status || "completed";
  tracking = false;
  relayRequest = null;
  pendingEvents = [];
  $("participant-notice").classList.add("hidden");
  try { sessionStorage.removeItem(draftKey()); } catch { /* Storage may be unavailable. */ }
  $("experiment").classList.add("hidden"); $("loading").classList.add("hidden"); $("consent").classList.add("hidden"); $("complete").classList.remove("hidden");
  $("completion-code").textContent = result.completion_code || session.session_id;
  renderOutsideAiQuestion();
  if (result.status === "withdrawn") {
    $("completion-title").textContent = "Your session has stopped.";
    $("completion-description").textContent = "No further activity will be collected on this page. Data already collected remains available to the researcher under the study protocol.";
  }
  const choice = session.public.scenario.options.find(o => o.id === result.final_option);
  $("completion-choice").textContent = session.public.task==='negotiation' ? (result.final_price!=null ? (result.agreement ? 'Simulated agreement: ' : 'Recorded price (no agreement): ')+session.public.negotiation.currency+' '+result.final_price.toFixed(2)+' per unit' : 'No agreement was recorded.') : choice ? "Final selection: " + choice.label : "No final supplier selection was recorded.";
  if(session.ozer) {
    clearInterval(ozerTimer);
    $("ozer-game").classList.add("hidden");
    $("ozer-game").replaceChildren();
    $("completion-choice").textContent = "";
    if (result.status === "completed") renderOzerResults(session.ozer.results);
  } else if(session.public.case_study&&result.status==='completed'){
    const outcome=JSON.parse(result.outcome_json || '{}');
    $('completion-title').textContent=result.action==='autonomous' ? 'The agent committed its study decision.' : 'Your outcome is recorded.';
    $('completion-choice').textContent=outcome.decision_action ? 'Simulated study outcome: '+actionLabel(outcome.decision_action) : 'The recommendation was rejected.';
    $('completion-analysis').classList.remove('hidden');$('completion-analysis-text').textContent=(latest?.content || session.messages.filter(m=>m.role==='assistant').at(-1)?.content || 'Human-only outcome. No agent was called.');
    if (session.public.case_study.id === "negotiation") {
      const price = outcome.decision_action?.price;
      const n = session.public.negotiation;
      $("completion-title").textContent = outcome.agreement || (result.action === "accept" && ["buyer", "supplier"].includes(session.public.agent_role)) ?
        "Your agreed price is recorded." : "Your negotiation outcome is recorded.";
      $("completion-choice").textContent = price == null ? "You finished without an agreement." :
        `${negotiationMoney(price)} per unit · ${n.quantity} units of ${n.item} · Total ${negotiationMoney(price * n.quantity)}`;
    }
  }
  if(result.status==='ended_system_limit'||result.status==='revoked'){
    $('completion-title').textContent='This study session has ended.';
    $('completion-description').textContent=result.system_message || (result.status === "revoked" ?
      "Access to this study session has ended. Return to your survey to continue." :
      "The study reached a system limit. Return to your survey to continue.");
    $('completion-choice').textContent='Return to the survey tab or window that brought you here. Use this completion code if the survey asks for it.';
    $('completion-analysis').classList.add('hidden');
    let back=$('survey-return');if(!back){back=document.createElement('a');back.id='survey-return';back.className='primary';back.textContent='Return to survey';$('complete').append(back);}
    try{const source=new URL(document.referrer);if(source.protocol==='https:'&&source.origin!==location.origin){back.href=source.href;back.classList.remove('hidden');}else back.classList.add('hidden');}catch{back.classList.add('hidden');}
  }
  // Exact origin binding comes from the parent's URL; the server CSP restricts allowed embedding origins.
  // Completion carries no transcript, private data, participant ID, or experimental condition.
  if (window.parent !== window && document.referrer) {
    try { window.parent.postMessage({type:"research-agent:complete",session_id:session.session_id,status:result.status,completion_code:result.completion_code || session.session_id}, new URL(document.referrer).origin); } catch { /* Return code remains visible. */ }
  }
}
function renderConsent(publicView) {
  $("consent-study").textContent = publicView.scenario.title || "Decision study";
  $("consent-text").textContent = publicView.scenario.consent_text;
  $("consent-check").checked = false;
  const {clipboard_notice, outside_ai_notice} = publicView.usage_evidence || {};
  $("clipboard-consent-text").textContent = clipboard_notice || "";
  $("clipboard-consent-label").classList.toggle("hidden", !clipboard_notice);
  $("clipboard-consent").checked = false;
  $("outside-ai-notice").textContent = outside_ai_notice || "";
  $("outside-ai-notice").classList.toggle("hidden", !outside_ai_notice);
}
function renderOutsideAiQuestion() {
  const visible = session.status === "completed" && Boolean(session.public.usage_evidence?.outside_ai_notice);
  const answer = session.usage_evidence?.outside_ai_answer;
  $("outside-ai-report").classList.toggle("hidden", !visible);
  $("outside-ai-form").classList.toggle("hidden", Boolean(answer));
  $("outside-ai-status").textContent = answer ? "Your answer was saved." : "";
}
$("outside-ai-form").onsubmit = async event => {
  event.preventDefault();
  const answer = new FormData(event.currentTarget).get("answer");
  const button = event.submitter;
  button.disabled = true;
  try {
    const result = await api("ai-report", {answer});
    session.usage_evidence = {...session.usage_evidence, outside_ai_answer:result.answer};
    renderOutsideAiQuestion();
  } catch (error) { $("outside-ai-status").textContent = error.message; }
  finally { button.disabled = false; }
};
function initBuilderPreview() {
  if (window.parent === window) throw new Error("Open this preview from Agent setup.");
  document.body.classList.add("builder-frame");
  $("participant-badge").textContent = "Read-only";
  const template = $("experiment").innerHTML;
  const readOnlyLinks = "summary,[data-study-pane],#open-brief,[data-close-dialog]";
  document.addEventListener("click", event => {
    const action = event.target.closest(readOnlyLinks);
    if (action?.matches("summary,[data-close-dialog]")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (action?.dataset.studyPane) setView(action.dataset.studyPane);
    else if (action?.id === "open-brief") $("brief-dialog").showModal();
  }, true);
  document.addEventListener("submit", event => {
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  window.addEventListener("message", event => {
    if (event.origin !== location.origin || event.source !== window.parent ||
        event.data?.type !== "research-agent:builder-render" || !event.data.public) return;
    const publicView = event.data.public;
    $("loading").classList.add("hidden");
    $("experiment").innerHTML = template;
    for (const id of ["experiment", "consent", "ozer-game"]) $(id).classList.add("hidden");
    for (const name of ["human-only", "negotiation-mode", "case-study-mode"]) document.body.classList.remove(name);
    selected = latest = finalPrice = null;
    const isOzer = publicView.case_study?.id === "ozer_game";
    const game = publicView.case_study?.game;
    session = {public:publicView, preview:true, status:"active", session_id:"builder-preview", round_id:1, messages:[]};
    if (isOzer) session.ozer = {
      stage:"decision", participant_role:publicView.agent_role === "retailer" ? "supplier" : "retailer",
      counterpart_role:publicView.agent_role,
      private_signal:Math.floor((game.signal_min + game.signal_max) / 2),
      retailer_report:Math.floor((game.signal_min + game.signal_max) / 2)
    };
    if (event.data.screen === "consent") {
      renderConsent(publicView);
      $("consent").classList.remove("hidden");
    } else {
      renderSession();
    }
    $("participant-badge").textContent = "Read-only";
    for (const control of document.querySelectorAll("main button,main input,main textarea,main select,main [role=separator]")) {
      if (control.matches(readOnlyLinks)) continue;
      control.tabIndex = -1;
      control.setAttribute("aria-disabled", "true");
      if (control.matches("input,textarea,select")) control.disabled = true;
    }
  });
  window.parent.postMessage({type:"research-agent:builder-ready"}, location.origin);
}
async function init() {
  if (builderPreview) { initBuilderPreview(); return; }
  const hash = new URLSearchParams(location.hash.slice(1));
  launchToken = hash.get("launch");
  if (launchToken) {
    // Keep the launch capability out of referrers, history, and screenshots after opening.
    history.replaceState(null, "", location.pathname + location.search);
    sessionStorage.setItem("research_pending_launch", launchToken);
  } else launchToken = sessionStorage.getItem("research_pending_launch");
  token = sessionStorage.getItem("research_session");
  if (launchToken) {
    token = null;
    const preview = await api("preview", {launch_token:launchToken});
    renderConsent(preview.public);
    renderSessionChrome(preview.preview, preview.public);
    $("consent").classList.remove("hidden"); $("loading").classList.add("hidden");
    $("consent-title").focus();
  } else if (token) { session = await api("session"); renderSession(); }
  else throw new Error("Open the participant launch link provided by your study. A condition cannot be selected from this page.");
}
$("consent-form").onsubmit = async e => {
  e.preventDefault(); const button = e.submitter; button.disabled = true;
  try {
    const result = await api("start", {launch_token:launchToken,consent:$("consent-check").checked,
      clipboard_consent:$("clipboard-consent").checked});
    token = result.session_token; sessionStorage.setItem("research_session", token); sessionStorage.removeItem("research_pending_launch");
    launchToken = null; session = await api("session"); renderSession();
  } catch (error) { showError(error); } finally { button.disabled = false; }
};
$("chat-form").onsubmit = e => { e.preventDefault(); send($("message-input").value); };
$("initial-recommendation").onclick = () => send(session.public.task==='negotiation' ? "Provide your opening unit-price offer for this order." : session.public.case_study ? 'Analyze this case using the available data.' : "Compare the suppliers and provide your initial recommendation.");
$("final-price").addEventListener("input",updateDecision);
$("use-agent-price").onclick=()=>{if(latest?.offer_price!=null){$("final-price").value=latest.offer_price;updateDecision();reviewChoice();}};
$("submit-decision").onclick = () => decide(); $("reject-decision").onclick = () => decide(true);
$("withdraw").onclick = async () => {
  if (!confirm("Stop participating? Your activity already recorded will remain in the study data.")) return;
  if (session.ozer) return withdrawOzer();
  try {
    await flush();
    complete(await api("withdraw", {}));
  } catch (error) { showError(error); }
};
$("copy-code").onclick = () => navigator.clipboard.writeText($("completion-code").textContent).catch(showError);
document.addEventListener("visibilitychange", () => { event("visibility", {state:document.visibilityState}); lastKey = null; if (document.visibilityState === "visible") expose(); else { replyReveal?.cancel(); endHover(); } flush(true).catch(() => {}); });
window.addEventListener("focus", () => event("focus", {state:"focused"}));
window.addEventListener("blur", () => { event("focus", {state:"blurred"}); lastKey = null; endHover(); });
window.addEventListener("pagehide", () => { event("page_exit"); flush(true).catch(() => {}); });
document.addEventListener("keydown", e => {
  if (!tracking || !session.public.telemetry_enabled || !["TEXTAREA","INPUT"].includes(e.target.tagName)) return;
  counts.keystrokes++;
  const ts = performance.now(); if (lastKey !== null && ts - lastKey < 2000) counts.typing_ms += ts - lastKey; lastKey = ts;
});
document.addEventListener("click", event => {
  if (tracking && session.public.telemetry_enabled && !event.target.closest(".theme-control")) counts.clicks++;
});
function clipboardField(target) {
  if (!target.matches?.('textarea, input[type="text"], input[type="number"]') || target.disabled || target.readOnly) return null;
  if (target.id === "message-input") return "prompt";
  if (target.id === "decision-reason") return "reason";
  if (target.matches('#final-price, input[data-case-field], #ozer-form input[name="value"]')) return "task_answer";
  return null;
}
function copiedStudyText(target) {
  const area = clipboardField(target);
  if (area && Number.isInteger(target.selectionStart)) {
    return {area, text:target.value.slice(target.selectionStart, target.selectionEnd)};
  }
  const selection = document.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return null;
  const ancestor = selection.getRangeAt(0).commonAncestorContainer;
  const scope = (ancestor.nodeType === Node.ELEMENT_NODE ? ancestor : ancestor.parentElement)?.closest(
    "#messages .message, #comparison-pane, #brief-dialog[open], #ozer-game");
  if (!scope || scope.closest(".hidden, [hidden]")) return null;
  return {area:scope.matches(".message.assistant") ? "agent_reply" : scope.matches(".message.user") ? "participant_message" : "study_task",
    text:selection.toString()};
}
for (const type of ["copy","paste","cut"]) document.addEventListener(type, e => {
  if (!tracking || session.status !== "active") return;
  // Read only the clipboard event or the study selection, including for assistive input.
  const capture = type !== "cut" && session.usage_evidence?.clipboard_capture && clipboardActionsQueued < 100;
  if (!session.public.telemetry_enabled && !capture) return;
  const pasted = type === "paste" ? e.clipboardData?.getData("text/plain") || "" : "";
  if (session.public.telemetry_enabled) {
    counts[{copy:"copies",paste:"pastes",cut:"cut"}[type]]++;
    if (type === "paste") counts.paste_characters += pasted.length;
  }
  if (!capture) return;
  const clip = type === "paste" ? {area:clipboardField(e.target), text:pasted} : copiedStudyText(e.target);
  if (!clip?.area || !clip.text.trim()) return;
  const text = clip.text.slice(0, 2000).replace(/[\uD800-\uDBFF]$/, "");
  if (!text.trim()) return;
  clipboardActionsQueued++;
  event("clipboard_text", {direction:type, area:clip.area, text, truncated:text.length < clip.text.length});
  flush().catch(() => {});
});
document.addEventListener("pointerover", e => { if (tracking && session.public.telemetry_enabled && e.target.closest("[data-track-hover]") && !e.relatedTarget?.closest?.("[data-track-hover]")) hoverStart = performance.now(); });
document.addEventListener("pointerout", e => {
  if (hoverStart !== null && e.target.closest("[data-track-hover]") && !e.relatedTarget?.closest?.("[data-track-hover]")) { counts.hover_ms += performance.now() - hoverStart; hoverStart = null; }
});
window.addEventListener("scroll", expose, {passive:true}); $("messages").addEventListener("scroll", expose, {passive:true});
function draftKey() { return "research_workspace:" + session?.session_id; }
function saveDraft() {
  if (!tracking || !session) return;
  try { sessionStorage.setItem(draftKey(),JSON.stringify({message:$("message-input").value,reason:$("decision-reason").value,selected,caseAction:session.public.case_study?caseAction():null,finalPrice:$("final-price").value,view:$("workbench").dataset.view,split:Number($("panel-divider").getAttribute("aria-valuenow"))})); }
  catch { /* The workspace remains usable when browser storage is disabled. */ }
}
function restoreDraft() {
  if (builderPreview) { setView(compactScreen.matches ? "comparison" : "split"); return; }
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(draftKey()) || "{}"); } catch { /* Ignore invalid or unavailable storage. */ }
  if (!saved || typeof saved !== "object") saved = {};
  $("message-input").value = typeof saved.message === "string" ? saved.message : "";
  $("decision-reason").value = typeof saved.reason === "string" ? saved.reason : "";
  if (saved.reason) document.querySelector(".decision-reason").open = true;
  if (session.public.scenario.options.some(o=>o.id===saved.selected)) selected = saved.selected;
  if(typeof saved.finalPrice==='string')$("final-price").value=saved.finalPrice;
  if(saved.caseAction&&session.public.case_study)for(const input of document.querySelectorAll('[data-case-field]'))input.value=saved.caseAction[input.dataset.caseField] ?? '';
  selectRadio(); setSplit(Number.isFinite(saved.split) ? saved.split : 51);
  setView(["split","comparison","conversation"].includes(saved.view) ? saved.view : compactScreen.matches ? "comparison" : "split");
  resizeComposer();
  updateParticipantOffer();
  if (saved.message) $("draft-status").textContent = "Draft restored in this tab";
}
function setView(view, focus = false) {
  if(session&&!session.public.agent_enabled)view=compactScreen.matches ? 'comparison' : 'split';
  if (compactScreen.matches && view === "split") view = "comparison";
  $("workbench").dataset.view = view;
  $("workbench").dataset.mobilePane = view; // Keep narrow-screen styles in sync.
  document.querySelectorAll("[data-study-pane]").forEach(b=>{const active=b.dataset.studyPane===view;b.classList.toggle("active",active);b.setAttribute("aria-pressed",String(active));});
  if (view !== "comparison") $("mobile-reply-dot").classList.add("hidden");
  if (focus) {
    const target = view === "conversation" ? $("message-input") : session.public.task==='negotiation' ?
      (session.public.case_study ? document.querySelector(".decision-area") : $("final-price")) :
      (document.querySelector('input[name="option"]:checked') || document.querySelector('input[name="option"]'));
    target?.focus({preventScroll:true});
  }
  saveDraft(); requestAnimationFrame(expose);
  requestAnimationFrame(resizeComposer);
}
function reviewChoice() {
  if ($("workbench").dataset.view === "conversation") setView(compactScreen.matches ? "comparison" : "split");
  const area = document.querySelector(".decision-area");
  area.focus({preventScroll:true}); area.scrollIntoView({block:"nearest",behavior:"instant"});
}
function resizeComposer() {
  const input = $("message-input");
  input.style.height = "auto";
  const limit = Math.min(176, Math.max(96, window.innerHeight * .28));
  input.style.height = Math.min(limit, Math.max(32, input.scrollHeight))+"px";
  input.style.overflowY = input.scrollHeight > limit ? "auto" : "hidden";
}
window.addEventListener("resize", resizeComposer);
function toast(message) { clearTimeout(toastTimer); $("workspace-toast").textContent=message; $("workspace-toast").classList.remove("hidden"); toastTimer=setTimeout(()=>$("workspace-toast").classList.add("hidden"),2500); }
document.querySelectorAll("[data-study-pane]").forEach(button=>button.addEventListener("click",()=>setView(button.dataset.studyPane,true)));
compactScreen.addEventListener("change",()=>setView(compactScreen.matches ? "comparison" : "split"));
$("jump-decision").onclick = reviewChoice;
$("open-brief").onclick = ()=>$("brief-dialog").showModal();
$("open-shortcuts").onclick = ()=>$("shortcuts-dialog").showModal();
document.querySelectorAll("[data-close-dialog]").forEach(button=>button.onclick=()=>$(button.dataset.closeDialog).close());
document.querySelectorAll("dialog").forEach(dialog=>dialog.addEventListener("click",e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}}));
$("message-input").addEventListener("input",()=>{resizeComposer();saveDraft();$("draft-status").textContent="Draft kept in this tab";});
$("decision-reason").addEventListener("input",saveDraft);
$("message-input").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!e.repeat)send(e.currentTarget.value);}});
document.addEventListener("keydown",e=>{
  if (!tracking || document.querySelector("dialog[open]")) return;
  if ((e.metaKey||e.ctrlKey)&&e.altKey) {
    const pane={Digit1:"comparison",Digit2:"split",Digit3:"conversation"}[e.code];
    if(pane){e.preventDefault();setView(pane,true);}
    if(e.code==="KeyD"){e.preventDefault();reviewChoice();}
  }
});
$("jump-latest").onclick = ()=>{ $("messages").scrollTo({top:$("messages").scrollHeight,behavior:"instant"});$("jump-latest").classList.add("hidden"); };
$("messages").addEventListener("scroll",()=>{if($("messages").scrollHeight-$("messages").scrollTop-$("messages").clientHeight<60)$("jump-latest").classList.add("hidden");},{passive:true});
$("panel-divider").title="Drag to resize · double-click to balance";
$("panel-divider").addEventListener("dblclick",()=>{setSplit(51);toast("Split view balanced");});
let resizing=false;
function setSplit(value) {
  const split=Math.max(36,Math.min(65,value));
  $("workbench").style.setProperty("--comparison-width",split+"%");
  $("panel-divider").setAttribute("aria-valuenow",String(Math.round(split)));
  resizeComposer();
  saveDraft();
}
$("panel-divider").addEventListener("pointerdown",e=>{resizing=true;e.currentTarget.setPointerCapture(e.pointerId);document.body.classList.add("resizing");});
$("panel-divider").addEventListener("pointermove",e=>{if(!resizing)return;const rect=$("workbench").getBoundingClientRect();setSplit((e.clientX-rect.left)/rect.width*100);});
function stopResize(){resizing=false;document.body.classList.remove("resizing");saveDraft();}
$("panel-divider").addEventListener("pointerup",stopResize);
$("panel-divider").addEventListener("lostpointercapture",stopResize);
$("panel-divider").addEventListener("keydown",e=>{const value=Number(e.currentTarget.getAttribute("aria-valuenow"));if(e.key==="ArrowLeft"||e.key==="ArrowRight"){e.preventDefault();setSplit(value+(e.key==="ArrowLeft"?-2:2));}if(e.key==="Home"){e.preventDefault();setSplit(51);}});
setInterval(() => { if (tracking) { expose(); flush().catch(() => {}); } }, 5000);
async function checkSessionAccess() {
  if (!token || session?.status !== "active" || document.hidden) return;
  try {
    const state = await api("status");
    if (state.status === "revoked" && session.status === "active") complete(state);
  } catch (error) {
    if (error.status === 403 && session.status === "active") complete({status:"revoked"});
  }
}
setInterval(checkSessionAccess, 10000);
window.addEventListener("focus", checkSessionAccess);
setInterval(pollRelay,1500);
init().catch(error => {
  $("loading").classList.add("hidden");
  if (!builderPreview && document.documentElement.dataset.testTheme) renderSessionChrome(true);
  showError(error);
});

function renderCaseSession(){
  const pack=session.public.case_study;
  document.body.classList.add('case-study-mode');document.body.classList.toggle('human-only',!session.public.agent_enabled);
  $('baseline-warning').classList.toggle('hidden',session.public.agent_enabled);
  $('task-mode-label').textContent=pack.id.replaceAll('_',' ')+' · '+(session.public.agent_enabled ? pack.mode : 'human-only')+' · round '+session.round_id;
  document.querySelector('[data-study-pane="comparison"]>span').textContent='Data';document.querySelector('.criteria-note summary').textContent='Task assumptions';
  $('comparison-title').textContent='Case data';$('supplier-count').textContent='Synthetic · '+pack.dataset_version;
  $('options').classList.add('hidden');$('case-data').classList.remove('hidden');
  $('case-data').innerHTML=Object.entries(pack.tables).map(([name,rows],index)=>`<details class="case-table" ${index===0?'open':''}><summary>${esc(name.replaceAll('_',' '))} <span>${rows.length} records</span></summary><div class="table-wrap"><table><thead><tr>${Object.keys(rows[0]||{}).map(key=>`<th>${esc(key.replaceAll('_',' '))}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${Object.values(row).map(value=>`<td>${esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`).join('');
  $('case-decision-fields').classList.remove('hidden');$('selected-summary').classList.add('hidden');
  $('decision-title').textContent=pack.mode==='autonomous'&&session.public.agent_enabled ? 'Automatic study decision' : 'Record an outcome';
  $('case-decision-fields').innerHTML=Object.entries(pack.decision.schema.properties).map(([key,schema])=>`<label>${esc(key.replaceAll('_',' '))}${schema.enum ? `<select data-case-field="${esc(key)}"><option value="">Choose…</option>${schema.enum.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('')}</select>` : `<input data-case-field="${esc(key)}" type="${['number','integer'].includes(schema.type)?'number':'text'}" ${schema.minimum!=null?`min="${schema.minimum}"`:''} ${schema.maximum!=null?`max="${schema.maximum}"`:''} step="${schema.type==='integer'?'1':schema.multipleOf || 'any'}">`}</label>`).join('');
  for(const input of document.querySelectorAll('[data-case-field]'))input.addEventListener('input',updateCaseDecision);
  $('message-input').placeholder='Ask about the case, assumptions, or evidence…';
  document.querySelector('#chat-empty h3').textContent=pack.mode==='autonomous' ? 'Ready to analyze and commit.' : 'Explore the case.';
  document.querySelector('#chat-empty p').textContent=pack.mode==='autonomous' ? 'The agent records its decision automatically after checking its response. No approval is requested.' : 'Analysis uses the tools and dataset assigned to this condition.';
  $('initial-recommendation').textContent=pack.mode==='autonomous' ? 'Run agent & commit study decision →' : 'Analyze the case →';
  if(!session.public.agent_enabled){$('chat-title').textContent='Independent condition';$('conversation-status').textContent='Agent off';document.querySelector('#chat-empty h3').textContent='Make your own decision.';document.querySelector('#chat-empty p').textContent='Review the case data and record your outcome. No agent is available.';}
  document.querySelector('.decision-note').textContent=pack.mode==='autonomous'&&session.public.agent_enabled ? 'Decisions are committed only inside this simulation.' : pack.mode==='recommender'&&session.public.agent_enabled ? 'Accept or reject explicitly. This is the recorded decision moment.' : 'Record your independent result after reviewing the data.';
  if (pack.id === "negotiation") {
    document.querySelector(".decision-area").classList.toggle("negotiation-offer-panel", pack.mode === "recommender" && session.public.agent_enabled);
    renderNegotiation();
    if (pack.mode === "recommender" && session.public.agent_enabled) {
      document.querySelector(".decision-note").textContent = "Accept to finish, or send another offer to continue negotiating.";
    }
  }
  for(const message of session.messages){renderMessage(message);if(message.role==='assistant')latest=message;}
  restoreDraft();trackStart();updateCaseDecision();requestAnimationFrame(expose);
}
function caseAction(){
  const fields=session.public.case_study.decision.schema.properties,result={};
  for(const input of document.querySelectorAll('[data-case-field]')){
    if(!input.value.trim())return null;
    const schema=fields[input.dataset.caseField];const value=['number','integer'].includes(schema.type)?Number(input.value):input.value;
    if(typeof value==='number'&&(!Number.isFinite(value)||schema.type==='integer'&&!Number.isInteger(value)||schema.minimum!=null&&value<schema.minimum||schema.maximum!=null&&value>schema.maximum))return null;
    result[input.dataset.caseField]=value;
  }
  return result;
}
function updateCaseDecision(){
  const pack=session.public.case_study,active=session.public.agent_enabled,proposal=latest?.decision_action;
  const recommends=active&&pack.mode==='recommender',automatic=active&&pack.mode==='autonomous';
  $('case-decision-fields').classList.toggle('hidden',recommends||automatic);document.querySelector('.decision-reason').classList.toggle('hidden',automatic);
  $('recommendation').classList.toggle('hidden',!proposal);
  if(proposal){$('recommended-name').textContent=actionLabel(proposal);$('recommendation-label').textContent=automatic?'Committed result':'Agent proposal';}
  $('selection-status').textContent=automatic?'No approval required':recommends ? (proposal?'Choose accept or reject':'Awaiting a proposal') : (caseAction()?'Ready to record':'Enter your result');
  $('submit-decision').classList.toggle('hidden',automatic);$('reject-decision').classList.toggle('hidden',!recommends);
  $('submit-decision').disabled=busy||finishing||(recommends?!proposal:!caseAction());$('reject-decision').disabled=busy||finishing||!proposal;
  $('submit-decision').textContent=recommends?'Accept proposal & record →':'Record your outcome →';$('reject-decision').textContent='Reject proposal';
  $('choice-shortcut-label').textContent=automatic?'Automatic decision':'Your outcome';saveDraft();
  if (pack.id === "negotiation") {
    const price = proposal?.price;
    const n = session.public.negotiation;
    $("recommendation-label").textContent = automatic ? "Recorded unit price" : $("chat-title").textContent + "’s current offer";
    if (price != null) $("recommended-name").textContent = negotiationMoney(price) + " per unit";
    $("negotiation-order-total").classList.toggle("hidden", price == null);
    $("negotiation-order-total").textContent = price == null ? "" :
      `Order total · ${negotiationMoney(price * n.quantity)} for ${n.quantity} units`;
    if (recommends) {
      document.querySelector(".decision-actions").classList.toggle("hidden", !proposal);
      document.querySelector(".decision-reason").classList.toggle("hidden", !proposal);
      const accepted = participantOffer != null && price === participantOffer;
      $("decision-title").textContent = "Current offer";
      $("selection-status").textContent = price == null ? "Awaiting an opening price" :
        accepted ? "Your offer was accepted" : "Ready to accept or counter";
      $("submit-decision").textContent = accepted ? "Confirm & finish" : "Accept & finish";
      $("reject-decision").textContent = "Finish without agreement";
    }
    $("choice-shortcut-label").textContent = "Current offer";
  }
}
async function decideCase(reject){
  if(finishing||busy)return;finishing=true;updateCaseDecision();
  try{
    expose();await flush();const recommends=session.public.agent_enabled&&session.public.case_study.mode==='recommender';
    const result=await api('decision',{action:recommends?(reject?'reject':'accept'):'independent',decision_action:recommends?(reject?null:latest?.decision_action):caseAction(),message_id:latest?.id||null,round_id:session.round_id,client_ms:performance.now(),page_id:pageId,reason:$('decision-reason').value});complete(result);
  }catch(error){showError(error);}finally{finishing=false;updateCaseDecision();}
}
