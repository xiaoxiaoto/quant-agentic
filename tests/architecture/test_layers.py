"""Architecture test: enforce dependency direction and code organization.

Walks every Python module under ``src/``, parses imports via ``ast``, and asserts
that each ``src.<other-layer>`` import is to a layer this file is allowed to import.

Allowed direction (a layer may import any layer to its left):

    types -> config -> repositories -> services -> agents -> runtime -> routers

Equivalently: higher-indexed layers in ``LAYERS`` may depend on lower-indexed
layers, never the reverse. Same-layer imports are always allowed.

Catches edge cases that ``import-linter`` misses:
- conditional imports inside ``if TYPE_CHECKING:`` and function bodies
- imports inside class bodies and nested scopes

Also enforces project code organization:
- non-exempt modules must contain a top-level class
- non-exempt modules must not define top-level functions
- class bodies must be ordered as private variables, public variables, private methods, public methods

Exempt paths are handled outside this ``src`` scan when applicable:
- main.py
- src/routers/**
- tests/**
"""

from __future__ import annotations

import ast
import pathlib

LAYERS: list[str] = [
    "types",
    "config",
    "repositories",
    "services",
    "agents",
    "runtime",
    "routers",
]

SRC_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent / "src"

CLASS_MEMBER_ORDER: dict[str, int] = {
    "private_variable": 0,
    "public_variable": 1,
    "private_method": 2,
    "public_method": 3,
}


def _layer_of(path: pathlib.Path) -> str | None:
    """Return the layer name for a file under ``src/``, or ``None`` if not in a known layer."""
    try:
        parts = path.relative_to(SRC_ROOT).parts
    except ValueError:
        return None
    if not parts:
        return None
    head = parts[0]
    return head if head in LAYERS else None


def _imported_layer(module: str | None) -> str | None:
    """Return the layer name for a ``src.<layer>...`` import, or ``None``."""
    if not module or not module.startswith("src."):
        return None
    parts = module.split(".")
    if len(parts) < 2:
        return None
    candidate = parts[1]
    return candidate if candidate in LAYERS else None


def _iter_imports(tree: ast.AST) -> list[tuple[str, int]]:
    """Yield ``(module, lineno)`` for every import in the tree."""
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                out.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
    return out


def _is_docstring(node: ast.stmt) -> bool:
    """Return whether a statement is a docstring expression."""
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _is_exempt_src_path(path: pathlib.Path) -> bool:
    """Return whether a source file is exempt from class organization checks."""
    rel = path.relative_to(SRC_ROOT)
    return bool(rel.parts) and rel.parts[0] == "routers"


def _module_has_content(tree: ast.Module) -> bool:
    """Return whether a module has content beyond docstrings, imports, and pass."""
    for node in tree.body:
        if _is_docstring(node):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Pass)):
            continue
        return True
    return False


def _target_name(node: ast.expr) -> str | None:
    """Return the assigned name for simple class-body assignments."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _assignment_name(node: ast.stmt) -> str | None:
    """Return the first simple assignment target name for class-body variables."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            name = _target_name(target)
            if name:
                return name
        return None
    if isinstance(node, ast.AnnAssign):
        return _target_name(node.target)
    if isinstance(node, ast.AugAssign):
        return _target_name(node.target)
    return None


def _is_private_name(name: str) -> bool:
    """Return whether a member name is private or protected."""
    return name.startswith("_")


def _class_member_kind(node: ast.stmt) -> str | None:
    """Return the ordered member category for a class-body statement."""
    name = _assignment_name(node)
    if name:
        return "private_variable" if _is_private_name(name) else "public_variable"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "private_method" if _is_private_name(node.name) else "public_method"
    return None


def test_layered_imports() -> None:
    """Fail if any module imports from a layer it is not allowed to depend on."""
    assert SRC_ROOT.is_dir(), f"Expected src root at {SRC_ROOT}"

    violations: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        own = _layer_of(py)
        if own is None:
            continue
        own_idx = LAYERS.index(own)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as exc:
            violations.append(f"{py}: syntax error — {exc}")
            continue
        for module, lineno in _iter_imports(tree):
            target = _imported_layer(module)
            if target is None or target == own:
                continue
            target_idx = LAYERS.index(target)
            if target_idx >= own_idx:
                rel = py.relative_to(SRC_ROOT.parent)
                violations.append(f"{rel}:{lineno}  {own} -> {target}  ({module})")

    assert not violations, "Layered architecture violations:\n  " + "\n  ".join(violations)


def test_class_managed_modules() -> None:
    """Fail if non-exempt source modules are not managed by classes."""
    assert SRC_ROOT.is_dir(), f"Expected src root at {SRC_ROOT}"

    violations: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        if _is_exempt_src_path(py):
            continue
        rel = py.relative_to(SRC_ROOT.parent)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as exc:
            violations.append(f"{rel}: syntax error - {exc}")
            continue
        if not _module_has_content(tree):
            continue

        top_level_classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        if not top_level_classes:
            violations.append(f"{rel}: non-exempt module must contain at least one top-level class")

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                violations.append(f"{rel}:{node.lineno}: top-level function `{node.name}` is not allowed")

        for class_node in top_level_classes:
            highest_seen = -1
            for member in class_node.body:
                if _is_docstring(member):
                    continue
                kind = _class_member_kind(member)
                if kind is None:
                    continue
                order = CLASS_MEMBER_ORDER[kind]
                if order < highest_seen:
                    violations.append(
                        f"{rel}:{member.lineno}: `{class_node.name}` member order must be "
                        "private variables, public variables, private methods, public methods"
                    )
                    break
                highest_seen = max(highest_seen, order)

    assert not violations, "Class organization violations:\n  " + "\n  ".join(violations)
