"""TLS context for NaloGO requests."""

import ssl

import httpx


def create_ssl_context() -> ssl.SSLContext:
    """Default httpx context, but TLS 1.3 key exchange limited to P-256.

    OpenSSL 3.5+ sends a large post-quantum key share in the ClientHello by default.
    On the SOCKS proxy -> lknpd.nalog.ru path such ClientHellos are intermittently
    dropped (EOF during handshake), so receipts get stuck in the queue. A P-256-only
    key share keeps TLS 1.3 and a small ClientHello.
    """
    ctx = httpx.create_ssl_context()
    ctx.set_ecdh_curve('prime256v1')
    return ctx
