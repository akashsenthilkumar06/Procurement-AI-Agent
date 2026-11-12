"""Server-owned workflow for the Ozer forecast information sharing protocol."""

import copy
import json
import re
from decimal import Decimal
from secrets import randbelow

from .agent import SystemLimit
from .config import DEFAULT, canonical, public_config, require
from .packs import resolve
from .providers import ProviderError, ProviderTimeout, agent_step
from .store import now, uid
from . import usage_evidence


COUNTERPART = {"human_supplier": "retailer", "human_retailer": "supplier"}
MODEL_TIMEOUT = 25


def is_ozer(config):
    return (config.get("case_study") or {}).get("id") == "ozer_game"


def is_demo_preview(session):
    return bool(session["preview"]) and session["model_snapshot"] == "deterministic-v1"


def configure(config, pack):
    config["architecture"].update(memory="none", max_input_tokens=8000, max_output_tokens=150)
    config["assignment"]["method"] = "random"
    config["scenario"]["consent_text"] = (
        "This study records your assigned role, round decisions, outcomes, and response times. "
        "It may also record interaction counts such as key presses and clicks. It does not "
        "record individual keys or clipboard contents. Participation is voluntary."
    )
    config["conditions"] = []
    for identifier, role in COUNTERPART.items():
        participant = "supplier" if role == "retailer" else "retailer"
        condition = copy.deepcopy(DEFAULT["conditions"][0])
        condition.update(
            id=identifier, label=f"Human {participant} · AI {role}", role=role,
            output="number", authority="chat", objective="Maximize your own role's expected profit.",
            prompt=("Choose the demand forecast to report to the supplier." if role == "retailer" else
                    "Choose the production quantity using only the retailer's report and public rules."),
            agent={"mode": "analytical", "allowed_tools": [], "data_access": {},
                   "max_tool_calls": 1, "tool_timeout_seconds": 3, "turn_timeout_seconds": MODEL_TIMEOUT},
        )
        config["conditions"].append(condition)


def validate_conditions(config, pack):
    conditions = config["conditions"]
    require({c["id"]: c["role"] for c in conditions} == COUNTERPART,
            "Ozer requires both fixed human/AI counterpart conditions")
    require(config["architecture"]["memory"] == "none",
            "Ozer decisions receive only the current round's permitted information")
    for condition in conditions:
        require(condition["agent_enabled"] and condition["agent"]["mode"] == "analytical"
                and condition["output"] == "number" and not condition["agent"]["data_access"],
                "Ozer counterparts submit one numeric decision in each round")
    if config["architecture"]["provider"] == "openai":
        from .engine import input_bound
        for condition in conditions:
            observation = pack["game"]["signal_max"] if condition["role"] == "retailer" else bounds(pack, "forecast")[1]
            minimum = input_bound(counterpart_input(pack, condition, observation))
            require(config["architecture"]["max_input_tokens"] >= minimum,
                    f"Ozer's AI {condition['role']} instructions need an input allowance of at least {minimum}. "
                    "Shorten the goal or instructions, or raise Maximum input allowance in Study controls.")


def draw(low, high):
    return low + randbelow(high - low + 1)


def payoff(game, signal, shock, production):
    demand = signal + shock
    sales = min(production, demand)
    return {
        "demand": demand,
        "sales": sales,
        "supplier_profit": game["wholesale_price"] * sales - game["production_cost"] * production,
        "retailer_profit": (game["retail_price"] - game["wholesale_price"]) * sales,
    }


def bounds(pack, field):
    schema = pack["decision"]["schema"]["properties"][field]
    return schema["minimum"], schema["maximum"]


def validate_quantity(pack, field, value):
    low, high = bounds(pack, field)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Enter a whole number between {low} and {high}.")
    return value


def counterpart_input(pack, condition, observation):
    role = condition["role"]
    game = pack["game"]
    field = "forecast" if role == "retailer" else "production"
    low, high = bounds(pack, field)
    observed_field = "private_signal" if role == "retailer" else "retailer_report"
    task = (
        "As retailer, submit the forecast report you want the supplier to receive. The supplier decides production."
        if role == "retailer" else
        "As supplier, choose your production. The retailer report may differ from the private signal."
    )
    rules = (
        f"You are the {role} in one round of an experimental forecasting game. {task} "
        f"The retailer observes a uniformly distributed integer signal from {game['signal_min']} "
        f"to {game['signal_max']}. Final demand equals the signal plus an independent uniform "
        f"integer shock from {game['shock_min']} to {game['shock_max']}, drawn after both decisions. "
        f"Sales equal min(production, demand). Production costs {game['production_cost']} per unit. "
        f"The wholesale price is {game['wholesale_price']} and the retail price is {game['retail_price']}. "
        "Supplier profit is wholesale revenue minus total production cost. "
        "Retailer profit is the retail minus wholesale margin multiplied by sales. "
        "Use the study-specific goal and instructions below when they are consistent with the game rules. "
        f"{canonical({'goal': condition['objective'], 'instructions': condition['prompt']})}\n"
        f"Return only your integer {field}, from {low} to {high}, as the numeric answer string."
    )
    return [{"role": "system", "content": rules},
            {"role": "user", "content": canonical({observed_field: observation})}]


