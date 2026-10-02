# SENTINEL optimizer/ · Allocation Model (MIP / CP-SAT)
#   max Σ x_pha · S_c(t + τ_pha) − λ · overload − μ · unserved Red
#   subject to: bed/ICU/OT capacity minus reserve, ALS for Red, specialty fit,
#   reachable route, blood-group stock, fund earmarks, zone fairness
import math
import time
from ortools.sat.python import cp_model
from routing_model import TravelTimes, live_graph, road_state_key

# ---------- Survival curves S_c(t) = s0 · e^(−t/τ)  (shape after Sacco / ReSTART; parameters assumed) ----------
SURVIVAL = {"RED": (0.90, 90), "YELLOW": (0.97, 400), "GREEN": (0.995, 4000), "GREY": (0.10, 60)}

# ---------- Model settings ----------
LOAD_MIN, HANDOVER_MIN = 5, 10           # minutes to load a patient / hand over at hospital
DELAY_MIN = {"no_specialty": 60, "over_surge": 60, "icu_full": 60, "no_blood": 30}   # outcome model (assumed)
LAMBDA_OVERLOAD = 150                    # λ: penalty per patient above a hospital's normal beds
MU_UNSERVED_RED = 300                    # μ: penalty per Red patient left waiting
FAIRNESS_EPS, FAIRNESS_WEIGHT = 0.3, 3   # zone fairness: served share within 30% of the average
KM_PER_MIN = 0.4                         # ~24 km/h average, for transport cost
TIME_LIMIT_S = 3.0                       # CP-SAT time limit per re-plan
MAX_AMB, MAX_HOSP = 4, 6                 # candidate ambulances / hospitals kept per patient

COMPATIBLE = {"O-": ["O-"], "O+": ["O+", "O-"], "A-": ["A-", "O-"], "A+": ["A+", "A-", "O+", "O-"],
              "B-": ["B-", "O-"], "B+": ["B+", "B-", "O+", "O-"], "AB-": ["AB-", "A-", "B-", "O-"],
              "AB+": ["AB+", "AB-", "A+", "A-", "B+", "B-", "O+", "O-"]}


def survival(colour, minutes):
    s0, tau = SURVIVAL[colour]
    return s0 * math.exp(-max(minutes, 0) / tau)


def survival_at_care(p, care_minute):
    # Survival probability if definitive care starts at care_minute (Yellow decays like Red once it deteriorates)
    inj, d = p["injured_at"], p.get("deteriorate_at")
    if p["triage"] == "YELLOW" and d is not None and care_minute > d:
        return survival("YELLOW", d - inj) * math.exp(-(care_minute - d) / SURVIVAL["RED"][1])
    return survival(p["triage"], care_minute - inj)


def colour_now(p, minute):
    d = p.get("deteriorate_at")
    if p["triage"] == "YELLOW" and d is not None and minute >= d:
        return "RED"
    return p["triage"]


def needs_icu(p):
    # Half of Red patients need ICU (w_ICU = 0.5 in the demand model)
    return p["triage"] == "RED" and int(p["id"][1:]) % 2 == 0


