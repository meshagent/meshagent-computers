import base64

import pytest

from meshagent.computers import utils as utils_module


@pytest.mark.parametrize(
    "url",
    [
        "https://maliciousbook.com",
        "https://login.maliciousbook.com/path",
        "https://evilvideos.com/watch?v=1",
        "https://EVILVIDEOS.COM/watch?v=1",
        "https://user:pass@evilvideos.com:443/path",
        "https://user@sub.ilanbigio.com/path",
        "https://evilvideos.com:bad/path",
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


@pytest.mark.parametrize(
    "url",
    [
        "http://exa\u2100mple.com",
        "http://exa\uff0fmple.com",
        "http://exa\uff1fmple.com",
        "http://exa\uff03mple.com",
        "http://user\uff20host.com",
    ],
)
def test_check_blocklisted_url_preserves_nfkc_netloc_validation(url: str) -> None:
    with pytest.raises(
        ValueError, match="contains invalid characters under NFKC normalization"
    ):
        utils_module.check_blocklisted_url(url)


@pytest.mark.parametrize("value", [None, [], "x", 3])
def test_sanitize_message_non_dict_inputs_raise_python_get_error(value) -> None:
    with pytest.raises(AttributeError, match="object has no attribute 'get'"):
        utils_module.sanitize_message(value)


@pytest.mark.parametrize(
    "payload",
    [
        bytes([0, 0, 1, 0, 1, 0, 16, 10, 0, 0, 1, 0, 32, 0, 4, 0, 0, 0, 22, 0, 0, 0])
        + b"abcd",
        bytes([0, 0, 2, 0, 1, 0, 16, 10, 0, 0, 1, 0, 32, 0, 4, 0, 0, 0, 22, 0, 0, 0])
        + b"abcd",
        bytes([0, 0, 1, 0, 1, 0, 16, 10, 0, 0, 1, 0, 32, 0, 4, 0, 0, 0, 100, 0, 0, 0])
        + b"abcd",
    ],
)
def test_calculate_image_dimensions_rejects_invalid_ico_payloads(
    payload: bytes,
) -> None:
    with pytest.raises(Exception):
        utils_module.calculate_image_dimensions(base64.b64encode(payload).decode())
