from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

import mafia_game

PROJECT_ROOT = Path(__file__).parents[2]
MARKDOWN_LINK = re.compile(r"\[[^]]*]\(([^)]+)\)")


def test_readme_local_links_resolve() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    missing: list[str] = []
    for target in MARKDOWN_LINK.findall(readme):
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        relative_path = Path(unquote(parsed.path))
        if not (PROJECT_ROOT / relative_path).exists():
            missing.append(target)

    assert not missing, "README contains broken local links: " + ", ".join(missing)


def test_distribution_metadata_matches_package() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    assert project["version"] == mafia_game.__version__
    assert (PROJECT_ROOT / "src" / "mafia_game" / "py.typed").is_file()


def test_database_migrations_use_packaged_alembic_revisions() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)

    dependencies = project["project"]["dependencies"]
    migrations = PROJECT_ROOT / "src" / "mafia_game" / "runs" / "migrations"
    assert any(dependency.startswith("alembic") for dependency in dependencies)
    assert any(dependency.startswith("sqlalchemy") for dependency in dependencies)
    assert (migrations / "env.py").is_file()
    assert (migrations / "script.py.mako").is_file()
    assert (migrations / "versions" / "0001_initial.py").is_file()
    assert not (migrations / "runner.py").exists()


def test_product_login_adapters_do_not_ship() -> None:
    provider_root = PROJECT_ROOT / "src" / "mafia_game" / "agents" / "providers"
    assert not (provider_root / "claude.py").exists()
    assert not (provider_root / "codex.py").exists()

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    assert not any(dependency.startswith("openai-codex") for dependency in dependencies)


def test_private_roster_names_are_excluded_from_distributions() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        build = tomllib.load(handle)["tool"]["hatch"]["build"]

    assert set(build["exclude"]) >= {
        "/examples/human.toml",
        "/examples/mixed_local.toml",
        "/examples/openrouter.toml",
        "/examples/openrouter_but_everyone_angry.toml",
        "/examples/openrouter_but_everyone_is_corporate.toml",
        "/examples/openrouter_but_everyone_is_exhausted.toml",
        "/examples/openrouter_but_everyone_is_inspirational.toml",
        "/examples/openrouter_but_everyone_is_paranoid.toml",
        "/examples/openrouter_but_everyone_is_the_protagonist.toml",
        "/examples/openrouter_fast.toml",
        "/examples/sonnet_vs_gpt.toml",
    }


def test_post_launch_simulation_does_not_ship() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        excludes = set(tomllib.load(handle)["tool"]["hatch"]["build"]["exclude"])

    assert excludes >= {
        "/src/mafia_game/cli/simulate.py",
        "/src/mafia_game/simulation/**",
        "/tests/simulation/**",
    }