def expire_round(store, db, session, stage, row, timestamp):
    if session["status"] != "active" or row is None:
        return False
    if stage == "decision" and timestamp >= row["deadline_at"]:
        participant = "retailer" if session["condition_id"] == "human_retailer" else "supplier"
        store.server_event(session, "ozer_decision_timeout", {"role": participant, "deadline_at": row["deadline_at"]}, db)
        store._end_limit(db, session, "ozer_decision_timeout",
                         "The one-minute decision window ended. Return to your survey to continue.")
        return True
    if stage == "counterpart" and timestamp >= row["agent_started_at"] + MODEL_TIMEOUT + 5:
        store._end_limit(db, session, "ozer_counterpart_timeout",
                         "The counterpart did not finish its decision. Return to your survey to continue.")
        return True
    return False


def expire_waiting_rounds(store):
    """Record expired rounds even if the participant has closed the study tab."""
    timestamp = now()
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        expired = db.execute(
            """SELECT s.*, p.stage, r.deadline_at, r.agent_started_at
               FROM ozer_progress p JOIN sessions s ON s.id=p.session_id
               JOIN ozer_rounds r ON r.session_id=s.id AND r.round_id=s.round_id
               WHERE s.status='active' AND ((p.stage='decision' AND r.deadline_at<=?)
                   OR (p.stage='counterpart' AND r.agent_started_at<=?))""",
            (timestamp, timestamp - MODEL_TIMEOUT - 5),
        ).fetchall()
        for session in expired:
            expire_round(store, db, dict(session), session["stage"], session, timestamp)