# ---------- The world: hospitals, ambulances, funds as they change ----------
class World:
    def __init__(self, S):
        self.S = S
        self.hosp = {h["id"]: {"id": h["id"], "name": h["name"], "node": h["node"], "lat": h["lat"], "lon": h["lon"],
                               "beds": h["beds"], "icu": h["icu"], "surge": h["surge_pct"], "ot": h["ot_per_hr"],
                               "spec": set(h["specialties"]), "blood": dict(h["blood"]),
                               "beds_used": 0, "icu_used": 0, "red_arrivals": []} for h in S["hospitals"]}
        self.amb = {a["id"]: {**a, "free_at": 0, "at": a["node"]} for a in S["ambulances"]}
        self.funds = {"treatment": 0, "blood": 0, "transport": 0, "any": 0}
        for d in S["donations"]:
            self.funds[d["earmark"]] += d["amount"]
        self.spent = {"treatment": 0, "blood": 0, "transport": 0, "any": 0}
        self.unfunded = 0
        self.applied, self.dispatched = set(), {}

    def apply_events(self, minute):
        for i, e in enumerate(self.S["events"]):
            if i in self.applied or e["t"] > minute:
                continue
            if e["type"] == "hospital_down":
                h = self.hosp[e["hospital"]]
                h["beds"], h["icu"] = h["beds"] // 2, 0
            elif e["type"] == "blood_shortage":
                self.hosp[e["hospital"]]["blood"][e["group"]] = 0
            self.applied.add(i)

    def bed_cap(self, h):
        return int(h["beds"] * (1 + h["surge"] / 100))

    def free_beds(self, h):
        return max(0, self.bed_cap(h) - h["beds_used"])

    def free_icu(self, h):
        return max(0, h["icu"] - h["icu_used"])

    def blood_for(self, h, group):
        return sum(h["blood"].get(g, 0) for g in COMPATIBLE[group])

    def take_blood(self, h, group, units):
        for g in COMPATIBLE[group]:
            take = min(units, h["blood"].get(g, 0))
            h["blood"][g] -= take
            units -= take

    def ot_queue(self, h, minute):
        # Expected wait for an operating theatre: Red arrivals in the last hour ÷ theatre throughput
        if h["ot"] <= 0:
            return 120
        recent = [t for t in h["red_arrivals"] if minute - 60 <= t <= minute]
        return min(120.0, 30.0 * len(recent) / h["ot"])

    def funds_left(self):
        left = {k: self.funds[k] - self.spent[k] for k in ["treatment", "blood", "transport"]}
        left["any"] = self.funds["any"] - self.spent["any"]
        return left

    def spend(self, category, amount):
        own = max(0, self.funds[category] - self.spent[category])
        from_own = min(own, amount)
        self.spent[category] += from_own
        rest = amount - from_own
        from_any = min(max(0, self.funds["any"] - self.spent["any"]), rest)
        self.spent["any"] += from_any
        self.unfunded += rest - from_any


def waiting_patients(world, minute):
    # Known Red/Yellow patients not yet picked up (Green walk or go by bus; Grey get care on site)
    return [p for p in world.S["casualties"]
            if p["injured_at"] <= minute and p["reported_at"] <= minute
            and p["id"] not in world.dispatched and colour_now(p, minute) in ("RED", "YELLOW")]


def make_option(world, p, h, a, minute, t_ap, t_ph):
    col = colour_now(p, minute)
    arrival = minute + t_ap + LOAD_MIN + t_ph
    q = world.ot_queue(h, arrival) if col == "RED" else 0
    s = survival_at_care(p, arrival + q)
    km = (t_ap + t_ph) * KM_PER_MIN
    costs = world.S["unit_costs"]
    return {"p": p, "h": h, "a": a, "colour": col, "t_ap": t_ap, "t_ph": t_ph, "arrival": arrival, "queue": q,
            "survival": s, "score": int(round(1000 * s)),
            "cost_treatment": int(costs["treatment"][col]),
            "cost_blood": int(costs["blood_per_unit"] * p["blood_units"]),
            "cost_transport": int(round(costs["transport_per_km"][a["type"]] * km))}


def build_options(world, tt, waiting, idle, minute):
    # Feasible (patient, hospital, ambulance) triples: ALS for Red, specialty fit, OT/ICU, reachable route
    opts = []
    for p in waiting:
        col = colour_now(p, minute)
        ambs = []
        for a in idle:
            if col == "RED" and a["type"] != "ALS":
                continue
            t = tt.minutes(a["at"], p["node"])
            if t is not None:
                ambs.append((t, a))
        ambs = sorted(ambs, key=lambda x: x[0])[:MAX_AMB]
        hosps = []
        for h in world.hosp.values():
            if p["specialty"] not in h["spec"]:
                continue
            if col == "RED" and h["ot"] <= 0:
                continue
            if needs_icu(p) and h["icu"] <= 0:
                continue
            t = tt.minutes(p["node"], h["node"])
            if t is not None:
                hosps.append((t, h))
        hosps = sorted(hosps, key=lambda x: x[0])[:MAX_HOSP]
        for t_ap, a in ambs:
            for t_ph, h in hosps:
                opts.append(make_option(world, p, h, a, minute, t_ap, t_ph))
    return opts


def reserve_by_hospital(world, dm, minute, zones, starts):
    # "Capacity minus reserve": hold back beds for people not yet reported (from the demand model's buffer U)
    r = dm.unreported_reserve(world.S["casualties"], minute, zones, starts, frozenset(world.dispatched))
    free = {hid: world.free_beds(h) for hid, h in world.hosp.items()}
    total = sum(free.values())
    return {hid: (int(round(r["beds"] * f / total)) if total else 0) for hid, f in free.items()}, r


