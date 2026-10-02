# SENTINEL ledger/ · Tamper-evident ledger
#   hash_n = SHA-256(record_n ‖ hash_{n−1}) · Ed25519 signature per actor role
#   Merkle root per batch → public anchor (Polygon Amoy testnet) · verifier · no personal data
import hashlib
import json
import sqlite3
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

GENESIS = "0" * 64


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text):
    return hashlib.sha256(text.encode()).hexdigest()


def pseudonym(real_id, salt, prefix="PT"):
    # One-way pseudonym: the ledger never stores the real ID, name or phone number
    return f"{prefix}-{sha256_hex(salt + real_id)[:10]}"


def otp_hash(otp, trip_id):
    # The ledger stores only a hash of the handover OTP, never the OTP itself
    return sha256_hex(f"{otp}:{trip_id}")


class Signer:
    # One Ed25519 key per actor role: optimizer, control room, hospital, finance, commander, donor portal
    def __init__(self):
        self.keys = {}

    def _key(self, role):
        if role not in self.keys:
            self.keys[role] = Ed25519PrivateKey.generate()
        return self.keys[role]

    def sign(self, role, message):
        return self._key(role).sign(message.encode()).hex()

    def save(self, path):
        from cryptography.hazmat.primitives import serialization as ser
        raw = {r: k.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption()).hex()
               for r, k in self.keys.items()}
        with open(path, "w") as f:
            json.dump(raw, f)

    def load(self, path):
        with open(path) as f:
            for r, h in json.load(f).items():
                self.keys[r] = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(h))
        return self

    def verify(self, role, message, signature_hex):
        try:
            self._key(role).public_key().verify(bytes.fromhex(signature_hex), message.encode())
            return True
        except Exception:
            return False


def merkle_root(hashes):
    level = list(hashes) or [GENESIS]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256_hex(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(hashes, index):
    # The sibling hashes needed to recompute the root from one record
    proof, level, i = [], list(hashes), index
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sib = i ^ 1
        proof.append({"hash": level[sib], "side": "left" if sib < i else "right"})
        level = [sha256_hex(level[k] + level[k + 1]) for k in range(0, len(level), 2)]
        i //= 2
    return proof


def verify_proof(leaf, proof, root):
    h = leaf
    for step in proof:
        h = sha256_hex(step["hash"] + h) if step["side"] == "left" else sha256_hex(h + step["hash"])
    return h == root


SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    seq INTEGER PRIMARY KEY, minute REAL, type TEXT, actor_role TEXT,
    payload TEXT, prev_hash TEXT, hash TEXT, signature TEXT);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
    BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS batches (
    batch INTEGER PRIMARY KEY, first_seq INTEGER, last_seq INTEGER, merkle_root TEXT, anchor TEXT);
