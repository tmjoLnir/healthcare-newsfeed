"""tools/store_sync.sh — the guards that keep an empty store off the channel.

The script is what makes the schedules trustworthy: it carries the database
between runs and refuses, loudly, in every case where continuing would either
publish an empty issue or overwrite good history. Those refusals are the
whole point, so they are tested rather than assumed.

`gh` is stubbed by a script on PATH that keeps its "release" in a directory,
so these run offline like the rest of the suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from healthcare_newsfeed.models import RawItem
from healthcare_newsfeed.store import Store

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "store_sync.sh"

GH_STUB = """#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path

remote = Path(os.environ["FAKE_RELEASE_DIR"])
argv = sys.argv[1:]
Path(os.environ["GH_CALLS"]).open("a").write(" ".join(argv) + "\\n")

if argv[:2] == ["release", "download"]:
    asset = argv[argv.index("--pattern") + 1]
    out = argv[argv.index("--output") + 1]
    src = remote / asset
    if not src.exists():
        sys.exit("release not found")
    shutil.copyfile(src, out)
elif argv[:2] == ["release", "view"]:
    sys.exit(0 if (remote / ".exists").exists() else 1)
elif argv[:2] == ["release", "create"]:
    (remote / ".exists").touch()
elif argv[:2] == ["release", "upload"]:
    shutil.copyfile(argv[3], remote / Path(argv[3]).name)
else:
    sys.exit(f"stub gh: unexpected {argv}")
"""


def make_store(path: Path, items: int) -> None:
    """A real store with `items` rows, built through the real code path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with Store(path) as store:
        store.migrate()
        store.upsert([
            RawItem(source_key="bbc_health", title=f"Item {n}",
                    url=f"https://bbc.test/{n}", published=None)
            for n in range(items)
        ])


@pytest.fixture
def env(tmp_path):
    """A working directory, a stubbed gh, and an empty release to talk to."""
    bindir, remote, state = tmp_path / "bin", tmp_path / "release", tmp_path / "state"
    for directory in (bindir, remote, state):
        directory.mkdir()
    stub = bindir / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)

    environ = {
        **os.environ,
        "PATH": f"{bindir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        "NEWSFEED_DB": str(tmp_path / "data" / "newsfeed.db"),
        "STORE_STATE_DIR": str(state),
        "STORE_RELEASE_TAG": "store",
        "FAKE_RELEASE_DIR": str(remote),
        "GH_CALLS": str(tmp_path / "gh-calls"),
    }
    environ.pop("STORE_BOOTSTRAP", None)
    return type("Env", (), {
        "environ": environ, "tmp": tmp_path, "remote": remote,
        "db": Path(environ["NEWSFEED_DB"]),
        "calls": lambda self: Path(environ["GH_CALLS"]).read_text()
        if Path(environ["GH_CALLS"]).exists() else "",
    })()


def run(env, verb, **overrides):
    return subprocess.run(
        [str(SCRIPT), verb], capture_output=True, text=True,
        env={**env.environ, **overrides}, cwd=env.tmp,
    )


def publish_release(env, items: int) -> None:
    """Put a healthy store of `items` rows in the fake release."""
    staged = env.tmp / "staged.db"
    make_store(staged, items)
    subprocess.run(["gzip", "-9", "-c", str(staged)], check=True,
                   stdout=(env.remote / "newsfeed.db.gz").open("wb"))
    (env.remote / ".exists").touch()


# --- restore ---------------------------------------------------------------

def test_restore_refuses_an_empty_start_by_default(env):
    """The refusal that keeps a lost store from publishing as a quiet week."""
    result = run(env, "restore")
    assert result.returncode != 0
    assert "STORE_BOOTSTRAP is not set" in result.stderr
    assert not env.db.exists()


def test_restore_bootstraps_only_when_asked(env):
    result = run(env, "restore", STORE_BOOTSTRAP="true")
    assert result.returncode == 0, result.stderr
    assert "BOOTSTRAP" in result.stdout
    assert "only correct once" in result.stdout


