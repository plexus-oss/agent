"""
Command execution for Plexus devices.

Handles remote command execution with allowlist/denylist security policy
and timeout enforcement. Isolated from the connector for security clarity.
"""

import asyncio
import fnmatch
import json
import logging
import os
import shlex
import subprocess
from typing import Optional, Callable, List

logger = logging.getLogger(__name__)

# Default denylist: dangerous commands that should never be executed remotely
DEFAULT_COMMAND_DENYLIST = [
    "rm -rf *", "rm -rf /", "rm -rf /*",
    "dd *",
    "mkfs*",
    "shutdown*", "reboot*",
    "format*",
    "> /dev/*",
    ":(){ :|:& };:",  # fork bomb
]


class CommandExecutor:
    """Executes remote commands with safety checks.

    Args:
        allowlist: If set, only commands matching these patterns will run.
        denylist: Commands matching these patterns are always blocked.
        on_status: Callback for status messages.
    """

    def __init__(
        self,
        allowlist: Optional[List[str]] = None,
        denylist: Optional[List[str]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        self.allowlist = allowlist
        self.denylist = denylist if denylist is not None else DEFAULT_COMMAND_DENYLIST
        self.on_status = on_status or (lambda x: None)
        self._current_process: Optional[subprocess.Popen] = None

    def is_command_allowed(self, command: str) -> tuple[bool, str]:
        """Check if a command is allowed by the allowlist/denylist policy.

        Returns (allowed, reason) tuple.
        """
        # Check denylist first -- blocked commands never run
        for pattern in self.denylist:
            if fnmatch.fnmatch(command, pattern) or fnmatch.fnmatch(command.strip(), pattern):
                return False, f"Command blocked by denylist: matches '{pattern}'"

        # If allowlist is set, command must match at least one pattern
        if self.allowlist:
            for pattern in self.allowlist:
                if fnmatch.fnmatch(command, pattern) or fnmatch.fnmatch(command.strip(), pattern):
                    return True, ""
            return False, "Command not in allowlist"

        return True, ""

    async def execute(self, data: dict, ws, running_flag: Callable[[], bool]):
        """Execute shell command and stream output with timeout enforcement."""
        command = data.get("command", "")
        cmd_id = data.get("id", "cmd")
        timeout_seconds = data.get("timeout_seconds", 300)

        if not command:
            return

        # Validate command against allowlist/denylist
        allowed, reason = self.is_command_allowed(command)
        if not allowed:
            self.on_status(f"Blocked: {command} ({reason})")
            await ws.send(json.dumps({
                "type": "output", "id": cmd_id, "event": "error",
                "error": f"Command rejected: {reason}",
                "command": command,
            }))
            return

        # ACK: confirm command receipt before execution
        await ws.send(json.dumps({
            "type": "output", "id": cmd_id, "event": "ack", "command": command
        }))

        self.on_status(f"Running: {command}")

        await ws.send(json.dumps({
            "type": "output", "id": cmd_id, "event": "start", "command": command
        }))

        try:
            args = shlex.split(command)
            self._current_process = subprocess.Popen(
                args, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=os.getcwd()
            )

            async def _read_output():
                """Read stdout lines in a thread to allow async timeout."""
                loop = asyncio.get_event_loop()
                while True:
                    line = await loop.run_in_executor(
                        None, self._current_process.stdout.readline
                    )
                    if not line:
                        break
                    if not running_flag():
                        break
                    await ws.send(json.dumps({
                        "type": "output", "id": cmd_id, "event": "data", "data": line
                    }))

            try:
                await asyncio.wait_for(_read_output(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                # Kill the process on timeout
                if self._current_process:
                    self._current_process.kill()
                    self._current_process.wait()
                self.on_status(f"Timeout after {timeout_seconds}s: {command}")
                await ws.send(json.dumps({
                    "type": "output", "id": cmd_id, "event": "timeout"
                }))
                return

            code = self._current_process.wait()
            await ws.send(json.dumps({
                "type": "output", "id": cmd_id, "event": "exit", "code": code
            }))

        except Exception as e:
            await ws.send(json.dumps({
                "type": "output", "id": cmd_id, "event": "error", "error": str(e)
            }))
        finally:
            self._current_process = None

    def cancel(self):
        """Cancel running command with SIGKILL fallback."""
        if self._current_process:
            self._current_process.terminate()
            try:
                self._current_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._current_process.kill()
            self.on_status("Cancelled")
