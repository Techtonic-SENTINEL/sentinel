# SENTINEL · Intelligent and Transparent Disaster Relief Resource Allocation

**Team TECHTONIC · ELEVATE 1.0 · Problem statement EL-02**

**Live demo:** https://sentinel-techtonic.streamlit.app

SENTINEL decides **which patient goes to which hospital, by which ambulance and route**, re-planning the whole system whenever the disaster changes, and records every allocation, handover and rupee in a **tamper-evident ledger**.
The prototype runs on a simulated monsoon flood and building collapse in **Kurla–Sion, Mumbai**, on the real OpenStreetMap road network.

## Results (same simulated disaster, same scoring rules)
| Metric | SENTINEL | Nearest hospital | FCFS |
|---|---|---|---|
| Expected survivors (Red+Yellow) | 46.4 | 37.1 | 39.1 |
| Red: avg minutes to care | 107.3 | 155.9 | 137.1 |
| Reached within 2 h | 55/72 | 58/72 | 55/72 |
| Needed transfer (wrong hospital) | 0 | 15 | 10 |
| Arrived at over-full hospital | 0 | 6 | 0 |
| Busiest hospital load % | 93 | 200 | 100 |
| Spending outside earmarks (₹) | 0 | 0 | 0 |

## How the code maps to our architecture (slide 4)
| Folder | Model | Key method |
|---|---|---|
| `sim/` | Scenario engine | SimPy discrete-event simulation: casualty report waves, deterioration, road closure, hospital failure, collapse, blood shortage |
| `demand/` | Demand model | D = M × Σ(N × w) × (1 + U), with buffer U for unreported casualties |
| `routing/` | Routing model | Dijkstra / A* on the OpenStreetMap graph; flooded roads time-penalised, closed roads removed, travel-time matrix rebuilt on each closure |
| `optimizer/` | Allocation model | Google OR-Tools CP-SAT: max Σ x·S(t+τ) − λ·overload − μ·unserved Red, subject to bed/ICU/OT capacity minus reserve, ALS for Red, specialty fit, reachable route, blood-group stock, fund earmarks, zone fairness; warm start; greedy fallback |
| `ledger/` | Transparency ledger | SHA-256 hash chain, Ed25519 signatures, Merkle batches and proofs, OTP handover, donor trace, pseudonymous IDs, role-based views |
| `web/` | Command dashboard | Streamlit + deck.gl (pydeck): live map, what-if re-planning, scorecard, zone cards, "why this hospital?", ledger explorer, donor trace |
| `notebooks/` | Step-by-step build | Google Colab notebooks 01–05 |

Public anchoring of Merkle roots is supported (Polygon Amoy / OpenTimestamps); the prototype keeps the roots in `data/anchors.json`

## Run it yourself
```
pip install -r requirements.txt
streamlit run web/app.py
```

## Assumptions (prototype)
Hospital capacities, blood stock, ambulance positions, costs, survival-curve parameters and outcome delays are **synthetic demo values**. Survival gains are **simulation results**, as in the published allocation research we build on (Sacco 2005; Mills, Argon & Ziya 2013; Dean & Nair 2014; Repoussis et al. 2016). No real patient data is used.
