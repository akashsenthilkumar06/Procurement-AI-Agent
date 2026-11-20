import copy
import hashlib
import json
import math
import re


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


DEFAULT = {
    "name": "Supplier selection pilot",
    "description": "Compare how people use agent recommendations and provisional decisions.",
    "status": "draft",
    "architecture": {
        "provider": "demo", "model": "deterministic-v1", "api_version": "responses-v1",
        "temperature": None, "reasoning_effort": None, "memory": "session",
        "knowledge_version": "supplier-demo-v1",
    },
    "scenario": {
        "title": "Choose a supply partner",
        "instructions": "You are sourcing a component for a new production run. Compare the three suppliers, consult the assistant, and submit your final selection. You may accept, reject, or override the assistant's choice.",
        "consent_text": "This pilot records your conversation, choices, response timings, and interaction counts within this page, including typing, clicks, hover, and copy/paste counts. It does not record individual keys or clipboard contents. Use a study code rather than your name. Participation is voluntary; you may stop at any time.",
        "knowledge": "Cost is per unit. Reliability is the percentage of on-time deliveries. Lead time is in days. Consider the trade-off between cost, delivery speed, and reliability.",
        "criteria": [
            {"id": "cost", "label": "Unit cost", "unit": "USD", "direction": "min", "weight": 0.4},
            {"id": "reliability", "label": "Reliability", "unit": "%", "direction": "max", "weight": 0.4},
            {"id": "lead_time", "label": "Lead time", "unit": "days", "direction": "min", "weight": 0.2},
        ],
        "options": [
            {"id": "atlas", "label": "Atlas Components", "values": {"cost": 92, "reliability": 91, "lead_time": 14}},
            {"id": "beacon", "label": "Beacon Supply", "values": {"cost": 104, "reliability": 98, "lead_time": 9}},
            {"id": "cedar", "label": "Cedar Industrial", "values": {"cost": 86, "reliability": 85, "lead_time": 21}},
        ],
    },
    "conditions": [
        {"id": "recommend", "label": "Recommendation", "role": "buyer", "objective": "Balance cost, reliability, and lead time using the configured weights.", "authority": "recommendation", "reasoning": "direct", "output": "recommendation", "selection": "weighted", "prompt": "Use neutral, concise language. Discuss the supplied scenario only.", "examples": [], "private_information": {}, "private_rules": {"min": {}, "max": {}}},
        {"id": "delegate", "label": "Agent decision with override", "role": "buyer", "objective": "Balance cost, reliability, and lead time using the configured weights.", "authority": "provisional_decision", "reasoning": "direct", "output": "recommendation", "selection": "weighted", "prompt": "Use neutral, concise language. Discuss the supplied scenario only.", "examples": [], "private_information": {}, "private_rules": {"min": {}, "max": {}}},
    ],
    "telemetry": {"enabled": True},
}


PINNED_MODEL = "gpt-4.1-mini-2025-04-14"
DEFAULT.update(case_study=None, task="supplier_selection", assignment={"method": "balanced"}, negotiation={
    "title": "Negotiate a supply agreement", "item": "Production component", "quantity": 100,
    "currency": "USD", "price_min": 50, "price_max": 150, "opening_price": 120,
    "concession_step": 5, "participant_role": "buyer",
    "instructions": "Negotiate a unit price for the stated quantity. Send offers or discuss terms, then record a final price or finish without agreement. No real order is placed.",
    "knowledge": "Prices are per unit. The stated quantity is fixed. Only unit price is negotiated in this pilot."})
DEFAULT["architecture"].update(spend_cap_usd=5.0, max_input_tokens=32000, max_output_tokens=1200,
    input_usd_per_million=0.40, output_usd_per_million=1.60, session_spend_usd=0.25)
for _condition in DEFAULT["conditions"]:
    _condition.update(agent_enabled=True, advice_quality="standard", agent=None)


