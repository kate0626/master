# util.py
import time
import json
import os
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError
import base64
import secrets


def load_signing_key(path):
    b = open(path, "rb").read()
    return SigningKey(b)


def load_verify_key(path):
    b = open(path, "rb").read()
    return VerifyKey(b)


def canonical_json(obj):
    # deterministic JSON serialization for signing
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def sign_message(signing_key, payload_dict):
    # payload_dict should not include 'signature'
    payload = payload_dict.copy()
    msg = canonical_json(payload).encode()
    sig = signing_key.sign(msg).signature  # bytes
    return base64.b64encode(sig).decode()


def verify_message(verify_key, payload_dict, signature_b64):
    payload = payload_dict.copy()
    msg = canonical_json(payload).encode()
    sig = base64.b64decode(signature_b64)
    try:
        verify_key.verify(msg, sig)
        return True
    except BadSignatureError:
        return False


def make_rw_message(
    origin_id, current_node, next_node, remaining_hops, token, attributes=None
):
    if attributes is None:
        attributes = {}
    payload = {
        "origin": origin_id,
        "current_node": current_node,
        "next_node": next_node,
        "remaining_hops": remaining_hops,
        "token": token,
        "attributes": attributes,
        "timestamp": int(time.time()),
        "nonce": secrets.token_hex(8),
    }
    return payload
