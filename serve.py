# /// script
# requires-python = ">=3.9"
# dependencies = ["cryptography>=42"]
# ///
"""
Serveur HTTPS local pour tester carto-pentes depuis l'iPhone sur le réseau local.

iOS Safari exige un « secure context » (HTTPS) pour Geolocation et
DeviceOrientation : en http://192.168.x.x les capteurs restent muets. Ce script
sert le dossier courant en HTTPS avec un certificat auto-signé (généré au premier
lancement) dont le SAN inclut les IP locales de la machine.

Usage :
    uv run serve.py            # port 8443 par défaut
    uv run serve.py 9000       # port personnalisé

Sur l'iPhone (même Wi-Fi) : ouvrir https://<IP-affichée>:8443/ dans Safari,
accepter l'avertissement de certificat (« Afficher les détails » → « visiter ce
site web ») une fois, puis « Activer les capteurs ».
"""

import datetime
import ipaddress
import socket
import ssl
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HERE = Path(__file__).resolve().parent
CERT = HERE / ".devcert.pem"
KEY = HERE / ".devkey.pem"


def local_ips():
    """IPv4 locales (hors loopback), pour le SAN du certificat et l'affichage."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    # Repli : route sortante (ne crée pas de trafic réel)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def ensure_cert():
    """Génère un certificat auto-signé si absent.

    Conforme aux exigences iOS/Safari pour les certificats serveur TLS, sinon
    Safari avorte la connexion (« la connexion réseau a été perdue ») :
      - validité ≤ 398 jours,
      - SAN avec les hôtes/IP,
      - ExtendedKeyUsage = serverAuth,
      - clé RSA ≥ 2048, signature SHA-256.
    """
    if CERT.exists() and KEY.exists():
        return
    print("Génération du certificat auto-signé (compatible iOS, clé EC P-256)…")
    # Clé ECDSA P-256 : le message « Certificate » de la poignée TLS reste petit
    # (~300 o vs ~1 ko en RSA-2048), ce qui évite les blocages de poignée TLS sur
    # les chemins réseau à MTU réduite (switches virtuels Hyper-V, VPN, PPPoE…).
    key = ec.generate_private_key(ec.SECP256R1())

    san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    for ip in local_ips():
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "carto-pentes-dev")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(days=397))  # iOS : ≤ 398 j
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True, content_commitment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    KEY.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def log_message(self, fmt, *args):  # journal compact
        sys.stderr.write("  %s\n" % (fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8443
    ensure_cert()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(CERT), keyfile=str(KEY))

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    print("\ncarto-pentes — serveur HTTPS local")
    print("  Local    : https://localhost:%d/" % port)
    for ip in local_ips():
        print("  iPhone   : https://%s:%d/   <-- ouvrir celle-ci dans Safari" % (ip, port))
    print("\n(Accepter l'avertissement de certificat une fois sur l'iPhone.)")
    print("Ctrl+C pour arrêter.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
