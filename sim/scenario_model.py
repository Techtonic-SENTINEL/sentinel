# SENTINEL sim/ · Scenario engine (SimPy)
# Casualty waves with START/SALT triage, deterioration, road closures, hospital failure,
# building collapse, blood shortage. Same logic as Notebook 2, packaged as one function.
import copy
import math
import random
import networkx as nx
import simpy

TIERS = {
    "large":  {"beds": 30, "icu": 8, "ot_per_hr": 2, "surge_pct": 20, "blood_units": 40,
               "specialties": ["general", "trauma", "burns", "dialysis", "paediatric"]},
    "medium": {"beds": 15, "icu": 4, "ot_per_hr": 1, "surge_pct": 20, "blood_units": 16,
               "specialties": ["general", "trauma", "paediatric"]},
    "small":  {"beds": 6, "icu": 0, "ot_per_hr": 0, "surge_pct": 10, "blood_units": 0,
               "specialties": ["general"]},
}
BLOOD_MIX = {"O+": 0.36, "B+": 0.31, "A+": 0.22, "AB+": 0.06, "O-": 0.02, "B-": 0.015, "A-": 0.01, "AB-": 0.005}
BIG_WORDS = ["municipal", "general", "tilak", "bhabha", "medical college", "government", "civil"]
TRIAGE_FLOOD = {"RED": 0.15, "YELLOW": 0.25, "GREEN": 0.55, "GREY": 0.05}
TRIAGE_COLLAPSE = {"RED": 0.30, "YELLOW": 0.40, "GREEN": 0.25, "GREY": 0.05}
SPEC_FLOOD = {"general": 0.45, "trauma": 0.30, "paediatric": 0.15, "burns": 0.05, "dialysis": 0.05}
SPEC_COLLAPSE = {"trauma": 0.50, "dialysis": 0.25, "general": 0.15, "paediatric": 0.10}
WAVES_FLOOD = [(0, 0.30, "Field team", 0.9), (20, 0.30, "108/112 calls", 0.7),
               (45, 0.25, "Rescue boats", 0.85), (90, 0.15, "Door-to-door search", 0.9)]
WAVES_COLLAPSE = [(75, 0.50, "Field team", 0.9), (95, 0.50, "Rescue team", 0.85)]
DONATIONS = [
    {"id": "DN-01", "donor": "CSR donor A", "amount": 1500000, "earmark": "treatment"},
    {"id": "DN-02", "donor": "NGO B", "amount": 800000, "earmark": "blood"},
    {"id": "DN-03", "donor": "Citizens UPI pool", "amount": 600000, "earmark": "transport"},
    {"id": "DN-04", "donor": "CSR donor C", "amount": 1200000, "earmark": "treatment"},
    {"id": "DN-05", "donor": "State relief fund", "amount": 900000, "earmark": "any"},
]
UNIT_COSTS = {"treatment": {"RED": 60000, "YELLOW": 20000, "GREEN": 3000, "GREY": 10000},
              "blood_per_unit": 1500, "transport_per_km": {"ALS": 60, "BLS": 30}}


