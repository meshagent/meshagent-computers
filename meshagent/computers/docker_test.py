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


@pytest.mark.asyncio
async def test_docker_computer_commands_match_python_source(monkeypatch) -> None:
    calls: list[tuple[tuple, dict]] = []

    async def fake_check_output(*args, **kwargs):
        calls.append((args, kwargs))
        return b"ok"

    monkeypatch.setattr(docker_module, "_async_check_output", fake_check_output)
    computer = DockerComputer()

    assert await computer._exec('echo "hi"') == "ok"
    assert calls.pop(0) == (
        ('docker exec cua-sample-app sh -c "echo \\"hi\\""',),
        {"shell": True},
    )

    assert await computer.screenshot(context=object()) == "ok"
    assert calls.pop(0) == (
        (
            'docker exec cua-sample-app sh -c "export DISPLAY=:99 && import -window root png:- | base64 -w 0"',
        ),
        {"shell": True},
    )

    await computer.click(context=object(), x=10, y=20, button="middle")
    await computer.click(context=object(), x=10, y=20, button="unknown")
    await computer.double_click(context=object(), x=3, y=4)
    await computer.scroll(context=object(), x=1, y=2, scroll_x=99, scroll_y=-2)
    await computer.type(context=object(), text="don't")
    await computer.move(context=object(), x=5, y=6)
    await computer.keypress(context=object(), keys=["ENTER", "SPACE", "x"])

    assert calls == [
        (
            (
                'docker exec cua-sample-app sh -c "DISPLAY=:99 xdotool mousemove 10 20 click 2"',
            ),
            {"shell": True},
        ),
        (
            (
                'docker exec cua-sample-app sh -c "DISPLAY=:99 xdotool mousemove 10 20 click 1"',
            ),
            {"shell": True},
        ),
        (
            (
                'docker exec cua-sample-app sh -c "DISPLAY=:99 xdotool mousemove 3 4 click --repeat 2 1"',
            ),
            {"shell": True},
        ),
        (
            ('docker exec cua-sample-app sh -c "DISPLAY=:99 xdotool mousemove 1 2"',),
            {"shell": True},
        ),
        (
            ('docker exec cua-sample-app sh -c "DISPLAY=:99 xdotool click 4"',),
            {"shell": True},
        ),
        (
            ('docker exec cua-sample-app sh -c "DISPLAY=:99 xdotool click 4"',),
            {"shell": True},
        ),
        (
            (
                "docker exec cua-sample-app sh -c \"DISPLAY=:99 xdotool type -- 'don'\\''t'\"",
            ),
            {"shell": True},
        ),
        (
            ('docker exec cua-sample-app sh -c "DISPLAY=:99 xdotool mousemove 5 6"',),
            {"shell": True},
        ),
        (
            (
                'docker exec cua-sample-app sh -c "DISPLAY=:99 xdotool key Return+space+x"',
            ),
            {"shell": True},
        ),
    ]