"""


class Ledger:
    def __init__(self, path, signer):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.signer = signer

    def last(self):
        row = self.db.execute("SELECT seq, hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return row if row else (0, GENESIS)

    def append(self, minute, rtype, actor_role, payload):
        seq, prev = self.last()
        body = {"seq": seq + 1, "minute": round(float(minute), 1), "type": rtype,
                "actor_role": actor_role, "payload": payload}
        h = sha256_hex(canonical(body) + prev)                 # hash_n = SHA-256(record_n ‖ hash_{n−1})
        sig = self.signer.sign(actor_role, h)
        self.db.execute("INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?)",
                        (seq + 1, body["minute"], rtype, actor_role, canonical(payload), prev, h, sig))
        self.db.commit()
        return seq + 1, h

    def rows(self):
        cur = self.db.execute("SELECT seq, minute, type, actor_role, payload, prev_hash, hash, signature FROM ledger ORDER BY seq")
        return [{"seq": r[0], "minute": r[1], "type": r[2], "actor_role": r[3], "payload": json.loads(r[4]),
                 "prev_hash": r[5], "hash": r[6], "signature": r[7]} for r in cur]

    def verify_chain(self):
        # Recompute every hash, check every link and every signature
        prev = GENESIS
        for r in self.rows():
            body = {"seq": r["seq"], "minute": r["minute"], "type": r["type"],
                    "actor_role": r["actor_role"], "payload": r["payload"]}
            if r["prev_hash"] != prev:
                return False, r["seq"], "link to previous record broken"
            if sha256_hex(canonical(body) + prev) != r["hash"]:
                return False, r["seq"], "record content does not match its hash"
            if not self.signer.verify(r["actor_role"], r["hash"], r["signature"]):
                return False, r["seq"], "signature invalid"
            prev = r["hash"]
        return True, None, "all records, links and signatures valid"

    def seal_batch(self):
        # Merkle root over all records since the last batch
        last = self.db.execute("SELECT MAX(last_seq) FROM batches").fetchone()[0] or 0
        rows = [r for r in self.rows() if r["seq"] > last]
        if not rows:
            return None
        root = merkle_root([r["hash"] for r in rows])
        n = (self.db.execute("SELECT COUNT(*) FROM batches").fetchone()[0] or 0) + 1
        self.db.execute("INSERT INTO batches VALUES (?,?,?,?,?)", (n, rows[0]["seq"], rows[-1]["seq"], root, None))
        self.db.commit()
        return n, root

    def batches(self):
        cur = self.db.execute("SELECT batch, first_seq, last_seq, merkle_root, anchor FROM batches ORDER BY batch")
        return [{"batch": r[0], "first_seq": r[1], "last_seq": r[2], "merkle_root": r[3], "anchor": r[4]} for r in cur]

    def recompute_roots(self):
        rows = {r["seq"]: r["hash"] for r in self.rows()}
        return {b["batch"]: merkle_root([rows[s] for s in range(b["first_seq"], b["last_seq"] + 1) if s in rows])
                for b in self.batches()}

    def proof_for(self, seq):
        rows = {r["seq"]: r["hash"] for r in self.rows()}
        for b in self.batches():
            if b["first_seq"] <= seq <= b["last_seq"]:
                hashes = [rows[s] for s in range(b["first_seq"], b["last_seq"] + 1)]
                return rows[seq], merkle_proof(hashes, seq - b["first_seq"]), b
        return None


def role_view(role, record, viewer_hospital=None, viewer_donation=None):
    # Privacy controls: the same record looks different to each role
    p = record["payload"]
    if role == "Commander":
        return record
    if role == "Hospital":
        if p.get("hospital") == viewer_hospital:
            return record
        return {"seq": record["seq"], "type": record["type"], "hidden": "another hospital's record"}
    if role == "Donor":
        if p.get("donation") == viewer_donation:
            keep = {k: p[k] for k in ["donation", "category", "amount", "trip", "hospital", "delivered_verified"] if k in p}
            return {"seq": record["seq"], "type": record["type"], "payload": keep, "hash": record["hash"]}
        return {"seq": record["seq"], "type": record["type"], "hidden": "not your donation"}
    return {"seq": record["seq"], "type": record["type"], "minute": record["minute"], "hash": record["hash"][:16] + "…"}


# ---------- Public anchor on Polygon Amoy testnet ----------
AMOY_RPCS = [                                   # tried in order until one answers
    "https://polygon-amoy.drpc.org",            # listed in Polygon's own docs
    "https://polygon-amoy-bor-rpc.publicnode.com",
    "https://polygon-amoy.api.onfinality.io/public",
    "https://rpc-amoy.polygon.technology",
]
AMOY_CHAIN_ID = 80002
AMOY_EXPLORER = "https://amoy.polygonscan.com/tx/"


def connect_amoy(verbose=False):
    from web3 import Web3
    for url in AMOY_RPCS:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))
            if w3.is_connected() and w3.eth.chain_id == AMOY_CHAIN_ID:
                if verbose:
                    print(f"  ✅ {url}")
                return w3
            if verbose:
                print(f"  ❌ {url} (no answer)")
        except Exception as e:
            if verbose:
                print(f"  ❌ {url} ({str(e)[:60]})")
    return None


def anchor_on_amoy(private_key_hex, roots_hex):
    # Sends ONE transaction to yourself whose data field holds all batch Merkle roots
    from eth_account import Account
    w3 = connect_amoy()
    if w3 is None:
        return None, "could not reach the Polygon Amoy network on any public RPC"
    acct = Account.from_key(private_key_hex)
    if w3.eth.get_balance(acct.address) == 0:
        return None, "wallet has 0 test POL: get free test POL from a faucet first"
    base = w3.eth.get_block("latest").get("baseFeePerGas", w3.to_wei(30, "gwei"))
    tip = max(w3.eth.max_priority_fee, w3.to_wei(30, "gwei"))
    tx = {"to": acct.address, "value": 0, "data": "0x" + "".join(roots_hex), "chainId": AMOY_CHAIN_ID,
          "nonce": w3.eth.get_transaction_count(acct.address),
          "maxPriorityFeePerGas": tip, "maxFeePerGas": 2 * base + tip}
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    return tx_hash, "anchored"


def read_anchor_from_amoy(tx_hash):
    # Reads the roots back from the public blockchain (anyone can do this)
    w3 = connect_amoy()
    if w3 is None:
        raise ConnectionError("could not reach the Polygon Amoy network")
    data = w3.eth.get_transaction(tx_hash)["input"]
    data = data.hex() if hasattr(data, "hex") else str(data)
    data = data[2:] if data.startswith("0x") else data
    return [data[i:i + 64] for i in range(0, len(data), 64)]
