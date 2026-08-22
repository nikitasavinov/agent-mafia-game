from __future__ import annotations

import ast
from pathlib import Path

FEATURE_ORDER = {
    "config": 0,
    "agents": 1,
    "gameplay": 2,
    "runs": 3,
    "cli": 4,
}


def test_feature_imports_follow_the_dependency_direction() -> None:
    package_root = Path(__file__).parents[2] / "src" / "mafia_game"
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root)
        importer = relative.parts[0]
        if importer not in FEATURE_ORDER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        for node in ast.walk(tree):
            dependencies = _feature_dependencies(node, relative)
            violations.extend(
                f"{relative}: {importer} imports {dependency}"
                for dependency in dependencies
                if FEATURE_ORDER[dependency] > FEATURE_ORDER[importer]
            )

    assert not violations, "Feature dependency violations:\n" + "\n".join(violations)


def test_shared_leaf_modules_depend_on_no_feature() -> None:
    """Modules outside the feature layers must stay importable from every layer.

    A root-level module that imports a feature acquires a hidden position in the
    ordering above, and the left-to-right rule stops holding for its consumers.
    """
    package_root = Path(__file__).parents[2] / "src" / "mafia_game"
    violations: list[str] = []

    for path in sorted(package_root.glob("*.py")):
        relative = path.relative_to(package_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        for node in ast.walk(tree):
            violations.extend(
                f"{relative}: imports {dependency}"
                for dependency in _feature_dependencies(node, relative)
            )

    assert not violations, "Shared modules must not import a feature:\n" + "\n".join(violations)


def _feature_dependencies(node: ast.AST, relative: Path) -> set[str]:
    modules: list[list[str]] = []
    if isinstance(node, ast.Import):
        modules.extend(alias.name.split(".") for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        if node.level == 0 and node.module is not None:
            modules.append(node.module.split("."))
        elif node.level > 0:
            module_parts = ["mafia_game", *relative.with_suffix("").parts]
            package_parts = module_parts[:-1]
            parent_parts = package_parts[: len(package_parts) - (node.level - 1)]
            modules.append(parent_parts + (node.module.split(".") if node.module else []))
    dependencies: set[str] = set()
    for parts in modules:
        if len(parts) >= 2 and parts[0] == "mafia_game" and parts[1] in FEATURE_ORDER:
            dependencies.add(parts[1])
    return dependencies
