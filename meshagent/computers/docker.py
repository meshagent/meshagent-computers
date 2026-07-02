import subprocess
import time
import asyncio

from .computer import ComputerContext


async def _async_check_output(*args, **kwargs):
    shell = kwargs.pop("shell", False)
    if shell:
        proc = await asyncio.create_subprocess_shell(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, args, output=stdout, stderr=stderr
        )
    return stdout


class DockerComputer:
    environment = "linux"
    dimensions = (1280, 720)  # Default fallback; will be updated in __enter__.

    def __init__(
        self,
        container_name="cua-sample-app",
        image="ghcr.io/openai/openai-cua-sample-app:latest",
        display=":99",
        port_mapping="5900:5900",
    ):
        self.container_name = container_name
        self.image = image
        self.display = display
        self.port_mapping = port_mapping

    async def __aenter__(self, context: ComputerContext):
        del context
        # Check if the container is running
        result = subprocess.run(
            ["docker", "ps", "-q", "-f", f"name={self.container_name}"],
            capture_output=True,
            text=True,
        )

        if not result.stdout.strip():
            raise RuntimeError(
                f"Container {self.container_name} is not running. Build and run with:\n"
                f"docker build -t {self.container_name} .\n"
                f"docker run --rm -it --name {self.container_name} "
                f"-p {self.port_mapping} -e DISPLAY={self.display} {self.container_name}"
            )

        # Fetch display geometry
        geometry = (
            await self._exec(f"DISPLAY={self.display} xdotool getdisplaygeometry")
        ).strip()
        if geometry:
            w, h = geometry.split()
            self.dimensions = (int(w), int(h))
        # print("Starting Docker container...")
        # # Run the container detached, removing it automatically when it stops
        # subprocess.check_call(
        #     [
        #         "docker",
        #         "run",
        #         "-d",
        #         "--rm",
        #         "--name",
        #         self.container_name,
        #         "-p",
        #         self.port_mapping,
        #         self.image,
        #     ]
        # )
        # # Give the container a moment to start
        # time.sleep(3)
        # print("Entering DockerComputer context")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # print("Stopping Docker container...")
        # subprocess.check_call(["docker", "stop", self.container_name])
        # print("Exiting DockerComputer context")
        pass

    async def _exec(self, cmd: str) -> str:
        """
        Run 'cmd' in the container.
        We wrap cmd in double quotes and escape any double quotes inside it,
        so spaces or quotes don't break the shell call.
        """
        # Escape any existing double quotes in cmd
        safe_cmd = cmd.replace('"', '\\"')

        # Then wrap the entire cmd in double quotes for `sh -c`
        docker_cmd = f'docker exec {self.container_name} sh -c "{safe_cmd}"'

        return (await _async_check_output(docker_cmd, shell=True)).decode(
            "utf-8", errors="ignore"
        )

    async def screenshot(self, context: ComputerContext) -> str:
        del context
        """
        Takes a screenshot with ImageMagick (import), returning base64-encoded PNG.
        Requires 'import'.
        """
        # cmd = (
        #     f"export DISPLAY={self.display} && "
        #     "import -window root /tmp/screenshot.png && "
        #     "base64 /tmp/screenshot.png"
        # )
        cmd = (
            f"export DISPLAY={self.display} && import -window root png:- | base64 -w 0"
        )

        return await self._exec(cmd)

    async def click(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
        button: str = "left",
    ) -> None:
        del context
        button_map = {"left": 1, "middle": 2, "right": 3}
        b = button_map.get(button, 1)
        await self._exec(f"DISPLAY={self.display} xdotool mousemove {x} {y} click {b}")

    async def double_click(self, context: ComputerContext, *, x: int, y: int) -> None:
        del context
        await self._exec(
            f"DISPLAY={self.display} xdotool mousemove {x} {y} click --repeat 2 1"
        )

    async def scroll(
        self,
        context: ComputerContext,
        *,
        x: int,
        y: int,
        scroll_x: int,
        scroll_y: int,
    ) -> None:
        del context
        """
        For simple vertical scrolling: xdotool click 4 (scroll up) or 5 (scroll down).
        """
        await self._exec(f"DISPLAY={self.display} xdotool mousemove {x} {y}")
        clicks = abs(scroll_y)
        button = 4 if scroll_y < 0 else 5
        for _ in range(clicks):
            await self._exec(f"DISPLAY={self.display} xdotool click {button}")

    async def type(self, context: ComputerContext, *, text: str) -> None:
        del context
        """
        Type the given text via xdotool, preserving spaces and quotes.
        """
        # Escape single quotes in the user text: ' -> '\'\''
        safe_text = text.replace("'", "'\\''")
        # Then wrap everything in single quotes for xdotool
        cmd = f"DISPLAY={self.display} xdotool type -- '{safe_text}'"
        await self._exec(cmd)

    async def wait(self, context: ComputerContext, *, ms: int = 1000) -> None:
        del context
        time.sleep(ms / 1000)

    async def move(self, context: ComputerContext, *, x: int, y: int) -> None:
        del context
        await self._exec(f"DISPLAY={self.display} xdotool mousemove {x} {y}")

    async def keypress(self, context: ComputerContext, *, keys: list[str]) -> None:
        del context
        mapping = {
            "ENTER": "Return",
            "LEFT": "Left",
            "RIGHT": "Right",
            "UP": "Up",
            "DOWN": "Down",
            "ESC": "Escape",
            "SPACE": "space",
            "BACKSPACE": "BackSpace",
            "TAB": "Tab",
        }
        mapped_keys = [mapping.get(key, key) for key in keys]
        combo = "+".join(mapped_keys)
        await self._exec(f"DISPLAY={self.display} xdotool key {combo}")

    async def drag(
        self,
        context: ComputerContext,
        *,
        path: list[dict[str, int]],
    ) -> None:
        del context
        if not path:
            return
        start_x = path[0]["x"]
        start_y = path[0]["y"]
        self._exec(
            f"DISPLAY={self.display} xdotool mousemove {start_x} {start_y} mousedown 1"
        )
        for point in path[1:]:
            await self._exec(
                f"DISPLAY={self.display} xdotool mousemove {point['x']} {point['y']}"
            )
        await self._exec(f"DISPLAY={self.display} xdotool mouseup 1")

    async def get_current_url(self, context: ComputerContext) -> str:
        del context
        raise RuntimeError("get_current_url is not supported by DockerComputer")

    async def goto(self, context: ComputerContext, *, url: str) -> None:
        del url
        del context
        raise RuntimeError("goto is not supported by DockerComputer")

    async def back(self, context: ComputerContext) -> None:
        del context
        raise RuntimeError("back is not supported by DockerComputer")

    async def forward(self, context: ComputerContext) -> None:
        del context
        raise RuntimeError("forward is not supported by DockerComputer")
