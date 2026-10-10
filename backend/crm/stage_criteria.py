"""Pipeline stage criteria: the standard set, or an install's own, per stage (#289).

The standard copy is `stage_criteria_standard.json` beside this file — the one place it is
written down. The frontend never bundles it; it reads the merged set from
`GET /api/crm/stage-criteria`, and `frontend/src/crm/stageCriteria.test.ts` reads the JSON
to pin that the copy stays vertical-neutral. An override is one `crm_stage_criteria` row;
deleting the row restores the standard.
"""

import json
from pathlib import Path

from psycopg2.extras import Json

from core.postgres import pg_execute, pg_fetchall, pg_fetchone
from crm.service import DEAL_STAGES

STANDARD: dict[str, dict] = json.loads(
    (Path(__file__).with_name("stage_criteria_standard.json")).read_text(encoding="utf-8")
)


def _check_stage(stage: str) -> None:
    if stage not in DEAL_STAGES:
        raise LookupError(f"Unknown stage: {stage}")


def _entry(stage: str, row: dict | None) -> dict:
    if row:
        return {"stage": stage, "summary": row["summary"], "checklist": row["checklist"],
                "source": "custom"}
    std = STANDARD[stage]
    return {"stage": stage, "summary": std["summary"], "checklist": list(std["checklist"]),
            "source": "standard"}


def list_criteria() -> list[dict]:
    """Every board stage in board order, each the install's override or the standard."""
    rows = {r["stage"]: r for r in pg_fetchall(
        "SELECT stage, summary, checklist FROM crm_stage_criteria ORDER BY stage"
    )}
    return [_entry(stage, rows.get(stage)) for stage in DEAL_STAGES]


def set_criteria(stage: str, summary: str, checklist: list[str]) -> dict:
    _check_stage(stage)
    row = pg_fetchone(
        """INSERT INTO crm_stage_criteria (stage, summary, checklist, updated_at)
           VALUES (%s, %s, %s, now())
           ON CONFLICT (stage) DO UPDATE
              SET summary = EXCLUDED.summary, checklist = EXCLUDED.checklist,
                  updated_at = EXCLUDED.updated_at
           RETURNING stage, summary, checklist""",
        (stage, summary, Json(checklist)),
    )
    return _entry(stage, row)


def reset_criteria(stage: str) -> dict:
    _check_stage(stage)
    pg_execute("DELETE FROM crm_stage_criteria WHERE stage = %s", (stage,))
    return _entry(stage, None)
