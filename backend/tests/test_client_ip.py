"""요청자 IP: 같은 서버의 IIS 에서 온 요청일 때만 X-Forwarded-For 를 믿는다."""
import unittest

from starlette.requests import Request

from app.services.client_ip import client_ip


def req(client, xff=None):
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers,
                    "client": (client, 5000) if client else None, "query_string": b""})


class ClientIpTest(unittest.TestCase):
    def test_direct_request_ignores_header(self):
        self.assertEqual(client_ip(req("203.0.113.9", "1.2.3.4")), "203.0.113.9")

    def test_trusted_proxy_uses_last_entry(self):
        # 앞쪽 값은 고객이 넣을 수 있다. IIS 가 붙인 것은 마지막 값
        self.assertEqual(client_ip(req("127.0.0.1", "1.2.3.4, 198.51.100.7")), "198.51.100.7")

    def test_strips_port(self):
        self.assertEqual(client_ip(req("127.0.0.1", "198.51.100.7:51234")), "198.51.100.7")

    def test_ipv6_with_port(self):
        self.assertEqual(client_ip(req("::1", "[2001:db8::1]:443")), "2001:db8::1")

    def test_plain_ipv6(self):
        self.assertEqual(client_ip(req("127.0.0.1", "2001:db8::1")), "2001:db8::1")

    def test_garbage_header_falls_back_to_peer(self):
        self.assertEqual(client_ip(req("127.0.0.1", "not-an-ip")), "127.0.0.1")
        self.assertEqual(client_ip(req("127.0.0.1", "")), "127.0.0.1")
        self.assertEqual(client_ip(req("127.0.0.1", "1.2.3.4, ")), "127.0.0.1")

    def test_no_header(self):
        self.assertEqual(client_ip(req("127.0.0.1")), "127.0.0.1")

    def test_no_peer(self):
        self.assertEqual(client_ip(req(None, "1.2.3.4")), "")


if __name__ == "__main__":
    unittest.main()
