# SENTINEL web/ · Command dashboard (Streamlit + deck.gl), visual redesign
# Same five models as before: sim/ (SimPy) · demand/ · routing/ · optimizer/ (OR-Tools CP-SAT) · ledger/
import base64
import copy
import html
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

st.set_page_config(page_title="SENTINEL command", page_icon="🛰️", layout="wide", initial_sidebar_state="auto")

# ------------------------------------------------------------------ design tokens
INK, PANEL, RAISED, LINE = "#0B1426", "#111D33", "#16243F", "#24375A"
TEXT, MUTED = "#E8EEF6", "#93A4BC"
SKY, WATER, SIGNAL, BASE = "#4DA3FF", "#2DD4BF", "#FF7A1A", "#B69CFF"
TRIAGE_HEX = {"RED": "#FF5A67", "YELLOW": "#FACC15", "GREEN": "#4ADE80", "GREY": "#94A3B8"}


def rgb(hex_, a=255):
    h = hex_.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)] + [a]


POLICIES = {"sentinel": "SENTINEL", "nearest": "Nearest hospital", "fcfs": "First come, first served"}
SHORT = {"sentinel": "SENTINEL", "nearest": "Nearest", "fcfs": "FCFS"}
POLICY_HEX = {"SENTINEL": SKY, "Nearest": "#5B6B85", "FCFS": "#7F8EA6"}
LIVE_TIME_LIMIT = 1.5
TOGGLES = {"road_closed": "Critical road closes at minute 35", "hospital_down": "Hospital loses power at minute 50",
           "flood_worsens": "Flood spreads at minute 60", "collapse": "Building collapses at minute 75",
           "blood_shortage": "O+ blood runs out at minute 80"}

