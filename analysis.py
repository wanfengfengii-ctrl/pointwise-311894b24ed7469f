"""Forensic analyzer for the partitioned-fetch archive incident.

analyze(capture, case) computes every distinct joint publication outcome the
case's causal (``after``) graph allows, returning one witness schedule per
distinct replay.signature.  It explores *merged reachable states* of the
production Collector (never permutations of schedules): two partial runs that
leave the same machine state and have completed the same events have identical
futures, so they are explored once.  State is kept at interval granularity
(the SQLite dump of the span table), never expanded per byte of content.
"""
import copy
import json

from collector import Collector
from rangecache import Cache
from replay import signature


def _snapshot(col):
    """Full machine state that determines all future transitions.

    The event log is excluded on purpose: it records history but never
    influences Collector.step or Collector.result.
    """
    return (col.cache.checkpoint(),
            copy.deepcopy(col.jobs),
            copy.deepcopy(col.pending),
            copy.deepcopy(col.publications))


def _restore(entities, snap):
    col = Collector(entities)
    col.cache.close()
    col.cache = Cache(snap[0])
    col.jobs = copy.deepcopy(snap[1])
    col.pending = copy.deepcopy(snap[2])
    col.publications = copy.deepcopy(snap[3])
    return col


def _key(done, snap):
    return json.dumps([sorted(done), snap[0], snap[1], snap[2], snap[3]],
                      sort_keys=True)


def analyze(capture, case):
    """Return one {"order", "signature"} record per distinct joint outcome.

    Breadth-first reachability over the quotient of runs by machine-state
    equivalence.  Collector.step is a deterministic function of the machine
    state and the event, and Portal responses are immutable recordings, so
    merging states that agree on (done set, cache image, jobs, pending,
    publications) preserves the set of reachable final signatures exactly.
    """
    spec = capture["cases"][case]
    events = {event["id"]: event for event in spec["events"]}
    entities = capture["entities"]

    start = Collector(entities)
    start_snap = _snapshot(start)
    start.cache.close()

    seen = {_key(frozenset(), start_snap)}
    frontier = [(frozenset(), start_snap, [])]
    outcomes = {}
    while frontier:
        done, snap, order = frontier.pop(0)
        enabled = sorted(
            eid for eid in events
            if eid not in done
            and set(events[eid].get("after", [])).issubset(done))
        if not enabled:
            if len(done) == len(events):
                col = _restore(entities, snap)
                sig = signature(col.result())
                col.cache.close()
                outcomes.setdefault(
                    json.dumps(sig, sort_keys=True),
                    {"order": order, "signature": sig})
            continue
        for eid in enabled:
            col = _restore(entities, snap)
            col.step(events[eid])
            next_done = done | {eid}
            next_snap = _snapshot(col)
            col.cache.close()
            key = _key(next_done, next_snap)
            if key not in seen:
                seen.add(key)
                frontier.append((next_done, next_snap, order + [eid]))
    return [outcomes[key] for key in sorted(outcomes)]


# ---------------------------------------------------------------------------
# Counterfactual experiments.  Each changes exactly one knob versus the
# observed incident run: response order, reopen boundary, or a single
# identity field of the recording package.  All are executed through the
# unmodified production entry point replay.replay.

def _capture_with(base, **entity_overrides):
    capture = copy.deepcopy(base)
    for name, changes in entity_overrides.items():
        capture["entities"][name].update(changes)
    return capture


def build_experiments(capture):
    spec = capture["cases"]["incident"]
    observed = list(spec["observed"])

    def moved(order, event, after_event):
        order = [eid for eid in order if eid != event]
        order.insert(order.index(after_event) + 1, event)
        return order

    # 1. Response order only: deliver the stale v7 responses (dna, dnb)
    #    after the v7-era publishes but before the v8 open t0 wipes the slot,
    #    and let the current-generation v8 responses (dta, dtb) land before
    #    the checkpoint/publish.  Same responses, same reopen, same identities.
    order1 = observed
    for event, anchor in (("dna", "pn"), ("dnb", "dna"),
                          ("dta", "dth"), ("dtb", "dta")):
        order1 = moved(order1, event, anchor)

    # 2. Reopen boundary only: relocate the checkpoint cp to just before pt
    #    (after dna/dtb), leaving every delivery and publish order intact.
    order2 = moved(observed, "cp", "dtb")

    # 3. Single identity change: give the foreign district a distinct strong
    #    ETag so Cache.attach no longer reuses the slot silently at s0.
    capture3 = _capture_with(capture, south={"etag": "\"v7s\""})

    # 4. Single identity change: give the foreign district a body identical
    #    to north's (same bytes, own principal/URL/ETag, which stays legal
    #    per the entity constraints in CONTRACT.md).
    capture4 = _capture_with(
        capture, south={"body": capture["entities"]["north"]["body"]})

    # 5. Response order only: let the stale v7 responses land before the
    #    v7-era publishes (after dsb), so the foreign spans they displace
    #    leave north byte-perfect but lineage-wrong, and starve pt of
    #    coverage (rejection, not pollution).
    order5 = observed
    for event, anchor in (("dna", "dsb"), ("dnb", "dna")):
        order5 = moved(order5, event, anchor)

    return [
        {"label": "order-stale-before-reset", "case": "incident",
         "order": order1},
        {"label": "reopen-boundary-inert", "case": "incident",
         "order": order2},
        {"label": "identity-foreign-etag", "case": "incident",
         "order": observed, "capture": capture3},
        {"label": "identity-shared-body", "case": "incident",
         "order": observed, "capture": capture4},
        {"label": "order-stale-before-publish", "case": "incident",
         "order": order5},
    ]


def main():
    from replay import load, replay
    capture = load()
    joint = analyze(capture, "incident")
    experiments = []
    for row in build_experiments(capture):
        result = replay(row["case"], row["order"], row.get("capture"))
        experiments.append({**row, "signature": signature(result)})
    evidence = {"joint": joint, "experiments": experiments}
    with open("evidence.json", "w") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=1,
                  sort_keys=True)
        handle.write("\n")
    print(json.dumps({"joint": len(joint), "experiments": len(experiments)},
                     sort_keys=True))


if __name__ == "__main__":
    main()
