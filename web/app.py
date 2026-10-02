# SENTINEL web/ · Command dashboard (Streamlit + deck.gl)
# Joins all five models: sim/ (SimPy) · demand/ · routing/ · optimizer/ (OR-Tools CP-SAT) · ledger/
import copy
import io
import json
import math
import os
import sys

import altair as alt
import pandas as pd
import pydeck as pdk
import qrcode
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ["routing", "demand", "optimizer", "ledger", "sim"]:
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)
DATA = os.path.join(ROOT, "data")

import demand_model as dm          # noqa: E402
import ledger_builder as lb        # noqa: E402
import ledger_model as lm          # noqa: E402
import optimizer_model as om       # noqa: E402
import routing_model as rm         # noqa: E402
import scenario_model as sm        # noqa: E402

st.set_page_config(page_title="SENTINEL · EL-02 · TECHTONIC", page_icon="🛰️", layout="wide")

RGB = {"RED": [239, 68, 68], "YELLOW": [245, 158, 11], "GREEN": [34, 197, 94], "GREY": [100, 116, 139]}
POLICIES = {"sentinel": "SENTINEL", "nearest": "Nearest hospital", "fcfs": "FCFS"}
LIVE_TIME_LIMIT = 1.5
TOGGLES = {"road_closed": "Critical road closes (min 35)", "hospital_down": "Hospital power failure (min 50)",
           "flood_worsens": "Flood worsens (min 60)", "collapse": "Building collapse (min 75)",
           "blood_shortage": "O+ blood runs out (min 80)"}


