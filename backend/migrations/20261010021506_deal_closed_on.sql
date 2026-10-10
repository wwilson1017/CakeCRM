-- #279: the day a Won deal actually closed (port of the upstream "Closed on" feature).
--
-- Not expected_close_date, which is the rep's forecast. Written only by the stage
-- writers in crm.service (set when a deal ENTERS won, cleared when it leaves won) and
-- by update_deal's Won-only edit. Nullable: a deal that is not won has no close day,
-- and a won deal with no recorded move into won stays undated for a rep to fill in.
ALTER TABLE deals ADD COLUMN IF NOT EXISTS closed_on DATE;

-- Backfill: the newest move INTO won, as a calendar day in the install timezone.
-- run_migrations hands the zone (chosen by zoneinfo from TIMEZONE) to this file as the
-- transaction-local setting cakecrm.timezone; outside it this falls back to UTC.
-- Only undated Won deals, so it is idempotent and never overwrites a rep's date.
UPDATE deals d
   SET closed_on = (
       SELECT (MAX(e.changed_at)
               AT TIME ZONE COALESCE(NULLIF(current_setting('cakecrm.timezone', true), ''), 'UTC'))::date
         FROM deal_stage_events e
        WHERE e.deal_id = d.id AND e.new_stage = 'won')
 WHERE d.stage = 'won' AND d.closed_on IS NULL
   AND EXISTS (SELECT 1 FROM deal_stage_events e WHERE e.deal_id = d.id AND e.new_stage = 'won');