def _dist(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def make_ambulances(bases, n_als, n_bls):
    out = []
    for i, kind in enumerate(["ALS"] * n_als + ["BLS"] * n_bls):
        b = bases[i % len(bases)]
        out.append({"id": f"A{i + 1:02d}", "type": kind, "base": b["id"], "node": b["node"],
                    "lat": b["lat"], "lon": b["lon"], "seats": 1 if kind == "ALS" else 2,
                    "cost_per_km": 60 if kind == "ALS" else 30})
    return out


def generate(G, base_hospitals, area, seed=42, horizon=120, n_als=5, n_bls=9, n_bases=5,
             n_flood=135, n_collapse=25):
    rng = random.Random(seed)
    center, flood_center = tuple(area["center"]), tuple(area["flood_center"])
    hospitals = copy.deepcopy(base_hospitals)

    def node_ll(n):
        return G.nodes[n]["y"], G.nodes[n]["x"]

    def exact_labels(n, mix):
        labels = []
        for key, share in mix.items():
            labels += [key] * round(n * share)
        biggest = max(mix, key=mix.get)
        while len(labels) < n:
            labels.append(biggest)
        labels = labels[:n]
        rng.shuffle(labels)
        return labels

    def pick(mix):
        return rng.choices(list(mix), weights=list(mix.values()))[0]

    # Zones
    off = 0.016
    zones = [
        {"id": "Z1", "name": "Flood core (Mithi River)", "lat": flood_center[0], "lon": flood_center[1], "weight": 0.40},
        {"id": "Z2", "name": "North sector", "lat": center[0] + off, "lon": center[1], "weight": 0.15},
        {"id": "Z3", "name": "East sector", "lat": center[0], "lon": center[1] + off, "weight": 0.15},
        {"id": "Z4", "name": "South sector", "lat": center[0] - off, "lon": center[1], "weight": 0.15},
        {"id": "Z5", "name": "South-west sector", "lat": center[0] - 0.012, "lon": center[1] - 0.014, "weight": 0.15},
    ]
    zone_radius = 700
    zone_by_id = {z["id"]: z for z in zones}
    all_nodes = list(G.nodes)
    for z in zones:
        dist = {n: _dist(z["lat"], z["lon"], *node_ll(n)) for n in all_nodes}
        inside = [n for n in all_nodes if dist[n] <= zone_radius]
        z["nodes"] = inside if len(inside) >= 20 else sorted(all_nodes, key=dist.get)[:40]
        z["center_node"] = min(all_nodes, key=dist.get)

    # Hospital capacities (synthetic)
    for h in hospitals:
        h["tier"] = "large" if any(w in h["name"].lower() for w in BIG_WORDS) else (
            "medium" if rng.random() < 0.45 else "small")
    if not any(h["tier"] == "large" for h in hospitals):
        hospitals[0]["tier"] = "large"
    for h in hospitals:
        t = TIERS[h["tier"]]
        h.update({"beds": t["beds"], "icu": t["icu"], "ot_per_hr": t["ot_per_hr"], "surge_pct": t["surge_pct"],
                  "specialties": t["specialties"],
                  "blood": {g: round(t["blood_units"] * s) for g, s in BLOOD_MIX.items()},
                  "capacity_note": "synthetic"})

    # Ambulance bases outside the flood
    safe = [n for n in all_nodes if _dist(*flood_center, *node_ll(n)) > area["flood_radius_m"] + 300]
    base_nodes = rng.sample(safe, n_bases)
    bases = [{"id": f"B{i + 1}", "node": int(b), "lat": node_ll(b)[0], "lon": node_ll(b)[1]} for i, b in enumerate(base_nodes)]
    ambulances = make_ambulances(bases, n_als, n_bls)

    # Casualties
    def make(n, first_id, injured_at, zone_mix, triage_mix, spec_mix, waves):
        triage = exact_labels(n, triage_mix)
        zone_ids = exact_labels(n, zone_mix)
        wave_ids = exact_labels(n, {i: w[1] for i, w in enumerate(waves)})
        people = []
        for i in range(n):
            z = zone_by_id[zone_ids[i]]
            node = rng.choice(z["nodes"])
            lat, lon = node_ll(node)
            colour, spec = triage[i], pick(spec_mix)
            report_t, _, source, confidence = waves[wave_ids[i]]
            people.append({
                "id": f"P{first_id + i:03d}", "zone": z["id"], "node": int(node), "lat": lat, "lon": lon,
                "triage": colour, "specialty": spec, "age_group": "child" if spec == "paediatric" else "adult",
                "blood_group": pick(BLOOD_MIX),
                "blood_units": rng.randint(2, 4) if colour == "RED" else (rng.randint(0, 1) if colour == "YELLOW" else 0),
                "needs_als": colour == "RED", "injured_at": injured_at, "reported_at": report_t,
                "source": source, "confidence": confidence,
                "deteriorate_at": injured_at + rng.randint(60, 150) if colour == "YELLOW" else None,
                "cause": "flood" if injured_at == 0 else "collapse"})
        return people

    casualties = (make(n_flood, 1, 0, {z["id"]: z["weight"] for z in zones}, TRIAGE_FLOOD, SPEC_FLOOD, WAVES_FLOOD)
                  + make(n_collapse, n_flood + 1, 75, {"Z3": 1.0}, TRIAGE_COLLAPSE, SPEC_COLLAPSE, WAVES_COLLAPSE))

    # Events: the critical road is the one used by the most fastest routes
    use, hosp_nodes = {}, {h["node"] for h in hospitals}
    for s in base_nodes + [z["center_node"] for z in zones]:
        _, paths = nx.single_source_dijkstra(G, s, weight="travel_time")
        for hn in hosp_nodes:
            p = paths.get(hn)
            if not p:
                continue
            for u, v in zip(p[:-1], p[1:]):
                if u in hosp_nodes or v in hosp_nodes:
                    continue
                key = (min(u, v), max(u, v))
                use[key] = use.get(key, 0) + 1
    road = max(use, key=use.get)
    big = [h for h in hospitals if h["tier"] == "large"]
    failing = min(big, key=lambda h: _dist(h["lat"], h["lon"], *flood_center))
    blood_h = max(hospitals, key=lambda h: sum(h["blood"].values()))
    fr = area["flood_radius_m"]
    events = [
        {"t": 0, "type": "flood", "factor": area["flood_factor"], "radius_m": fr,
         "text": f"Flood begins: roads within {fr} m of the Mithi River are {area['flood_factor']:.0f}× slower"},
        {"t": 35, "type": "road_closed", "u": int(road[0]), "v": int(road[1]),
         "text": f"Critical road closed (used by {use[road]} fastest routes)"},
        {"t": 50, "type": "hospital_down", "hospital": failing["id"], "text": f"{failing['id']} power failure: ICU lost, beds halved"},
        {"t": 60, "type": "flood", "factor": 5.0, "radius_m": fr + 200, "text": f"Flood worsens: {fr + 200} m radius, roads 5× slower"},
        {"t": 75, "type": "collapse", "zone": "Z3", "text": "Building collapse in Zone 3: 25 new casualties"},
        {"t": 80, "type": "blood_shortage", "hospital": blood_h["id"], "group": "O+", "text": f"{blood_h['id']} runs out of O+ blood"},
    ]

    # SimPy run with no response: reports, deterioration, events
    env = simpy.Environment()
    timeline, snapshots, known = [], [], set()
    state = {c["id"]: c["triage"] for c in casualties}

    def report_wave(env, t, source, ids):
        yield env.timeout(t)
        known.update(ids)
        timeline.append((env.now, f"{len(ids)} casualties reported by {source}"))

    def deterioration(env, c):
        yield env.timeout(c["deteriorate_at"])
        if state[c["id"]] == "YELLOW":
            state[c["id"]] = "RED"

    def disaster_events(env):
        for ev in sorted(events, key=lambda e: e["t"]):
            yield env.timeout(ev["t"] - env.now)
            timeline.append((env.now, ev["text"]))

    def snapshot(env):
        yield env.timeout(0.01)
        while True:
            exists = [c for c in casualties if c["injured_at"] <= env.now]
            snapshots.append({"minute": round(env.now), "true casualties": len(exists),
                              "known to control room": len(known),
                              "Red now": sum(state[c["id"]] == "RED" for c in exists)})
            yield env.timeout(5)

    waves = {}
    for c in casualties:
        waves.setdefault((c["reported_at"], c["source"]), []).append(c["id"])
    for (t, source), ids in sorted(waves.items()):
        env.process(report_wave(env, t, source, ids))
    for c in casualties:
        if c["deteriorate_at"] is not None:
            env.process(deterioration(env, c))
    env.process(disaster_events(env))
    env.process(snapshot(env))
    env.run(until=horizon + 1)
    timeline.sort(key=lambda x: x[0])

    return {"seed": seed, "horizon_min": horizon,
            "zones": [{k: v for k, v in z.items() if k != "nodes"} for z in zones], "zone_radius_m": zone_radius,
            "hospitals": hospitals, "ambulances": ambulances, "bases": bases, "casualties": casualties,
            "events": events, "donations": copy.deepcopy(DONATIONS), "unit_costs": copy.deepcopy(UNIT_COSTS),
            "no_response_snapshots": snapshots, "timeline": timeline}
