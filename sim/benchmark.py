# SENTINEL sim/ · Benchmark: the same three policies across many SimPy-generated disasters
import statistics
import time

import demand_model as dm
import optimizer_model as om
import scenario_model as sm

POLICIES = ["sentinel", "nearest", "fcfs"]


def run_one(G, hospitals, area, seed, time_limit=1.0):
    S = sm.generate(G, hospitals, area, seed=seed)
    row = {"seed": seed}
    for p in POLICIES:
        world, _ = om.simulate(p, S, G, tuple(area["flood_center"]), dm, time_limit=time_limit)
        m = om.metrics(world, S)
        row[p] = {"survivors": m["Expected survivors (Red+Yellow)"], "red_minutes": m["Red: avg minutes to care"],
                  "load": m["Busiest hospital load %"], "transfers": m["Needed transfer (wrong hospital)"],
                  "overfull": m["Arrived at over-full hospital"], "reached": m["Reached within 2 h"]}
    return row


def summarize(runs):
    out = {"n_runs": len(runs), "seeds": [r["seed"] for r in runs], "runs": runs, "summary": {}}
    for p in POLICIES:
        sv = [r[p]["survivors"] for r in runs]
        out["summary"][p] = {
            "survivors_mean": round(statistics.mean(sv), 1), "survivors_min": round(min(sv), 1), "survivors_max": round(max(sv), 1),
            "red_minutes_mean": round(statistics.mean(r[p]["red_minutes"] for r in runs), 1),
            "load_mean": round(statistics.mean(r[p]["load"] for r in runs)),
            "transfers_mean": round(statistics.mean(r[p]["transfers"] for r in runs), 1)}
    s = out["summary"]
    for other in ["nearest", "fcfs"]:
        out[f"wins_vs_{other}"] = sum(r["sentinel"]["survivors"] > r[other]["survivors"] for r in runs)
        out[f"gain_vs_{other}_pct"] = round(100 * (s["sentinel"]["survivors_mean"] - s[other]["survivors_mean"]) / s[other]["survivors_mean"], 1)
        out[f"red_faster_vs_{other}_pct"] = round(100 * (s[other]["red_minutes_mean"] - s["sentinel"]["red_minutes_mean"]) / s[other]["red_minutes_mean"], 1)
    return out


def run(G, hospitals, area, seeds, done=None, time_limit=1.0, on_progress=print, save=None):
    runs = list(done or [])
    have = {r["seed"] for r in runs}
    for i, seed in enumerate(seeds, start=1):
        if seed in have:
            continue
        t0 = time.time()
        row = run_one(G, hospitals, area, seed, time_limit)
        runs.append(row)
        if save:
            save(runs)
        on_progress(f"  disaster {i:>2}/{len(seeds)} (seed {seed}): SENTINEL {row['sentinel']['survivors']} · "
                    f"nearest {row['nearest']['survivors']} · FCFS {row['fcfs']['survivors']} expected survivors "
                    f"({time.time() - t0:.0f} s)")
    runs.sort(key=lambda r: r["seed"])
    return summarize(runs)
