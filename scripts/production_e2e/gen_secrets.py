"""Write a test CA, a certificate for the fake services, and a GitHub App key."""

import datetime
import ipaddress  # noqa: F401
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
now = datetime.datetime.now(datetime.UTC)


def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


ca_key = key()
ca_name = x509.Name(
    [x509.NameAttribute(NameOID.COMMON_NAME, "SecondShift E2E Test CA")]
)
ca = (
    x509.CertificateBuilder()
    .subject_name(ca_name)
    .issuer_name(ca_name)
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(days=1))
    .not_valid_after(now + datetime.timedelta(days=7))
    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
    .add_extension(
        x509.KeyUsage(False, False, False, False, False, True, True, False, False),
        critical=True,
    )
    .add_extension(
        x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
    )
    .sign(ca_key, hashes.SHA256())
)
leaf_key = key()
names = ["api.github.com", "github.com", "model.e2e.test", "hooks.e2e.test"]
leaf = (
    x509.CertificateBuilder()
    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
    .issuer_name(ca_name)
    .public_key(leaf_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(days=1))
    .not_valid_after(now + datetime.timedelta(days=7))
    .add_extension(
        x509.SubjectAlternativeName([x509.DNSName(name) for name in names]),
        critical=False,
    )
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(
        x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
        critical=False,
    )
    .add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
        critical=False,
    )
    .sign(ca_key, hashes.SHA256())
)
pem = serialization.Encoding.PEM
(out / "ca.pem").write_bytes(ca.public_bytes(pem))
(out / "fake.pem").write_bytes(leaf.public_bytes(pem))
(out / "fake.key").write_bytes(
    leaf_key.private_bytes(
        pem,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
)
app_key = key().private_bytes(
    pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
)
# The deployment guide's format: one double-quoted line with \n escapes.
(out / "github_app_key.env").write_text(
    'SECONDSHIFT_GITHUB_APP_PRIVATE_KEY="'
    + app_key.decode().strip().replace("\n", "\\n")
    + '"\n'
)
