# server_a.py
from flask import Flask, request, jsonify
from util import *
import requests

app = Flask(__name__)

# Paths to A's keys and B's public key (pre-shared)
A_PRIV = "serverA_priv.key"
A_PUB_OF_B = "serverB_pub.key"  # A knows B's pubkey
B_URL = "http://127.0.0.1:5001/rw_hop"  # endpoint for server B

signing_key = load_signing_key(A_PRIV)
verify_key_b = load_verify_key(A_PUB_OF_B)

# simple replay protection store (in-memory for prototype)
seen_nonces = set()


@app.route("/start_rw", methods=["POST"])
def start_rw():
    data = request.json or {}
    start_node = data.get("start_node", "A0")
    max_hops = data.get("max_hops", 5)
    token = data.get("token", "valid_token")
    # decide next node for demo purposes; crossing community boundary -> go to B's node
    next_node = "B1" if max_hops > 0 else None
    payload = make_rw_message("serverA", start_node, next_node, max_hops, token)
    signature = sign_message(signing_key, payload)
    payload["signature"] = signature

    # Send to server B (cross-community hop)
    resp = requests.post(B_URL, json=payload)
    return jsonify({"sent_to_B": resp.json(), "payload": payload})


@app.route("/rw_hop", methods=["POST"])
def rw_hop():
    msg = request.json
    signature = msg.pop("signature", None)
    # verify from B? Here A receives maybe responses; in this demo A might not be target of cross hop
    return jsonify({"status": "ok_received_by_A", "received": msg})


if __name__ == "__main__":
    app.run(port=5000)
