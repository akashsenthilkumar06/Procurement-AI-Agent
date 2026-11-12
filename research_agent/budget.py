"""Session admission reservations plus conservative per-request accounting, in micro-USD."""
import math
import os
import time
from .providers import ProviderError


def cap_micros(config):
    cap=config['architecture']['spend_cap_usd']
    if os.environ.get('SERVER_SPEND_CAP_USD'):
        try:
            ceiling=float(os.environ['SERVER_SPEND_CAP_USD'])
            if not math.isfinite(ceiling) or ceiling<=0:raise ValueError()
        except ValueError:raise ProviderError('Server spend cap is invalid') from None
        cap=min(cap,ceiling)
    return math.floor(cap*1_000_000)


def step_allowance(config):
    a=config['architecture']
    return math.ceil(a['max_input_tokens']*a['input_usd_per_million']+a['max_output_tokens']*a['output_usd_per_million'])


def accounting(db,timestamp):
    spent=db.execute('SELECT COALESCE(SUM(COALESCE(charged_micros,reserved_micros)),0) FROM provider_spend').fetchone()[0]
    spent += db.execute("SELECT COALESCE(SUM(COALESCE(charged_micros,reserved_micros)),0) FROM setup_spend").fetchone()[0]
    held=db.execute("""SELECT COALESCE(SUM(MAX(0,b.allocation_micros-COALESCE((SELECT SUM(COALESCE(p.charged_micros,p.reserved_micros)) FROM provider_spend p WHERE p.session_id=b.session_id),0))),0)
        FROM session_budgets b JOIN sessions s ON s.id=b.session_id
        WHERE b.released_at IS NULL AND s.status='active' AND s.created_at>?""",(timestamp-86400,)).fetchone()[0]
    return {'committed_micros':spent,'reserved_session_micros':held,'allocated_micros':spent+held}


def reserve_workspace_call(store, cost_config):
    reserve = step_allowance(cost_config)
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        timestamp = time.time()
        cap = cap_micros(store._revision_view(store._study_revision(db))["config"])
        if accounting(db, timestamp)["allocated_micros"] + reserve > cap:
            raise ValueError("The study API spending limit has been reached.")
        if db.execute("SELECT 1 FROM setup_spend WHERE charged_micros IS NULL AND created_at > ?",
                      (timestamp - 55,)).fetchone():
            raise ValueError("A workspace AI request is already in progress. Try again shortly.")
        return db.execute("INSERT INTO setup_spend(created_at,reserved_micros) VALUES(?,?)",
                          (timestamp, reserve)).lastrowid


def settle_workspace_call(store, charge_id, cost_config, response):
    # A response without usage still consumes the reserved allowance.
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
    rates = cost_config["architecture"]
    charged = None
    if all(type(count) is int and count >= 0 for count in (tokens_in, tokens_out)):
        charged = math.ceil(tokens_in * rates["input_usd_per_million"] +
                            tokens_out * rates["output_usd_per_million"])
    with store.db() as db:
        db.execute("UPDATE setup_spend SET charged_micros=COALESCE(?,reserved_micros) WHERE id=?",
                   (charged, charge_id))


def remaining(db,session_id):
    row=db.execute('SELECT allocation_micros,released_at FROM session_budgets WHERE session_id=?',(session_id,)).fetchone()
    if row is None or row['released_at'] is not None:return None
    used=db.execute('SELECT COALESCE(SUM(COALESCE(charged_micros,reserved_micros)),0) FROM provider_spend WHERE session_id=?',(session_id,)).fetchone()[0]
    return row['allocation_micros']-used
