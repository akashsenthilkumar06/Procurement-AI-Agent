"""Summarize downloaded CSV exports: python3 examples/analyze_exports.py /path/to/exports"""
import csv
import json
import statistics
import sys
from pathlib import Path


def read(folder, name):
    path = folder / (name + ".csv")
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def summarize(folder):
    sessions = [s for s in read(folder, "sessions") if s["preview"] == "0"]
    ids = {s["id"] for s in sessions}
    decisions = [d for d in read(folder, "decisions") if d["session_id"] in ids]
    messages = [m for m in read(folder, "messages") if m["session_id"] in ids]
    events = [e for e in read(folder, "events") if e["session_id"] in ids]
    print(f"Study sessions: {len(sessions)}; final decisions: {len(decisions)}")
    for condition,version in sorted({(s["condition_id"],s.get("config_version",s["revision_id"])) for s in sessions}):
        group = [d for d in decisions if d["condition_id"] == condition and d.get("config_version",d["revision_id"])==version]
        print(f"\nCondition: {condition}; config v{version}; decisions: {len(group)}")
        for action in ("accept", "reject", "override", "independent"):
            print(f"  {action}: {sum(d['action'] == action for d in group)}")
        for field in ("client_deliberation_ms", "outcome_score", "score_regret", "final_price"):
            values = [float(d[field]) for d in group if d.get(field)]
            print(f"  {field}: mean={statistics.mean(values):.2f}, n={len(values)}" if values else f"  {field}: no observations")
        latencies = [json.loads(m["metadata_json"])["latency_ms"] for m in messages if m["role"] == "assistant" and m["condition_id"] == condition and m.get("config_version",m["revision_id"])==version]
        latencies = [v for v in latencies if v is not None]
        if latencies:
            print(f"  adapter latency mean: {statistics.mean(latencies):.2f} ms")
        print("  simulated agreements:",sum(d.get("agreement")=="1" for d in group))
        counts = {}
        for event in events:
            if event["condition_id"] == condition and event.get("config_version",event["revision_id"])==version and event["type"] == "interaction_counts":
                for k,v in json.loads(event["payload_json"]).items(): counts[k] = counts.get(k,0) + v
        print("  interaction totals:", counts)


if __name__ == "__main__":
    summarize(Path(sys.argv[1]))
