"""The server's own certificate: made once with openssl (EC P-256, 10
years), its SHA-256 fingerprint (pinned by the helpers when they pair),
and the TLS context the server listens with."""

from __future__ import annotations

import hashlib
import os
import ssl
import subprocess
from pathlib import Path


def generate_certificate(tls_dir: Path, common_name: str) -> bool:
    cert, key = tls_dir / "cert.pem", tls_dir / "key.pem"
    if cert.exists() and key.exists():
        return False
    tls_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tls_dir, 0o700)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "3650", "-subj", f"/CN={common_name}"],
                   check=True, capture_output=True)
    os.chmod(key, 0o600)
    return True


def certificate_sha256(cert_path: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text())
    return hashlib.sha256(der).hexdigest().upper()


def display_fingerprint(sha256_hex: str) -> str:
    """The first 128 bits in 8 groups of 4, for a person to compare."""
    head = sha256_hex[:32]
    return "-".join(head[i:i + 4] for i in range(0, 32, 4))


def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert_path), str(key_path))
    return context
