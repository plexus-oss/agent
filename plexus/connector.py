"""
WebSocket connector for remote terminal access.

Connects to the Plexus server and allows remote command execution.
"""

import asyncio
import json
import os
import platform
import subprocess
import sys
from typing import Optional, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from plexus.config import get_api_key, get_endpoint, get_device_id


class PlexusConnector:
    """
    WebSocket client that connects to Plexus and executes commands remotely.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        device_id: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        self.api_key = api_key or get_api_key()
        self.endpoint = (endpoint or get_endpoint()).rstrip("/")
        self.device_id = device_id or get_device_id()
        self.on_status = on_status or (lambda x: None)

        self._ws = None
        self._running = False
        self._current_process: Optional[subprocess.Popen] = None

    def _get_ws_url(self) -> str:
        """Convert HTTP endpoint to WebSocket URL."""
        url = self.endpoint.replace("https://", "wss://").replace("http://", "ws://")
        return f"{url}/api/agent/ws"

    async def connect(self):
        """Connect to the Plexus server and listen for commands."""
        if not self.api_key:
            raise ValueError("No API key configured. Run 'plexus init' first.")

        ws_url = self._get_ws_url()
        self.on_status(f"Connecting to {ws_url}...")

        headers = [
            ("x-api-key", self.api_key),
            ("x-device-id", self.device_id),
            ("x-platform", platform.system()),
            ("x-python-version", platform.python_version()),
        ]

        self._running = True

        while self._running:
            try:
                async with websockets.connect(
                    ws_url,
                    additional_headers=headers,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    self.on_status("Connected! Waiting for commands...")

                    # Send initial handshake
                    await ws.send(json.dumps({
                        "type": "handshake",
                        "device_id": self.device_id,
                        "platform": platform.system(),
                        "cwd": os.getcwd(),
                    }))

                    # Listen for messages
                    async for message in ws:
                        await self._handle_message(message)

            except ConnectionClosed as e:
                self.on_status(f"Connection closed: {e.reason}")
                if self._running:
                    self.on_status("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)
            except Exception as e:
                self.on_status(f"Connection error: {e}")
                if self._running:
                    self.on_status("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "execute":
                await self._execute_command(data)
            elif msg_type == "cancel":
                self._cancel_current()
            elif msg_type == "ping":
                await self._ws.send(json.dumps({"type": "pong"}))

        except json.JSONDecodeError:
            self.on_status(f"Invalid message: {message}")

    async def _execute_command(self, data: dict):
        """Execute a shell command and stream output back."""
        command = data.get("command", "")
        cmd_id = data.get("id", "unknown")

        if not command:
            return

        self.on_status(f"Executing: {command}")

        # Send start notification
        await self._ws.send(json.dumps({
            "type": "output",
            "id": cmd_id,
            "event": "start",
            "command": command,
        }))

        try:
            # Execute command
            self._current_process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=os.getcwd(),
            )

            # Stream output line by line
            for line in iter(self._current_process.stdout.readline, ""):
                if not self._running:
                    break
                await self._ws.send(json.dumps({
                    "type": "output",
                    "id": cmd_id,
                    "event": "data",
                    "data": line,
                }))

            # Wait for process to complete
            return_code = self._current_process.wait()

            # Send completion
            await self._ws.send(json.dumps({
                "type": "output",
                "id": cmd_id,
                "event": "exit",
                "code": return_code,
            }))

        except Exception as e:
            await self._ws.send(json.dumps({
                "type": "output",
                "id": cmd_id,
                "event": "error",
                "error": str(e),
            }))

        finally:
            self._current_process = None

    def _cancel_current(self):
        """Cancel the currently running command."""
        if self._current_process:
            self._current_process.terminate()
            self.on_status("Command cancelled")

    def disconnect(self):
        """Disconnect from the server."""
        self._running = False
        self._cancel_current()
        if self._ws:
            asyncio.create_task(self._ws.close())


def run_connector(
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
):
    """Run the connector (blocking)."""
    connector = PlexusConnector(
        api_key=api_key,
        endpoint=endpoint,
        on_status=on_status,
    )

    try:
        asyncio.run(connector.connect())
    except KeyboardInterrupt:
        connector.disconnect()
