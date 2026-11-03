"""Encrypt login provisioning to the runner's own key, separate from app keys."""

import base64
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings

from secondshift.runner.protocol import RunnerProtocolError


def key_padding() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def runner_private_key() -> rsa.RSAPrivateKey:
    root: Path = settings.AGENT_AUTH_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "provisioning-key.pem"
    if not path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        encoded = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        # Atomic publication: another worker never observes a half-written key.
        with tempfile.NamedTemporaryFile(
            dir=root, prefix="provisioning-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink()
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise RunnerProtocolError("Invalid agent provisioning key.")
    return key


def provisioning_public_key() -> str:
    return (
        runner_private_key()
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
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


def decrypt_agent_auth(envelope: str) -> str:
    try:
        payload = json.loads(envelope)
        envelope_key = runner_private_key().decrypt(
            base64.b64decode(payload["key"], validate=True), key_padding()
        )
        return Fernet(envelope_key).decrypt(payload["data"].encode()).decode()
    except (ValueError, TypeError, KeyError, InvalidToken):
        raise RunnerProtocolError(
            "Invalid encrypted agent credential bundle."
        ) from None
