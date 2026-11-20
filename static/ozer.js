"use strict";

let ozerTimer, ozerWorking = false, ozerWithdrawing = false, ozerViewVersion = 0;
let ozerAnswers = {};
const ozerSteps = [["general", "Overview"], ["instructions", "Task rules"], ["role", "Your role"], ["quiz", "Understanding check"]];
const ozerNumber = value => Number(value).toLocaleString();
const ozerMoney = value => Number(value).toLocaleString("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: 5
});

function ozerFacts(entries) {
  return `<dl class="ozer-facts">${entries.map(([label, value]) =>
    `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>`;
}

function ozerQuiz(state, game, canSkip) {
  const item = (number, name, question, options) => `<fieldset class="ozer-question">
    <legend><span>${number}.</span> ${question}</legend>
    <div class="ozer-answers">${options.map(([value, label]) => `
      <label class="ozer-answer">
        <input type="radio" name="${name}" value="${value}" required ${ozerAnswers[name] === value ? "checked" : ""}>
        <span>${label}</span>
      </label>`).join("")}</div>
    </fieldset>`;
  const instructions = canSkip ? "Choose one answer per question to test the check." :
    "Answer all three questions before starting your practice rounds.";
  return `<p class="study-intro">${instructions} There’s no timer on this screen.</p>
    ${state.quiz_retry ? '<p id="ozer-quiz-retry" class="study-retry" tabindex="-1">Some answers were incorrect. Your choices are saved here so you can review the rules and try again.</p>' : ""}
    <details class="ozer-rules">
      <summary>Review the task rules and your role</summary>
      <div class="ozer-rules-body">
        <p>Only the ${game.paid_rounds} paid periods count toward your performance payment. The ${game.practice_rounds} practice periods are for learning the task.</p>
        <p>${ozerRoleDescription(state.participant_role === "retailer")}</p>
        ${ozerRules(game)}
      </div>
    </details>
    <form id="ozer-form" class="ozer-form ozer-quiz-form">
      ${item(1, "signal", "Who sees the private demand signal before making a decision?", [["supplier", "Supplier"], ["retailer", "Retailer"], ["both", "Both players"]])}
      ${item(2, "paid", "Which periods count toward the performance payment?", [["all", `All ${game.practice_rounds + game.paid_rounds} periods`], ["paid_only", `The ${game.paid_rounds} paid periods`], ["practice", "Only the practice periods"]])}
      ${item(3, "decision", "What will you submit each round?", [["supplier", "A production quantity as the supplier"], ["retailer", "A forecast report as the retailer"]])}
      <div class="study-onboarding-actions">
        ${canSkip ? '<button id="ozer-skip-quiz" class="secondary" type="button">Skip to practice</button>' : ""}
        <button class="primary" type="submit">Check answers</button>
      </div>
    </form>`;
}

function ozerRules(game) {
  return `<ol class="ozer-sequence">
    <li><strong>The retailer sends a forecast</strong><p>Each period starts with a new private demand signal (X) from ${game.signal_min} to ${game.signal_max}. The retailer sees the signal and sends a forecast report (F).</p></li>
    <li><strong>The supplier chooses production</strong><p>The supplier sees the retailer’s report, then chooses how many units to produce (Q).</p></li>
    <li><strong>The game calculates sales</strong><p>Once both decisions are locked, the game adds a shock from ${game.shock_min} to ${game.shock_max} to the private signal. Sales equal the smaller of production and final demand. Unused units do not carry over.</p></li>
    </ol>
    <section class="ozer-payoff">
      <h2>How profit is calculated</h2>
      <p class="help">Prices are in experimental dollars per unit.</p>
      ${ozerFacts([["Production cost", game.production_cost], ["Wholesale price", game.wholesale_price], ["Retail price", game.retail_price]])}
      <p><strong>Supplier:</strong> Wholesale revenue minus the cost of all units produced.</p>
      <p><strong>Retailer:</strong> The retail margin on units sold.</p>
    </section>`;
}

function ozerRoleDescription(retailer) {
  return retailer ? "You see the private demand signal and send a forecast to the supplier. Your AI counterpart decides how much to produce." :
    "You see the retailer’s forecast report and decide how much to produce. Your AI counterpart sends the report.";
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
  const stepIndex = ozerSteps.findIndex(([key]) => key === stage);
  const onboarding = stepIndex >= 0;
  if (stage !== "quiz") ozerAnswers = {};
  const round = session.round_id;
  const timedRound = ["decision", "counterpart", "feedback"].includes(stage);
  const period = round <= game.practice_rounds ? `Practice ${round} of ${game.practice_rounds}` :
    `Paid round ${round - game.practice_rounds} of ${game.paid_rounds}`;
  const retailer = state.participant_role === "retailer";
  let title, content, next;
  if (stage === "general") {
    title = "What to expect";
    content = `<p class="study-intro">You’ll make a series of supply chain decisions with the same AI counterpart. You’ll learn the rules and practice before the paid periods start.</p>
      ${ozerFacts([["Practice periods", game.practice_rounds], ["Paid periods", game.paid_rounds], ["Time per decision", `${game.decision_seconds} seconds`]])}
      <section class="ozer-payoff"><h2>${session.preview ? "About this test" : "Payment"}</h2>
      <p>Practice profits do not count toward payment.</p>
      <p>${session.preview ? (session.public.provider_mode === "openai" ?
        "This preview uses the saved OpenAI model and role instructions. No participant payment is due." :
        "This test uses built-in responses and illustrates how payment is calculated.") :
        `The participation payment is ${ozerMoney(game.participation_usd)}. Positive total paid-period profits are converted at ${ozerNumber(game.experimental_dollars_per_usd)} experimental dollars per USD.`}</p></section>`;
    if (!session.preview) content += "<p>Payments are private. Please do not communicate with other participants during the experiment. Ask the experimenter if you need help.</p>";
    next = "Read task instructions";
  } else if (stage === "instructions") {
    title = "How the forecasting task works";
    content = `<p class="study-intro">Each period follows the same three steps. You have ${game.decision_seconds} seconds to submit your choice once the decision screen opens.</p>` + ozerRules(game);
    next = "See my role";
  } else if (stage === "role") {
    title = `You are the ${state.participant_role}`;
    content = `<p class="study-intro">${ozerRoleDescription(retailer)}</p>
      ${ozerFacts([
        ["What you see", retailer ? "Private demand signal (X)" : "Retailer’s forecast report (F)"],
        ["What you submit", retailer ? "Forecast report (F)" : "Production quantity (Q)"]
      ])}`;
    next = "Check my understanding";
  } else if (stage === "quiz") {
    title = "Check your understanding";
    content = ozerQuiz(state, game, session.preview && !builderPreview);
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
    ${timedRound ? `<strong>${period}</strong>` : `<span>Step ${stepIndex + 1} of ${ozerSteps.length}</span>`}</header>
    ${onboarding ? `<ol class="ozer-steps" aria-label="Study introduction progress">${ozerSteps.map(([, label], index) =>
      `<li ${index === stepIndex ? 'aria-current="step"' : ""}><span aria-hidden="true">${index + 1}</span>${label}</li>`).join("")}</ol>` : ""}
    <article class="ozer-card study-onboarding-card">
      <div class="ozer-title study-onboarding-heading"><h1 id="ozer-title" tabindex="-1">${esc(title)}</h1>
        ${stage === "decision" ? '<span id="ozer-clock" class="ozer-clock" role="timer" aria-label="Seconds remaining"></span>' : ""}</div>
      ${timedRound ? `<p class="ozer-role">You: ${state.participant_role} · Counterpart: ${state.counterpart_role}</p>` : ""}
      ${content}${next ? `<div class="study-onboarding-actions"><button class="primary" id="ozer-next">${esc(next)} <span aria-hidden="true">→</span></button></div>` : ""}
    </article>
    ${session.status === "active" ? '<footer class="ozer-footer"><button id="ozer-withdraw" class="text-button danger">Stop participating</button></footer>' : ""}`;
  panel.classList.remove("hidden");
  if (builderPreview) {
    if ($("ozer-clock")) $("ozer-clock").textContent = game.decision_seconds + "s";
    return;
  }
  ($("ozer-quiz-retry") || $("ozer-title")).focus();
  if (!tracking) trackStart();

  $("ozer-next")?.addEventListener("click", () => ozerSend({action: "continue"}));
  $("ozer-skip-quiz")?.addEventListener("click", () => ozerSend({action: "skip_quiz"}));
  $("ozer-withdraw")?.addEventListener("click", () => $("withdraw").click());
  const form = $("ozer-form");
  if (form) form.onsubmit = e => {
    e.preventDefault();
    const fields = new FormData(form);
    if (stage === "quiz") {
      ozerAnswers = Object.fromEntries(fields);
      ozerSend({action: "quiz", answers: ozerAnswers});
    }
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
