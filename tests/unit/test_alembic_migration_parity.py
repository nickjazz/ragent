"""Every post-squash migrations/0NN_*.sql file must have a chained
alembic/versions revision reaching head — see docs/00_rule.md §Every
post-squash migration needs its own chained alembic revision.

squash's `upgrade()` re-reads migrations/schema.sql at apply time, so a
DB-replay test against a fresh database can never detect a missing chained
revision: squash always "self-heals" fresh installs from whatever
schema.sql says right now, regardless of whether a later 0NN file ever got
its own revision. Only an already-squashed DB (one that never re-runs
squash's upgrade()) is exposed to the gap, so the check has to be static —
over the files and the revision graph — not a database replay.
"""

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from ragent.bootstrap.init_schema import iter_statements

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "migrations"

# 001-011 predate the squash and were folded into it; only files numbered
# after the squash need their own chained revision.
_SQUASHED_CUTOFF = 11


def _post_squash_migration_files() -> dict[str, Path]:
    files = {}
    for f in MIGRATIONS_DIR.glob("*.sql"):
        m = re.match(r"(\d+)_", f.name)
        if m and int(m.group(1)) > _SQUASHED_CUTOFF:
            files[m.group(1)] = f
    return dict(sorted(files.items()))


_ADDITIVE_DDL = re.compile(
    r"\b(?:CREATE\s+TABLE|ADD\s+(?:INDEX|COLUMN|KEY|UNIQUE))\b(?!\s+IF\s+NOT\s+EXISTS)",
    re.IGNORECASE,
)


def _revisions_to_head() -> list[str]:
    script_dir = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    return [rev.revision for rev in script_dir.walk_revisions()]


def test_every_post_squash_migration_has_a_chained_revision() -> None:
    visited = _revisions_to_head()
    assert "squash" in visited, "revision chain does not terminate at squash"

    for number in _post_squash_migration_files():
        assert number in visited, (
            f"migrations/{number}_*.sql has no chained alembic/versions/{number}_*.py "
            "revision reaching head. Editing migrations/schema.sql alone never reaches "
            "an already-squashed database — add a revision file with down_revision "
            "pointing at the current head (docs/00_rule.md §Every post-squash migration "
            "needs its own chained alembic revision)."
        )


def test_every_post_squash_migration_has_defensive_ddl() -> None:
    for f in _post_squash_migration_files().values():
        for stmt in iter_statements(f.read_text(encoding="utf-8")):
            if _ADDITIVE_DDL.search(stmt):
                raise AssertionError(
                    f"migrations/{f.name} has an additive DDL statement without "
                    f"IF NOT EXISTS: {stmt!r} — squash dynamically re-reads "
                    "migrations/schema.sql on fresh installs, so a chained revision "
                    "without IF NOT EXISTS crashes with a duplicate-object error "
                    "(docs/00_rule.md §Defensive DDL)."
                )