LOGO = base64.b64encode(f"""<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>
<circle cx='32' cy='32' r='29' fill='none' stroke='{SKY}' stroke-width='3' opacity='.35'/>
<circle cx='32' cy='32' r='19' fill='none' stroke='{SKY}' stroke-width='3' opacity='.7'/>
<path d='M32 32 L32 3 A29 29 0 0 1 58.6 20.5 Z' fill='{SKY}' opacity='.22'/>
<rect x='27' y='20' width='10' height='24' rx='2' fill='{TEXT}'/><rect x='20' y='27' width='24' height='10' rx='2' fill='{TEXT}'/>
<circle cx='50' cy='15' r='4' fill='{SIGNAL}'/></svg>""".encode()).decode()

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
html, body, .stApp, .stMarkdown, button, input, textarea, select, label,
[data-testid="stWidgetLabel"], [data-baseweb] {{ font-family: 'IBM Plex Sans', system-ui, sans-serif; }}
.stApp {{ background: radial-gradient(1100px 520px at 0% -8%, rgba(77,163,255,.10), transparent 62%), {INK}; color: {TEXT}; }}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1440px; }}
h1, h2, h3, h4 {{ font-family: 'Barlow Condensed', 'IBM Plex Sans', sans-serif !important; letter-spacing: .01em; }}
[data-testid="stSidebar"] > div:first-child {{ background: #0D1830; border-right: 1px solid {LINE}; }}
[data-testid="stSidebar"] [data-testid="stForm"] {{ border: 1px solid {LINE}; border-radius: 12px; background: {PANEL}; }}
div[data-baseweb="tab-list"] {{ gap: 4px; background: {PANEL}; padding: 5px; border-radius: 12px; border: 1px solid {LINE}; }}
button[data-baseweb="tab"] {{ height: 40px; border-radius: 9px; padding: 0 16px; color: {MUTED}; background: transparent; }}
button[data-baseweb="tab"][aria-selected="true"] {{ background: {RAISED}; color: {TEXT}; box-shadow: inset 0 -2px 0 {SKY}; }}
button[data-baseweb="tab"] p {{ font-size: .95rem; font-weight: 500; }}
div[data-baseweb="tab-highlight"], div[data-baseweb="tab-border"] {{ display: none; }}
.stButton > button, [data-testid="stFormSubmitButton"] button {{ border-radius: 10px; font-weight: 600; border: 1px solid {LINE}; }}
[data-testid="stFormSubmitButton"] button {{ background: {SKY}; color: #06101F; border: none; }}
[data-testid="stDataFrame"] {{ border: 1px solid {LINE}; border-radius: 12px; overflow: hidden; }}
[data-testid="stExpander"] {{ border: 1px solid {LINE}; border-radius: 12px; background: {PANEL}; }}
[data-testid="stDeckGlJsonChart"] {{ border-radius: 14px; overflow: hidden; border: 1px solid {LINE}; }}
footer {{ visibility: hidden; }}

.sx-head {{ display: flex; align-items: center; justify-content: space-between; gap: 18px; flex-wrap: wrap; margin-bottom: 14px; }}
.sx-brand {{ display: flex; align-items: center; gap: 14px; }}
.sx-brand img {{ width: 52px; height: 52px; }}
.sx-title {{ font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 2.9rem; line-height: .95; letter-spacing: .03em; margin: 0; color: {TEXT}; }}
.sx-sub {{ color: {MUTED}; font-size: .98rem; margin-top: 4px; }}
.sx-tags {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.sx-tag {{ border: 1px solid {LINE}; background: {PANEL}; color: {TEXT}; border-radius: 999px; padding: 6px 12px; font-size: .85rem; }}
.sx-tag b {{ color: {SKY}; font-weight: 600; }}
.sx-live {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #4ADE80; margin-right: 7px; box-shadow: 0 0 0 4px rgba(74,222,128,.15); }}

.sx-strip {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); background: {PANEL};
             border: 1px solid {LINE}; border-radius: 14px; margin: 4px 0 18px; overflow: hidden; }}
.sx-read {{ padding: 14px 18px; border-right: 1px solid {LINE}; border-bottom: 1px solid {LINE}; margin-bottom: -1px; }}
.sx-read:last-child {{ border-right: none; }}
.sx-read .k {{ color: {MUTED}; font-size: .84rem; }}
.sx-read .v {{ font-family: 'Barlow Condensed', sans-serif; font-weight: 600; font-size: 2.15rem; line-height: 1.1; color: {TEXT}; }}
.sx-read .d {{ font-size: .82rem; margin-top: 2px; }}
.good {{ color: #4ADE80; }} .bad {{ color: #FF8A94; }} .flat {{ color: {MUTED}; }}

.sx-legend {{ display: flex; flex-wrap: wrap; gap: 6px 16px; color: {MUTED}; font-size: .86rem; margin: 10px 2px 0; }}
.sx-legend span {{ display: inline-flex; align-items: center; gap: 7px; }}
.sx-legend i {{ display: inline-block; width: 11px; height: 11px; border-radius: 50%; }}
.sx-legend i.line {{ width: 18px; height: 4px; border-radius: 2px; }}
.sx-legend i.ring {{ background: transparent !important; border: 2px solid {MUTED}; }}

.sx-events {{ border-left: 2px solid {LINE}; margin: 6px 0 0 6px; padding-left: 14px; }}
.sx-ev {{ position: relative; padding: 5px 0; color: {MUTED}; font-size: .9rem; }}
.sx-ev::before {{ content: ''; position: absolute; left: -20px; top: 11px; width: 10px; height: 10px; border-radius: 50%;
                  background: {INK}; border: 2px solid {LINE}; }}
.sx-ev.done {{ color: {TEXT}; }} .sx-ev.done::before {{ background: {SIGNAL}; border-color: {SIGNAL}; }}
.sx-ev b {{ font-family: 'Barlow Condensed', sans-serif; font-size: 1.02rem; color: {TEXT}; margin-right: 8px; }}

.sx-zones {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 12px; }}
.sx-zone {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 14px; padding: 14px 16px; }}
.sx-zone.flood {{ border-color: rgba(45,212,191,.55); box-shadow: inset 0 3px 0 {WATER}; }}
.sx-zone h4 {{ margin: 0; font-size: 1.35rem; color: {TEXT}; }}
.sx-zone .meta {{ color: {MUTED}; font-size: .82rem; margin: 2px 0 10px; }}
.sx-tri {{ display: flex; gap: 6px; margin-bottom: 10px; }}
.sx-tri span {{ flex: 1; text-align: center; border-radius: 8px; padding: 4px 0; font-family: 'Barlow Condensed', sans-serif;
               font-size: 1.25rem; font-weight: 600; color: #0B1426; }}
.sx-need {{ display: grid; grid-template-columns: 1fr auto; row-gap: 3px; font-size: .9rem; color: {MUTED}; }}
.sx-need b {{ color: {TEXT}; font-weight: 600; text-align: right; }}

.sx-why {{ width: 100%; border-collapse: separate; border-spacing: 0 6px; font-size: .92rem; }}
.sx-why td {{ background: {PANEL}; padding: 9px 12px; border-top: 1px solid {LINE}; border-bottom: 1px solid {LINE}; color: {MUTED}; }}
.sx-why td:first-child {{ border-left: 1px solid {LINE}; border-radius: 10px 0 0 10px; color: {TEXT}; font-weight: 600; }}
.sx-why td:last-child {{ border-right: 1px solid {LINE}; border-radius: 0 10px 10px 0; }}
.sx-why tr.chosen td {{ background: rgba(77,163,255,.12); border-color: rgba(77,163,255,.5); color: {TEXT}; }}
.sx-bar {{ height: 7px; border-radius: 4px; background: {RAISED}; overflow: hidden; min-width: 90px; }}
.sx-bar i {{ display: block; height: 100%; background: {SKY}; }}

.sx-note {{ color: {MUTED}; font-size: .86rem; }}
.sx-big {{ font-family: 'Barlow Condensed', sans-serif; font-weight: 700; font-size: 4.2rem; line-height: .9; color: {SKY}; }}
.sx-alert {{ border-radius: 12px; padding: 12px 14px; font-size: .93rem; margin-top: 8px; }}
.sx-alert.red {{ background: rgba(255,90,103,.12); border: 1px solid rgba(255,90,103,.5); color: #FFD3D7; }}
.sx-alert.ok {{ background: rgba(74,222,128,.10); border: 1px solid rgba(74,222,128,.45); color: #CFF7DC; }}
.sx-alert.warn {{ background: rgba(250,204,21,.08); border: 1px solid rgba(250,204,21,.4); color: #FDF0B2; }}
.sx-side-brand {{ display: flex; gap: 10px; align-items: center; margin: 2px 0 14px; }}
.sx-side-brand img {{ width: 36px; }}
.sx-side-brand b {{ font-family: 'Barlow Condensed', sans-serif; font-size: 1.6rem; letter-spacing: .04em; }}
.sx-side-brand span {{ display: block; color: {MUTED}; font-size: .8rem; }}
.sx-foot {{ color: {MUTED}; font-size: .82rem; border-top: 1px solid {LINE}; padding-top: 14px; margin-top: 28px; }}
@media (max-width: 640px) {{ .sx-title {{ font-size: 2.2rem; }} .sx-read .v {{ font-size: 1.8rem; }} .sx-big {{ font-size: 3.2rem; }} }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def H(s):
    st.markdown(s, unsafe_allow_html=True)


def esc(s):
    return html.escape(str(s))


def chart_style(c):
    return (c.configure(background="transparent", font="IBM Plex Sans")
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor="#1C2B47", domainColor=LINE, tickColor=LINE,
                            labelFontSize=12, titleFontSize=12)
            .configure_legend(labelColor=TEXT, titleColor=MUTED, orient="bottom", labelFontSize=12))


# ------------------------------------------------------------------ data & models (cached)
def load_json(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


def has(name):
    return os.path.exists(os.path.join(DATA, name))


@st.cache_resource(show_spinner="Loading the Kurla–Sion road network…")
def graph():
    return rm.load_graph(os.path.join(DATA, "mumbai_roads.graphml"))


def _wkt(wkt):
    inner = wkt[wkt.index("(") + 1: wkt.rindex(")")]
    return [[float(x) for x in p.split()[:2]] for p in inner.split(",")]


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
                path = _wkt(d["geometry"])
            except ValueError:
                pass
        mid = path[len(path) // 2]
        out.append({"k": k, "path": path, "mid": (mid[1], mid[0])})
    return out


@st.cache_data(show_spinner=False)
def flooded_paths(radius, center):
    return [{"path": s["path"]} for s in road_segments()
            if rm.straight_line_m(s["mid"][0], s["mid"][1], center[0], center[1]) <= radius]


@st.cache_data(show_spinner=False)
def all_paths():
    return [{"path": s["path"]} for s in road_segments()]


@st.cache_data(show_spinner=False)
def closed_paths(keys):
    keys = set(keys)
    return [{"path": s["path"], "name": "Closed road", "detail": "Removed from the routing graph"}
            for s in road_segments() if s["k"] in keys]


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


@st.cache_data(show_spinner=False)
def baseline_results():
    R = load_json("results.json")
    names = {"SENTINEL": "sentinel", "Nearest hospital": "nearest", "FCFS": "fcfs"}
    return {names[k]: {"metrics": v, "allocations": R["allocations"][names[k]],
                       "log": R["replan_log"] if names[k] == "sentinel" else []} for k, v in R["metrics"].items()}


@st.cache_data(show_spinner=False)
def state(key):
    if key is None:
        return load_json("scenario.json"), baseline_results()
    return what_if(scenario(key[0]), dict(key[1]), key[2], key[3]), run_all(*key)


@st.cache_resource(show_spinner="Building the signed ledger…")
def official_ledger():
    R, S = load_json("results.json"), load_json("scenario.json")
    ledger, _, _ = lb.build(S, R["allocations"]["sentinel"], R["override_log"])
    return ledger


@st.cache_data(show_spinner=False)
def satellite():
    return load_json("satellite.json") if has("satellite.json") else None


@st.cache_data(show_spinner=False)
def demand_trace(key):
    S, _ = state(key)
    starts = dm.incident_starts(S["events"])
    rows = []
    for t in range(0, S["horizon_min"] + 1, 5):
        rows += [{"minute": t, "estimate": "True need (hidden)",
                  "beds": sum(dm.W["beds"][dm.triage_now(c, t)] for c in S["casualties"] if c["injured_at"] <= t)},
                 {"minute": t, "estimate": "Reported only",
                  "beds": dm.total_demand(S["casualties"], t, S["zones"], starts, with_buffer=False)["beds"]},
                 {"minute": t, "estimate": "SENTINEL with buffer U",
                  "beds": dm.total_demand(S["casualties"], t, S["zones"], starts)["beds"]}]
    return pd.DataFrame(rows)


def qr_png(text):
    buf = io.BytesIO()
    qrcode.make(text, border=2).save(buf, format="PNG")
    return buf.getvalue()


def metres(a, b):
    dy = (a[0] - b[0]) * 111320
    dx = (a[1] - b[1]) * 111320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def delta(a, b, lower_better=False, unit="", pct=False):
    d = a - b
    if pct and b:
        txt = f"{100 * d / b:+.0f}%"
    else:
        txt = f"{d:+.1f}{unit}" if isinstance(d, float) else f"{d:+d}{unit}"
    better = (d < 0) if lower_better else (d > 0)
    cls = "flat" if d == 0 else ("good" if better else "bad")
    return f"<span class='{cls}'>{txt} vs nearest hospital</span>"


# ------------------------------------------------------------------ sidebar
base_S = load_json("scenario.json")
area = load_json("area.json")
FLOOD_CENTER = tuple(area["flood_center"])
default_als = sum(a["type"] == "ALS" for a in base_S["ambulances"])
default_bls = sum(a["type"] == "BLS" for a in base_S["ambulances"])

with st.sidebar:
    H(f"<div class='sx-side-brand'><img src='data:image/svg+xml;base64,{LOGO}'/><div><b>SENTINEL</b>"
      f"<span>Team TECHTONIC, problem statement EL-02</span></div></div>")
    role = st.selectbox("View as", ["Commander", "Hospital", "Donor", "Public"],
                        help="Each role sees only what it is allowed to see.")
    viewer_h = viewer_d = None
    if role == "Hospital":
        viewer_h = st.selectbox("Your hospital", [h["id"] for h in base_S["hospitals"]],
                                format_func=lambda i: f"{i}: {next(h['name'] for h in base_S['hospitals'] if h['id'] == i)}")
    if role == "Donor":
        viewer_d = st.selectbox("Your donation", [d["id"] for d in base_S["donations"]],
                                format_func=lambda i: f"{i}: {next(d['donor'] for d in base_S['donations'] if d['id'] == i)}")
    st.markdown("#### Scenario")
    with st.form("whatif", border=True):
        seed = st.number_input("Disaster number (SimPy seed)", min_value=1, max_value=9999, value=int(base_S["seed"]),
                               help="The same number always gives the same disaster. Try another to generate a new one.")
        toggles = {k: st.toggle(label, value=True) for k, label in TOGGLES.items()}
        n_als = st.slider("Advanced life support ambulances", 2, 10, default_als)
        n_bls = st.slider("Basic life support ambulances", 3, 16, default_bls)
        go = st.form_submit_button("Re-plan all three policies", width="stretch")
    st.caption("Re-planning runs OR-Tools CP-SAT live and takes 30–90 seconds.")
    default = (seed == base_S["seed"] and all(toggles.values()) and n_als == default_als and n_bls == default_bls)
    if go:
        if default:
            st.session_state.pop("settings", None)
        else:
            st.session_state["settings"] = (int(seed), tuple(sorted(toggles.items())), n_als, n_bls)
    if st.session_state.get("settings") and st.button("Back to the baseline disaster", width="stretch"):
        st.session_state.pop("settings", None)
        st.rerun()

key = st.session_state.get("settings")
if key:
    with st.spinner("OR-Tools CP-SAT is re-planning the whole disaster three times…"):
        S, results = state(key)
    scen_label = f"What-if: disaster {key[0]}, {key[2]} ALS and {key[3]} BLS ambulances"
else:
    S, results = state(None)
    scen_label = "Baseline disaster 42"

# ------------------------------------------------------------------ header + readout strip
H(f"""<div class='sx-head'><div class='sx-brand'><img src='data:image/svg+xml;base64,{LOGO}'/>
<div><div class='sx-title'>SENTINEL</div><div class='sx-sub'>Disaster relief command for Kurla–Sion, Mumbai, during a monsoon flood and building collapse</div></div></div>
<div class='sx-tags'><span class='sx-tag'><span class='sx-live'></span>Models running live</span>
<span class='sx-tag'>{esc(scen_label)}</span><span class='sx-tag'>Viewing as <b>{esc(role)}</b></span></div></div>""")

ms, mn = results["sentinel"]["metrics"], results["nearest"]["metrics"]
L0 = official_ledger()
ok0, _, _ = L0.verify_chain()
H(f"""<div class='sx-strip'>
<div class='sx-read'><div class='k'>Expected survivors with SENTINEL</div><div class='v'>{ms['Expected survivors (Red+Yellow)']}</div>
<div class='d'>{delta(ms['Expected survivors (Red+Yellow)'], mn['Expected survivors (Red+Yellow)'], pct=True)}</div></div>
<div class='sx-read'><div class='k'>Red patients, minutes to care</div><div class='v'>{ms['Red: avg minutes to care']}</div>
<div class='d'>{delta(ms['Red: avg minutes to care'], mn['Red: avg minutes to care'], lower_better=True, unit=' min')}</div></div>
<div class='sx-read'><div class='k'>Busiest hospital load</div><div class='v'>{ms['Busiest hospital load %']}%</div>
<div class='d'><span class='flat'>{mn['Busiest hospital load %']}% with nearest hospital</span></div></div>
<div class='sx-read'><div class='k'>Audit ledger</div><div class='v'>{'Verified' if ok0 else 'Broken'}</div>
<div class='d'><span class='flat'>{len(L0.rows())} signed records</span></div></div></div>""")

tabs = st.tabs(["Live operations", "Impact", "Demand by zone", "Why this hospital", "Audit ledger", "Donor trace", "Ask SENTINEL"])


# ------------------------------------------------------------------ tab 1: live operations (fragment = fast)
@st.cache_data(show_spinner=False)
def ops_layers(key, policy, minute, role, viewer_h):
    S, results = state(key)
    allocs = results[policy]["allocations"]
    sent = {a["patient"]: a for a in allocs if a["dispatch_min"] <= minute}
    hosp = {h["id"]: h for h in S["hospitals"]}
    floods = [e for e in S["events"] if e["type"] == "flood" and e["t"] <= minute]
    radius = floods[-1]["radius_m"] if floods else 0
    closed = tuple(sorted((min(e["u"], e["v"]), max(e["u"], e["v"])) for e in S["events"]
                          if e["type"] == "road_closed" and e["t"] <= minute))
    dots, arcs, zones = [], [], []
    if role in ("Commander", "Hospital"):
        for c in S["casualties"]:
            if c["injured_at"] > minute:
                continue
            a = sent.get(c["id"])
            if a and a["arrival_min"] <= minute:
                continue
            col = om.colour_now(c, minute)
            known = c["reported_at"] <= minute
            dots.append({"p": [c["lon"], c["lat"]], "fill": rgb(TRIAGE_HEX[col], 235 if known else 0),
                         "line": rgb("#FFFFFF", 200) if known else rgb(TRIAGE_HEX[col], 220),
                         "name": f"{c['id']}, {col.title()}",
                         "detail": f"{c['specialty'].title()}, {'reported' if known else 'not yet reported'}"})
        for a in sent.values():
            if role == "Hospital" and a["hospital"] != viewer_h:
                continue
            c = next(x for x in S["casualties"] if x["id"] == a["patient"])
            h = hosp[a["hospital"]]
            arcs.append({"from": [c["lon"], c["lat"]], "to": [h["lon"], h["lat"]], "src": rgb(TRIAGE_HEX[a["triage"]], 230),
                         "dst": rgb(SKY if policy == "sentinel" else "#C7D2E3", 230),
                         "name": f"{a['patient']} to {a['hospital']}",
                         "detail": f"Ambulance {a['ambulance']} ({a['ambulance_type']}), arrives minute {a['arrival_min']:.0f}"})
    else:
        for z in S["zones"]:
            n = sum(1 for c in S["casualties"] if c["zone"] == z["id"] and c["reported_at"] <= minute and c["injured_at"] <= minute)
            zones.append({"p": [z["lon"], z["lat"]], "r": 140 + 22 * n, "name": f"{z['id']} {z['name']}",
                          "detail": f"{n} reported casualties"})
    load = {}
    for a in sent.values():
        load[a["hospital"]] = load.get(a["hospital"], 0) + 1
    down = {e["hospital"] for e in S["events"] if e["type"] == "hospital_down" and e["t"] <= minute}
    hpts = []
    for h in S["hospitals"]:
        beds = h["beds"] // 2 if h["id"] in down else h["beds"]
        pct = 100 * load.get(h["id"], 0) / max(beds, 1)
        colr = "#4ADE80" if pct < 70 else ("#FACC15" if pct <= 100 else "#FF5A67")
        hpts.append({"p": [h["lon"], h["lat"]], "fill": rgb(colr), "label": h["id"],
                     "r": 70 + 5 * h["beds"], "name": f"{h['id']} {h['name']}",
                     "detail": f"{load.get(h['id'], 0)} of {beds} beds used ({pct:.0f}%){', power failure' if h['id'] in down else ''}"})
    known_n = sum(1 for c in S["casualties"] if c["injured_at"] <= minute and c["reported_at"] <= minute)
    true_n = sum(1 for c in S["casualties"] if c["injured_at"] <= minute)
    red_wait = sum(1 for c in S["casualties"] if c["injured_at"] <= minute and c["reported_at"] <= minute
                   and c["id"] not in sent and om.colour_now(c, minute) == "RED")
    return {"radius": radius, "closed": closed, "dots": dots, "arcs": arcs, "zones": zones, "hospitals": hpts,
            "known": known_n, "true": true_n, "sent": len(sent), "red_wait": red_wait}


@st.fragment
def live_operations():
    c1, c2, c3 = st.columns([5, 4, 1.4], vertical_alignment="bottom")
    minute = c1.slider("Minutes since the flood began", 0, S["horizon_min"], 45, step=5, key="ops_min")
    pol = c2.segmented_control("Plan shown on the map", list(POLICIES), format_func=lambda p: {"sentinel": "SENTINEL", "nearest": "Nearest hospital", "fcfs": "FCFS"}[p],
                               default="sentinel", key="ops_pol") or "sentinel"
    tilt = c3.toggle("3D", key="ops_3d", help="Tilt the map to see ambulance routes as arcs")
    d = ops_layers(key, pol, minute, role, viewer_h)
    sat = satellite()
    layers = []
    if sat:
        layers.append(pdk.Layer("BitmapLayer", data=None, image=sat["url"], bounds=sat["bounds"], opacity=1.0))
    else:
        layers.append(pdk.Layer("PathLayer", all_paths(), get_path="path", get_color=rgb("#41557A", 200),
                                get_width=1, width_units='"pixels"'))
    if d["radius"]:
        layers.append(pdk.Layer("ScatterplotLayer", [{"p": [FLOOD_CENTER[1], FLOOD_CENTER[0]], "name": "Flood zone",
                                                      "detail": f"Roads within {d['radius']} m are slowed"}],
                                get_position="p", get_radius=d["radius"], get_fill_color=rgb(WATER, 38), stroked=True,
                                get_line_color=rgb(WATER, 230), line_width_min_pixels=2, pickable=True))
        layers.append(pdk.Layer("PathLayer", flooded_paths(d["radius"], FLOOD_CENTER), get_path="path",
                                get_color=rgb(WATER, 170), get_width=2, width_units='"pixels"'))
    if d["closed"]:
        cp = closed_paths(d["closed"])
        layers.append(pdk.Layer("PathLayer", cp, get_path="path", get_color=rgb(INK, 230), get_width=11, width_units='"pixels"'))
        layers.append(pdk.Layer("PathLayer", cp, get_path="path", get_color=rgb(SIGNAL), get_width=6,
                                width_units='"pixels"', pickable=True))
    if d["zones"]:
        layers.append(pdk.Layer("ScatterplotLayer", d["zones"], get_position="p", get_radius="r",
                                get_fill_color=rgb(SKY, 70), stroked=True, get_line_color=rgb(SKY, 220),
                                line_width_min_pixels=1.5, pickable=True))
    layers.append(pdk.Layer("ArcLayer", d["arcs"], get_source_position="from", get_target_position="to",
                            get_source_color="src", get_target_color="dst", get_width=2.4, get_height=0.35,
                            great_circle=False, pickable=True))
    layers.append(pdk.Layer("ScatterplotLayer", d["dots"], get_position="p", get_fill_color="fill", get_line_color="line",
                            stroked=True, filled=True, get_radius=28, radius_min_pixels=4.5, radius_max_pixels=9,
                            line_width_min_pixels=1.6, pickable=True))
    layers.append(pdk.Layer("ScatterplotLayer", [{"p": [b["lon"], b["lat"]], "name": f"Ambulance base {b['id']}",
                                                  "detail": "Ambulances start here"} for b in S["bases"]],
                            get_position="p", get_fill_color=rgb(BASE), get_line_color=rgb(INK), stroked=True,
                            get_radius=45, radius_min_pixels=5, line_width_min_pixels=2, pickable=True))
    layers.append(pdk.Layer("ScatterplotLayer", d["hospitals"], get_position="p", get_fill_color="fill",
                            get_line_color=rgb("#FFFFFF", 235), stroked=True, get_radius="r", radius_min_pixels=8,
                            radius_max_pixels=22, line_width_min_pixels=2.5, pickable=True))
    layers.append(pdk.Layer("TextLayer", d["hospitals"], get_position="p", get_text="label", get_size=13,
                            get_color=rgb(TEXT), get_pixel_offset=[0, -22], font_family='"IBM Plex Sans, Arial, sans-serif"',
                            font_weight=600, background=True, get_background_color=rgb(INK, 215),
                            background_padding=[5, 2, 5, 2]))
    view = pdk.ViewState(latitude=area["center"][0], longitude=area["center"][1], zoom=13.6,
                         pitch=45 if tilt else 0, bearing=-12 if tilt else 0)
    tooltip = {"html": "<b>{name}</b><br/><span style='color:#93A4BC'>{detail}</span>",
               "style": {"backgroundColor": PANEL, "color": TEXT, "fontFamily": "IBM Plex Sans, sans-serif",
                         "fontSize": "13px", "border": f"1px solid {LINE}", "borderRadius": "10px", "padding": "8px 10px"}}
    st.pydeck_chart(pdk.Deck(layers=layers, map_style=None, initial_view_state=view, tooltip=tooltip), height=560)
    attribution = f" Satellite imagery: {sat['attribution']}." if sat else ""
    H(f"""<div class='sx-legend'>
<span><i style='background:{TRIAGE_HEX["RED"]}'></i>Red</span><span><i style='background:{TRIAGE_HEX["YELLOW"]}'></i>Yellow</span>
<span><i style='background:{TRIAGE_HEX["GREEN"]}'></i>Green</span><span><i class='ring'></i>Not yet reported</span>
<span><i style='background:#4ADE80;border:2px solid #fff'></i>Hospital, colour = how full</span>
<span><i style='background:{BASE}'></i>Ambulance base</span><span><i class='line' style='background:{WATER}'></i>Flooded road</span>
<span><i class='line' style='background:{SIGNAL}'></i>Closed road</span>
<span><i class='line' style='background:linear-gradient(90deg,{TRIAGE_HEX["RED"]},{SKY})'></i>Patient to hospital</span></div>
<div class='sx-note' style='margin-top:6px'>Roads, hospitals and ambulance bases are from OpenStreetMap; casualties and capacities are simulated.{attribution}</div>""")
    H(f"""<div class='sx-strip' style='margin-top:14px'>
<div class='sx-read'><div class='k'>Known to the control room</div><div class='v'>{d['known']} <span style='color:{MUTED};font-size:1.3rem'>of {d['true']}</span></div><div class='d flat'>The rest are not reported yet</div></div>
<div class='sx-read'><div class='k'>Patients dispatched</div><div class='v'>{d['sent']}</div><div class='d flat'>{POLICIES[pol]}</div></div>
<div class='sx-read'><div class='k'>Red patients waiting</div><div class='v'>{d['red_wait']}</div><div class='d flat'>Reported, not yet picked up</div></div></div>""")
    evs = "".join(f"<div class='sx-ev {'done' if e['t'] <= minute else ''}'><b>{e['t']} min</b>{esc(e['text'])}</div>"
                  for e in sorted(S["events"], key=lambda e: e["t"]))
    H(f"<h3 style='margin:8px 0 2px'>Events</h3><div class='sx-events'>{evs}</div>")


with tabs[0]:
    live_operations()

# ------------------------------------------------------------------ tab 2: impact
with tabs[1]:
    if has("benchmark.json"):
        B = load_json("benchmark.json")
        s = B["summary"]
        a, b = st.columns([1.1, 2])
        with a:
            H(f"<div class='sx-big'>{B['gain_vs_nearest_pct']:+.0f}%</div>"
              f"<div style='font-size:1.08rem;margin-top:6px'>more expected survivors than sending everyone to the nearest hospital, "
              f"averaged over {B['n_runs']} simulated disasters.</div>")
        with b:
            H(f"""<div class='sx-strip' style='margin:0;grid-template-columns:repeat(2,minmax(0,1fr))'>
<div class='sx-read'><div class='k'>Red patients reach care</div><div class='v'>{B['red_faster_vs_nearest_pct']:.0f}% faster</div><div class='d flat'>{s['sentinel']['red_minutes_mean']} vs {s['nearest']['red_minutes_mean']} min</div></div>
<div class='sx-read'><div class='k'>Better than nearest hospital</div><div class='v'>{B['wins_vs_nearest']} of {B['n_runs']}</div><div class='d flat'>disasters</div></div>
<div class='sx-read'><div class='k'>Versus first come, first served</div><div class='v'>{B['gain_vs_fcfs_pct']:+.0f}%</div><div class='d flat'>expected survivors</div></div>
<div class='sx-read'><div class='k'>Sent to the wrong hospital</div><div class='v'>{s['sentinel']['transfers_mean']:g}</div><div class='d flat'>vs {s['nearest']['transfers_mean']:g} with nearest</div></div></div>""")
        per = pd.DataFrame([{"disaster": r["seed"], "policy": SHORT[p], "expected survivors": r[p]["survivors"]}
                            for r in B["runs"] for p in POLICIES])
        line = alt.Chart(per).mark_line(point=alt.OverlayMarkDef(size=36), strokeWidth=2.2).encode(
            x=alt.X("disaster:O", title="Simulated disaster (SimPy seed)", axis=alt.Axis(labelAngle=0, labelOverlap=True)),
            y=alt.Y("expected survivors:Q", title="Expected survivors", scale=alt.Scale(zero=False)),
            color=alt.Color("policy:N", title=None, scale=alt.Scale(domain=list(POLICY_HEX), range=list(POLICY_HEX.values()))),
            tooltip=["disaster", "policy", "expected survivors"]).properties(height=300)
        st.markdown("### Every disaster, all three policies")
        st.altair_chart(chart_style(line), theme=None, width="stretch")
    st.markdown("### This disaster")
    st.caption(scen_label)
    rows = []
    for p in POLICIES:
        m = results[p]["metrics"]
        rows += [{"policy": SHORT[p], "measure": "Expected survivors (higher is better)", "value": float(m["Expected survivors (Red+Yellow)"])},
                 {"policy": SHORT[p], "measure": "Red patients, minutes to care (lower is better)", "value": float(m["Red: avg minutes to care"])},
                 {"policy": SHORT[p], "measure": "Busiest hospital load % (lower is better)", "value": float(m["Busiest hospital load %"])}]
    df = pd.DataFrame(rows)
    cols = st.columns(3)
    for col, measure in zip(cols, df["measure"].unique()):
        d = df[df["measure"] == measure]
        base = alt.Chart(d).encode(x=alt.X("policy:N", sort=list(POLICY_HEX), title=None, axis=alt.Axis(labelAngle=0)),
                                   y=alt.Y("value:Q", title=None, axis=alt.Axis(grid=True, tickCount=4)))
        bars = base.mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6, size=46).encode(
            color=alt.Color("policy:N", legend=None, scale=alt.Scale(domain=list(POLICY_HEX), range=list(POLICY_HEX.values()))))
        labels = base.mark_text(dy=-10, color=TEXT, font="Barlow Condensed", fontSize=17, fontWeight=600).encode(
            text=alt.Text("value:Q", format=".0f"))
        col.markdown(f"**{measure}**")
        col.altair_chart(chart_style((bars + labels).properties(height=250)), theme=None, width="stretch")
    with st.expander("All measures for this disaster"):
        st.dataframe(pd.DataFrame({POLICIES[p]: results[p]["metrics"] for p in POLICIES}).astype(str), width="stretch")
    log = results["sentinel"]["log"]
    if log:
        with st.expander("How SENTINEL re-planned, every 5 minutes"):
            st.dataframe(pd.DataFrame(log), hide_index=True, width="stretch")

# ------------------------------------------------------------------ tab 3: demand by zone
with tabs[2]:
    st.latex(r"D_z = M \times \sum_c \left(N_c \times w_c\right) \times (1 + U)")
    st.caption("N: known patients per triage colour. w: resource use per patient. M: disaster-type multiplier. "
               "U: buffer for people not yet reported, large at first and shrinking as reports are verified.")

    @st.fragment
    def demand_zones():
        t = st.slider("Minutes since the flood began", 0, S["horizon_min"], 45, step=5, key="dem_min")
        starts = dm.incident_starts(S["events"])
        cards = []
        for z in S["zones"]:
            D, det = dm.zone_demand(S["casualties"], t, z["id"], starts)
            N = {c: sum(x["N"][c] for x in det.values()) for c in dm.TRIAGE}
            seen = dm.known_patients(S["casualties"], t, z["id"])
            conf = f"{sum(c['confidence'] for c in seen) / len(seen):.2f}" if seen else "no reports"
            floods = [e for e in S["events"] if e["type"] == "flood" and e["t"] <= t]
            flooded = bool(floods) and metres((z["lat"], z["lon"]), FLOOD_CENTER) <= floods[-1]["radius_m"]
            U = max([x["U"] for x in det.values()], default=0)
            tri = "".join(f"<span style='background:{TRIAGE_HEX[c]}'>{N[c]}</span>" for c in dm.TRIAGE)
            need = "".join(f"<span>{lab}</span><b>{val}</b>" for lab, val in [
                ("Beds", round(D["beds"])), ("ICU beds", round(D["icu"])), ("Operations", round(D["ot"])),
                ("Blood units", round(D["blood_units"])), ("ALS trips", round(D["als_trips"])), ("Dialysis", f"{D['dialysis']:.1f}")])
            cards.append(f"<div class='sx-zone {'flood' if flooded else ''}'><h4>{esc(z['id'])} {esc(z['name'])}</h4>"
                         f"<div class='meta'>{'Flooded roads' if flooded else 'Roads open'}, report confidence {conf}, buffer U {U}</div>"
                         f"<div class='sx-tri'>{tri}</div><div class='sx-need'>{need}</div></div>")
        H(f"<div class='sx-zones'>{''.join(cards)}</div>")

    demand_zones()
    st.markdown("### Beds needed over time")
    tr = demand_trace(key)
    ch = alt.Chart(tr).mark_line(strokeWidth=2.6, interpolate="step-after").encode(
        x=alt.X("minute:Q", title="Minutes since the flood began"), y=alt.Y("beds:Q", title="Beds needed"),
        color=alt.Color("estimate:N", title=None, scale=alt.Scale(
            domain=["True need (hidden)", "Reported only", "SENTINEL with buffer U"], range=["#FF5A67", "#5B6B85", SKY])),
        strokeDash=alt.condition(alt.datum.estimate == "Reported only", alt.value([5, 4]), alt.value([1, 0])),
        tooltip=["minute", "estimate", alt.Tooltip("beds:Q", format=".0f")]).properties(height=300)
    st.altair_chart(chart_style(ch), theme=None, width="stretch")
    st.caption("The buffer keeps SENTINEL's estimate close to the true need while reports are still arriving.")

# ------------------------------------------------------------------ tab 4: why this hospital
with tabs[3]:
    if role not in ("Commander", "Hospital"):
        H("<div class='sx-alert warn'>Patient-level decisions are visible to the Commander and Hospital roles only. "
          "Switch the role in the sidebar to see them.</div>")
    else:
        cands = [a for a in results["sentinel"]["allocations"] if a.get("why") and (role == "Commander" or a["hospital"] == viewer_h)]
        if not cands:
            H("<div class='sx-alert warn'>SENTINEL sent no patients to this hospital in this disaster.</div>")
        else:
            pick = st.selectbox("Choose a patient", cands, format_func=lambda a: f"{a['patient']}, {a['colour_at_dispatch'].title()}, "
                                f"{a['specialty']}, dispatched at minute {a['dispatch_min']}")
            H(f"<p style='font-size:1.05rem;margin:6px 0 4px'>Sent to <b>{pick['hospital']}</b> by ambulance <b>{pick['ambulance']}</b> "
              f"({pick['ambulance_type']}). Care starts at minute {pick['care_min']:.0f}, expected survival "
              f"<b style='color:{SKY}'>{pick['survival']:.0%}</b>.</p>"
              f"<div class='sx-note'>Every hospital SENTINEL considered for this patient, and why it was or wasn't chosen.</div>")
            rows = []
            for w in pick["why"]:
                chosen = w["reason"] == "CHOSEN"
                sv = w["survival"]
                bar = f"<div class='sx-bar'><i style='width:{(sv or 0) * 100:.0f}%'></i></div>" if sv is not None else ""
                mins = "" if w["minutes"] is None else f"{w['minutes']} min"
                pct = "" if sv is None else f"{sv:.0%}"
                why = "Chosen" if chosen else esc(w["reason"][0].upper() + w["reason"][1:])
                rows.append(f"<tr class='{'chosen' if chosen else ''}'><td>{esc(w['hospital'])}</td><td>{esc(w['name'])}</td>"
                            f"<td>{mins}</td><td>{bar}{pct}</td><td>{why}</td></tr>")
            H(f"<table class='sx-why'>{''.join(rows)}</table>")
            st.caption("A commander can override any decision. Every override is written to the audit ledger with who, when and why.")

# ------------------------------------------------------------------ tab 5: audit ledger
with tabs[4]:
    L = official_ledger()
    rows = L.rows()
    anchors = load_json("anchors.json") if has("anchors.json") else {}
    roots = [b["merkle_root"] for b in L.batches()]
    ok, bad, msg = L.verify_chain()
    if anchors.get("tx"):
        anchor_txt = f"<a href='{lm.AMOY_EXPLORER + anchors['tx']}' target='_blank' style='color:{SKY}'>Polygon Amoy</a>"
    elif anchors.get("bitcoin_block"):
        anchor_txt = f"<a href='https://mempool.space/block/{anchors['bitcoin_block']}' target='_blank' style='color:{SKY}'>Bitcoin block</a>"
    elif anchors.get("method", "").startswith("OpenTimestamps"):
        anchor_txt = "OpenTimestamps, pending"
    else:
        anchor_txt = "Kept separately"
    H(f"""<div class='sx-strip'>
<div class='sx-read'><div class='k'>Signed records</div><div class='v'>{len(rows)}</div><div class='d flat'>Hash-chained with SHA-256</div></div>
<div class='sx-read'><div class='k'>Merkle batches</div><div class='v'>{len(L.batches())}</div><div class='d flat'>One root every 15 minutes</div></div>
<div class='sx-read'><div class='k'>Verifier</div><div class='v'>{'Valid' if ok else 'Broken'}</div><div class='d {'good' if ok else 'bad'}'>{esc(msg)}</div></div>
<div class='sx-read'><div class='k'>Published roots</div><div class='v' style='font-size:1.5rem;padding-top:6px'>{anchor_txt}</div>
<div class='d flat'>{'Match this ledger' if anchors.get('roots') == roots else 'Not compared'}</div></div></div>""")

    @st.fragment
    def tamper():
        st.markdown("### Try to cheat it")
        spend = next(r for r in rows if r["type"] == "SPEND")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Edit one payment.** An insider switches off the protection and changes an amount.")
            if st.button("Edit a payment", width="stretch", key="atk1"):
                c, old, new = lb.attack_edit(L, spend["seq"])
                okc, badc, msgc = c.verify_chain()
                H(f"<div class='sx-alert red'>Record {spend['seq']} changed from ₹{old:,} to ₹{new:,}. "
                  f"Tamper detected at record {badc}: {esc(msgc)}.</div>")
        with c2:
            st.markdown("**Rewrite all history.** The insider re-hashes and re-signs every record with stolen keys.")
            if st.button("Rewrite the ledger", width="stretch", key="atk2"):
                c = lb.attack_rewrite(L, spend["seq"])
                okc, _, _ = c.verify_chain()
                published = anchors.get("roots") or roots
                mism = [b["batch"] for b, p in zip(c.batches(), published) if b["merkle_root"] != p]
                H(f"<div class='sx-alert warn'>The internal check says {'valid' if okc else 'broken'}, because every record was re-signed.</div>"
                  f"<div class='sx-alert red'>{'Tamper detected: batch ' + str(mism[0]) + ' no longer matches the published root.' if mism else 'No mismatch found.'}</div>")
        st.markdown("### Prove one record is included")
        seq = st.number_input("Record number", 1, len(rows), spend["seq"], key="proof_seq")
        leaf, proof, b = L.proof_for(int(seq))
        valid = lm.verify_proof(leaf, proof, b["merkle_root"])
        H(f"<div class='sx-alert {'ok' if valid else 'red'}'>Record {int(seq)} is in batch {b['batch']}. "
          f"Its proof uses {len(proof)} sibling hashes and {'recomputes the batch root exactly' if valid else 'does not match'}.</div>")

    tamper()
    st.markdown("### Records")
    types = sorted({r["type"] for r in rows})
    ftype = st.multiselect("Record types", types, default=[t for t in ["ALLOCATE", "OVERRIDE", "SPEND"] if t in types])
    shown = [lm.role_view(role, r, viewer_hospital=viewer_h, viewer_donation=viewer_d) for r in rows if r["type"] in ftype][:300]
    st.dataframe(pd.DataFrame([{"record": v.get("seq"), "type": v.get("type"), "minute": v.get("minute"),
                                "contents": json.dumps(v.get("payload", v.get("hidden", "")))[:150],
                                "hash": (v.get("hash") or "")[:16]} for v in shown]),
                 hide_index=True, width="stretch", height=360,
                 column_config={"record": st.column_config.NumberColumn(width="small"),
                                "minute": st.column_config.NumberColumn(format="%.0f", width="small"),
                                "contents": st.column_config.TextColumn(width="large")})
    if has("merkle_roots.txt") and has("merkle_roots.txt.ots"):
        d1, d2 = st.columns(2)
        d1.download_button("Download the Merkle roots", open(os.path.join(DATA, "merkle_roots.txt"), "rb"), "merkle_roots.txt", width="stretch")
        d2.download_button("Download the timestamp proof", open(os.path.join(DATA, "merkle_roots.txt.ots"), "rb"), "merkle_roots.txt.ots", width="stretch")

# ------------------------------------------------------------------ tab 6: donor trace
with tabs[5]:
    L = official_ledger()
    rows = L.rows()
    if role == "Public":
        tot = pd.DataFrame([r["payload"] for r in rows if r["type"] == "SPEND"]).groupby("category", as_index=False)["amount"].sum()
        tot["lakh"] = tot["amount"] / 1e5
        bars = alt.Chart(tot).mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6, size=60, color=SKY).encode(
            x=alt.X("category:N", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("lakh:Q", title="₹ lakh spent"))
        st.markdown("### Where the relief money went")
        st.altair_chart(chart_style(bars.properties(height=280)), theme=None, width="stretch")
        st.caption("The public sees totals only. Donors see every payment from their own donation.")
    else:
        dons = [d["id"] for d in base_S["donations"]]
        donation = viewer_d if role == "Donor" else st.selectbox(
            "Donation", dons, format_func=lambda i: f"{i}: {next(d['donor'] for d in base_S['donations'] if d['id'] == i)}")
        d = next(x for x in base_S["donations"] if x["id"] == donation)
        mine = [r for r in rows if r["type"] == "SPEND" and r["payload"]["donation"] == donation]
        handovers = {r["payload"]["trip"]: r for r in rows if r["type"] == "HANDOVER"}
        used = sum(r["payload"]["amount"] for r in mine)
        left, right = st.columns([3, 1.1])
        with left:
            H(f"""<div class='sx-strip' style='margin-top:0'>
<div class='sx-read'><div class='k'>{esc(d['donor'])} gave</div><div class='v'>₹{d['amount'] / 1e5:.1f} lakh</div><div class='d flat'>for {esc(d['earmark'])}</div></div>
<div class='sx-read'><div class='k'>Spent so far</div><div class='v'>₹{used / 1e5:.2f} lakh</div><div class='d flat'>{100 * used / d['amount']:.0f}% of the donation</div></div>
<div class='sx-read'><div class='k'>Payments</div><div class='v'>{len(mine)}</div><div class='d flat'>each tied to a verified handover</div></div></div>""")
            st.dataframe(pd.DataFrame([{"record": r["seq"], "trip": r["payload"]["trip"], "hospital": r["payload"]["hospital"],
                                        "for": r["payload"]["category"], "amount (₹)": r["payload"]["amount"],
                                        "handover verified at minute": round(handovers[r["payload"]["trip"]]["minute"])}
                                       for r in mine]), hide_index=True, width="stretch", height=330,
                         column_config={"amount (₹)": st.column_config.NumberColumn(format="₹%d")})
        with right:
            if mine:
                leaf, proof, b = L.proof_for(mine[0]["seq"])
                st.image(qr_png(f"SENTINEL donor trace | donation={donation} | record={mine[0]['seq']} | hash={leaf} | root={b['merkle_root']}"),
                         caption="Donor receipt: scan to check the record and its Merkle root", width="stretch")
                ok_p = lm.verify_proof(leaf, proof, b["merkle_root"])
                H(f"<div class='sx-alert {'ok' if ok_p else 'red'}'>Merkle proof for record {mine[0]['seq']} {'checks out' if ok_p else 'fails'}.</div>")

# ------------------------------------------------------------------ tab 7: Ask SENTINEL (Gemini assistant)
import requests  # noqa: E402

GEMINI = "https://generativelanguage.googleapis.com/v1beta"
SKIP_WORDS = ["embedding", "image", "tts", "audio", "live", "vision", "aqa", "learnlm", "computer", "robotics", "native", "veo", "imagen"]


def gemini_key():
    try:
        k = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        k = None
    return k or os.environ.get("GEMINI_API_KEY")


@st.cache_data(ttl=3600, show_spinner=False)
def gemini_models(key):
    # Asks Google which models this key may use, then ranks them: stable Flash first, Gemma last
    try:
        r = requests.get(f"{GEMINI}/models", params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=20)
        models = r.json().get("models", []) if r.ok else []
    except Exception:
        models = []
    ranked = []
    for m in models:
        name = m.get("name", "").split("/")[-1]
        if "generateContent" not in m.get("supportedGenerationMethods", []) or any(w in name for w in SKIP_WORDS):
            continue
        ver = 0.0
        for part in name.replace("gemini-", "").replace("gemma-", "").split("-"):
            try:
                ver = float(part)
                break
            except ValueError:
                continue
        unstable = any(w in name for w in ["preview", "exp", "latest"])
        if name.startswith("gemini") and "flash" in name and "lite" not in name:
            tier = 0
        elif name.startswith("gemini") and "flash" in name:
            tier = 1
        elif name.startswith("gemma"):
            tier = 3
        else:
            tier = 2
        ranked.append((tier, unstable, -ver, name))
    ranked.sort()
    names = [r[3] for r in ranked]
    return names or ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemma-3-27b-it"]


def ask_gemini(key, system, history, question):
    # Returns (answer, model) or raises RuntimeError with a plain-English reason
    last_err = "no model answered"
    for model in gemini_models(key)[:4]:
        contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["text"]}]} for m in history[-8:]]
        if model.startswith("gemma"):
            contents.append({"role": "user", "parts": [{"text": system + "\n\nQuestion: " + question}]})
            body = {"contents": contents}
        else:
            contents.append({"role": "user", "parts": [{"text": question}]})
            body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents}
        body["generationConfig"] = {"temperature": 0.3, "maxOutputTokens": 2048}
        try:
            r = requests.post(f"{GEMINI}/models/{model}:generateContent", json=body,
                              headers={"x-goog-api-key": key}, timeout=60)
        except Exception:
            last_err = "the AI service could not be reached"
            continue
        if r.status_code == 429:
            last_err = "the free AI quota is busy right now; try again in a minute"
            continue
        if r.status_code in (400, 401, 403) and "API key" in r.text:
            raise RuntimeError("the Gemini API key was rejected; check it in the app's Secrets")
        if not r.ok:
            last_err = f"the AI service returned an error ({r.status_code})"
            continue
        parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if text:
            return text, model
        last_err = "the AI returned an empty answer"
    raise RuntimeError(last_err)


@st.cache_data(show_spinner=False)
def chat_context(key, role, viewer_h, viewer_d, scen_label):
    S, results = state(key)
    out = [f"SCENARIO: {scen_label}. Simulated monsoon flood and building collapse in Kurla-Sion, Mumbai, on the real "
           f"OpenStreetMap road network. {len(S['casualties'])} casualties over {S['horizon_min']} minutes. "
           f"Hospital capacities, survival curves and costs are synthetic demo values."]
    out.append("EVENTS: " + "; ".join(f"minute {e['t']}: {e['text']}" for e in sorted(S["events"], key=lambda e: e["t"])))
    out.append("RESULTS (same disaster, same scoring rules):")
    for p in POLICIES:
        out.append(f"- {POLICIES[p]}: " + ", ".join(f"{k} = {v}" for k, v in results[p]["metrics"].items()))
    if has("benchmark.json"):
        B = load_json("benchmark.json")
        s = B["summary"]
        out.append(f"BENCHMARK over {B['n_runs']} simulated disasters: SENTINEL {B['gain_vs_nearest_pct']:+.0f}% expected survivors "
                   f"vs nearest hospital (better in {B['wins_vs_nearest']}/{B['n_runs']}), {B['gain_vs_fcfs_pct']:+.0f}% vs FCFS, "
                   f"Red patients reach care {B['red_faster_vs_nearest_pct']:.0f}% faster; average wrong-hospital transfers "
                   f"SENTINEL {s['sentinel']['transfers_mean']} vs nearest {s['nearest']['transfers_mean']}.")
    load = {}
    for a in results["sentinel"]["allocations"]:
        load[a["hospital"]] = load.get(a["hospital"], 0) + 1
    out.append("HOSPITALS (id, name, size, free beds, ICU, specialties, patients SENTINEL sent):")
    for h in S["hospitals"]:
        out.append(f"- {h['id']} {h['name']}: {h['tier']}, {h['beds']} beds, {h['icu']} ICU, "
                   f"{', '.join(h['specialties'])}; received {load.get(h['id'], 0)}")
    starts = dm.incident_starts(S["events"])
    for t in (30, 60, 90):
        z = dm.total_demand(S["casualties"], t, S["zones"], starts)
        out.append(f"DEMAND at minute {t} (with buffer U): beds {z['beds']:.0f}, ICU {z['icu']:.0f}, operations {z['ot']:.0f}, "
                   f"blood units {z['blood_units']:.0f}, ALS trips {z['als_trips']:.0f}")
    if role in ("Commander", "Hospital"):
        out.append("SENTINEL DECISIONS (patient, colour, zone, specialty, hospital, ambulance, minutes, survival, alternatives):")
        for a in results["sentinel"]["allocations"]:
            if role == "Hospital" and a["hospital"] != viewer_h:
                continue
            alts = [f"{w['hospital']} ({w['reason']})" for w in a.get("why", []) if w["reason"] != "CHOSEN"][:3]
            out.append(f"- {a['patient']} {a['colour_at_dispatch']} {a['zone']} {a['specialty']} -> {a['hospital']} by "
                       f"{a['ambulance']} ({a['ambulance_type']}), dispatched {a['dispatch_min']}, arrives {a['arrival_min']:.0f}, "
                       f"care {a['care_min']:.0f}, survival {a['survival']:.0%}; not chosen: {', '.join(alts) or 'n/a'}")
        sent = {a["patient"] for a in results["sentinel"]["allocations"]}
        waiting = [c["id"] + " " + c["triage"] for c in S["casualties"] if c["triage"] in ("RED", "YELLOW") and c["id"] not in sent]
        if role == "Commander":
            out.append(f"NOT REACHED WITHIN THE HORIZON: {', '.join(waiting) or 'none'}")
    else:
        out.append("PRIVACY: this viewer may not see patient-level decisions. Answer with totals only.")
    L = official_ledger()
    rows = L.rows()
    ok, _, _ = L.verify_chain()
    out.append(f"LEDGER: {len(rows)} signed SHA-256 hash-chained records, {len(L.batches())} Merkle batches, verifier {'valid' if ok else 'broken'}.")
    spends = [r["payload"] for r in rows if r["type"] == "SPEND"]
    for d in S["donations"]:
        if role == "Donor" and d["id"] != viewer_d:
            continue
        mine = [p for p in spends if p.get("donation") == d["id"]]
        by_h = {}
        for p in mine:
            by_h[p["hospital"]] = by_h.get(p["hospital"], 0) + p["amount"]
        out.append(f"DONATION {d['id']} ({d['donor']}, Rs {d['amount']:,}, earmark {d['earmark']}): used Rs {sum(p['amount'] for p in mine):,} "
                   f"in {len(mine)} payments; by hospital: {', '.join(f'{k} Rs {v:,}' for k, v in sorted(by_h.items())) or 'none'}")
    return "\n".join(out)


def chat_system(role, context):
    return ("You are the assistant inside SENTINEL, a disaster-relief command dashboard built for a hackathon prototype. "
            f"The person asking is viewing as: {role}. Answer only from the DATA below. If the answer is not in the data, say so "
            "plainly; never invent numbers, patients or hospitals. SENTINEL's OR-Tools CP-SAT optimiser makes the allocation "
            "decisions; you explain and summarise them, you do not change them. If the viewer is Public or a Donor, never discuss "
            "individual patients. Keep answers short (under 120 words) unless asked for a report, use plain English, cite patient "
            "and hospital IDs and minutes, and mention that values are simulated when giving numbers to someone outside the team.\n\n"
            "DATA:\n" + context)


SUGGEST = {
    "Commander": ["Give me a 5-line situation report", "Which hospital is under the most pressure?",
                  "Why was P012 sent where it went?", "How does SENTINEL beat the nearest-hospital rule?"],
    "Hospital": ["Which patients are coming to us?", "Which of our patients are Red?",
                 "Why were patients sent here instead of elsewhere?", "Summarise our load for the shift lead"],
    "Donor": ["Where did my donation go?", "Is the ledger verified?", "What did my money pay for?", "How is fraud prevented?"],
    "Public": ["What is happening right now?", "How is relief money tracked?", "How many people were helped?",
               "Is the data trustworthy?"],
}

with tabs[6]:
    st.markdown("### Ask SENTINEL")
    st.caption("Ask in plain English. Answers come only from this disaster's data on this page, using Google Gemini; "
               "SENTINEL's optimiser still makes every decision.")
    api_key = gemini_key()
    chat_id = f"chat_{role}_{viewer_h}_{viewer_d}_{key}"
    history = st.session_state.setdefault(chat_id, [])
    if not api_key:
        H("<div class='sx-alert warn'>The assistant needs a Gemini API key. On share.streamlit.io open this app's "
          "<b>Settings → Secrets</b> and add one line: <code>GEMINI_API_KEY = \"your key\"</code>, then save.</div>")
    cols = st.columns(4)
    picked = None
    for i, (col, q) in enumerate(zip(cols, SUGGEST[role])):
        if col.button(q, key=f"sg_{role}_{i}", width="stretch", disabled=not api_key):
            picked = q
    for m in history:
        with st.chat_message("user" if m["role"] == "user" else "assistant"):
            st.markdown(m["text"])
    typed = st.chat_input("Ask about patients, hospitals, routes, funds or the ledger", disabled=not api_key, key=f"ci_{chat_id}")
    question = typed or picked
    if question and api_key:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Reading SENTINEL's data…"):
                try:
                    answer, used = ask_gemini(api_key, chat_system(role, chat_context(key, role, viewer_h, viewer_d, scen_label)),
                                              history, question)
                    st.markdown(answer)
                    st.caption(f"Answered by {used} from SENTINEL's data")
                    history += [{"role": "user", "text": question}, {"role": "assistant", "text": answer}]
                except RuntimeError as e:
                    H(f"<div class='sx-alert red'>No answer: {esc(e)}.</div>")
    if history and st.button("Clear the conversation", key=f"clr_{chat_id}"):
        st.session_state[chat_id] = []
        st.rerun()

H("<div class='sx-foot'>Running here: a SimPy scenario engine, the demand model, Dijkstra and A* routing on OpenStreetMap roads, "
  "OR-Tools CP-SAT allocation, a SHA-256 and Merkle audit ledger, and a Gemini assistant that explains the data. Capacities, costs and survival curves are synthetic "
  "demo values, and survival gains are simulation results.</div>")
