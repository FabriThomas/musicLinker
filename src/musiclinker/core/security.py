"""LAN IP resolution, pairing token validation, and loopback verification."""

from __future__ import annotations

import hmac
import secrets
import socket
from urllib.parse import quote

from fastapi import HTTPException, Request, status


def detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("10.255.255.255", 1))
            ip_addr = sock.getsockname()[0]
            if ip_addr and not ip_addr.startswith("127."):
                return ip_addr
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for addr in socket.gethostbyname_ex(hostname)[2]:
            if not addr.startswith("127."):
                return addr
    except Exception:
        pass
    return "127.0.0.1"


def generate_pair_token() -> str:
    return secrets.token_urlsafe(24)


def is_loopback_host(client_host: str | None) -> bool:
    if not client_host:
        return False
    return client_host in ("127.0.0.1", "::1", "localhost") or client_host.startswith("127.")


def verify_request_access(request: Request, expected_token: str, require_auth_on_lan: bool = True) -> None:
    client_host = request.client.host if request.client else None
    if is_loopback_host(client_host) or not require_auth_on_lan:
        return

    provided = (
        request.query_params.get("pair_token")
        or request.headers.get("X-Pair-Token")
        or request.cookies.get("ml_pair_token")
        or ""
    )
    if not provided or not hmac.compare_digest(provided, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="LAN pairing token required. Scan or paste the pairing URL from the MusicLinker Desktop App.",
        )


def build_pairing_url(port: int, pair_token: str) -> str:
    lan_ip = detect_lan_ip()
    return f"http://{lan_ip}:{port}/?pair_token={quote(pair_token)}"