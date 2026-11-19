"""요청자 IP 읽기 (로그인 기록용).

고객 요청은 같은 서버의 IIS 를 거쳐 온다. X-Forwarded-For 는 누구나 넣을 수 있는 머리말이므로
상대가 믿을 수 있는 프록시(config.TRUSTED_PROXIES)일 때만 쓰고, IIS 가 붙인 마지막 값만 본다.
"""
import ipaddress

from starlette.requests import Request

from ..config import TRUSTED_PROXIES


def _clean(value: str) -> str:
    """'1.2.3.4:5678' · '[2001:db8::1]:443' 에서 포트를 떼고, 올바른 IP 가 아니면 빈 글자."""
    v = (value or "").strip()
    if v.startswith("["):
        v = v[1:].split("]", 1)[0]
    elif v.count(":") == 1:
        v = v.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return ""


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if peer in TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for", "")
        ip = _clean(forwarded.split(",")[-1]) if forwarded else ""
        if ip:
            return ip
    return peer