def _fits(world, o, taken, reserve, spend_tmp):
    p, h, hid = o["p"], o["h"], o["h"]["id"]
    if o["a"]["id"] in taken["amb"]:
        return False
    beds = taken["beds"].get(hid, 0) + 1
    if beds > world.free_beds(h):
        return False
    if o["colour"] != "RED" and beds > world.free_beds(h) - reserve.get(hid, 0):
        return False
    if needs_icu(p) and taken["icu"].get(hid, 0) + 1 > world.free_icu(h):
        return False
    u = p["blood_units"]
    if u and (taken["blood"].get((hid, p["blood_group"]), 0) + u > world.blood_for(h, p["blood_group"])
              or taken["blood_total"].get(hid, 0) + u > sum(h["blood"].values())):
        return False
    left = world.funds_left()
    extra = 0
    for cat, key in [("treatment", "cost_treatment"), ("blood", "cost_blood"), ("transport", "cost_transport")]:
        extra += max(0, spend_tmp[cat] + o[key] - max(left[cat], 0))
    return extra <= max(left["any"], 0)


def greedy_plan(world, opts, waiting, minute, reserve):
    # Fallback: Red first, then by report time; each takes its best feasible option
    by_p = {}
    for i, o in enumerate(opts):
        by_p.setdefault(o["p"]["id"], []).append(i)
    order = sorted(waiting, key=lambda p: (colour_now(p, minute) != "RED", p["reported_at"], p["id"]))
    taken = {"amb": set(), "beds": {}, "icu": {}, "blood": {}, "blood_total": {}}
    spend_tmp = {"treatment": 0, "blood": 0, "transport": 0}
    chosen = []
    for p in order:
        best = None
        for i in by_p.get(p["id"], []):
            o = opts[i]
            if not _fits(world, o, taken, reserve, spend_tmp):
                continue
            h = o["h"]
            over = 1 if h["beds_used"] + taken["beds"].get(h["id"], 0) + 1 > h["beds"] else 0
            val = o["score"] - LAMBDA_OVERLOAD * over
            if best is None or val > best[0]:
                best = (val, i)
        if best:
            o, hid = opts[best[1]], opts[best[1]]["h"]["id"]
            taken["amb"].add(o["a"]["id"])
            taken["beds"][hid] = taken["beds"].get(hid, 0) + 1
            if needs_icu(p):
                taken["icu"][hid] = taken["icu"].get(hid, 0) + 1
            if p["blood_units"]:
                k = (hid, p["blood_group"])
                taken["blood"][k] = taken["blood"].get(k, 0) + p["blood_units"]
                taken["blood_total"][hid] = taken["blood_total"].get(hid, 0) + p["blood_units"]
            for cat, key in [("treatment", "cost_treatment"), ("blood", "cost_blood"), ("transport", "cost_transport")]:
                spend_tmp[cat] += o[key]
            chosen.append(best[1])
    return chosen


