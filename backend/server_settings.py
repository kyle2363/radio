"""Explicit server exposure policy; secrets stay out of DB/UI settings exports."""
from dataclasses import dataclass
import ipaddress
import os
from urllib.parse import urlsplit

from api_security import ApiSecurity, SecuritySettings


@dataclass(frozen=True)
class ServerSettings:
    bind_host: str
    security: SecuritySettings
    tls_cert_file: str | None = None
    tls_key_file: str | None = None


def load_server_settings(env=None):
    env = os.environ if env is None else env
    host = env.get("RADIO_BIND_HOST", "127.0.0.1").strip()
    if not host:
        raise ValueError("RADIO_BIND_HOST must be explicit and nonempty")
    password = env.get("RADIO_PASSWORD", "")
    secret = env.get("RADIO_AUTH_SECRET", "")
    public_origin = env.get("RADIO_PUBLIC_ORIGIN", "").strip()
    cert = env.get("RADIO_TLS_CERT") or None
    key = env.get("RADIO_TLS_KEY") or None
    if bool(cert) != bool(key):
        raise ValueError("Configure both RADIO_TLS_CERT and RADIO_TLS_KEY")
    try:
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    exposed = not local or bool(public_origin)
    if exposed and not (password and secret):
        raise ValueError("Network/proxy access requires RADIO_PASSWORD and RADIO_AUTH_SECRET")
    if exposed and not (cert or public_origin or env.get("RADIO_ALLOW_INSECURE_LAN") == "1"):
        raise ValueError("Configure HTTPS or explicitly opt into plain LAN HTTP")
    hosts = tuple(value.strip() for value in env.get("RADIO_ALLOWED_HOSTS", "localhost,127.0.0.1,::1").split(",") if value.strip())
    if public_origin:
        public_host = urlsplit(public_origin).hostname
        if public_host and public_host not in hosts:
            hosts = (*hosts, public_host)
    origins = tuple(value.strip() for value in env.get("RADIO_TRUSTED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if value.strip())
    security = SecuritySettings(password=password, signing_secret=secret, allowed_hosts=hosts,
                                trusted_origins=origins, public_origin=public_origin,
                                public_get_paths=("/", "/assets/"))
    ApiSecurity(security)  # Fail before binding if security configuration is invalid.
    return ServerSettings(host, security, cert, key)
