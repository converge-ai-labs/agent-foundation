from pathlib import Path

import yaml
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts.check_migration_heads import check_graphs, main

ROOT = Path(__file__).parents[2]


def test_concurrent_heads_report_paths_and_reviewed_merge_restores_graph(tmp_path, capsys):
    config = Config()
    config.set_main_option("script_location", str(tmp_path / "migrations"))
    # Generate a real Alembic history, without running env.py or any upgrade.
    config.config_file_name = str(tmp_path / "alembic.ini")
    command.init(config, str(tmp_path / "migrations"))
    base = command.revision(config, message="base", rev_id="base_revision")
    assert base is not None
    first = command.revision(config, message="first", rev_id="first_revision", head=base.revision)
    second = command.revision(config, message="second", rev_id="second_revision", head=base.revision, splice=True)
    assert first is not None and second is not None
    assert check_graphs((("Fixture", ScriptDirectory.from_config(config)),)) == 1
    error = capsys.readouterr().err
    assert "found 2" in error
    assert first.revision in error and first.path in error
    assert second.revision in error and second.path in error
    assert "reviewed Alembic merge revision" in error
    command.merge(config, (first.revision, second.revision), rev_id="merged_revision")
    assert check_graphs((("Fixture", ScriptDirectory.from_config(config)),)) == 0


def test_repository_graphs_do_not_open_database(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Migration graph checks must not create an engine")

    monkeypatch.setattr("sqlalchemy.create_engine", forbidden)
    monkeypatch.setattr("a13n_harness_ui.storage.migration.create_engine", forbidden)
    monkeypatch.setattr("a13n_service.migrations.runner.create_engine", forbidden)
    assert main() == 0


def test_hook_runs_even_when_merge_has_no_conflicting_migration_files():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hook = next(hook for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "migration-heads")
    assert hook["always_run"] is True
    assert hook["pass_filenames"] is False
    assert "files" not in hook
    assert hook["entry"] == "uv run --locked python scripts/check_migration_heads.py"
