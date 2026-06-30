import pytest

from meshagent.computers import utils as utils_module


@pytest.mark.parametrize(
    "url",
    [
        "https://maliciousbook.com",
        "https://login.maliciousbook.com/path",
        "https://evilvideos.com/watch?v=1",
        "https://user:pass@evilvideos.com:443/path",
        "//evilvideos.com/path",
    ],
)
def test_check_blocklisted_url_rejects_blocklisted_domains(url: str) -> None:
    with pytest.raises(ValueError, match="Blocked URL"):
        utils_module.check_blocklisted_url(url)


def test_check_blocklisted_url_allows_other_domains() -> None:
    utils_module.check_blocklisted_url("https://example.com/path")


@pytest.mark.parametrize(
    "url",
    [
        "EVILVIDEOS.COM/path",
        "evilvideos.com/path",
        "http://evilvideos.com\\@example.com/path",
        "http://sub.ilanbigio.com.",
        "http:// evilvideos.com /path",
    ],
)
def test_check_blocklisted_url_preserves_urlparse_hostname_edges(url: str) -> None:
    utils_module.check_blocklisted_url(url)


def test_check_blocklisted_url_raises_urlparse_errors() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        utils_module.check_blocklisted_url("http://[::1")
