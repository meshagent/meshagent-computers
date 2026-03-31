from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import socket
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx
from playwright.async_api import Browser, Page
import playwright

from meshagent.api.room_server_client import RoomClient
from meshagent.api.websocket_protocol import WebSocketClientProtocol

from .base_playwright import BasePlaywrightComputer
from .computer import ComputerContext

if TYPE_CHECKING:
    from stagehand import AsyncStagehand
    from stagehand.types.session_start_params import Browser as StagehandBrowserConfig
    from stagehand.types.session_start_params import (
        BrowserbaseSessionCreateParams as StagehandBrowserbaseSessionCreateParams,
    )
else:
    AsyncStagehand = Any
    StagehandBrowserConfig = dict[str, Any]
    StagehandBrowserbaseSessionCreateParams = dict[str, Any]


_STAGEHAND_ENV_LOCK = asyncio.Lock()
_CDP_URL_ATTEMPTS = 40
_CDP_URL_TIMEOUT_SECONDS = 1.0
_CDP_URL_RETRY_DELAY_SECONDS = 0.1
_PLAYWRIGHT_BROWSERS_PATH_ENV_VAR = "PLAYWRIGHT_BROWSERS_PATH"
_PLAYWRIGHT_DEFAULT_BROWSERS_DIR_NAME = "ms-playwright"
_PLAYWRIGHT_BROWSERS_JSON_PATH = (
    Path(playwright.__file__).resolve().parent / "driver" / "package" / "browsers.json"
)
_PLAYWRIGHT_EXECUTABLE_PATHS: dict[str, dict[str, tuple[str, ...]]] = {
    "chromium": {
        "linux-x64": ("chrome-linux64", "chrome"),
        "linux-arm64": ("chrome-linux", "chrome"),
        "mac-x64": (
            "chrome-mac-x64",
            "Google Chrome for Testing.app",
            "Contents",
            "MacOS",
            "Google Chrome for Testing",
        ),
        "mac-arm64": (
            "chrome-mac-arm64",
            "Google Chrome for Testing.app",
            "Contents",
            "MacOS",
            "Google Chrome for Testing",
        ),
        "win-x64": ("chrome-win64", "chrome.exe"),
    },
    "chromium-headless-shell": {
        "linux-x64": ("chrome-headless-shell-linux64", "chrome-headless-shell"),
        "linux-arm64": ("chrome-linux", "headless_shell"),
        "mac-x64": ("chrome-headless-shell-mac-x64", "chrome-headless-shell"),
        "mac-arm64": ("chrome-headless-shell-mac-arm64", "chrome-headless-shell"),
        "win-x64": ("chrome-headless-shell-win64", "chrome-headless-shell.exe"),
    },
}


def _playwright_registry_directory() -> Path:
    env_defined = os.getenv(_PLAYWRIGHT_BROWSERS_PATH_ENV_VAR)
    if env_defined == "0":
        return _PLAYWRIGHT_BROWSERS_JSON_PATH.parent.parent.parent / ".local-browsers"
    if isinstance(env_defined, str) and env_defined.strip() != "":
        return Path(env_defined).expanduser()

    home = Path.home()
    if sys_platform := platform.system():
        if sys_platform == "Linux":
            cache_home = os.getenv("XDG_CACHE_HOME")
            if isinstance(cache_home, str) and cache_home.strip() != "":
                return (
                    Path(cache_home).expanduser()
                    / _PLAYWRIGHT_DEFAULT_BROWSERS_DIR_NAME
                )
            return home / ".cache" / _PLAYWRIGHT_DEFAULT_BROWSERS_DIR_NAME
        if sys_platform == "Darwin":
            return home / "Library" / "Caches" / _PLAYWRIGHT_DEFAULT_BROWSERS_DIR_NAME
        if sys_platform == "Windows":
            local_appdata = os.getenv("LOCALAPPDATA")
            if isinstance(local_appdata, str) and local_appdata.strip() != "":
                return (
                    Path(local_appdata).expanduser()
                    / _PLAYWRIGHT_DEFAULT_BROWSERS_DIR_NAME
                )
            return home / "AppData" / "Local" / _PLAYWRIGHT_DEFAULT_BROWSERS_DIR_NAME

    raise RuntimeError(f"unsupported platform for Playwright registry: {sys_platform}")


def _playwright_host_platform() -> str | None:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        if machine in {"x86_64", "amd64"}:
            return "linux-x64"
        if machine in {"arm64", "aarch64"}:
            return "linux-arm64"
        return None
    if system == "Darwin":
        if machine in {"x86_64", "amd64"}:
            return "mac-x64"
        if machine in {"arm64", "aarch64"}:
            return "mac-arm64"
        return None
    if system == "Windows":
        if machine in {"x86_64", "amd64", "arm64", "aarch64"}:
            return "win-x64"
        return None
    return None


