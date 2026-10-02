# SENTINEL demand/ · Demand Model:  D_z = M × Σ_c (N_c × w_c) × (1 + U)
import math

TRIAGE = ["RED", "YELLOW", "GREEN", "GREY"]
RESOURCES = ["beds", "icu", "ot", "blood_units", "als_trips", "bls_trips", "dialysis"]

# w_c · resource use per patient of colour c (planning weights, assumed)
W = {
    "beds":        {"RED": 1.0, "YELLOW": 1.0, "GREEN": 0.0, "GREY": 0.0},
    "icu":         {"RED": 0.5, "YELLOW": 0.0, "GREEN": 0.0, "GREY": 0.0},
    "ot":          {"RED": 1.0, "YELLOW": 0.2, "GREEN": 0.0, "GREY": 0.0},
    "blood_units": {"RED": 3.0, "YELLOW": 0.5, "GREEN": 0.0, "GREY": 0.0},
    "als_trips":   {"RED": 1.0, "YELLOW": 0.0, "GREEN": 0.0, "GREY": 0.0},
    "bls_trips":   {"RED": 0.0, "YELLOW": 0.5, "GREEN": 0.1, "GREY": 0.0},
    "dialysis":    {"RED": 1.0, "YELLOW": 0.0, "GREEN": 0.0, "GREY": 0.0},
}

# M · disaster-type multiplier per resource
# collapse dialysis 0.216 = share of crush patients needing dialysis (Kahramanmaraş 2023 study); others assumed
M = {
    "flood":    {"beds": 1.0, "icu": 1.0, "ot": 0.8, "blood_units": 1.0, "als_trips": 1.0, "bls_trips": 1.0, "dialysis": 0.05},
    "collapse": {"beds": 1.0, "icu": 1.2, "ot": 1.5, "blood_units": 1.3, "als_trips": 1.0, "bls_trips": 1.0, "dialysis": 0.216},
}

# U · buffer for unverified / unreported casualties
C0 = 0.27      # share of casualties known at the start (first counts run 3-4x low)
T_COVER = 40   # minutes for reports to catch up (assumed)
KAPPA = 0.2    # extra buffer for low-confidence sources (assumed)

def coverage(minutes_since_start):
    return 1 - (1 - C0) * math.exp(-max(minutes_since_start, 0) / T_COVER)

def uncertainty_buffer(minutes_since_start, avg_confidence=1.0):
    return (1 / coverage(minutes_since_start) - 1) + KAPPA * (1 - avg_confidence)

def triage_now(c, minute, treated=frozenset()):
    # Yellow patients deteriorate to Red if not treated in time
    if (c["triage"] == "YELLOW" and c.get("deteriorate_at") is not None
            and minute >= c["deteriorate_at"] and c["id"] not in treated):
        return "RED"
    return c["triage"]

def known_patients(casualties, minute, zone=None):
    return [c for c in casualties
            if c["injured_at"] <= minute and c["reported_at"] <= minute
            and (zone is None or c["zone"] == zone)]

def incident_starts(events):
    starts = {"flood": 0}
    for e in events:
        if e["type"] == "collapse":
            starts["collapse"] = e["t"]
    return starts

def zone_demand(casualties, minute, zone, starts, treated=frozenset(), with_buffer=True):
    # Returns D_z for every resource, plus the N_c counts and U used, per cause
    D = {r: 0.0 for r in RESOURCES}
    detail = {}
    for cause, start in starts.items():
        if minute < start:
            continue
        people = [c for c in known_patients(casualties, minute, zone) if c["cause"] == cause]
        if not people:
            continue
        N = {col: 0 for col in TRIAGE}
        for c in people:
            N[triage_now(c, minute, treated)] += 1
        avg_conf = sum(c["confidence"] for c in people) / len(people)
        U = uncertainty_buffer(minute - start, avg_conf) if with_buffer else 0.0
        for r in RESOURCES:
            D[r] += M[cause][r] * sum(N[col] * W[r][col] for col in TRIAGE) * (1 + U)
        detail[cause] = {"N": N, "U": round(U, 2), "confidence": round(avg_conf, 2)}
    return D, detail

def total_demand(casualties, minute, zones, starts, treated=frozenset(), with_buffer=True):
    total = {r: 0.0 for r in RESOURCES}
    for z in zones:
        D, _ = zone_demand(casualties, minute, z["id"], starts, treated, with_buffer)
        for r in RESOURCES:
            total[r] += D[r]
    return total

def unreported_reserve(casualties, minute, zones, starts, treated=frozenset()):
    # Expected demand from people not yet reported = buffered − known.
    # The optimiser holds this much capacity back ("capacity minus reserve").
    a = total_demand(casualties, minute, zones, starts, treated, True)
    b = total_demand(casualties, minute, zones, starts, treated, False)
    return {r: max(a[r] - b[r], 0.0) for r in RESOURCES}
