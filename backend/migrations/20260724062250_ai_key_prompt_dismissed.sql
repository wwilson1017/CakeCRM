-- Durable dismissal for the first-run "add an AI key" nudge (issue #9).
-- Mirrors crm_meta.onboarding_dismissed; the CRM stays fully usable with zero
-- AI keys, so this nudge is a prompt (dismissible), never a gate.
ALTER TABLE crm_meta ADD COLUMN IF NOT EXISTS ai_key_prompt_dismissed BOOLEAN NOT NULL DEFAULT FALSE;