def _playwright_browser_revision(*, browser_name: str) -> str | None:
    if not _PLAYWRIGHT_BROWSERS_JSON_PATH.exists():
        return None
    payload = json.loads(_PLAYWRIGHT_BROWSERS_JSON_PATH.read_text())
    browsers = payload.get("browsers")
    if not isinstance(browsers, list):
        return None
    for browser in browsers:
        if not isinstance(browser, dict):
            continue
        if browser.get("name") != browser_name:
            continue
        revision = browser.get("revision")
        if isinstance(revision, str) and revision.strip() != "":
            return revision
    return None


def _playwright_managed_browser_executable_path(*, browser_name: str) -> Path | None:
    host_platform = _playwright_host_platform()
    if host_platform is None:
        return None
    revision = _playwright_browser_revision(browser_name=browser_name)
    if revision is None:
        return None
    tokens = _PLAYWRIGHT_EXECUTABLE_PATHS.get(browser_name, {}).get(host_platform)
    if tokens is None:
        return None
    browser_dir = (
        _playwright_registry_directory()
        / f"{browser_name.replace('-', '_')}-{revision}"
    )
    return browser_dir.joinpath(*tokens)


def _effective_local_chrome_path(*, local_chrome_path: str | None) -> str | None:
    if isinstance(local_chrome_path, str) and local_chrome_path.strip() != "":
        return local_chrome_path
    chrome_path = os.getenv("CHROME_PATH")
    if isinstance(chrome_path, str) and chrome_path.strip() != "":
        return chrome_path
    return None


def _playwright_local_browser_available(
    *,
    local_headless: bool,
    local_chrome_path: str | None,
) -> bool:
    configured_path = _effective_local_chrome_path(local_chrome_path=local_chrome_path)
    if configured_path is not None:
        return Path(configured_path).expanduser().exists()

    browser_name = "chromium-headless-shell" if local_headless else "chromium"
    executable_path = _playwright_managed_browser_executable_path(
        browser_name=browser_name
    )
    return executable_path is not None and executable_path.exists()


def _stagehand_sea_binary_available() -> bool:
    try:
        from stagehand.lib.sea_binary import resolve_binary_path
    except (ImportError, ModuleNotFoundError):
        return False

    try:
        resolve_binary_path()
    except FileNotFoundError:
        return False

    return True


def stagehand_available(
    *,
    local_headless: bool = True,
    local_chrome_path: str | None = None,
) -> bool:
    try:
        import stagehand  # noqa: F401
    except ModuleNotFoundError:
        return False
    return _stagehand_sea_binary_available() and _playwright_local_browser_available(
        local_headless=local_headless,
        local_chrome_path=local_chrome_path,
    )


def _require_stagehand_class() -> type[AsyncStagehand]:
    from stagehand import AsyncStagehand as _AsyncStagehand

    return _AsyncStagehand


@dataclass(frozen=True)
class StagehandComputerConfig:
    model_name: str = "openai/gpt-5.4"
    server: Literal["local", "remote"] = "local"
    browserbase_api_key: str | None = None
    browserbase_project_id: str | None = None
    browser: StagehandBrowserConfig | None = None
    browserbase_session_create_params: (
        StagehandBrowserbaseSessionCreateParams | None
    ) = None
    browserbase_session_id: str | None = None
    dom_settle_timeout_ms: float | None = None
    experimental: bool | None = None
    self_heal: bool | None = None
    system_prompt: str | None = None
    verbose: Literal[0, 1, 2] | None = None
    local_host: str = "127.0.0.1"
    local_port: int = 0
    local_headless: bool = True
    local_chromium_sandbox: bool = False
    local_chrome_path: str | None = None
    local_ready_timeout_s: float = 30.0
    local_shutdown_on_close: bool = True
    timeout: float | None = None
    max_retries: int = 2


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _room_openai_base_url(*, room: RoomClient) -> str:
    if isinstance(room.protocol, WebSocketClientProtocol):
        base_url = room.protocol.url
    else:
        base_url = room.room_url

    if base_url.startswith("ws://"):
        base_url = "http://" + base_url[len("ws://") :]
    elif base_url.startswith("wss://"):
        base_url = "https://" + base_url[len("wss://") :]

    return base_url.rstrip("/") + "/openai/v1"


