-- #352: system notes are not customer contact; readers test this column, never message text.
ALTER TABLE crm_chatter ADD COLUMN IF NOT EXISTS system_note BOOLEAN NOT NULL DEFAULT false;

-- Backfill historical system text, including the whole leading run of merge-copy prefixes.
UPDATE crm_chatter
   SET system_note = true
 WHERE NOT system_note
   AND regexp_replace(message, '^(\[Merged from (deal|company) #[0-9]+\] )+', '')
       ~ '^(Confirmed AI-populated value for |Archived — |Restored from archive\.|Closed on: |Deal lost — |Merged deal #|Merged company #)';
