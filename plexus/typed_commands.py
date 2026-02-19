"""
Typed command system for Plexus devices.

Allows devices to declare structured commands with typed parameters,
validation, and auto-generated UI schemas. The dashboard renders
controls (sliders, dropdowns, toggles) from the command schema.

Usage:
    from plexus import Plexus, param

    px = Plexus(api_key="plx_xxx")

    @px.command("set_speed")
    @param("rpm", type="float", min=0, max=10000, unit="RPM")
    @param("ramp_time", type="float", default=1.0, unit="seconds")
    def set_motor_speed(rpm, ramp_time):
        motor.set_speed(rpm, ramp_time)
        return {"actual_rpm": motor.read_rpm()}

    @px.command("home")
    def home_axis():
        robot.home()
        return {"position": robot.get_position()}
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Parameter & Command Descriptors
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ParamDescriptor:
    """Describes a single command parameter with type, constraints, and metadata."""

    name: str
    type: str = "float"             # float, int, string, bool, enum
    description: str = ""
    unit: str = ""
    min: Optional[float] = None     # For float/int
    max: Optional[float] = None     # For float/int
    step: Optional[float] = None    # For sliders
    default: Any = None
    required: bool = True
    choices: Optional[List[str]] = None  # For enum type

    def to_schema(self) -> dict:
        """Serialize to JSON schema for the dashboard."""
        schema: dict = {"name": self.name, "type": self.type}
        if self.description:
            schema["description"] = self.description
        if self.unit:
            schema["unit"] = self.unit
        if self.min is not None:
            schema["min"] = self.min
        if self.max is not None:
            schema["max"] = self.max
        if self.step is not None:
            schema["step"] = self.step
        if self.default is not None:
            schema["default"] = self.default
        schema["required"] = self.required
        if self.choices:
            schema["choices"] = self.choices
        return schema

    def validate(self, value: Any) -> tuple:
        """Validate a value against constraints. Returns (ok, error_message)."""
        if self.type == "float":
            if not isinstance(value, (int, float)):
                return False, f"Expected number for '{self.name}', got {type(value).__name__}"
            if self.min is not None and value < self.min:
                return False, f"'{self.name}' must be >= {self.min}"
            if self.max is not None and value > self.max:
                return False, f"'{self.name}' must be <= {self.max}"
        elif self.type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                return False, f"Expected integer for '{self.name}', got {type(value).__name__}"
            if self.min is not None and value < self.min:
                return False, f"'{self.name}' must be >= {int(self.min)}"
            if self.max is not None and value > self.max:
                return False, f"'{self.name}' must be <= {int(self.max)}"
        elif self.type == "string":
            if not isinstance(value, str):
                return False, f"Expected string for '{self.name}', got {type(value).__name__}"
        elif self.type == "bool":
            if not isinstance(value, bool):
                return False, f"Expected boolean for '{self.name}', got {type(value).__name__}"
        elif self.type == "enum":
            if self.choices and value not in self.choices:
                return False, f"'{self.name}' must be one of {self.choices}, got '{value}'"
        return True, ""


@dataclass
class CommandDescriptor:
    """Describes a typed command with its handler and parameter schema."""

    name: str
    handler: Callable
    description: str = ""
    params: List[ParamDescriptor] = field(default_factory=list)

    def to_schema(self) -> dict:
        """Serialize to JSON schema for the dashboard."""
        schema: dict = {"name": self.name}
        if self.description:
            schema["description"] = self.description
        if self.params:
            schema["params"] = [p.to_schema() for p in self.params]
        return schema


# ─────────────────────────────────────────────────────────────────────────────
# Decorator
# ─────────────────────────────────────────────────────────────────────────────

def param(
    name: str,
    *,
    type: str = "float",
    description: str = "",
    unit: str = "",
    min: Optional[float] = None,
    max: Optional[float] = None,
    step: Optional[float] = None,
    default: Any = None,
    required: bool = True,
    choices: Optional[List[str]] = None,
):
    """
    Decorator to describe a command parameter.

    Stack multiple @param decorators for multi-parameter commands.
    Order matters — topmost @param is the first parameter.

    Args:
        name: Parameter name (must match the function argument name)
        type: "float", "int", "string", "bool", or "enum"
        description: Human-readable description
        unit: Display unit (e.g., "RPM", "celsius", "meters")
        min: Minimum value (float/int types)
        max: Maximum value (float/int types)
        step: Step size for UI sliders (float/int types)
        default: Default value (makes parameter optional)
        required: Whether the parameter is required (default True)
        choices: Valid values for enum type

    Example:
        @px.command("set_speed")
        @param("rpm", type="float", min=0, max=10000, unit="RPM")
        @param("direction", type="enum", choices=["cw", "ccw"], default="cw")
        def set_speed(rpm, direction):
            ...
    """
    if default is not None:
        required = False

    descriptor = ParamDescriptor(
        name=name,
        type=type,
        description=description,
        unit=unit,
        min=min,
        max=max,
        step=step,
        default=default,
        required=required,
        choices=choices,
    )

    def decorator(fn):
        if not hasattr(fn, "_plexus_params"):
            fn._plexus_params = []
        # Insert at front so stacked decorators preserve top-to-bottom order
        fn._plexus_params.insert(0, descriptor)
        return fn

    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

class CommandRegistry:
    """
    Registry for typed commands.

    Attached to a Plexus client — the connector reads it on connect
    to advertise capabilities, and dispatches incoming typed commands.
    """

    def __init__(self):
        self._commands: Dict[str, CommandDescriptor] = {}

    def register(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        params: Optional[List[ParamDescriptor]] = None,
    ):
        """Register a typed command."""
        self._commands[name] = CommandDescriptor(
            name=name,
            handler=handler,
            description=description,
            params=params or [],
        )

    def get(self, name: str) -> Optional[CommandDescriptor]:
        """Get a command descriptor by name."""
        return self._commands.get(name)

    def names(self) -> List[str]:
        """Get all registered command names."""
        return list(self._commands.keys())

    def get_schemas(self) -> List[dict]:
        """Get JSON schemas for all registered commands."""
        return [cmd.to_schema() for cmd in self._commands.values()]

    def __len__(self) -> int:
        return len(self._commands)

    def __contains__(self, name: str) -> bool:
        return name in self._commands

    async def execute(self, name: str, params: dict, ws, cmd_id: str):
        """
        Execute a typed command with validation and result streaming.

        Validates all parameters against their descriptors, applies defaults,
        then calls the handler. Results are sent back via WebSocket.
        """
        cmd = self._commands.get(name)
        if not cmd:
            await ws.send(json.dumps({
                "type": "command_result",
                "id": cmd_id,
                "event": "error",
                "error": f"Unknown command: {name}",
            }))
            return

        # Validate and build kwargs
        kwargs = {}
        for param_desc in cmd.params:
            if param_desc.name in params:
                value = params[param_desc.name]
                valid, err = param_desc.validate(value)
                if not valid:
                    await ws.send(json.dumps({
                        "type": "command_result",
                        "id": cmd_id,
                        "event": "error",
                        "error": err,
                    }))
                    return
                kwargs[param_desc.name] = value
            elif param_desc.default is not None:
                kwargs[param_desc.name] = param_desc.default
            elif param_desc.required:
                await ws.send(json.dumps({
                    "type": "command_result",
                    "id": cmd_id,
                    "event": "error",
                    "error": f"Missing required parameter: {param_desc.name}",
                }))
                return

        # ACK
        await ws.send(json.dumps({
            "type": "command_result",
            "id": cmd_id,
            "event": "ack",
            "command": name,
        }))

        # Execute handler
        try:
            result = cmd.handler(**kwargs)
            if asyncio.iscoroutine(result):
                result = await result

            # Normalize result to dict
            if result is None:
                result = {"status": "ok"}
            elif not isinstance(result, dict):
                result = {"value": result}

            await ws.send(json.dumps({
                "type": "command_result",
                "id": cmd_id,
                "event": "result",
                "command": name,
                "result": result,
            }))

        except Exception as e:
            logger.error(f"Command '{name}' failed: {e}")
            await ws.send(json.dumps({
                "type": "command_result",
                "id": cmd_id,
                "event": "error",
                "command": name,
                "error": str(e),
            }))
