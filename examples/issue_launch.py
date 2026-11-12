"""Run on a trusted server. Never distribute this server's key to the participant."""
import argparse
import json
import os
import urllib.request


def issue_launch(origin, participant_id, condition_id, integration_key, assignment_method="external"):
    origin = origin.rstrip("/")
    if not origin.startswith("https://") and not origin.startswith("http://127.0.0.1:"):
        raise ValueError("Use HTTPS outside local development")
    req = urllib.request.Request(origin + "/api/launch", data=json.dumps({"participant_id":participant_id,"assignment_method":assignment_method,**({"condition_id":condition_id} if assignment_method=="external" else {})}).encode(),
        headers={"Content-Type":"application/json", "X-Research-Request":"1", "X-Integration-Key":integration_key})
    with urllib.request.urlopen(req, timeout=15) as response:
        result = json.load(response)
    return origin + "/participate#launch=" + result["launch_token"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("origin")
    parser.add_argument("participant_id")
    parser.add_argument("condition_id", nargs="?", default=None)
    parser.add_argument("--assignment", choices=("external","random","balanced"), default="external")
    args = parser.parse_args()
    if args.assignment=="external" and not args.condition_id: parser.error("Specify a condition ID or --assignment random/balanced")
    print(issue_launch(args.origin, args.participant_id, args.condition_id, os.environ["INTEGRATION_KEY"],args.assignment))
