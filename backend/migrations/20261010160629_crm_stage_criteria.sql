-- #289: an install's own "criteria to enter this stage", per pipeline stage.
--
-- One row per OVERRIDDEN stage; a stage with no row shows the standard copy, which lives in
-- backend/crm/stage_criteria_standard.json (not in the database, so a fix to the standard
-- text reaches every install that has not replaced it). Deleting a row is "Reset to standard".
-- No CHECK on stage: the stage list is code (crm.service.DEAL_STAGES) and the service refuses
-- an unknown key, so a constraint here would only need a migration of its own to move.
CREATE TABLE IF NOT EXISTS crm_stage_criteria (
    stage       TEXT PRIMARY KEY,
    summary     TEXT NOT NULL,
    checklist   JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