# ---------------------------------------------------------------- data & models (cached)
def load_json(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


def has(name):
    return os.path.exists(os.path.join(DATA, name))


@st.cache_resource(show_spinner="Loading the real Kurla–Sion road network…")
def graph():
    return rm.load_graph(os.path.join(DATA, "mumbai_roads.graphml"))


def wkt_coords(wkt):
    # "LINESTRING (72.87 19.06, 72.88 19.07)" -> [[72.87, 19.06], [72.88, 19.07]]
    inner = wkt[wkt.index("(") + 1: wkt.rindex(")")]
    return [[float(x) for x in pt.split()[:2]] for pt in inner.split(",")]


@st.cache_resource
def road_segments():
    G, seen, out = graph(), set(), []
    for u, v, d in G.edges(data=True):
        k = (min(u, v), max(u, v))
        if k in seen:
            continue
        seen.add(k)
        a, b = G.nodes[u], G.nodes[v]
        path = [[a["x"], a["y"]], [b["x"], b["y"]]]
        if isinstance(d.get("geometry"), str) and "(" in d["geometry"]:
            try:
                path = wkt_coords(d["geometry"])
            except ValueError:
                pass
        mid = path[len(path) // 2]
        out.append({"u": u, "v": v, "path": path, "mid": (mid[1], mid[0])})
    return out


@st.cache_data(show_spinner="SimPy is generating a new disaster…")
def scenario(seed):
    base = load_json("scenario.json")
    if seed == base["seed"]:
        return base
    return sm.generate(graph(), load_json("hospitals.json"), load_json("area.json"), seed=seed)


def what_if(S, toggles, n_als, n_bls):
    S2 = copy.deepcopy(S)
    keep = []
    for e in S2["events"]:
        key = "flood_worsens" if (e["type"] == "flood" and e["t"] > 0) else e["type"]
        if toggles.get(key, True):
            keep.append(e)
    S2["events"] = keep
    if not toggles.get("collapse", True):
        S2["casualties"] = [c for c in S2["casualties"] if c["cause"] != "collapse"]
    S2["ambulances"] = sm.make_ambulances(S2["bases"], n_als, n_bls)
    return S2


@st.cache_data(show_spinner=False)
def run_all(seed, toggle_items, n_als, n_bls):
    S2 = what_if(scenario(seed), dict(toggle_items), n_als, n_bls)
    area = load_json("area.json")
    out = {}
    for p in POLICIES:
        world, log = om.simulate(p, S2, graph(), tuple(area["flood_center"]), dm, time_limit=LIVE_TIME_LIMIT)
        out[p] = {"metrics": om.metrics(world, S2), "allocations": list(world.dispatched.values()), "log": log}
    return out


def baseline_results():
    R = load_json("results.json")
    names = {"SENTINEL": "sentinel", "Nearest hospital": "nearest", "FCFS": "fcfs"}
    return {names[k]: {"metrics": v, "allocations": R["allocations"][names[k]],
                       "log": R["replan_log"] if names[k] == "sentinel" else []}
            for k, v in R["metrics"].items()}


@st.cache_resource(show_spinner="Building the signed ledger…")
def official_ledger():
    R, S = load_json("results.json"), load_json("scenario.json")
    ledger, signer, _ = lb.build(S, R["allocations"]["sentinel"], R["override_log"])
    return ledger


def qr_png(text):
    buf = io.BytesIO()
    qrcode.make(text).save(buf, format="PNG")
    return buf.getvalue()


def metres(a, b):
    dy = (a[0] - b[0]) * 111320
    dx = (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


# ---------------------------------------------------------------- sidebar
st.sidebar.markdown("## 🛰️ SENTINEL")
st.sidebar.caption("Team TECHTONIC · ELEVATE 1.0 · PS EL-02\n\nIntelligent and Transparent Disaster Relief Resource Allocation")
role = st.sidebar.selectbox("View as (role-based access)", ["Commander", "Hospital", "Donor", "Public"])
base_S = load_json("scenario.json")
viewer_h = viewer_d = None
if role == "Hospital":
    viewer_h = st.sidebar.selectbox("Your hospital", [h["id"] for h in base_S["hospitals"]])
if role == "Donor":
    viewer_d = st.sidebar.selectbox("Your donation", [d["id"] for d in base_S["donations"]])
st.sidebar.divider()
st.sidebar.markdown("**What-if scenario**")
with st.sidebar.form("whatif"):
    seed = st.number_input("Disaster seed (SimPy)", min_value=1, max_value=9999, value=int(base_S["seed"]),
                           help="Same number = same disaster. Change it to generate a new one.")
    toggles = {k: st.checkbox(label, value=True) for k, label in TOGGLES.items()}
    n_als = st.slider("ALS ambulances", 2, 10, sum(a["type"] == "ALS" for a in base_S["ambulances"]))
    n_bls = st.slider("BLS ambulances", 3, 16, sum(a["type"] == "BLS" for a in base_S["ambulances"]))
    go = st.form_submit_button("▶ Re-plan with all three policies", width="stretch")
default = (seed == base_S["seed"] and all(toggles.values())
           and n_als == sum(a["type"] == "ALS" for a in base_S["ambulances"])
           and n_bls == sum(a["type"] == "BLS" for a in base_S["ambulances"]))
if go and not default:
    st.session_state["settings"] = (int(seed), tuple(sorted(toggles.items())), n_als, n_bls)
elif go and default:
    st.session_state.pop("settings", None)
settings = st.session_state.get("settings")
st.sidebar.caption("Capacities, survival curves and costs are synthetic demo values. All models run live.")

# ---------------------------------------------------------------- current scenario & results
area = load_json("area.json")
FLOOD_CENTER = tuple(area["flood_center"])
if settings:
    S = what_if(scenario(settings[0]), dict(settings[1]), settings[2], settings[3])
    with st.spinner("OR-Tools CP-SAT is re-planning the whole disaster three times (≈30–90 s)…"):
        results = run_all(*settings)
    label = f"What-if run · seed {settings[0]} · {settings[2]} ALS / {settings[3]} BLS"
else:
    S = base_S
    results = baseline_results()
    label = "Baseline scenario · seed 42 · results from Notebook 4"

st.markdown("# 🛰️ SENTINEL · disaster relief command")
st.caption(f"Kurla–Sion, Mumbai · monsoon flood + building collapse · {label} · viewing as **{role}**")

tabs = st.tabs(["🗺️ Live map", "⚖️ Scorecard", "📊 Demand & zone cards", "❓ Why this hospital?",
                "🔗 Ledger", "💸 Donor trace"])

# ---------------------------------------------------------------- tab 1: map
with tabs[0]:
    c1, c2 = st.columns([3, 1])
    minute = c1.slider("Minute after the flood", 0, S["horizon_min"], 45, step=5)
    policy = c2.radio("Plan", list(POLICIES), format_func=POLICIES.get, horizontal=False)
    allocs = results[policy]["allocations"]
    sent = {a["patient"]: a for a in allocs if a["dispatch_min"] <= minute}
    floods = [e for e in S["events"] if e["type"] == "flood" and e["t"] <= minute]
    radius = floods[-1]["radius_m"] if floods else 0
    closed = {(min(e["u"], e["v"]), max(e["u"], e["v"])) for e in S["events"] if e["type"] == "road_closed" and e["t"] <= minute}

    roads = []
    for seg in road_segments():
        k = (min(seg["u"], seg["v"]), max(seg["u"], seg["v"]))
        flooded = radius and rm.straight_line_m(seg["mid"][0], seg["mid"][1], *FLOOD_CENTER) <= radius
        roads.append({"path": seg["path"], "color": [239, 68, 68] if k in closed else ([56, 189, 248] if flooded else [71, 85, 105]),
                      "width": 6 if k in closed else 1})
    layers = [pdk.Layer("PathLayer", roads, get_path="path", get_color="color", get_width="width",
                        width_units="pixels", width_min_pixels=1)]
    if radius:
        layers.append(pdk.Layer("ScatterplotLayer", [{"p": [FLOOD_CENTER[1], FLOOD_CENTER[0]], "name": "Flood zone"}],
                                get_position="p", get_radius=radius, get_fill_color=[56, 189, 248, 35],
                                stroked=True, get_line_color=[56, 189, 248], line_width_min_pixels=1, pickable=True))
    if role in ("Commander", "Hospital"):
        dots = []
        for c in S["casualties"]:
            if c["injured_at"] > minute:
                continue
            a = sent.get(c["id"])
            if a and a["arrival_min"] <= minute:
                continue
            col = om.colour_now(c, minute)
            known = c["reported_at"] <= minute
            dots.append({"p": [c["lon"], c["lat"]], "color": RGB[col] + [235 if known else 60],
                         "name": f"{c['id']} · {col} · {c['specialty']} · {'reported' if known else 'NOT YET REPORTED'}"})
        layers.append(pdk.Layer("ScatterplotLayer", dots, get_position="p", get_fill_color="color",
                                get_radius=35, radius_min_pixels=3, pickable=True))
        hosp = {h["id"]: h for h in S["hospitals"]}
        lines = [{"from": [a_["lon"], a_["lat"]], "to": [hosp[s_["hospital"]]["lon"], hosp[s_["hospital"]]["lat"]],
                  "color": [56, 189, 248, 200] if policy == "sentinel" else [203, 213, 225, 170],
                  "name": f"{s_['patient']} → {s_['hospital']} by {s_['ambulance']}"}
                 for s_ in sent.values() for a_ in [next(x for x in S["casualties"] if x["id"] == s_["patient"])]
                 if role == "Commander" or s_["hospital"] == viewer_h]
        layers.append(pdk.Layer("LineLayer", lines, get_source_position="from", get_target_position="to",
                                get_color="color", get_width=2, pickable=True))
    else:
        agg = []
        for z in S["zones"]:
            n = sum(1 for c in S["casualties"] if c["zone"] == z["id"] and c["reported_at"] <= minute and c["injured_at"] <= minute)
            agg.append({"p": [z["lon"], z["lat"]], "r": 120 + 25 * n, "name": f"{z['id']} · {n} reported casualties"})
        layers.append(pdk.Layer("ScatterplotLayer", agg, get_position="p", get_radius="r",
                                get_fill_color=[245, 158, 11, 110], pickable=True))
    load = {}
    for a in sent.values():
        load[a["hospital"]] = load.get(a["hospital"], 0) + 1
    down = {e["hospital"] for e in S["events"] if e["type"] == "hospital_down" and e["t"] <= minute}
    hpts = []
    for h in S["hospitals"]:
        beds = h["beds"] // 2 if h["id"] in down else h["beds"]
        pct = 100 * load.get(h["id"], 0) / max(beds, 1)
        colr = [34, 197, 94] if pct < 70 else ([245, 158, 11] if pct <= 100 else [239, 68, 68])
        hpts.append({"p": [h["lon"], h["lat"]], "color": colr, "label": h["id"],
                     "name": f"{h['id']} · {h['name']} · {load.get(h['id'], 0)}/{beds} beds ({pct:.0f}%)"})
    layers.append(pdk.Layer("ScatterplotLayer", hpts, get_position="p", get_fill_color="color", get_radius=90,
                            radius_min_pixels=7, stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=2, pickable=True))
    layers.append(pdk.Layer("TextLayer", hpts, get_position="p", get_text="label", get_size=13,
                            get_color=[255, 255, 255], get_pixel_offset=[0, -18]))
    layers.append(pdk.Layer("ScatterplotLayer", [{"p": [b["lon"], b["lat"]], "name": f"Ambulance base {b['id']}"} for b in S["bases"]],
                            get_position="p", get_fill_color=[167, 139, 250], get_radius=60, radius_min_pixels=5, pickable=True))
    st.pydeck_chart(pdk.Deck(layers=layers, map_style=None, tooltip={"text": "{name}"},
                             initial_view_state=pdk.ViewState(latitude=area["center"][0], longitude=area["center"][1], zoom=13.4)))
    st.caption("Map drawn from the real OpenStreetMap road graph the optimiser uses · blue roads = flooded · red = closed · "
               "faint dots = casualties not yet reported · hospital colour = bed load")

    true_n = sum(1 for c in S["casualties"] if c["injured_at"] <= minute)
    known_n = sum(1 for c in S["casualties"] if c["injured_at"] <= minute and c["reported_at"] <= minute)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Known to control room", f"{known_n} / {true_n}", help="True total is hidden from the control room")
    m2.metric("Patients dispatched", len(sent))
    m3.metric("Red waiting", sum(1 for c in S["casualties"] if c["injured_at"] <= minute and c["reported_at"] <= minute
                                 and c["id"] not in sent and om.colour_now(c, minute) == "RED"))
    m4.metric("Events so far", sum(1 for e in S["events"] if e["t"] <= minute))
    with st.expander("Event timeline"):
        st.dataframe(pd.DataFrame([{"minute": e["t"], "event": e["text"]} for e in S["events"]]), hide_index=True)

# ---------------------------------------------------------------- tab 2: scorecard
with tabs[1]:
    st.subheader("Same disaster, same scoring rules: SENTINEL vs the static rules in the problem statement")
    table = pd.DataFrame({POLICIES[p]: results[p]["metrics"] for p in POLICIES})
    st.dataframe(table.astype(str), width="stretch")
    s_, n_ = results["sentinel"]["metrics"], results["nearest"]["metrics"]
    k1, k2, k3 = st.columns(3)
    k1.metric("Expected survivors (SENTINEL)", s_["Expected survivors (Red+Yellow)"],
              f"{s_['Expected survivors (Red+Yellow)'] - n_['Expected survivors (Red+Yellow)']:+.1f} vs nearest")
    k2.metric("Red: minutes to care", s_["Red: avg minutes to care"],
              f"{s_['Red: avg minutes to care'] - n_['Red: avg minutes to care']:+.1f} vs nearest", delta_color="inverse")
    k3.metric("Busiest hospital load %", s_["Busiest hospital load %"],
              f"{s_['Busiest hospital load %'] - n_['Busiest hospital load %']:+d} vs nearest", delta_color="inverse")
    COLORS = alt.Scale(domain=list(POLICIES.values()), range=["#38BDF8", "#64748B", "#94A3B8"])
    long = pd.DataFrame([{"policy": POLICIES[p], "metric": name, "value": float(results[p]["metrics"][key])}
                         for p in POLICIES for name, key in [("Expected survivors ↑", "Expected survivors (Red+Yellow)"),
                                                             ("Red: minutes to care ↓", "Red: avg minutes to care"),
                                                             ("Busiest hospital load % ↓", "Busiest hospital load %")]])
    cc = st.columns(3)
    for col, name in zip(cc, long["metric"].unique()):
        d = long[long["metric"] == name]
        base = alt.Chart(d).encode(x=alt.X("policy:N", sort=list(POLICIES.values()), title=None, axis=alt.Axis(labelAngle=0)),
                                   y=alt.Y("value:Q", title=None, scale=alt.Scale(zero=True)))
        chart = (base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(color=alt.Color("policy:N", scale=COLORS, legend=None))
                 + base.mark_text(dy=-8, color="#E2E8F0").encode(text=alt.Text("value:Q", format=".0f")))
        col.markdown(f"**{name}**")
        col.altair_chart(chart.properties(height=260), width="stretch")

    if has("benchmark.json"):
        B = load_json("benchmark.json")
        st.markdown(f"#### Across {B['n_runs']} different simulated disasters (SimPy seeds {B['seeds'][0]}–{B['seeds'][-1]})")
        rows_ = []
        for p in POLICIES:
            m = B["summary"][p]
            rows_.append({"policy": POLICIES[p],
                          "expected survivors (mean, range)": f"{m['survivors_mean']} ({m['survivors_min']}–{m['survivors_max']})",
                          "Red minutes to care (mean)": m["red_minutes_mean"],
                          "busiest hospital load % (mean)": m["load_mean"],
                          "wrong-hospital transfers (mean)": m["transfers_mean"]})
        st.dataframe(pd.DataFrame(rows_), hide_index=True, width="stretch")
        st.success(f"SENTINEL had more expected survivors than the nearest-hospital rule in {B['wins_vs_nearest']}/{B['n_runs']} disasters "
                   f"(average {B['gain_vs_nearest_pct']:+.0f}%) and than FCFS in {B['wins_vs_fcfs']}/{B['n_runs']} "
                   f"(average {B['gain_vs_fcfs_pct']:+.0f}%).")
        per = pd.DataFrame([{"seed": r["seed"], "policy": POLICIES[p], "expected survivors": r[p]["survivors"]}
                            for r in B["runs"] for p in POLICIES])
        st.altair_chart(alt.Chart(per).mark_line(point=True).encode(
            x=alt.X("seed:O", title="disaster (SimPy seed)"), y=alt.Y("expected survivors:Q", scale=alt.Scale(zero=False)),
            color=alt.Color("policy:N", scale=COLORS)).properties(height=280), width="stretch")
    log = results["sentinel"]["log"]
    if log:
        with st.expander("Event-driven re-planning log (CP-SAT, warm start, greedy fallback)"):
            st.dataframe(pd.DataFrame(log), hide_index=True)
    st.info("Change the scenario in the sidebar (turn events off, change ambulances, or a new SimPy seed) "
            "and press ▶ to re-plan all three policies live with OR-Tools CP-SAT.")

# ---------------------------------------------------------------- tab 3: demand
with tabs[2]:
    st.subheader("Demand model:  D = M × Σ(N × w) × (1 + U)")
    t3 = st.slider("Minute", 0, S["horizon_min"], 45, step=5, key="dmin")
    starts = dm.incident_starts(S["events"])
    rows = []
    for z in S["zones"]:
        D, det = dm.zone_demand(S["casualties"], t3, z["id"], starts)
        N = {c: sum(d["N"][c] for d in det.values()) for c in dm.TRIAGE}
        seen = dm.known_patients(S["casualties"], t3, z["id"])
        floods = [e for e in S["events"] if e["type"] == "flood" and e["t"] <= t3]
        flooded = floods and metres((z["lat"], z["lon"]), FLOOD_CENTER) <= floods[-1]["radius_m"]
        rows.append({"zone": f"{z['id']} {z['name']}", "Red": N["RED"], "Yellow": N["YELLOW"], "Green": N["GREEN"],
                     "Grey": N["GREY"], "confidence": round(sum(c["confidence"] for c in seen) / len(seen), 2) if seen else None,
                     "access": "Flooded" if flooded else "Open", "U": max([d["U"] for d in det.values()], default=0),
                     "beds": round(D["beds"]), "ICU": round(D["icu"]), "ops": round(D["ot"]),
                     "blood": round(D["blood_units"]), "ALS trips": round(D["als_trips"]), "dialysis": round(D["dialysis"], 1)})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    mins = list(range(0, S["horizon_min"] + 1, 5))
    trace = pd.DataFrame({
        "True need (hidden)": [sum(dm.W["beds"][dm.triage_now(c, t)] for c in S["casualties"] if c["injured_at"] <= t) for t in mins],
        "Naive count": [dm.total_demand(S["casualties"], t, S["zones"], starts, with_buffer=False)["beds"] for t in mins],
        "SENTINEL (with U)": [dm.total_demand(S["casualties"], t, S["zones"], starts)["beds"] for t in mins]}, index=mins)
    st.markdown("**Beds needed over time: the buffer U keeps SENTINEL closer to the truth**")
    st.line_chart(trace)

# ---------------------------------------------------------------- tab 4: why
with tabs[3]:
    st.subheader("“Why this hospital?” Every decision is explainable")
    if role not in ("Commander", "Hospital"):
        st.warning("Patient-level decisions are visible to Commander and Hospital roles only.")
    else:
        cands = [a for a in results["sentinel"]["allocations"] if a.get("why") and (role == "Commander" or a["hospital"] == viewer_h)]
        if not cands:
            st.info("No SENTINEL decisions for this hospital in this scenario.")
        else:
            pick_ = st.selectbox("Patient", cands, format_func=lambda a: f"{a['patient']} · {a['colour_at_dispatch']} · "
                                 f"{a['specialty']} · minute {a['dispatch_min']} → {a['hospital']}")
            st.markdown(f"Sent to **{pick_['hospital']}** by **{pick_['ambulance']} ({pick_['ambulance_type']})** · "
                        f"care at minute {pick_['care_min']} · expected survival **{pick_['survival']:.0%}**")
            st.dataframe(pd.DataFrame(pick_["why"]), hide_index=True, width="stretch")
            st.caption("Human overrides are allowed but always ledgered with actor ID (see the OVERRIDE record in the Ledger tab).")

# ---------------------------------------------------------------- tab 5: ledger
with tabs[4]:
    st.subheader("Tamper-evident ledger: SHA-256 chain · Ed25519 signatures · Merkle batches · public anchor")
    L = official_ledger()
    rows = L.rows()
    a1, a2, a3 = st.columns(3)
    a1.metric("Records", len(rows))
    a2.metric("Merkle batches", len(L.batches()))
    ok, bad, msg = L.verify_chain()
    a3.metric("Verifier", "VALID ✅" if ok else f"BROKEN at #{bad}")
    st.caption(msg)
    types = sorted({r["type"] for r in rows})
    ftype = st.multiselect("Record types", types, default=["ALLOCATE", "OVERRIDE", "SPEND"])
    shown = [lm.role_view(role, r, viewer_hospital=viewer_h, viewer_donation=viewer_d) for r in rows if r["type"] in ftype][:300]
    st.dataframe(pd.DataFrame([{"seq": v.get("seq"), "type": v.get("type"), "minute": v.get("minute"),
                                "data": json.dumps(v.get("payload", v.get("hidden", "")))[:140],
                                "hash": (v.get("hash") or "")[:18]} for v in shown]), hide_index=True, width="stretch")

    anchors = load_json("anchors.json") if has("anchors.json") else {}
    roots = [b["merkle_root"] for b in L.batches()]
    st.markdown("#### Public anchor")
    if anchors.get("tx"):
        st.success(f"Merkle roots anchored on Polygon Amoy · [view transaction]({lm.AMOY_EXPLORER + anchors['tx']})")
    elif anchors.get("method", "").startswith("OpenTimestamps"):
        if anchors.get("bitcoin_block"):
            st.success(f"Merkle roots anchored in Bitcoin block #{anchors['bitcoin_block']} via OpenTimestamps · "
                       f"[block](https://mempool.space/block/{anchors['bitcoin_block']})")
        else:
            st.info("Merkle roots submitted to OpenTimestamps (Bitcoin) · pending Bitcoin confirmation")
        if has("merkle_roots.txt") and has("merkle_roots.txt.ots"):
            d1, d2 = st.columns(2)
            d1.download_button("Download merkle_roots.txt", open(os.path.join(DATA, "merkle_roots.txt"), "rb"), "merkle_roots.txt")
            d2.download_button("Download proof (.ots)", open(os.path.join(DATA, "merkle_roots.txt.ots"), "rb"), "merkle_roots.txt.ots")
            st.caption("Verify both files at opentimestamps.org")
    else:
        st.info("Public anchor: production feature (Polygon). The prototype keeps a separate copy of the roots.")
    if anchors.get("roots"):
        st.write("Rebuilt ledger roots match the published roots:", "✅ yes" if roots == anchors["roots"] else "❌ no")

    st.markdown("#### Try to cheat it")
    spend = next(r for r in rows if r["type"] == "SPEND")
    b1, b2 = st.columns(2)
    if b1.button("Attack 1 · edit one payment"):
        c, old, new = lb.attack_edit(L, spend["seq"])
        okc, badc, msgc = c.verify_chain()
        b1.error(f"Record #{spend['seq']} changed ₹{old:,} → ₹{new:,} · 🚨 TAMPER DETECTED at #{badc}: {msgc}")
    if b2.button("Attack 2 · rewrite history perfectly"):
        c = lb.attack_rewrite(L, spend["seq"])
        okc, _, _ = c.verify_chain()
        published = anchors.get("roots") or roots
        mism = [b["batch"] for b, p in zip(c.batches(), published) if b["merkle_root"] != p]
        b2.warning(f"Internal verifier says: {'VALID' if okc else 'BROKEN'} (the insider re-signed everything)")
        b2.error(f"🚨 TAMPER DETECTED: batch {mism[0]} no longer matches the published root" if mism else "No mismatch")

    st.markdown("#### Merkle proof for any record")
    seq = st.number_input("Record #", 1, len(rows), spend["seq"])
    leaf, proof, b = L.proof_for(int(seq))
    st.write(f"Record #{int(seq)} is in batch {b['batch']} · {len(proof)} sibling hashes · proof valid:",
             "✅" if lm.verify_proof(leaf, proof, b["merkle_root"]) else "❌")

# ---------------------------------------------------------------- tab 6: donor trace
with tabs[5]:
    st.subheader("Donor trace: follow every rupee to a verified handover")
    L = official_ledger()
    rows = L.rows()
    if role == "Public":
        tot = pd.DataFrame([r["payload"] for r in rows if r["type"] == "SPEND"]).groupby("category")["amount"].sum()
        st.bar_chart(tot)
        st.caption("Public view: totals only. Donors see their own donation in detail.")
    else:
        dons = [d["id"] for d in base_S["donations"]]
        donation = viewer_d if role == "Donor" else st.selectbox("Donation", dons)
        d = next(x for x in base_S["donations"] if x["id"] == donation)
        mine = [r for r in rows if r["type"] == "SPEND" and r["payload"]["donation"] == donation]
        handovers = {r["payload"]["trip"]: r for r in rows if r["type"] == "HANDOVER"}
        used = sum(r["payload"]["amount"] for r in mine)
        c1, c2, c3 = st.columns(3)
        c1.metric("Donated", f"₹{d['amount'] / 1e5:.1f} lakh", d["earmark"])
        c2.metric("Used", f"₹{used / 1e5:.2f} lakh")
        c3.metric("Payments", len(mine))
        st.dataframe(pd.DataFrame([{"record": r["seq"], "trip": r["payload"]["trip"], "hospital": r["payload"]["hospital"],
                                    "category": r["payload"]["category"], "₹": r["payload"]["amount"],
                                    "handover (OTP verified)": f"minute {handovers[r['payload']['trip']]['minute']}"}
                                   for r in mine]), hide_index=True, width="stretch")
        if mine:
            leaf, proof, b = L.proof_for(mine[0]["seq"])
            st.write(f"Merkle proof for record #{mine[0]['seq']}:", "✅ valid" if lm.verify_proof(leaf, proof, b["merkle_root"]) else "❌")
            st.image(qr_png(f"SENTINEL donor trace | donation={donation} | record={mine[0]['seq']} | hash={leaf} | root={b['merkle_root']}"),
                     caption="Donor receipt QR", width=220)

st.divider()
st.caption("SENTINEL prototype · TECHTONIC · models: SimPy scenario engine · demand model D = M·Σ(N·w)·(1+U) · "
           "Dijkstra/A* routing on OpenStreetMap · OR-Tools CP-SAT allocation · SHA-256/Merkle/Ed25519 ledger. "
           "Survival gains are simulation results with stated assumptions.")
