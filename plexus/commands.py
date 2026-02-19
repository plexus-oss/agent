"""
Command execution for Plexus devices.

Handles remote command execution with allowlist-based security policy
and timeout enforcement. Isolated from the connector for security clarity.

Security model:
    - By default, ALL shell commands are BLOCKED unless an explicit allowlist
      is provided. This is a secure-by-default design.
    - Users must opt in to shell execution by configuring an allowlist of
      permitted command patterns (e.g., ["python *", "ls *", "cat *"]).
    - An optional denylist provides defense-in-depth against allowlist
      patterns that are too broad.
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

# Default denylist: defense-in-depth against overly broad allowlist patterns
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

    Security: Commands are BLOCKED by default. You must provide an explicit
    allowlist to enable shell execution. This prevents arbitrary command
    injection even if an attacker gains WebSocket access.

    Args:
        allowlist: Command patterns to permit (required to enable execution).
                   If None or empty, ALL commands are blocked.
        denylist: Commands matching these patterns are always blocked,
                  even if they match the allowlist (defense-in-depth).
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
        # No allowlist = no shell execution (secure by default)
        if not self.allowlist:
            return False, "Shell execution disabled (no allowlist configured)"

        # Check denylist first -- blocked commands never run
        for pattern in self.denylist:
            if fnmatch.fnmatch(command, pattern) or fnmatch.fnmatch(command.strip(), pattern):
                return False, f"Command blocked by denylist: matches '{pattern}'"

        # Command must match at least one allowlist pattern
        for pattern in self.allowlist:
            if fnmatch.fnmatch(command, pattern) or fnmatch.fnmatch(command.strip(), pattern):
                return True, ""
        return False, "Command not in allowlist"

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
