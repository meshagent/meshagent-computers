import subprocess

import pytest

from meshagent.computers import docker as docker_module
from meshagent.computers.docker import DockerComputer, _async_check_output


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


@pytest.mark.asyncio
async def test_docker_computer_aenter_awaits_exec_before_stripping_geometry(
    monkeypatch,
):
    class Result:
        stdout = "container-id\n"

    run_calls = []

    def fake_run(*args, **kwargs):
        run_calls.append((args, kwargs))
        return Result()

    async def fake_exec(self, cmd):
        exec_calls.append(cmd)
        return "1600 900\n"

    exec_calls = []
    monkeypatch.setattr(docker_module.subprocess, "run", fake_run)
    monkeypatch.setattr(DockerComputer, "_exec", fake_exec)

    computer = DockerComputer()
    result = await computer.__aenter__(context=object())

    assert result is computer
    assert computer.dimensions == (1600, 900)
    assert run_calls == [
        (
            (
                [
                    "docker",
                    "ps",
                    "-q",
                    "-f",
                    "name=cua-sample-app",
                ],
            ),
            {"capture_output": True, "text": True},
        )
    ]
    assert exec_calls == ["DISPLAY=:99 xdotool getdisplaygeometry"]