def normalize(config):
    """Add v2 defaults in memory. Never rewrite immutable historical JSON or hashes."""
    c = copy.deepcopy(config)
    if not isinstance(c, dict):
        return c
    for key in ("case_study", "task", "assignment", "negotiation"):
        if key not in c:
            c[key] = copy.deepcopy(DEFAULT[key])
    if isinstance(c.get("architecture"), dict):
        for key in ("spend_cap_usd", "max_input_tokens", "max_output_tokens", "input_usd_per_million", "output_usd_per_million", "session_spend_usd"):
            c["architecture"].setdefault(key, DEFAULT["architecture"][key])
    if isinstance(c.get("conditions"), list):
        for condition in c["conditions"]:
            if isinstance(condition, dict):
                condition.setdefault("agent_enabled", True)
                condition.setdefault("advice_quality", "standard")
                condition.setdefault("agent", None)
    return c


def require(ok, message):
    if not ok:
        raise ValueError(message)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def text_field(value, name, limit=12000):
    require(isinstance(value, str) and len(value) <= limit, f"Invalid {name}")


def validate(config):
    c = normalize(config)
    require(isinstance(c, dict), "Configuration must be an object")
    require(set(c) == set(DEFAULT), "Unexpected or missing top-level configuration fields")
    pack = None
    if c['case_study'] is not None:
        from .packs import pin, resolve
        from .tools import REGISTRY, MODE_SCOPES
        require(isinstance(c['case_study'],dict), 'Invalid case-study reference')
        c['case_study']=pin(c['case_study']);pack,repository=resolve(c['case_study'])
        for name in pack['allowed_tools']:require(name in REGISTRY, 'Pack uses an unknown tool')
    text_field(c["name"], "study name", 200)
    require(bool(c["name"].strip()), "Study name is required")
    text_field(c["description"], "description")
    require(c["status"] in ("draft", "active", "closed"), "Invalid study status")
    require(c["task"] in ("supplier_selection", "negotiation"), "Choose supplier selection or negotiation")
    require(isinstance(c["assignment"], dict) and set(c["assignment"]) == {"method"} and c["assignment"]["method"] in ("external", "random", "balanced"), "Invalid assignment method")
    n = c["negotiation"]
    require(isinstance(n, dict) and set(n) == set(DEFAULT["negotiation"]), "Invalid negotiation fields")
    for key in ("title", "item", "currency", "instructions", "knowledge"):
        text_field(n[key], "negotiation " + key)
    require(bool(n["item"].strip()) and bool(n["title"].strip()), "Negotiation title and item are required")
    require(type(n["quantity"]) is int and 1 <= n["quantity"] <= 1000000, "Quantity must be a positive integer")
    require(n["participant_role"] in ("buyer", "supplier"), "Invalid participant role")
    require(re.fullmatch(r"[A-Z]{3}", n["currency"]) is not None, "Use a three-letter currency code")
    require(all(number(n[k]) for k in ("price_min", "price_max", "opening_price", "concession_step")), "Negotiation prices must be finite")
    require(0 <= n["price_min"] < n["price_max"] <= 1000000 and n["price_min"] <= n["opening_price"] <= n["price_max"], "Invalid public price range or opening price")
    require(0 < n["concession_step"] <= n["price_max"]-n["price_min"], "Invalid concession step")
    for key in ("price_min", "price_max", "opening_price", "concession_step"):
        require(abs(n[key]*100-round(n[key]*100)) < 1e-6, "Prices must have at most two decimals")
    a = c["architecture"]
    require(isinstance(a, dict) and set(a) == set(DEFAULT["architecture"]), "Invalid architecture fields")
    require(a["provider"] in ("demo", "openai", "relay"), "Provider must be demo, openai, or relay")
    require(a["memory"] in ("none", "session"), "Cross-participant memory is not implemented; choose none or session")
    for k in ("model", "api_version", "knowledge_version"):
        text_field(a[k], k, 200)
        require(bool(a[k].strip()), f"{k} is required")
    require(a["api_version"] == "responses-v1", "Only responses-v1 is supported")
    require(a["reasoning_effort"] in (None, "none", "minimal", "low", "medium", "high", "xhigh"), "Invalid reasoning effort")
    require(a["temperature"] is None or (number(a["temperature"]) and 0 <= a["temperature"] <= 2), "Invalid temperature")
    require(number(a["spend_cap_usd"]) and 0 <= a["spend_cap_usd"] <= 100000, "Invalid application spend cap")
    require(type(a["max_input_tokens"]) is int and 1000 <= a["max_input_tokens"] <= 32000, "Input limit must be 1000–32000 tokens")
    require(type(a["max_output_tokens"]) is int and 100 <= a["max_output_tokens"] <= 4000, "Output limit must be 100–4000 tokens")
    require(all(number(a[k]) and 0 < a[k] <= 1000 for k in ("input_usd_per_million", "output_usd_per_million")), "Positive token prices are required")
    require(number(a["session_spend_usd"]) and 0<a["session_spend_usd"]<=100000, "Invalid per-session spend allowance")
    if a["provider"] == "openai":
        require(re.search(r"-\d{4}-\d{2}-\d{2}$", a["model"]) is not None, "API studies require a dated model snapshot, not an alias")
        require(number(a["temperature"]), "Set a fixed numeric temperature for API studies")
        require(a["spend_cap_usd"] > 0, "Set a positive spend cap for API studies")
        if a["model"] == PINNED_MODEL:
            require(a["reasoning_effort"] is None, "The pinned GPT-4.1 mini model does not support a reasoning-effort parameter")
    if a["provider"] == "demo":
        require(a["model"] == "deterministic-v1", "Demo provider requires deterministic-v1")
        require(a["temperature"] is None and a["reasoning_effort"] is None, "Demo provider does not support model generation settings")
    if a["provider"] == "relay":
        require(a["model"] == "chat-relay-v1", "Relay provider requires chat-relay-v1")
        require(a["temperature"] is None and a["reasoning_effort"] is None, "Manual relay cannot enforce model generation settings")
    s = c["scenario"]
    require(isinstance(s, dict) and set(s) == set(DEFAULT["scenario"]), "Invalid scenario fields")
    for k in ("title", "instructions", "knowledge", "consent_text"):
        text_field(s[k], k)
    require(bool(s["consent_text"].strip()), "Consent text is required")
    require(isinstance(s["criteria"], list) and 1 <= len(s["criteria"]) <= 20, "Use 1–20 criteria")
    require(isinstance(s["options"], list) and 2 <= len(s["options"]) <= 30, "Use 2–30 options")
    ids = set()
    for item in s["criteria"]:
        require(isinstance(item, dict) and set(item) == {"id", "label", "unit", "direction", "weight"}, "Invalid criterion fields")
        require(isinstance(item["id"], str) and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", item["id"]) and item["id"] not in ids, "Criterion IDs must be unique slugs")
        ids.add(item["id"])
        text_field(item["label"], "criterion label", 100)
        text_field(item["unit"], "criterion unit", 40)
        require(item["direction"] in ("min", "max") and number(item["weight"]) and 0 <= item["weight"] <= 100, "Invalid criterion direction or weight")
    require(sum(x["weight"] for x in s["criteria"]) > 0, "At least one positive weight is required")
    option_ids = set()
    for item in s["options"]:
        require(isinstance(item, dict) and set(item) == {"id", "label", "values"}, "Invalid option fields")
        require(isinstance(item["id"], str) and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", item["id"]) and item["id"] not in option_ids, "Option IDs must be unique slugs")
        option_ids.add(item["id"])
        text_field(item["label"], "option label", 100)
        require(isinstance(item["values"], dict) and set(item["values"]) == ids and all(number(v) for v in item["values"].values()), "Every option needs a finite value for every criterion")
    require(isinstance(c["conditions"], list) and 1 <= len(c["conditions"]) <= 20, "Use 1–20 conditions")
    condition_ids = set()
    for cond in c["conditions"]:
        require(isinstance(cond, dict) and set(cond) == set(DEFAULT["conditions"][0]), "Invalid condition fields")
        require(isinstance(cond["id"], str) and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", cond["id"]) and cond["id"] not in condition_ids, "Condition IDs must be unique slugs")
        condition_ids.add(cond["id"])
        for k in ("label", "role", "objective", "prompt"):
            text_field(cond[k], k)
        require(type(cond["agent_enabled"]) is bool, "Agent enabled must be a boolean")
        require(cond["advice_quality"] in ("standard", "flawed"), "Invalid advice quality")
        agent=cond['agent']
        if pack:
            require(a['provider']!='relay', 'Case-study agents use demo or OpenAI; legacy relay remains available outside packs')
            require(isinstance(agent,dict) and set(agent)=={'mode','allowed_tools','data_access','max_tool_calls','tool_timeout_seconds','turn_timeout_seconds'}, 'Invalid agent settings')
            require(agent['mode'] in MODE_SCOPES, 'Invalid agent authority mode')
            require(cond['role'] in (('supplier','retailer') if pack['id']=='ozer_game' else ('buyer','supplier','genai_tool')), 'Invalid agent role')
            require(isinstance(agent['allowed_tools'],list) and len(agent['allowed_tools'])==len(set(agent['allowed_tools'])) and set(agent['allowed_tools'])<=set(pack['allowed_tools']), 'Tools must be a subset of the pack tools')
            require(isinstance(agent['data_access'],dict), 'Invalid data access')
            for table,fields in agent['data_access'].items():
                require(table in repository.manifest['tables'] and isinstance(fields,list) and all(isinstance(f,str) for f in fields) and set(fields)<=set(repository.manifest['tables'][table]['fields']), 'Unknown data fields')
            require(type(agent['max_tool_calls']) is int and 1<=agent['max_tool_calls']<=12, 'Use 1–12 tool calls per turn')
            require(number(agent['tool_timeout_seconds']) and .05<=agent['tool_timeout_seconds']<=10, 'Invalid tool timeout')
            require(number(agent['turn_timeout_seconds']) and 1<=agent['turn_timeout_seconds']<=90, 'Invalid turn timeout')
            if cond['output']=='number' and agent['mode']!='analytical':require(len(pack['decision']['schema']['properties'])==1 and next(iter(pack['decision']['schema']['properties'].values()))['type'] in ('number','integer'), 'Numeric pack output requires a single numeric decision field')
        else:require(agent is None, 'Choose a case-study pack to configure an agent')
        require(cond["authority"] in ("recommendation", "provisional_decision", "chat"), "Invalid authority")
        require(cond["reasoning"] in ("direct", "role", "few_shot", "explanation"), "Invalid reasoning mode")
        require(cond["output"] in ("number", "explanation", "recommendation"), "Invalid output type")
        require(cond["selection"] in ("weighted", "model"), "Invalid selection engine")
        require(a["provider"] != "demo" or cond["selection"] == "weighted", "Model selection requires an LLM provider")
        require(isinstance(cond["examples"], list) and len(cond["examples"]) <= 10, "Use at most 10 examples")
        for ex in cond["examples"]:
            require(isinstance(ex, dict) and set(ex) == {"user", "assistant"}, "Examples need user and assistant fields")
            for v in ex.values():
                text_field(v, "example")
        require(isinstance(cond["private_information"], dict), "Private information must be an object")
        rules = cond["private_rules"]
        require(isinstance(rules, dict) and set(rules) == {"min", "max"}, "Private rules need min and max objects")
        for bounds in rules.values():
            require(isinstance(bounds, dict) and set(bounds) <= (ids | {"price"}) and all(number(v) for v in bounds.values()), "Invalid private bound")
        for k in rules["min"].keys() & rules["max"].keys():
            require(rules["min"][k] <= rules["max"][k], "Private minimum exceeds maximum")
        if c["task"] != "negotiation":
            require(all("price" not in bounds for bounds in rules.values()), "Price bounds apply only to negotiation")
        for bounds in rules.values():
            if "price" in bounds:
                require(n["price_min"] <= bounds["price"] <= n["price_max"], "Private price limit must lie inside the public range")
        if private_mode(cond) and not pack:
            require(cond["selection"] == "weighted", "Private conditions require weighted selection and approved template outputs")
    if pack and pack['id'] == 'ozer_game':
        from .ozer import validate_conditions
        validate_conditions(c, pack)
    telemetry = c["telemetry"]
    require(isinstance(telemetry, dict) and "enabled" in telemetry
            and set(telemetry) <= {"enabled", "clipboard_text", "outside_ai_question"}
            and all(type(value) is bool for value in telemetry.values()), "Invalid telemetry settings")
    require(len(canonical(c)) < 200000, "Configuration is too large")
    return c


def private_mode(cond):
    return bool(cond["private_information"] or any(cond["private_rules"].values()))


def live_supplier_preview(config, condition_id):
    """Use the saved public supplier data in a separate, conversational preview."""
    condition = next((group for group in config["conditions"] if group["id"] == condition_id), None)
    if (config["case_study"] or config["task"] != "supplier_selection" or not condition
            or not condition["agent_enabled"] or private_mode(condition)
            or condition["output"] == "number" or condition["advice_quality"] != "standard"):
        raise ValueError("Live supplier preview requires a public supplier group with text replies.")
    architecture = config["architecture"]
    if architecture["provider"] != "openai":
        architecture.update(provider="openai", model=PINNED_MODEL, temperature=0, reasoning_effort=None)
    condition["selection"] = "model"
    return validate(config)


def public_negotiation(config, condition):
    negotiation = config["negotiation"]
    participant_role = negotiation["participant_role"]
    if condition.get("agent_enabled", True) and condition["role"] in ("buyer", "supplier"):
        participant_role = "buyer" if condition["role"] == "supplier" else "supplier"
    terms = {key: negotiation[key] for key in ("title", "item", "quantity", "currency", "price_min", "price_max", "knowledge")}
    terms["participant_role"] = participant_role
    if (config.get("case_study") or {}).get("id") == "negotiation":
        from .packs import resolve
        pack, _ = resolve(config["case_study"])
        if pack.get("offer_policy"):
            terms.update(price_min=pack["offer_policy"]["public_min"], price_max=pack["offer_policy"]["public_max"])
    return terms


def public_config(config, condition, *, preview=False):
    # Explicit allowlists. Negotiation policy limits and raw private fields never cross this boundary.
    config = normalize(config)
    negotiation = config["negotiation"]
    scenario = config["scenario"] if config["task"] == "supplier_selection" else {
        "title": negotiation["title"], "instructions": negotiation["instructions"],
        "knowledge": negotiation["knowledge"], "consent_text": config["scenario"]["consent_text"],
        "criteria": [], "options": []}
    result = {"scenario": scenario, "task": config["task"], "authority": condition["authority"],
            "agent_enabled": condition.get("agent_enabled", True), "agent_role": condition["role"],
            "output": condition["output"], "telemetry_enabled": config["telemetry"]["enabled"],
            "provider_mode": config["architecture"]["provider"],
            "negotiation": public_negotiation(config, condition)}
    if preview:
        result["test_group"] = condition["label"]
    if config['case_study']:
        from .packs import public_pack
        result['case_study']=public_pack(config,condition)
        result['scenario']={**result['scenario'],'title':result['case_study']['title'],**result['case_study']['scenario']}
    from .usage_evidence import consent_fields
    collection = consent_fields(config)
    if any(collection.values()):
        result["usage_evidence"] = collection
        if collection["clipboard_notice"]:
            # Older stock consent copy described the counts-only behavior.
            text = result["scenario"]["consent_text"].replace(
                "It does not record individual keys or clipboard contents.", "It does not record individual keys.")
            result["scenario"] = {**result["scenario"], "consent_text": text}
    return result
