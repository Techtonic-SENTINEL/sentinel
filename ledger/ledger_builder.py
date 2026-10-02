# SENTINEL ledger/ · Builds the ledger from SENTINEL's allocations (same steps as Notebook 5),
# so the website can verify it, attack it and trace donations live.
import random
import ledger_model as lm

SALT = "sentinel-demo-salt"


def build(S, allocations, override_log, path=":memory:", signer=None, demo_rejected_otp=True):
    signer = signer or lm.Signer()
    ledger = lm.Ledger(path, signer)
    rng = random.Random(7)
    otps = {}
    allocations = sorted(allocations, key=lambda a: (a["dispatch_min"], a["patient"]))
    pools = [{"id": d["id"], "earmark": d["earmark"], "left": d["amount"]} for d in S["donations"]]

    def draw(category, amount):
        out = []
        for pool in [p for p in pools if p["earmark"] == category] + [p for p in pools if p["earmark"] == "any"]:
            take = min(pool["left"], amount)
            if take > 0:
                pool["left"] -= take
                amount -= take
                out.append((pool["id"], take))
            if amount <= 0:
                break
        if amount > 0:
            out.append(("UNFUNDED", amount))
        return out

    events = []
    for d in S["donations"]:
        events.append((0, 0, "DONATION_RECEIVED", "donor_portal",
                       {"donation": d["id"], "donor": d["donor"], "amount": d["amount"], "earmark": d["earmark"]}))
    for n, a in enumerate(allocations, start=1):
        trip, pt = f"T{n:03d}", lm.pseudonym(a["patient"], SALT)
        otp = f"{rng.randint(0, 999999):06d}"
        otps[trip] = otp
        events.append((a["dispatch_min"], 1, "ALLOCATE", "optimizer",
                       {"trip": trip, "patient": pt, "triage": a["colour_at_dispatch"], "hospital": a["hospital"],
                        "ambulance": a["ambulance"], "expected_survival": a["survival"]}))
        events.append((a["dispatch_min"], 2, "DISPATCH", "control_room",
                       {"trip": trip, "patient": pt, "ambulance": a["ambulance"], "hospital": a["hospital"],
                        "eta_min": a["arrival_min"], "otp_hash": lm.otp_hash(otp, trip)}))
        events.append((a["arrival_min"], 4, "HANDOVER", "hospital",
                       {"trip": trip, "patient": pt, "hospital": a["hospital"], "otp_verified": True}))
        for category, amount in a["costs"].items():
            for donation, part in draw(category, amount):
                if part > 0:
                    events.append((a["arrival_min"], 5, "SPEND", "finance",
                                   {"trip": trip, "patient": pt, "hospital": a["hospital"], "donation": donation,
                                    "category": category, "amount": part, "delivered_verified": True}))
    for o in override_log:
        events.append((o["minute"], 3, "OVERRIDE", "commander",
                       {"patient": lm.pseudonym(o["patient"], SALT), "from": o["from"], "to": o["to"],
                        "actor_id": "commander-01", "reason": o["reason"]}))
    events.sort(key=lambda e: (e[0], e[1]))
    window = 0
    for minute, _, rtype, role, payload in events:
        if minute // 15 > window and ledger.last()[0] > 0:
            ledger.seal_batch()
            window = minute // 15
        ledger.append(minute, rtype, role, payload)
    ledger.seal_batch()

    if demo_rejected_otp and allocations:          # the wrong-OTP attempt shown in Notebook 5, Step 6
        d = next(r for r in ledger.rows() if r["type"] == "DISPATCH" and r["payload"]["trip"] == "T001")
        ledger.append(d["minute"], "HANDOVER_REJECTED", "hospital",
                      {"trip": "T001", "hospital": d["payload"]["hospital"], "reason": "wrong OTP"})
        ledger.seal_batch()
    return ledger, signer, otps


def copy_of(ledger):
    c = lm.Ledger(":memory:", ledger.signer)
    ledger.db.backup(c.db)
    return c


def attack_edit(ledger, seq, factor=10):
    # Attack 1: an insider switches off protection and edits one payment
    c = copy_of(ledger)
    c.db.execute("DROP TRIGGER IF EXISTS ledger_no_update")
    r = next(x for x in c.rows() if x["seq"] == seq)
    fake = dict(r["payload"], amount=r["payload"].get("amount", 0) * factor)
    c.db.execute("UPDATE ledger SET payload = ? WHERE seq = ?", (lm.canonical(fake), seq))
    c.db.commit()
    return c, r["payload"].get("amount"), fake.get("amount")


def attack_rewrite(ledger, seq, factor=10):
    # Attack 2: an insider edits a payment, recomputes every hash, re-signs everything, rewrites batch roots
    c = copy_of(ledger)
    c.db.execute("DROP TRIGGER IF EXISTS ledger_no_update")
    prev = lm.GENESIS
    for r in c.rows():
        payload = r["payload"]
        if r["seq"] == seq:
            payload = dict(payload, amount=payload.get("amount", 0) * factor)
        body = {"seq": r["seq"], "minute": r["minute"], "type": r["type"], "actor_role": r["actor_role"], "payload": payload}
        h = lm.sha256_hex(lm.canonical(body) + prev)
        c.db.execute("UPDATE ledger SET payload=?, prev_hash=?, hash=?, signature=? WHERE seq=?",
                     (lm.canonical(payload), prev, h, c.signer.sign(r["actor_role"], h), r["seq"]))
        prev = h
    for bnum, root in c.recompute_roots().items():
        c.db.execute("UPDATE batches SET merkle_root=? WHERE batch=?", (root, bnum))
    c.db.commit()
    return c
