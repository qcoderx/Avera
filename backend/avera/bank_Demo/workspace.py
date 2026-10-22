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


BUGGY_SOURCE = '''"""Idempotency policy for outbound bank transfers."""


def build_idempotency_key(transfer_intent_id: str, attempt: int) -> str:
    """Return the provider key for one delivery attempt."""
    if not transfer_intent_id:
        raise ValueError("transfer_intent_id is required")
    if attempt < 1:
        raise ValueError("attempt must be positive")

    # BUG: a timeout retry becomes a new payment instead of the same intent.
    return f"{transfer_intent_id}:{attempt}"
'''


SAFE_SOURCE = '''"""Idempotency policy for outbound bank transfers."""


def build_idempotency_key(transfer_intent_id: str, attempt: int) -> str:
    """Return one stable provider key for every retry of an intent."""
    if not transfer_intent_id:
        raise ValueError("transfer_intent_id is required")
    if attempt < 1:
        raise ValueError("attempt must be positive")

    return transfer_intent_id
'''


TEST_SOURCE = '''"""Regression tests for outbound transfer idempotency."""

import unittest

from transfer_service import build_idempotency_key


class TransferIdempotencyTests(unittest.TestCase):
    def test_timeout_retry_reuses_the_original_intent_key(self):
        first = build_idempotency_key("intent-bank-x-001", 1)
        retry = build_idempotency_key("intent-bank-x-001", 2)
        self.assertEqual(first, retry)

    def test_new_transfer_intent_gets_a_different_key(self):
        first = build_idempotency_key("intent-bank-x-001", 1)
        second = build_idempotency_key("intent-bank-x-002", 1)
        self.assertNotEqual(first, second)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            build_idempotency_key("", 1)
        with self.assertRaises(ValueError):
            build_idempotency_key("intent-bank-x-001", 0)


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
            ast.Call, ast.Compare, ast.Lt, ast.Return,
        }
        for node in ast.walk(tree):
            if type(node) not in allowed:
                raise UnsafeRepair(f"source contains disallowed syntax: {type(node).__name__}")
            if isinstance(node, ast.Call) and not (
                isinstance(node.func, ast.Name) and node.func.id == "ValueError"
            ):
                raise UnsafeRepair("source may only call ValueError")

        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        if len(functions) != 1 or functions[0].name != "build_idempotency_key":
            raise UnsafeRepair("source must define only build_idempotency_key")
        if [arg.arg for arg in functions[0].args.args] != ["transfer_intent_id", "attempt"]:
            raise UnsafeRepair("idempotency function signature changed")

        namespace = {"__builtins__": {"ValueError": ValueError, "str": str, "int": int}}
        exec(compile(tree, "<bank-demo-repair>", "exec"), namespace)
        function = namespace["build_idempotency_key"]
        if function("intent-a", 1) != function("intent-a", 2):
            raise UnsafeRepair("retry key is still unstable")
        if function("intent-a", 1) == function("intent-b", 1):
            raise UnsafeRepair("different transfer intents share a key")

    def idempotency_key(self, intent_id: str, attempt: int) -> str:
        name = f"avera_bank_demo_transfer_{uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, self.source_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load transfer service")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build_idempotency_key(intent_id, attempt)

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
