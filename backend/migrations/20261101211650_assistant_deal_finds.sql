-- Additive conversation guard state: deals found by CRM tools before a write.
-- No deal FK: these are remembered finds, not CRM work product. CRM reset clears
-- this table alongside deals because RESTART IDENTITY can reuse an old deal id, and
-- crm_epoch (crm_meta.updated_at when the find was made) stops a find saved after a
-- reset from counting.
CREATE TABLE IF NOT EXISTS assistant_deal_finds (
    conversation_id TEXT NOT NULL REFERENCES assistant_conversations(id) ON DELETE CASCADE,
    deal_id INTEGER NOT NULL,
    crm_epoch TIMESTAMPTZ,
    found_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, deal_id)
);