def cpsat_plan(world, opts, waiting, minute, reserve, hint=None, time_limit=TIME_LIMIT_S):
    # The CP-SAT model: every waiting patient, hospital and ambulance decided together
    if not opts:
        return [], "NO OPTIONS", 0.0
    model = cp_model.CpModel()
    x = [model.new_bool_var(f"x{i}") for i in range(len(opts))]
    pids = [p["id"] for p in waiting]
    y = {pid: model.new_bool_var(f"wait_{pid}") for pid in pids}
    by_p, by_a, by_h = {}, {}, {}
    for i, o in enumerate(opts):
        by_p.setdefault(o["p"]["id"], []).append(i)
        by_a.setdefault(o["a"]["id"], []).append(i)
        by_h.setdefault(o["h"]["id"], []).append(i)

    for pid in pids:                                        # each patient: one trip or waits
        model.add(sum(x[i] for i in by_p.get(pid, [])) + y[pid] == 1)
    for idx in by_a.values():                               # each ambulance: one trip
        model.add(sum(x[i] for i in idx) <= 1)
    overload = []
    for hid, idx in by_h.items():
        h = world.hosp[hid]
        model.add(sum(x[i] for i in idx) <= world.free_beds(h))                     # bed capacity (incl. surge)
        yellow = [i for i in idx if opts[i]["colour"] != "RED"]
        if yellow:                                                                   # minus reserve for unreported
            model.add(sum(x[i] for i in yellow) <= max(0, world.free_beds(h) - reserve.get(hid, 0)))
        icu = [i for i in idx if needs_icu(opts[i]["p"])]
        if icu:                                                                      # ICU capacity
            model.add(sum(x[i] for i in icu) <= world.free_icu(h))
        groups = {}
        for i in idx:
            if opts[i]["p"]["blood_units"]:
                groups.setdefault(opts[i]["p"]["blood_group"], []).append(i)
        for g, gi in groups.items():                                                 # blood-group stock
            model.add(sum(x[i] * opts[i]["p"]["blood_units"] for i in gi) <= world.blood_for(h, g))
        if groups:
            allb = [i for gi in groups.values() for i in gi]
            model.add(sum(x[i] * opts[i]["p"]["blood_units"] for i in allb) <= sum(h["blood"].values()))
        o_h = model.new_int_var(0, 10000, f"overload_{hid}")                          # λ · overload
        model.add(o_h >= h["beds_used"] + sum(x[i] for i in idx) - h["beds"])
        overload.append(o_h)

    left = world.funds_left()                                                       # fund earmarks
    any_left = max(left["any"], 0)
    pools = []
    for cat, key in [("treatment", "cost_treatment"), ("blood", "cost_blood"), ("transport", "cost_transport")]:
        pool = model.new_int_var(0, any_left, f"any_{cat}")
        model.add(sum(x[i] * opts[i][key] for i in range(len(opts))) <= max(left[cat], 0) + pool)
        pools.append(pool)
    model.add(sum(pools) <= any_left)

    shortfall = []                                                                  # zone fairness
    zones = {}
    for p in waiting:
        zones.setdefault(p["zone"], []).append(p["id"])
    n = len(pids)
    if len(zones) > 1:
        served_total = n - sum(y.values())
        for z, members in zones.items():
            nz = len(members)
            served_z = nz - sum(y[m] for m in members)
            s = model.new_int_var(0, n * nz + 1, f"short_{z}")
            model.add(n * served_z - nz * served_total + math.ceil(FAIRNESS_EPS * n * nz) + s >= 0)
            shortfall.append(s)

    reds = [y[p["id"]] for p in waiting if colour_now(p, minute) == "RED"]
    model.maximize(sum(opts[i]["score"] * x[i] for i in range(len(opts)))
                   - LAMBDA_OVERLOAD * sum(overload) - MU_UNSERVED_RED * sum(reds)
                   - FAIRNESS_WEIGHT * sum(shortfall))

    if hint is not None:                                                            # warm start
        hs = set(hint)
        for i in range(len(opts)):
            model.add_hint(x[i], 1 if i in hs else 0)
        served = {opts[i]["p"]["id"] for i in hs}
        for pid in pids:
            model.add_hint(y[pid], 0 if pid in served else 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 4
    t0 = time.time()
    status = solver.solve(model)
    ms = (time.time() - t0) * 1000
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return [i for i in range(len(opts)) if solver.value(x[i])], solver.status_name(status), ms
    return None, solver.status_name(status), ms


def nearest_plan(world, tt, waiting, idle, minute):
    # Baseline 1: Red first; each goes to the NEAREST hospital, ignoring capacity and specialty
    order = sorted(waiting, key=lambda p: (colour_now(p, minute) != "RED", p["reported_at"], p["id"]))
    free, out = list(idle), []
    for p in order:
        col = colour_now(p, minute)
        hs = [(tt.minutes(p["node"], h["node"]), h) for h in world.hosp.values()]
        hs = [x for x in hs if x[0] is not None]
        ams = [(tt.minutes(a["at"], p["node"]), a) for a in free if col != "RED" or a["type"] == "ALS"]
        ams = [x for x in ams if x[0] is not None]
        if not hs or not ams:
            continue
        t_ph, h = min(hs, key=lambda x: x[0])
        t_ap, a = min(ams, key=lambda x: x[0])
        out.append(make_option(world, p, h, a, minute, t_ap, t_ph))
        free.remove(a)
    return out


def fcfs_plan(world, tt, waiting, idle, minute):
    # Baseline 2: first-come-first-served by report time; nearest hospital that still has a normal bed
    order = sorted(waiting, key=lambda p: (p["reported_at"], p["id"]))
    free, out, planned = list(idle), [], {}
    for p in order:
        col = colour_now(p, minute)
        hs = [(tt.minutes(p["node"], h["node"]), h) for h in world.hosp.values()]
        hs = sorted([x for x in hs if x[0] is not None], key=lambda x: x[0])
        ams = [(tt.minutes(a["at"], p["node"]), a) for a in free if col != "RED" or a["type"] == "ALS"]
        ams = [x for x in ams if x[0] is not None]
        if not hs or not ams:
            continue
        with_bed = [x for x in hs if x[1]["beds_used"] + planned.get(x[1]["id"], 0) < x[1]["beds"]]
        t_ph, h = (with_bed or hs)[0]
        t_ap, a = min(ams, key=lambda x: x[0])
        out.append(make_option(world, p, h, a, minute, t_ap, t_ph))
        planned[h["id"]] = planned.get(h["id"], 0) + 1
        free.remove(a)
    return out


def why_this_hospital(world, tt, o, reserve, minute):
    # "Why this hospital?" card: every hospital compared for this patient, with the reason it was or wasn't chosen
    p, a, rows = o["p"], o["a"], []
    for h in world.hosp.values():
        t_ph = tt.minutes(p["node"], h["node"])
        reason, surv = None, None
        if p["specialty"] not in h["spec"]:
            reason = f"no {p['specialty']} service"
        elif o["colour"] == "RED" and h["ot"] <= 0:
            reason = "no operating theatre"
        elif needs_icu(p) and h["icu"] <= 0:
            reason = "no ICU (or ICU lost)"
        elif t_ph is None:
            reason = "road cut off"
        else:
            arr = minute + o["t_ap"] + LOAD_MIN + t_ph
            q = world.ot_queue(h, arr) if o["colour"] == "RED" else 0
            surv = survival_at_care(p, arr + q)
            if h["id"] == o["h"]["id"]:
                reason = "CHOSEN"
            elif world.free_beds(h) <= 0:
                reason = "full"
            elif o["colour"] != "RED" and world.free_beds(h) - reserve.get(h["id"], 0) <= 0:
                reason = "beds held for unreported Red patients"
            elif needs_icu(p) and world.free_icu(h) <= 0:
                reason = "ICU full"
            elif p["blood_units"] and world.blood_for(h, p["blood_group"]) < p["blood_units"]:
                reason = f"not enough {p['blood_group']}-compatible blood"
            else:
                reason = "lower survival for the whole system when all patients are planned together"
        rows.append({"hospital": h["id"], "name": h["name"], "minutes": None if t_ph is None else round(t_ph, 1),
                     "survival": None if surv is None else round(surv, 3), "reason": reason})
    rows.sort(key=lambda r: (r["reason"] != "CHOSEN", -(r["survival"] or 0)))
    return rows


def dispatch(world, o, minute, policy, why=None):
    # Send the ambulance and score the outcome with the SAME rules for every policy
    p, h, a = o["p"], o["h"], o["a"]
    delay, problems = 0, []
    if p["specialty"] not in h["spec"]:
        delay += DELAY_MIN["no_specialty"]; problems.append("no specialty: transfer needed")
    if h["beds_used"] + 1 > world.bed_cap(h):
        delay += DELAY_MIN["over_surge"]; problems.append("hospital over surge capacity")
    if needs_icu(p):
        if h["icu_used"] + 1 > h["icu"]:
            delay += DELAY_MIN["icu_full"]; problems.append("ICU full")
        else:
            h["icu_used"] += 1
    if p["blood_units"]:
        if world.blood_for(h, p["blood_group"]) < p["blood_units"]:
            delay += DELAY_MIN["no_blood"]; problems.append("blood not in stock")
        else:
            world.take_blood(h, p["blood_group"], p["blood_units"])
    col = colour_now(p, o["arrival"])
    q = world.ot_queue(h, o["arrival"]) if col == "RED" else 0
    if col == "RED":
        h["red_arrivals"].append(o["arrival"])
    h["beds_used"] += 1
    care = o["arrival"] + q + delay
    s = survival_at_care(p, care)
    a["free_at"], a["at"] = o["arrival"] + HANDOVER_MIN, h["node"]
    world.spend("treatment", o["cost_treatment"])
    world.spend("blood", o["cost_blood"])
    world.spend("transport", o["cost_transport"])
    rec = {"policy": policy, "patient": p["id"], "zone": p["zone"], "triage": p["triage"], "colour_at_dispatch": o["colour"],
           "specialty": p["specialty"], "hospital": h["id"], "ambulance": a["id"], "ambulance_type": a["type"],
           "dispatch_min": minute, "arrival_min": round(o["arrival"], 1), "care_min": round(care, 1),
           "survival": round(s, 4), "problems": problems,
           "costs": {"treatment": o["cost_treatment"], "blood": o["cost_blood"], "transport": o["cost_transport"]}}
    if why is not None:
        rec["why"] = why
    world.dispatched[p["id"]] = rec
    return rec


def simulate(policy, S, G, flood_center, dm, epoch_min=5, time_limit=TIME_LIMIT_S):
    # Run the 2-hour disaster with one policy: "sentinel", "nearest" or "fcfs"
    world = World(S)
    zones, starts = S["zones"], dm.incident_starts(S["events"])
    cache, log = {}, []
    for minute in range(0, S["horizon_min"] + 1, epoch_min):
        world.apply_events(minute)
        key = road_state_key(S["events"], minute)
        if key not in cache:                     # travel-time matrix rebuilt on each closure / flood change
            cache[key] = TravelTimes(live_graph(G, S["events"], minute, flood_center))
        tt = cache[key]
        idle = [a for a in world.amb.values() if a["free_at"] <= minute]
        waiting = waiting_patients(world, minute)
        info = {"minute": minute, "waiting": len(waiting), "idle_ambulances": len(idle), "dispatched": 0}
        if waiting and idle:
            if policy == "sentinel":
                reserve, r = reserve_by_hospital(world, dm, minute, zones, starts)
                opts = build_options(world, tt, waiting, idle, minute)
                hint = greedy_plan(world, opts, waiting, minute, reserve)
                chosen, status, ms = cpsat_plan(world, opts, waiting, minute, reserve, hint, time_limit)
                if chosen is None:
                    chosen, status = hint, f"FALLBACK (greedy) after {status}"
                decisions = [opts[i] for i in chosen]
                info.update({"options": len(opts), "solver": status, "solve_ms": round(ms),
                             "reserve_beds": round(r["beds"], 1)})
                whys = [why_this_hospital(world, tt, o, reserve, minute) for o in decisions]
            else:
                decisions = (nearest_plan if policy == "nearest" else fcfs_plan)(world, tt, waiting, idle, minute)
                whys = [None] * len(decisions)
            for o, w in zip(decisions, whys):
                dispatch(world, o, minute, policy, w)
            info["dispatched"] = len(decisions)
        log.append(info)
    return world, log


def metrics(world, S):
    horizon = S["horizon_min"]
    pts = [p for p in S["casualties"] if p["triage"] in ("RED", "YELLOW")]
    surv, red_t, reached, transfers, overfull = 0.0, [], 0, 0, 0
    for p in pts:
        r = world.dispatched.get(p["id"])
        if r:
            surv += r["survival"]; reached += 1
            transfers += "no specialty: transfer needed" in r["problems"]
            overfull += "hospital over surge capacity" in r["problems"]
            care = r["care_min"]
        else:
            care = horizon + 60                   # not reached in 2 h: assume care one hour later
            surv += survival_at_care(p, care)
        if p["triage"] == "RED":
            red_t.append(care - p["injured_at"])
    load = max(h["beds_used"] / max(h["beds"], 1) * 100 for h in world.hosp.values())
    return {"Expected survivors (Red+Yellow)": round(surv, 1),
            "Red: avg minutes to care": round(sum(red_t) / len(red_t), 1),
            "Reached within 2 h": f"{reached}/{len(pts)}",
            "Needed transfer (wrong hospital)": transfers,
            "Arrived at over-full hospital": overfull,
            "Busiest hospital load %": round(load),
            "Spending outside earmarks (₹)": world.unfunded}


def log_override(override_log, minute, patient, from_h, to_h, actor, reason):
    # Human override: allowed, but always recorded (goes to the ledger in Notebook 5)
    entry = {"minute": minute, "patient": patient, "from": from_h, "to": to_h, "actor": actor, "reason": reason}
    override_log.append(entry)
    return entry
