import csv
import importlib
import io
import json
import sys
from pathlib import Path
from replay import ROOT, load, replay, signature
from portal import digest

MARKER = "ARCHIVE_ENTITY_MISMATCH"

def logrows(result):
    rows = []
    for raw in result["log"]:
        row = {"event": raw["event"], "op": raw["op"]}
        for key in ("job", "request", "ticket", "entity", "range"):
            if key in raw:
                row[key] = raw[key]
        if "context" in raw:
            row["context"] = raw["context"]
        if "response" in raw:
            row["response"] = {key: value for key, value in raw["response"].items()
                               if key != "body"}
        if "inventory" in raw:
            row["inventory"] = raw["inventory"]
        if "artifact" in raw:
            row["artifact"] = {key: value for key, value in raw["artifact"].items()
                               if key not in ("text", "owners")}
        rows.append(row)
    return rows

def summary(result):
    rows = []
    for artifact in result["publications"]:
        row = dict(artifact)
        if row["status"] == "published":
            records = list(csv.DictReader(io.StringIO(row["text"])))
            row["total"] = sum(int(record["qty"]) for record in records)
        rows.append(row)
    return {"publications": rows, "bad": result["bad"]}

def normal():
    result = replay("normal")
    assert not result["bad"]
    actual = [list(csv.DictReader(io.StringIO(row["text"])))
              for row in result["publications"]]
    assert actual == [
        [{"well": "N001", "qty": "010"}, {"well": "N002", "qty": "020"}],
        [{"well": "N001", "qty": "011"}, {"well": "N002", "qty": "021"}],
        [{"well": "N001", "qty": "011"}, {"well": "N002", "qty": "021"}]]
    assert all(row["status"] == "published" and row["origin_equal"]
               for row in result["publications"])
    print("NORMAL_REGRESSION_OK")
    return result

def incident():
    result = replay("incident")
    print(json.dumps(summary(result), sort_keys=True))
    if result["bad"]:
        print(MARKER, file=sys.stderr)
        return 1
    return 0

def baseline():
    normal()
    first, second = replay("incident"), replay("incident")
    assert first == second and first["bad"]
    recorded = [json.loads(line) for line in
                (ROOT / "logs" / "incident.jsonl").read_text().splitlines()]
    assert recorded == logrows(first)
    assert all(row["status"] == "published" for row in first["publications"])
    assert all(len(row["text"].encode("ascii")) == 27 for row in first["publications"])
    assert all(row["sha256"] == digest(row["text"].encode("ascii"))
               for row in first["publications"])
    print(json.dumps(summary(first), sort_keys=True))
    print("BASELINE_EVIDENCE_OK")

def report():
    baseline()
    stored = json.loads((ROOT / "evidence.json").read_text())
    computed = importlib.import_module("analysis").analyze(load(), "incident")
    key = lambda row: json.dumps(row["signature"], sort_keys=True, separators=(",", ":"))
    assert computed and len({key(row) for row in computed}) == len(computed)
    assert {key(row) for row in computed} == {key(row) for row in stored["joint"]}
    for row in computed + stored["joint"]:
        assert row["signature"] == signature(replay("incident", row["order"]))
    assert stored["experiments"]
    for row in stored["experiments"]:
        result = replay(row["case"], row["order"], row.get("capture"))
        assert row["signature"] == signature(result)
    text = (ROOT / "REPORT.md").read_text()
    assert text.strip()
    print(json.dumps({"joint": len(computed),
                      "experiments": len(stored["experiments"])}, sort_keys=True))
    print("REPORT_EVIDENCE_REPLAY_OK")

def main():
    mode = sys.argv[1]
    if mode == "normal":
        normal()
    elif mode == "incident":
        return incident()
    elif mode == "baseline":
        baseline()
    elif mode == "report":
        report()
    elif mode == "demo":
        print(json.dumps({"normal": summary(replay("normal")),
                          "incident": summary(replay("incident"))}, sort_keys=True))
    else:
        raise ValueError("UNKNOWN_MODE")
    return 0

if __name__ == "__main__":
    sys.exit(main())