@contextmanager
def _temporary_environment(overrides: dict[str, str | None]):
    previous: dict[str, str | None] = {}
    for name, value in overrides.items():
        previous[name] = os.environ.get(name)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class StagehandComputer(BasePlaywrightComputer):
    def __init__(
        self,
        *,
        dimensions: tuple[int, int] | None = None,
        starting_url: str | None = None,
        stagehand_config: StagehandComputerConfig | None = None,
    ) -> None:
        super().__init__(dimensions=dimensions, starting_url=starting_url)
        self._stagehand_config = stagehand_config or StagehandComputerConfig()
        self._stagehand: AsyncStagehand | None = None
        self._stagehand_session_id: str | None = None

    @property
    def stagehand_config(self) -> StagehandComputerConfig:
        return self._stagehand_config

    def update_stagehand_config(
        self,
        *,
        config: StagehandComputerConfig | None = None,
        **changes: Any,
    ) -> None:
        if config is not None and changes:
            raise ValueError("pass config or keyword changes, not both")
        if config is not None:
            self._stagehand_config = config
            return
        if changes:
            self._stagehand_config = replace(self._stagehand_config, **changes)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self._close_stagehand()
        finally:
            await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _close_stagehand(self) -> None:
        if self._stagehand is None:
            self._stagehand_session_id = None
            return

        session_id = self._stagehand_session_id
        stagehand = self._stagehand
        self._stagehand = None
        self._stagehand_session_id = None

        with contextlib.suppress(Exception):
            if isinstance(session_id, str) and session_id.strip() != "":
                await stagehand.sessions.end(session_id)

        with contextlib.suppress(Exception):
            await stagehand.close()

    async def _resolve_cdp_ws_url(self, *, port: int) -> str:
        url = f"http://127.0.0.1:{port}/json/version"
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=_CDP_URL_TIMEOUT_SECONDS) as client:
            for _ in range(_CDP_URL_ATTEMPTS):
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    payload = response.json()
                    websocket_debugger_url = payload.get("webSocketDebuggerUrl")
                    if isinstance(websocket_debugger_url, str):
                        return websocket_debugger_url
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                await asyncio.sleep(_CDP_URL_RETRY_DELAY_SECONDS)

        raise RuntimeError(
            f"Unable to resolve CDP websocket URL from {url}. Last error: {last_error}"
        )

    def _runtime_stagehand_kwargs(self, *, context: ComputerContext) -> dict[str, Any]:
        config = self._stagehand_config
        room = context.room
        token = room.protocol.token
        return {
            "browserbase_api_key": config.browserbase_api_key or "local",
            "browserbase_project_id": config.browserbase_project_id or "local",
            "model_api_key": token,
            "server": config.server,
            "local_host": config.local_host,
            "local_port": config.local_port,
            "local_headless": config.local_headless,
            "local_chrome_path": config.local_chrome_path,
            "local_ready_timeout_s": config.local_ready_timeout_s,
            "local_openai_api_key": token,
            "local_shutdown_on_close": config.local_shutdown_on_close,
            "timeout": config.timeout,
            "max_retries": config.max_retries,
        }

    def _runtime_stagehand_start_kwargs(
        self,
        *,
        browser: StagehandBrowserConfig | None,
    ) -> dict[str, Any]:
        config = self._stagehand_config
        start_kwargs: dict[str, Any] = {
            "model_name": config.model_name,
        }
        if browser is not None:
            start_kwargs["browser"] = browser
        if config.browserbase_session_create_params is not None:
            start_kwargs["browserbase_session_create_params"] = (
                config.browserbase_session_create_params
            )
        if config.browserbase_session_id is not None:
            start_kwargs["browserbase_session_id"] = config.browserbase_session_id
        if config.dom_settle_timeout_ms is not None:
            start_kwargs["dom_settle_timeout_ms"] = config.dom_settle_timeout_ms
        if config.experimental is not None:
            start_kwargs["experimental"] = config.experimental
        if config.self_heal is not None:
            start_kwargs["self_heal"] = config.self_heal
        if config.system_prompt is not None:
            start_kwargs["system_prompt"] = config.system_prompt
        if config.verbose is not None:
            start_kwargs["verbose"] = config.verbose
        return start_kwargs

    def _default_local_browser_config(self, *, cdp_url: str) -> StagehandBrowserConfig:
        width, height = self.dimensions
        config = cast(
            dict[str, Any],
            {
                "type": "local",
                "launchOptions": {
                    "cdpUrl": cdp_url,
                    "headless": self._stagehand_config.local_headless,
                    "viewport": {"width": width, "height": height},
                },
            },
        )
        if self._stagehand_config.browser is None:
            return cast(StagehandBrowserConfig, config)

        merged_browser = dict(cast(dict[str, Any], self._stagehand_config.browser))
        merged_launch_options = dict(config["launchOptions"])
        provided_launch_options = merged_browser.get("launchOptions")
        if isinstance(provided_launch_options, dict):
            merged_launch_options.update(provided_launch_options)
        merged_launch_options["cdpUrl"] = cdp_url
        merged_browser["type"] = "local"
        merged_browser["launchOptions"] = merged_launch_options
        return cast(StagehandBrowserConfig, merged_browser)

    async def _start_local_stagehand_session(
        self,
        context: ComputerContext,
    ) -> tuple[Browser, Page]:
        width, height = self.dimensions
        cdp_port = _pick_free_port()
        launch_args = [
            f"--window-size={width},{height}",
            f"--remote-debugging-port={cdp_port}",
            "--disable-extensions",
            "--disable-file-system",
        ]
        browser: Browser | None = None
        stagehand: AsyncStagehand | None = None
        try:
            context.emit_startup(
                state="in_progress",
                details=("Launching local browser.",),
            )
            launch_kwargs: dict[str, Any] = {
                "chromium_sandbox": self._stagehand_config.local_chromium_sandbox,
                "headless": self._stagehand_config.local_headless,
                "args": launch_args,
                "env": {},
            }
            executable_path = _effective_local_chrome_path(
                local_chrome_path=self._stagehand_config.local_chrome_path
            )
            if executable_path is not None:
                launch_kwargs["executable_path"] = executable_path

            browser = await self._playwright.chromium.launch(**launch_kwargs)
            page = await browser.new_page()
            await page.set_viewport_size({"width": width, "height": height})
            await page.goto("about:blank", wait_until="domcontentloaded")

            cdp_url = await self._resolve_cdp_ws_url(port=cdp_port)
            stagehand_class = _require_stagehand_class()

            context.emit_startup(
                state="in_progress",
                details=("Starting Stagehand local session.",),
            )
            async with _STAGEHAND_ENV_LOCK:
                with _temporary_environment(
                    {
                        "OPENAI_BASE_URL": _room_openai_base_url(room=context.room),
                        "MESHAGENT_SESSION_ID": context.room.session_id,
                    }
                ):
                    stagehand = stagehand_class(
                        **self._runtime_stagehand_kwargs(context=context)
                    )
                    response = await stagehand.sessions.start(
                        **self._runtime_stagehand_start_kwargs(
                            browser=self._default_local_browser_config(cdp_url=cdp_url)
                        )
                    )

            self._stagehand = stagehand
            self._stagehand_session_id = response.data.session_id
            await page.goto(self.starting_url)
            return browser, page
        except Exception:
            if stagehand is not None:
                with contextlib.suppress(Exception):
                    await stagehand.close()
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()
            raise

    async def _start_remote_stagehand_session(
        self,
        context: ComputerContext,
    ) -> tuple[Browser, Page]:
        stagehand_class = _require_stagehand_class()
        stagehand = stagehand_class(**self._runtime_stagehand_kwargs(context=context))
        try:
            context.emit_startup(
                state="in_progress",
                details=("Starting Stagehand browser session.",),
            )
            response = await stagehand.sessions.start(
                **self._runtime_stagehand_start_kwargs(
                    browser=self._stagehand_config.browser
                )
            )
            cdp_url = response.data.cdp_url
            if not isinstance(cdp_url, str) or cdp_url.strip() == "":
                raise RuntimeError("Stagehand session did not return a CDP URL")

            browser = await self._playwright.chromium.connect_over_cdp(
                cdp_url,
                timeout=60_000,
            )
            browser_context = (
                browser.contexts[0]
                if len(browser.contexts) > 0
                else await browser.new_context(
                    viewport={"width": self.dimensions[0], "height": self.dimensions[1]}
                )
            )
            page = (
                browser_context.pages[0]
                if len(browser_context.pages) > 0
                else await browser_context.new_page()
            )
            await page.set_viewport_size(
                {"width": self.dimensions[0], "height": self.dimensions[1]}
            )
            await page.goto(self.starting_url)
            self._stagehand = stagehand
            self._stagehand_session_id = response.data.session_id
            return browser, page
        except Exception:
            with contextlib.suppress(Exception):
                await stagehand.close()
            raise

    async def _get_browser_and_page(
        self,
        context: ComputerContext,
    ) -> tuple[Browser, Page]:
        if not stagehand_available(
            local_headless=self._stagehand_config.local_headless,
            local_chrome_path=self._stagehand_config.local_chrome_path,
        ):
            raise RuntimeError(
                "StagehandComputer requires the stagehand module and a local browser executable"
            )

        await self._close_stagehand()

        if self._stagehand_config.server == "local":
            return await self._start_local_stagehand_session(context)

        return await self._start_remote_stagehand_session(context)
