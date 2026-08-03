"""
One-time self-signed TLS cert for the manager's HTTPS listener. Fine for a
closed LAN where the 5 client agents are configured to pin this exact cert
(via ca_cert_path in each agent's config.json) -- there's no public CA
involved, and no browser trust needed since only the agents talk HTTPS here
(the dashboard itself can stay on plain HTTP on localhost, see README).

Run once: py generate_cert.py [extra-hostname-or-ip ...]
Re-run any time to rotate; agents just need the new cert.pem re-copied.
"""

import ipaddress
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from paths import base_dir

CERT_DIR = base_dir() / "certs"


def generate(extra_names=(), force=False) -> Path:
    """Writes certs/cert.pem + key.pem, returns the cert path. Callable both
    from the CLI below and directly from the installer GUI (no input())."""
    CERT_DIR.mkdir(exist_ok=True)
    cert_path = CERT_DIR / "cert.pem"
    key_path = CERT_DIR / "key.pem"

    if (cert_path.exists() or key_path.exists()) and not force:
        raise FileExistsError(f"{cert_path.name}/{key_path.name} already exist in {CERT_DIR}")

    hostname = socket.gethostname()
    san_names = [x509.DNSName(hostname), x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    for extra in extra_names:
        try:
            san_names.append(x509.IPAddress(ipaddress.ip_address(extra)))
        except ValueError:
            san_names.append(x509.DNSName(extra))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path


def main():
    cert_path = CERT_DIR / "cert.pem"
    key_path = CERT_DIR / "key.pem"
    if cert_path.exists() or key_path.exists():
        answer = input(f"{cert_path.name}/{key_path.name} already exist in {CERT_DIR} -- overwrite? [y/N] ")
        if answer.strip().lower() != "y":
            print("aborted")
            return

    result = generate(extra_names=sys.argv[1:], force=True)
    hostname = socket.gethostname()
    print(f"wrote {result} and {key_path}")
    print(f"cert is valid for: {hostname}, localhost, 127.0.0.1" + (f", {', '.join(sys.argv[1:])}" if len(sys.argv) > 1 else ""))
    print("copy cert.pem to each client_agent install and set ca_cert_path in config.json")


if __name__ == "__main__":
    main()
