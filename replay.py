import json
import sys
from pathlib import Path
from collector import Collector

ROOT = Path(__file__).resolve().parent

def load():
    return json.loads((ROOT / "data" / "captures.json").read_text())

def replay(case, order=None, capture=None):
    data = load() if capture is None else capture
    spec = data["cases"][case]
    events = {event["id"]: event for event in spec["events"]}
    order = spec["observed"] if order is None else order
    if len(order) != len(events) or set(order) != set(events):
        raise ValueError("SCHEDULE_EVENT_SET")
    runner, done = Collector(data["entities"]), set()
    try:
        for name in order:
            event = events[name]
            if not set(event.get("after", [])).issubset(done):
                raise ValueError("SCHEDULE_CAUSALITY:" + name)
            runner.step(event)
            done.add(name)
        return runner.result()
    finally:
        runner.cache.close()

def signature(result):
    return [{key: row[key] for key in
             (("id", "status", "text", "owners") if row["status"] == "published"
              else ("id", "status", "reason"))}
            for row in result["publications"]]

def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    case = args[0]
    order = json.loads(Path(args[1]).read_text()) if len(args) == 2 else None
    result = replay(case, order)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["bad"]:
        print("ARCHIVE_ENTITY_MISMATCH", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
