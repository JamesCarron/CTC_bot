"""CTC_bot - triathlon club results tracker.

Discovers RaceClocker events from the admin console, parses their embedded
results payload, resolves athlete identity from claims, and renders
per-athlete trend lines.
"""

__version__ = "0.2.0"


def _use_system_certificates() -> None:
    """Verify TLS against the OS certificate store rather than certifi's bundle.

    This machine sits behind something that inspects TLS and presents its own
    root CA. That root is trusted by Windows (curl and browsers work) but is
    absent from certifi, so plain `requests` fails every HTTPS call with
    CERTIFICATE_VERIFY_FAILED - including to unrelated hosts.

    `truststore` points Python at the Windows certificate store, which fixes it
    without weakening verification. Certificates are still fully validated;
    only the trust source changes. Never disable verification instead.
    """
    try:
        import truststore
    except ImportError:  # optional - environments without a TLS proxy are fine
        return
    try:
        truststore.inject_into_ssl()
    except Exception:  # pragma: no cover - never block import over this
        pass


_use_system_certificates()