def test_restore_brings_back_the_stored_items(env):
    publish_release(env, items=40)
    result = run(env, "restore")
    assert result.returncode == 0, result.stderr
    assert "restored 40 items" in result.stdout
    with Store(env.db) as store:
        assert store.conn.execute("SELECT count(*) FROM items").fetchone()[0] == 40


def test_restore_rejects_a_corrupt_asset_without_offering_bootstrap(env):
    """A corrupt asset must not be papered over by starting fresh — that would
    publish an empty store over the last good one."""
    publish_release(env, items=40)
    asset = env.remote / "newsfeed.db.gz"
    subprocess.run(["gzip", "-9", "-c"], input=b"definitely not a database" * 100,
                   stdout=asset.open("wb"), check=True)
    result = run(env, "restore")
    assert result.returncode != 0
    assert "unusable" in result.stderr
    assert "would publish an empty store" in result.stderr


# --- save ------------------------------------------------------------------

def test_save_refuses_without_a_restore_receipt(env):
    """No receipt means restore never proved this file descends from the
    stored one, so uploading it could destroy history."""
    make_store(env.db, 10)
    result = run(env, "save")
    assert result.returncode != 0
    assert "no restore receipt" in result.stderr
    assert not (env.remote / "newsfeed.db.gz").exists()


def test_save_refuses_a_store_that_shrank(env):
    publish_release(env, items=40)
    assert run(env, "restore").returncode == 0
    env.db.unlink()
    make_store(env.db, 5)                      # a different, smaller database
    result = run(env, "save")
    assert result.returncode != 0
    assert "shrank from 40 to 5 items" in result.stderr


def test_save_uploads_a_grown_store(env):
    publish_release(env, items=40)
    assert run(env, "restore").returncode == 0
    with Store(env.db) as store:
        store.upsert([RawItem(source_key="kff", title="New",
                              url="https://kff.test/new", published=None)])
    result = run(env, "save")
    assert result.returncode == 0, result.stderr
    assert "saved 41 items (+1)" in result.stdout
    assert "--clobber" in env.calls()


def test_save_creates_the_release_on_the_first_run(env):
    """The release is created with its first asset, never before it — so a
    release that exists always has a store in it."""
    assert run(env, "restore", STORE_BOOTSTRAP="true").returncode == 0
    make_store(env.db, 3)
    result = run(env, "save")
    assert result.returncode == 0, result.stderr
    assert "creating release" in result.stdout
    assert (env.remote / "newsfeed.db.gz").exists()


def test_save_refuses_a_corrupt_store(env):
    publish_release(env, items=40)
    assert run(env, "restore").returncode == 0
    env.db.write_bytes(b"truncated garbage")
    result = run(env, "save")
    assert result.returncode != 0
    assert "not a healthy store" in result.stderr


def test_save_refuses_a_database_that_is_not_ours(env):
    """A valid SQLite file with the wrong schema is still the wrong file."""
    assert run(env, "restore", STORE_BOOTSTRAP="true").returncode == 0
    import sqlite3
    env.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(env.db)
    conn.execute("CREATE TABLE something_else (x INTEGER)")
    conn.commit()
    conn.close()
    result = run(env, "save")
    assert result.returncode != 0
    assert "missing table" in result.stderr


def test_round_trip_survives_a_second_cycle(env):
    """Two full poll-shaped cycles: what the second restores is what the first
    saved, plus what it added."""
    assert run(env, "restore", STORE_BOOTSTRAP="true").returncode == 0
    make_store(env.db, 12)
    assert run(env, "save").returncode == 0

    shutil.rmtree(env.db.parent)               # a fresh runner
    assert run(env, "restore").returncode == 0
    with Store(env.db) as store:
        assert store.conn.execute("SELECT count(*) FROM items").fetchone()[0] == 12
        store.upsert([RawItem(source_key="who_news", title="Later",
                              url="https://who.test/later", published=None)])
    assert run(env, "save").returncode == 0

    shutil.rmtree(env.db.parent)
    result = run(env, "restore")
    assert "restored 13 items" in result.stdout
