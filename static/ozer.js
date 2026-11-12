"use strict";

let ozerTimer, ozerWorking = false, ozerWithdrawing = false, ozerViewVersion = 0;
const ozerNumber = value => Number(value).toLocaleString();
const ozerMoney = value => Number(value).toLocaleString("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: 5
});

function ozerFacts(entries) {
  return `<dl class="ozer-facts">${entries.map(([label, value]) =>
    `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>`;
}

function ozerQuiz(state) {
  const item = (name, question, options) => `<label>${question}
    <select name="${name}" required><option value="">Select an answer</option>
      ${options.map(([value, text]) => `<option value="${value}">${text}</option>`).join("")}
    </select></label>`;
  return `${state.quiz_retry ? '<p role="alert">Some answers were incorrect. Review the rules and try again.</p>' : ""}
    <form id="ozer-form" class="ozer-form">
      ${item("signal", "Who sees the exact private signal before decisions?", [["supplier", "Supplier"], ["retailer", "Retailer"], ["both", "Both players"]])}
      ${item("paid", "Which periods count toward payment?", [["all", "All 23 periods"], ["paid_only", "The 20 paid periods"], ["practice", "Only the practice periods"]])}
      ${item("decision", "Which role makes the decision you will enter?", [["supplier", "Supplier chooses production"], ["retailer", "Retailer submits a forecast"]])}
      <button class="primary" type="submit">Check answers</button>
    </form>`;
}

function ozerRules(game) {
  return `<p>Each period starts with a new private signal, X, drawn from ${game.signal_min} to ${game.signal_max}.
      The retailer sees X and sends a forecast report, F. The supplier sees F and chooses a production quantity, Q.</p>
    <p>Once both decisions are locked, a shock from ${game.shock_min} to ${game.shock_max} is added to X.
      This is final demand. Sales are the smaller of production and final demand. Unused units do not carry over.</p>
    ${ozerFacts([["Production cost", game.production_cost], ["Wholesale price", game.wholesale_price], ["Retail price", game.retail_price]])}
    <p class="help">Prices are in experimental dollars per unit. Supplier profit is wholesale revenue minus the cost of all units produced. Retailer profit is the retail margin on units sold.</p>`;
}

function ozerDecision(state, pack) {
  const retailer = state.participant_role === "retailer";
  const field = retailer ? "forecast" : "production";
  const range = pack.decision.schema.properties[field];
  const observation = retailer ? state.private_signal : state.retailer_report;
  return `<div class="ozer-observation"><span>${retailer ? "Your private forecast signal · X" : "Retailer’s forecast report · F"}</span>
      <strong>${esc(ozerNumber(observation))}</strong></div>
    <form id="ozer-form" class="ozer-form">
      <label>${retailer ? "Forecast to share with the supplier" : "Your production quantity"}
        <input name="value" type="number" inputmode="numeric" step="1" min="${range.minimum}" max="${range.maximum}" required autocomplete="off">
      </label>
      <p class="help">Enter a whole number from ${range.minimum} to ${range.maximum}. Submitting locks your decision for this round.</p>
      <button class="primary" type="submit">Submit ${field}</button>
    </form>`;
}

function ozerFeedback(result) {
  const metrics = [
    ["Forecast report", result.forecast], ["Production quantity", result.production],
    ["Final demand", result.demand], ["Units sold", result.sales],
    ["Your round profit", result.profit], [result.practice ? "Practice profit so far" : "Paid profit so far", result.cumulative_profit]
  ];
  if ("private_signal" in result) metrics.unshift(["Your private signal", result.private_signal]);
  return ozerFacts(metrics.map(([label, value]) => [label, ozerNumber(value)])) +
    '<p class="help">Profits are in experimental dollars.</p>';
}

function renderOzer() {
  clearInterval(ozerTimer);
  const state = session.ozer;
  const pack = session.public.case_study;
  const game = pack.game;
  const stage = state.stage;
  const round = session.round_id;
  const timedRound = ["decision", "counterpart", "feedback"].includes(stage);
  const period = round <= game.practice_rounds ? `Practice ${round} of ${game.practice_rounds}` :
    `Paid round ${round - game.practice_rounds} of ${game.paid_rounds}`;
  const retailer = state.participant_role === "retailer";
  let title, content, next;
  if (stage === "general") {
    title = "Before the game";
    content = `<p>You will play ${game.practice_rounds} practice periods and ${game.paid_rounds} paid periods with the same AI counterpart. Practice profits are excluded from the payment calculation.</p>
      <p>${session.preview ? (session.public.provider_mode === "openai" ?
        "This preview uses the saved OpenAI model and role instructions. No participant payment is due." :
        "This is a keyless preview. Results illustrate the study’s payment calculation.") :
        `The participation payment is ${ozerMoney(game.participation_usd)}. Positive total paid-period profits are converted at ${ozerNumber(game.experimental_dollars_per_usd)} experimental dollars per USD.`}</p>`;
    if (!session.preview) content += "<p>Payments are private. Please do not communicate with other participants during the experiment. Ask the experimenter if you need help.</p>";
    next = "Read task instructions";
  } else if (stage === "instructions") {
    title = "How the forecasting task works";
    content = ozerRules(game) + `<p>You have ${game.decision_seconds} seconds to submit each decision after the decision screen opens.</p>`;
    next = "See my role";
  } else if (stage === "role") {
    title = `You are the ${state.participant_role}`;
    content = retailer ? "<p>Your AI counterpart is the supplier. You will see the private demand signal and send a forecast to the supplier.</p>" :
      "<p>Your AI counterpart is the retailer. You will see the retailer’s forecast and decide how much to produce.</p>";
    next = "Check my understanding";
  } else if (stage === "quiz") {
    title = "Check your understanding";
    content = ozerQuiz(state) + `<details class="ozer-rules"><summary>Review task rules</summary>${ozerRules(game)}</details>`;
  } else if (stage === "decision") {
    title = retailer ? "Send a demand forecast" : "Choose how much to produce";
    content = ozerDecision(state, pack);
  } else if (stage === "feedback") {
    title = "Round results";
    content = ozerFeedback(state.feedback);
    next = round === game.practice_rounds + game.paid_rounds ? "See final results" :
      round === game.practice_rounds ? "Start the paid periods" : "Continue to the next round";
  } else {
    title = "Your counterpart is deciding";
    content = '<p role="status">This round will continue automatically when its decision is ready.</p>';
  }
  const panel = $("ozer-game");
  panel.innerHTML = `<header class="ozer-header"><span>Ozer Game · Forecast information sharing</span>
    ${timedRound ? `<strong>${period}</strong>` : ""}</header>
    <article class="ozer-card">
      <div class="ozer-title"><h1 id="ozer-title" tabindex="-1">${esc(title)}</h1>
        ${stage === "decision" ? '<span id="ozer-clock" class="ozer-clock" role="timer" aria-label="Seconds remaining"></span>' : ""}</div>
      ${timedRound ? `<p class="ozer-role">You: ${state.participant_role} · Counterpart: ${state.counterpart_role}</p>` : ""}
      ${content}${next ? `<button class="primary" id="ozer-next">${esc(next)}</button>` : ""}
    </article>
    ${session.status === "active" ? '<footer class="ozer-footer"><button id="ozer-withdraw" class="text-button danger">Stop participating</button></footer>' : ""}`;
  panel.classList.remove("hidden");
  $("return-workspace").classList.toggle("hidden", !session.preview);
  $("participant-badge").textContent = session.preview ? "Preview · keyless" : "Participant session";
  if (builderPreview) {
    if ($("ozer-clock")) $("ozer-clock").textContent = game.decision_seconds + "s";
    return;
  }
  $("ozer-title").focus();
  if (!tracking) trackStart();

  $("ozer-next")?.addEventListener("click", () => ozerSend({action: "continue"}));
  $("ozer-withdraw")?.addEventListener("click", () => $("withdraw").click());
  const form = $("ozer-form");
  if (form) form.onsubmit = e => {
    e.preventDefault();
    const fields = new FormData(form);
    if (stage === "quiz") ozerSend({action: "quiz", answers: Object.fromEntries(fields)});
    else ozerSend({action: "submit", value: Number(fields.get("value"))});
  };

  if (stage === "decision") {
    const shownAt = performance.now();
    const tick = () => {
      const seconds = Math.max(0, Math.ceil(state.deadline_at - state.server_now - (performance.now() - shownAt) / 1000));
      $("ozer-clock").textContent = seconds + "s";
      if (seconds === 0 && !ozerWorking) ozerSend({action: "refresh"});
    };
    ozerTimer = setInterval(tick, 250);
    tick();
  } else if (stage === "counterpart") {
    ozerTimer = setInterval(async () => {
      if (ozerWorking || ozerWithdrawing) return;
      const viewVersion = ozerViewVersion;
      ozerWorking = true;
      try {
        const current = await api("session");
        if (viewVersion !== ozerViewVersion) return;
        if (current.status !== "active" || current.ozer.stage !== "counterpart" || current.round_id !== round) {
          session = current;
          renderSession();
        }
      } catch (error) {
        if (viewVersion === ozerViewVersion) showError(error);
      }
      finally { ozerWorking = false; }
    }, 1000);
  }
}

async function ozerSend(data) {
  if (ozerWorking || ozerWithdrawing) return;
  const viewVersion = ozerViewVersion;
  ozerWorking = true;
  $("participant-notice").classList.add("hidden");
  const controls = [...$("ozer-game").querySelectorAll("button:not(#ozer-withdraw), input, select")];
  controls.forEach(control => { control.disabled = true; });
  flush().catch(() => {});
  try {
    const pending = api("ozer", {...data, round_id: session.round_id, stage: session.ozer.stage});
    if (data.action === "submit" && session.ozer.participant_role === "retailer") {
      $("ozer-title").textContent = "Waiting for the supplier’s decision";
    }
    const current = await pending;
    if (viewVersion !== ozerViewVersion) return;
    session = current;
    renderSession();
  } catch (error) {
    if (viewVersion !== ozerViewVersion) return;
    controls.forEach(control => { control.disabled = false; });
    showError(error);
  } finally { ozerWorking = false; }
}

async function withdrawOzer() {
  if (ozerWithdrawing) return;
  ozerWithdrawing = true;
  try {
    const current = await api("withdraw", {});
    ozerViewVersion++;
    flush().catch(() => {});
    session = current;
    renderSession();
  } catch (error) {
    showError(error);
  } finally {
    ozerWithdrawing = false;
  }
}

function renderOzerResults(result) {
  $("completion-title").textContent = "Your final results";
  $("completion-description").textContent = session.preview ? "Preview results · no payment is issued." :
    "Your researcher can verify your payment using these results.";
  $("completion-choice").textContent = "Return to your survey and use the completion code if asked.";
  const summary = document.createElement("div");
  summary.innerHTML = ozerFacts([
    ["Paid rounds completed", result.paid_rounds],
    ["Paid profit · experimental dollars", ozerNumber(result.paid_profit)],
    ["Performance bonus", ozerMoney(result.performance_usd)],
    ["Participation", ozerMoney(result.participation_usd)],
    ["Total calculated payment", ozerMoney(result.total_usd)]
  ]);
  $("ozer-results")?.remove();
  summary.id = "ozer-results";
  $("completion-description").after(summary);
}
