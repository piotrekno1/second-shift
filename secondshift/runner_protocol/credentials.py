"""Encrypt a subscription login to the runner's provisioning key.

The runner publishes the public key and keeps the private one. Only the runner
can read what the control plane sends.
"""

import base64
import hashlib
import hmac
import json

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from secondshift.runner_protocol.sessions import RunnerProtocolError


def key_padding() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def encrypt_agent_auth(public_key: str, auth: str) -> str:
    key = serialization.load_pem_public_key(public_key.encode())
    if not isinstance(key, rsa.RSAPublicKey):
        raise RunnerProtocolError("Invalid agent provisioning public key.")
    envelope_key = Fernet.generate_key()
    return json.dumps(
        {
            "key": base64.b64encode(key.encrypt(envelope_key, key_padding())).decode(),
            "data": Fernet(envelope_key).encrypt(auth.encode()).decode(),
        }
    )


def key_fingerprint(public_key: str) -> str:
    """SHA-256 of the key's DER form, as operators compare it."""
    key = serialization.load_pem_public_key(public_key.encode())
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()


def same_key(first: str, second: str) -> bool:
    try:
        return hmac.compare_digest(key_fingerprint(first), key_fingerprint(second))
    except ValueError:
        return False