class OzerGame:
    def __init__(self, store, session, config, condition):
        self.store = store
        self.session_id = session["id"]
        self.config = config
        self.condition = condition
        self.pack, _ = resolve(config["case_study"])
        self.game = self.pack["game"]
        self.participant = "supplier" if condition["role"] == "retailer" else "retailer"
        self.total_rounds = self.game["practice_rounds"] + self.game["paid_rounds"]

    def _read(self, db):
        row = db.execute("SELECT * FROM sessions WHERE id=?", (self.session_id,)).fetchone()
        if row is None:
            raise PermissionError("Invalid session token")
        session = dict(row)
        progress = db.execute("SELECT stage FROM ozer_progress WHERE session_id=?", (self.session_id,)).fetchone()
        round_row = db.execute(
            "SELECT * FROM ozer_rounds WHERE session_id=? AND round_id=?",
            (self.session_id, session["round_id"]),
        ).fetchone()
        return session, progress["stage"] if progress else None, round_row

    def _stage(self, db, stage):
        db.execute("UPDATE ozer_progress SET stage=? WHERE session_id=?", (stage, self.session_id))

    def _log(self, db, session, name, **payload):
        self.store.server_event(session, "ozer_" + name, payload, db)

    def view(self):
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            session, stage, row = self._read(db)
            if expire_round(self.store, db, session, stage, row, now()):
                session, stage, row = self._read(db)
            result = {
                "session_id": self.session_id, "status": session["status"],
                "round_id": session["round_id"], "preview": bool(session["preview"]),
                "public": public_config(self.config, self.condition), "messages": [], "decision": None,
                "usage_evidence": usage_evidence.participant_state(db, self.session_id),
                "end_reason": session["end_reason"], "system_message": session["system_message"],
            }
            game = result["ozer"] = {"stage": stage, "participant_role": self.participant,
                                     "counterpart_role": self.condition["role"], "server_now": now()}
            if is_demo_preview(session):
                result["public"]["provider_mode"] = "demo"
            if session["status"] == "active" and stage == "decision":
                game["deadline_at"] = row["deadline_at"]
                if self.participant == "retailer":
                    game["private_signal"] = row["signal"]
                else:
                    game["retailer_report"] = row["forecast"]
            if (session["status"] == "active" and stage == "feedback") or session["status"] == "completed":
                decisions = db.execute(
                    "SELECT outcome_json FROM decisions WHERE session_id=? ORDER BY round_id", (self.session_id,)
                )
                rounds = [json.loads(d["outcome_json"]) for d in decisions]
                if stage == "feedback":
                    game["feedback"] = self._feedback(rounds)
                else:
                    game["results"] = self._results(rounds)
        return result

    def _feedback(self, rounds):
        current = rounds[-1]
        profit_key = self.participant + "_profit"
        visible = {key: current[key] for key in ("forecast", "production", "demand", "sales", "practice")}
        visible["profit"] = current[profit_key]
        visible["cumulative_profit"] = sum(r[profit_key] for r in rounds if r["practice"] == current["practice"])
        if self.participant == "retailer":
            visible["private_signal"] = current["signal"]
        return visible

    def _results(self, rounds):
        paid = [r for r in rounds if not r["practice"]]
        profit = sum(r[self.participant + "_profit"] for r in paid)
        bonus = Decimal(max(0, profit)) / self.game["experimental_dollars_per_usd"]
        return {"paid_rounds": len(paid), "paid_profit": profit,
                "performance_usd": str(bonus), "participation_usd": self.game["participation_usd"],
                "total_usd": str(Decimal(self.game["participation_usd"]) + bonus)}

    def _open_decision(self, db, session):
        opened = now()
        deadline = opened + self.game["decision_seconds"]
        db.execute(
            "UPDATE ozer_rounds SET decision_opened_at=?,deadline_at=? WHERE session_id=? AND round_id=?",
            (opened, deadline, self.session_id, session["round_id"]),
        )
        self._stage(db, "decision")
        self._log(db, session, "decision_opened", role=self.participant, opened_at=opened, deadline_at=deadline)

    def _start_round(self, db, session):
        signal = draw(self.game["signal_min"], self.game["signal_max"])
        db.execute("INSERT INTO ozer_rounds (session_id,round_id,signal) VALUES (?,?,?)",
                   (self.session_id, session["round_id"], signal))
        self._log(db, session, "round_started", signal=signal,
                  practice=session["round_id"] <= self.game["practice_rounds"])
        if self.participant == "retailer":
            self._open_decision(db, session)
            return None
        self._begin_counterpart(db, session)
        return signal

    def _begin_counterpart(self, db, session):
        db.execute("UPDATE ozer_rounds SET agent_started_at=? WHERE session_id=? AND round_id=?",
                   (now(), self.session_id, session["round_id"]))
        self._stage(db, "counterpart")

    def _settle(self, db, session, row):
        shock = draw(self.game["shock_min"], self.game["shock_max"])
        practice = session["round_id"] <= self.game["practice_rounds"]
        result = {
            "round_id": session["round_id"], "practice": practice, "participant_role": self.participant,
            "signal": row["signal"], "shock": shock, "forecast": row["forecast"], "production": row["production"],
            "decision_opened_at": row["decision_opened_at"], "deadline_at": row["deadline_at"],
            "human_submitted_at": row["human_submitted_at"],
            **payoff(self.game, row["signal"], shock, row["production"]),
        }
        db.execute(
            """INSERT INTO decisions (id,session_id,action,created_at,server_since_seen_ms,reason,
                participant_id,condition_id,config_version,round_id,model_snapshot,dataset_version,
                temperature,task,outcome_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid(), self.session_id, "ozer_round", now(),
             (row["human_submitted_at"] - row["decision_opened_at"]) * 1000, "",
             session["participant_id"], session["condition_id"], session["config_version"], session["round_id"],
             session["model_snapshot"], session["dataset_version"], session["temperature"], "ozer_game", canonical(result)),
        )
        self._stage(db, "feedback")
        self._log(db, session, "round_settled", practice=practice)
        if session["round_id"] == self.total_rounds:
            self._record_completion(db, session)

    def _record_completion(self, db, session):
        completed = db.execute(
            "UPDATE sessions SET status='completed',ended_at=? WHERE id=? AND status='active'",
            (now(), self.session_id),
        ).rowcount
        if completed:
            self.store._release_session(db, self.session_id)
            self._log(db, session, "completed", rounds=self.total_rounds)

    def _transition(self, db, session, stage, row, data, timestamp):
        action = data.get("action")
        if action == "continue" and stage in ("general", "instructions", "role"):
            self._stage(db, {"general": "instructions", "instructions": "role", "role": "quiz"}[stage])
        elif action == "quiz" and stage == "quiz":
            expected = {"signal": "retailer", "paid": "paid_only", "decision": self.participant}
            passed = data.get("answers") == expected
            self._log(db, session, "comprehension", passed=passed)
            if passed:
                return self._start_round(db, session), False
            return None, True
        elif action == "submit" and stage == "decision":
            field = "forecast" if self.participant == "retailer" else "production"
            value = validate_quantity(self.pack, field, data.get("value"))
            submitted = timestamp
            db.execute(f"UPDATE ozer_rounds SET {field}=?,human_submitted_at=? WHERE session_id=? AND round_id=?",
                       (value, submitted, self.session_id, session["round_id"]))
            self._log(db, session, "human_decision", role=self.participant, field=field, value=value,
                      response_ms=round((submitted - row["decision_opened_at"]) * 1000, 3))
            if self.participant == "retailer":
                self._begin_counterpart(db, session)
                return value, False
            self._settle(db, session, dict(row, production=value, human_submitted_at=submitted))
        elif action == "continue" and stage == "feedback":
            if session["round_id"] == self.total_rounds:
                # Earlier sessions may still be active when they reach final feedback.
                self._record_completion(db, session)
                self._stage(db, "results")
            else:
                session["round_id"] += 1
                db.execute("UPDATE sessions SET round_id=? WHERE id=?", (session["round_id"], self.session_id))
                return self._start_round(db, session), False
        elif action != "refresh":
            raise ValueError("That action is unavailable at this step.")
        return None, False

    def advance(self, data):
        observation = None
        quiz_retry = False
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            session, stage, row = self._read(db)
            timestamp = now()
            final_feedback_continue = (
                session["status"] == "completed" and stage == "feedback"
                and session["round_id"] == self.total_rounds and data.get("action") == "continue"
            )
            if (not expire_round(self.store, db, session, stage, row, timestamp)
                    and (session["status"] == "active" or final_feedback_continue)
                    and type(data.get("round_id")) is int and data["round_id"] == session["round_id"]
                    and data.get("stage") == stage):
                observation, quiz_retry = self._transition(db, session, stage, row, data, timestamp)
        if observation is not None:
            self._run_counterpart(session, observation)
        result = self.view()
        if quiz_retry and result["ozer"]["stage"] == "quiz":
            result["ozer"]["quiz_retry"] = True
        return result

    def _run_counterpart(self, session, observation):
        field = "forecast" if self.participant == "supplier" else "production"
        started = now()
        provider = "demo" if is_demo_preview(session) else self.config["architecture"]["provider"]
        try:
            if provider == "demo":
                low, high = bounds(self.pack, field)
                value = min(high, max(low, observation))
                metadata = {"provider": "demo"}
            else:
                items = counterpart_input(self.pack, self.condition, observation)
                result, metadata = self.store.provider_call(
                    session, f"ozer:{session['round_id']}", self.config, self.condition, items,
                    invoke=lambda: agent_step(self.config, self.condition, items, [], {}, MODEL_TIMEOUT),
                )
                final = result.get("final")
                answer = final.get("answer") if isinstance(final, dict) else None
                if (result.get("calls") or not isinstance(answer, str) or
                        not re.fullmatch(r"[0-9]{1,8}", answer) or final.get("action") is not None):
                    raise ProviderError("Invalid counterpart decision")
                value = int(answer)
                metadata["provider"] = provider
            validate_quantity(self.pack, field, value)
        except SystemLimit as error:
            self.store.end_system_limit(session, error.reason, str(error))
            return
        except ProviderTimeout:
            self.store.end_system_limit(session, "ozer_counterpart_timeout",
                                        "The counterpart did not finish its decision.")
            return
        except (ProviderError, ValueError):
            self.store.end_system_limit(session, "ozer_counterpart_failed",
                                        "The counterpart could not submit a valid decision.")
            return

        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current, stage, row = self._read(db)
            if (current["status"] != "active" or current["round_id"] != session["round_id"]
                    or stage != "counterpart" or expire_round(self.store, db, current, stage, row, now())):
                return
            db.execute(f"UPDATE ozer_rounds SET {field}=? WHERE session_id=? AND round_id=?",
                       (value, self.session_id, current["round_id"]))
            self._log(db, current, "counterpart_decision", role=self.condition["role"], field=field, value=value,
                      latency_ms=round((now() - started) * 1000, 3), provider=metadata["provider"],
                      returned_model=metadata.get("returned_model"))
            if self.participant == "supplier":
                self._open_decision(db, current)
            else:
                self._settle(db, current, dict(row, production=value))
