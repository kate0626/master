# server_b.py
from flask import Flask, request, jsonify
from util import *
import time

app = Flask(__name__)

B_PRIV = "serverB_priv.key"
B_PUB_OF_A = "serverA_pub.key"  # B knows A's pubkey

verify_key_a = load_verify_key(B_PUB_OF_A)
signing_key_b = load_signing_key(B_PRIV)

seen_nonces = set()


@app.route("/rw_hop", methods=["POST"])
def rw_hop():
    msg = request.json
    signature = msg.get("signature")
    payload = {k: v for k, v in msg.items() if k != "signature"}
    # verify signature using A's public key
    ok = verify_message(verify_key_a, payload, signature)
    if not ok:
        return jsonify({"status": "bad_signature"}), 403

    # replay check: timestamp tolerance and nonce
    now = int(time.time())
    ts = payload.get("timestamp", 0)
    if abs(now - ts) > 30:
        return jsonify({"status": "timestamp_invalid"}), 403
    nonce = payload.get("nonce")
    if nonce in seen_nonces:
        return jsonify({"status": "replay_detected"}), 403
    seen_nonces.add(nonce)

    # token / attributes checks (very simple demo)
    if payload.get("token") != "valid_token":
        return jsonify({"status": "invalid_token"}), 403

    # accept and possibly continue RW locally / or send back result
    # (for demo, B will respond with ack and optionally sign an ack)
    ack = {"status": "accepted", "received_node": payload.get("next_node")}
    ack_sig = sign_message(signing_key_b, ack)
    ack["signature"] = ack_sig
    return jsonify(ack)


if __name__ == "__main__":
    app.run(port=5001)
