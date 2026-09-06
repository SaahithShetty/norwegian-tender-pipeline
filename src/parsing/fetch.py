"""Optional interactive fetch of tender annexes through a real browser.

Why this exists, and why it is not the default path:

The tender document list is public, but the file endpoint
(`GetFile.ashx`) sits behind a Cloudflare interactive challenge — a plain request
returns *"Just a moment… Enable JavaScript and cookies to continue"* with HTTP 403.

**Tested, and the automated path does not get through.** In an ordinary Chrome window
a person clears the challenge and the spreadsheet downloads. Driving a browser through
CDP does not: three routes were tried against the live endpoint — clicking the link by
element reference, navigating straight to the href, and `fetch()` from inside the page
with `credentials:'include'` — and all three returned `403 text/html` with the
challenge still displayed. Cloudflare fingerprints the automation itself, so cookies
and login state are not the deciding factor.

What that leaves is a genuinely interactive flow: this module opens the tender page and
waits for a person to clear the verification, then downloads. It is opt-in
(`run.py --fetch-annexes`) and never runs unattended.

Because of that, the annex is committed to the repository. A reviewer reproduces the
full result by cloning and running `python run.py` — no browser, no account, no flag.

The browser is driven through the `agent-browser` CLI when it is installed. It is a
development convenience, never a runtime dependency: if the CLI is missing, the
function explains what to do by hand and returns nothing rather than failing.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_CLI: Final[str] = "agent-browser"
_CHALLENGE_MARKERS: Final[tuple[str, ...]] = (
    "just a moment",
    "enable javascript and cookies",
    "security verification",
    "checking your browser",
)
# How long to wait for a person to clear the challenge before giving up.
_CHALLENGE_TIMEOUT_SECONDS: Final[int] = 180
_POLL_SECONDS: Final[int] = 5


class BrowserUnavailable(RuntimeError):
    """Raised when the browser CLI needed for interactive fetching is not installed."""


def _run(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Invoke the browser CLI, turning a timeout into a failed result.

    A hung browser is an ordinary outcome here, not an exceptional one: the whole
    point of this module is that the site may not cooperate. Returning a non-zero
    result lets each caller decide what to do, instead of unwinding the run.
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed CLI, args are not user input
            [_CLI, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("%s %s timed out after %ss", _CLI, args[0] if args else "", timeout)
        return subprocess.CompletedProcess(
            args=[_CLI, *args], returncode=124, stdout="", stderr="timed out"
        )


def browser_available() -> bool:
    return shutil.which(_CLI) is not None


def _page_text() -> str:
    result = _run("read", timeout=60)
    return result.stdout.lower()


def _awaiting_challenge() -> bool:
    text = _page_text()
    return any(marker in text for marker in _CHALLENGE_MARKERS)


def fetch_annexes(
    tender_url: str,
    destination: Path,
    *,
    interactive: bool = True,
) -> list[Path]:
    """Open a tender page in a real browser and download its annex spreadsheets.

    Args:
        tender_url: the tender's document page (the `competitionDocsUrl` of a notice).
        destination: directory to save annexes into.
        interactive: wait for a person to clear the Cloudflare challenge. With
            `False` the function gives up as soon as a challenge appears, which is
            the right behaviour for an unattended run.

    Returns the files that were downloaded. An empty list means the annexes were not
    retrieved — never an exception, so a failed fetch degrades to empty pack columns
    rather than ending the run.
    """
    if not browser_available():
        raise BrowserUnavailable(
            f"{_CLI!r} is not installed. Download the annexes manually and place "
            f"them in {destination} — see data/manual/README.md."
        )

    destination.mkdir(parents=True, exist_ok=True)
    before = set(destination.glob("*.xlsx"))
    downloaded: list[Path] = []

    # try/finally, not a trailing close(): any _run may raise TimeoutExpired, and an
    # early return would otherwise leave a browser process running after the pipeline
    # has moved on.
    try:
        logger.info("opening %s", tender_url)
        _run("open", tender_url, timeout=120)
        time.sleep(5)

        if _awaiting_challenge():
            if not interactive:
                logger.warning(
                    "verification challenge present and interactive=False; skipping"
                )
                return []
            print(
                "\n  A browser window is open on the tender page.\n"
                "  Clear the verification (and sign in if prompted), then leave it open.\n"
                f"  Waiting up to {_CHALLENGE_TIMEOUT_SECONDS}s...\n"
            )
            if not _wait_for_challenge(_CHALLENGE_TIMEOUT_SECONDS):
                logger.warning("challenge not cleared in time; skipping fetch")
                return []

        downloaded = _download_price_forms(destination)
    finally:
        _close_browser()

    new_files = sorted(set(destination.glob("*.xlsx")) - before)
    logger.info("fetched %d annex file(s)", len(new_files))
    return new_files or downloaded


def _close_browser() -> None:
    """Shut the browser session down, swallowing any failure.

    Cleanup runs on the failure path too, where the CLI may itself be unresponsive;
    an error closing the browser must not mask the original problem.
    """
    try:
        _run("close", "--all", timeout=30)
    except Exception as exc:  # noqa: BLE001 - cleanup must never raise
        logger.debug("could not close browser session: %s", exc)


def _wait_for_challenge(timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _awaiting_challenge():
            return True
        time.sleep(_POLL_SECONDS)
    return False


def _download_price_forms(destination: Path) -> list[Path]:
    """Click through to each Prisskjema (price form) linked on the current page."""
    snapshot = _run("snapshot", "-i", "-u", timeout=90).stdout
    # A document appears twice in the tree: as a table cell and as the link inside
    # it. Only the link carries an href, so the cell rows are filtered out.
    targets = [
        line
        for line in snapshot.splitlines()
        if "prisskjema" in line.lower() and "url=" in line
    ]
    if not targets:
        logger.warning("no Prisskjema link found on the page")
        return []

    saved: list[Path] = []
    for index, line in enumerate(targets):
        name = _extract_filename(line) or f"annex-{index}.xlsx"
        target = destination / name

        # Element references are invalidated whenever the page changes, and a
        # download navigates. Re-snapshot before each file so the ref is current.
        if index:
            _run("snapshot", "-i", "-u", timeout=90)
        ref = _extract_ref(line)
        if ref is None:
            logger.warning("no element reference for %s", name)
            continue

        result = _run("download", ref, str(target), timeout=180)
        if target.exists():
            saved.append(target)
            logger.info("downloaded %s", target.name)
            continue

        # `download` re-resolves the element by its accessible name, which fails for
        # these links because the name is a filename. Navigating straight to the href
        # works instead: the browser has already cleared the challenge for this
        # origin, so the file is served rather than the interstitial.
        url = _extract_url(line)
        if url and _download_via_navigation(url, target):
            saved.append(target)
            logger.info("downloaded %s", target.name)
            continue

        logger.warning("download of %s failed: %s", name, result.stderr.strip()[:160])
    return saved


def _download_via_navigation(url: str, target: Path) -> bool:
    """Navigate to a file URL in the open browser and save what comes back.

    Returns False rather than raising if the response is the challenge page again.
    """
    _run("open", url, timeout=120)
    time.sleep(4)
    if _awaiting_challenge():
        return False

    # Read the response as base64 from inside the page, so the bytes come through
    # the browser's own session rather than a fresh unauthenticated request.
    script = (
        "(async()=>{const r=await fetch(location.href,{credentials:'include'});"
        "if(!r.ok)return 'ERR:'+r.status;const b=await r.arrayBuffer();"
        "return btoa(String.fromCharCode(...new Uint8Array(b)));})()"
    )
    result = _run("eval", script, timeout=180)
    payload = result.stdout.strip().strip('"')
    if not payload or payload.startswith("ERR:"):
        return False

    import base64
    import binascii

    try:
        data = base64.b64decode(payload)
    except (binascii.Error, ValueError):
        return False

    if not data.startswith(b"PK"):  # xlsx files are zip archives
        return False

    target.write_bytes(data)
    return True


def _extract_url(snapshot_line: str) -> str | None:
    match = re.search(r"url=(\S+?)[\],\s]", snapshot_line + " ")
    return match.group(1) if match else None


def _extract_ref(snapshot_line: str) -> str | None:
    """Pull the '@eN' element reference out of a snapshot line.

    A snapshot line looks like:
        - link "Name.xlsx" [ref=e60, url=https://...]
    so the reference ends at the first comma as well as at the closing bracket —
    taking everything up to "]" would capture the url too.
    """
    match = re.search(r"\[ref=(e\d+)", snapshot_line)
    return f"@{match.group(1)}" if match else None


def _extract_filename(snapshot_line: str) -> str | None:
    """Pull the quoted link text, which is the document's filename."""
    first = snapshot_line.find('"')
    if first == -1:
        return None
    second = snapshot_line.find('"', first + 1)
    if second == -1:
        return None
    name = snapshot_line[first + 1 : second].strip()
    return name if name.lower().endswith(".xlsx") else None
