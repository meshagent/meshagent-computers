import subprocess

import pytest

from meshagent.computers.docker import _async_check_output


@pytest.mark.asyncio
async def test_async_check_output_shell_executes_and_preserves_failure_fields():
    assert await _async_check_output("printf stdout", shell=True) == b"stdout"

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        await _async_check_output(
            "printf stdout; printf stderr >&2; exit 7",
            shell=True,
        )

    exc = exc_info.value
    assert exc.returncode == 7
    assert exc.cmd == ("printf stdout; printf stderr >&2; exit 7",)
    assert exc.output == b"stdout"
    assert exc.stderr == b"stderr"
