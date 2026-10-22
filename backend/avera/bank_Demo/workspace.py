"""Isolated source workspace used by the mock-bank repair agent."""

from __future__ import annotations

import ast
import difflib
import importlib.util
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


BUGGY_SOURCE = '''"""Gateway routing policy for outbound Bank X transfers."""


def select_gateway(destination_bank: str, gateway_a_healthy: bool) -> str:
    """Choose the active route for a destination bank."""
    if not destination_bank:
        raise ValueError("destination_bank is required")
    if destination_bank != "BANK_X":
        raise ValueError("only BANK_X is supported in this demo")

    if gateway_a_healthy:
        return "gateway_a.bank_x"

    # BUG: Bank X remains pinned to Gateway A after its health check fails.
    return "gateway_a.bank_x"
'''


SAFE_SOURCE = '''"""Gateway routing policy for outbound Bank X transfers."""


def select_gateway(destination_bank: str, gateway_a_healthy: bool) -> str:
    """Choose the active route for a destination bank."""
    if not destination_bank:
        raise ValueError("destination_bank is required")
    if destination_bank != "BANK_X":
        raise ValueError("only BANK_X is supported in this demo")

    if gateway_a_healthy:
        return "gateway_a.bank_x"

    return "gateway_b.bank_x"
'''


TEST_SOURCE = '''"""Regression tests for outbound gateway routing."""

import unittest

from transfer_service import select_gateway


class TransferGatewayRoutingTests(unittest.TestCase):
    def test_bank_x_fails_over_when_gateway_a_is_unhealthy(self):
        route = select_gateway("BANK_X", gateway_a_healthy=False)
        self.assertEqual(route, "gateway_b.bank_x")

    def test_bank_x_stays_on_gateway_a_while_it_is_healthy(self):
        route = select_gateway("BANK_X", gateway_a_healthy=True)
        self.assertEqual(route, "gateway_a.bank_x")

    def test_unknown_destinations_are_rejected(self):
        with self.assertRaises(ValueError):
            select_gateway("", gateway_a_healthy=False)
        with self.assertRaises(ValueError):
            select_gateway("BANK_Y", gateway_a_healthy=False)


if __name__ == "__main__":
    unittest.main()
'''


@dataclass(frozen=True)
class TestRun:
    passed: bool
    output: str
    command: str


class UnsafeRepair(ValueError):
    """Raised when model-produced source exceeds the demo sandbox policy."""


class RepairWorkspace:
    """Owns the only files the repair agent may read, edit, or execute."""

    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or os.getenv("AVERA_BANK_DEMO_WORKSPACE")
        self._temporary = None
        if configured:
            self.root = Path(configured).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)
        else:
            self._temporary = tempfile.TemporaryDirectory(prefix="avera-bank-demo-")
            self.root = Path(self._temporary.name)
        self.source_path = self.root / "transfer_service.py"
        self.test_path = self.root / "test_transfer_service.py"
        self.reset()

    @property
    def display_source_path(self) -> str:
        return "bank_Demo/workspace/transfer_service.py"

    def reset(self) -> None:
        self.source_path.write_text(BUGGY_SOURCE, encoding="utf-8")
        self.test_path.write_text(TEST_SOURCE, encoding="utf-8")
        pycache = self.root / "__pycache__"
        if pycache.exists():
            for item in pycache.iterdir():
                item.unlink(missing_ok=True)
            pycache.rmdir()

    def read_source(self) -> str:
        return self.source_path.read_text(encoding="utf-8")

    def write_source(self, source: str) -> None:
        self.validate_source(source)
        pending = self.root / ".transfer_service.py.pending"
        pending.write_text(source.rstrip() + "\n", encoding="utf-8")
        pending.replace(self.source_path)

    @staticmethod
    def validate_source(source: str) -> None:
        if not source or len(source) > 4_000 or source.count(chr(96)) >= 3:
            raise UnsafeRepair("replacement source is empty, fenced, or too large")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise UnsafeRepair("replacement source is not valid Python") from exc

        allowed = {
            ast.Module, ast.Expr, ast.Constant, ast.FunctionDef, ast.arguments,
            ast.arg, ast.If, ast.UnaryOp, ast.Not, ast.Name, ast.Load, ast.Raise,
            ast.Call, ast.Compare, ast.NotEq, ast.Return,
        }
        for node in ast.walk(tree):
            if type(node) not in allowed:
                raise UnsafeRepair(f"source contains disallowed syntax: {type(node).__name__}")
            if isinstance(node, ast.Call) and not (
                isinstance(node.func, ast.Name) and node.func.id == "ValueError"
            ):
                raise UnsafeRepair("source may only call ValueError")

        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        if len(functions) != 1 or functions[0].name != "select_gateway":
            raise UnsafeRepair("source must define only select_gateway")
        if [arg.arg for arg in functions[0].args.args] != ["destination_bank", "gateway_a_healthy"]:
            raise UnsafeRepair("gateway routing function signature changed")

        namespace = {"__builtins__": {"ValueError": ValueError, "str": str, "bool": bool}}
        exec(compile(tree, "<bank-demo-repair>", "exec"), namespace)
        function = namespace["select_gateway"]
        if function("BANK_X", False) != "gateway_b.bank_x":
            raise UnsafeRepair("failed Gateway A does not fail over to Gateway B")
        if function("BANK_X", True) != "gateway_a.bank_x":
            raise UnsafeRepair("healthy Gateway A route changed unexpectedly")

    def gateway_route(self, destination_bank: str, *, gateway_a_healthy: bool) -> str:
        name = f"avera_bank_demo_transfer_{uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, self.source_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load transfer service")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.select_gateway(destination_bank, gateway_a_healthy)

    def run_tests(self) -> TestRun:
        command = f"{Path(sys.executable).name} -m unittest -v test_transfer_service.py"
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", "test_transfer_service.py"],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        output = (completed.stdout + completed.stderr).strip()
        return TestRun(completed.returncode == 0, output[-4_000:], command)

    @staticmethod
    def diff(before: str, after: str) -> str:
        return "".join(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/bank_Demo/workspace/transfer_service.py",
            tofile="b/bank_Demo/workspace/transfer_service.py",
        ))

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
