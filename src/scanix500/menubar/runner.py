from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from scanix500.menubar.profiles import Profile

_KNOWN_MESSAGES = {"No pages scanned.", "All pages were blank; no PDF written."}


@dataclass
class ScanResult:
    ok: bool
    partial: bool
    message: str
    output_paths: list[str]


def parse_scan_output(returncode: int, stdout: str, stderr: str) -> ScanResult:
    stdout_lines = [line for line in stdout.splitlines() if line]
    message_line = stdout_lines[0] if stdout_lines and stdout_lines[0] in _KNOWN_MESSAGES else None
    output_paths = [] if message_line else stdout_lines

    if returncode == 0:
        if message_line:
            message = message_line
        elif output_paths:
            message = ", ".join(output_paths)
        else:
            message = "Scan complete"
        return ScanResult(ok=True, partial=False, message=message, output_paths=output_paths)

    stderr_lines = [line for line in stderr.splitlines() if line]
    multi_feed = any("Multi-feed detected" in line for line in stderr_lines)

    if multi_feed:
        stderr_message = stderr_lines[0] if stderr_lines else "Multi-feed detected"
        if output_paths:
            message = f"{stderr_message} — partial scan saved to {output_paths[0]}"
        elif message_line:
            message = f"{stderr_message} ({message_line})"
        else:
            message = stderr_message
        return ScanResult(ok=False, partial=True, message=message, output_paths=output_paths)

    message = stderr_lines[0] if stderr_lines else f"scanix500 exited with code {returncode}"
    return ScanResult(ok=False, partial=False, message=message, output_paths=[])


def notification_title(result: ScanResult) -> str:
    if result.ok and not result.partial:
        return "Scan complete"
    if result.partial:
        return "Scan partially completed"
    return "Scan failed"


def _scanix500_executable() -> str:
    # launchd starts the menu bar app with a minimal PATH that excludes the
    # venv's bin/, so prefer the scanix500 sitting next to this interpreter
    # (where `pip install -e .` puts it) before falling back to PATH.
    sibling = Path(sys.executable).parent / "scanix500"
    if sibling.exists():
        return str(sibling)
    found = shutil.which("scanix500")
    return found if found else "scanix500"


def _build_argv(profile: Profile) -> list[str]:
    argv = [_scanix500_executable(), profile.destination]
    if profile.skip_blank_filter:
        argv.append("--skip-blank-filter")
    if profile.skip_ocr:
        argv.append("--skip-ocr")
    if profile.split_on_blank:
        argv.append("--split-on-blank")
    return argv


def run_scan(profile: Profile) -> ScanResult:
    result = subprocess.run(_build_argv(profile), capture_output=True, text=True)
    return parse_scan_output(result.returncode, result.stdout, result.stderr)
