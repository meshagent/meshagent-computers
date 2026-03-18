import pytest

from meshagent.computers import utils as utils_module


@pytest.mark.parametrize(
    "url",
    [
        "https://maliciousbook.com",
        "https://login.maliciousbook.com/path",
        "https://evilvideos.com/watch?v=1",
    ],
)
def test_check_blocklisted_url_rejects_blocklisted_domains(url: str) -> None:
    with pytest.raises(ValueError, match="Blocked URL"):
        utils_module.check_blocklisted_url(url)


def test_check_blocklisted_url_allows_other_domains() -> None:
    utils_module.check_blocklisted_url("https://example.com/path")
