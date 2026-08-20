-- In-app password change (issue #78).
--
-- The live login credential lives here once the user sets one. AUTH_PASSWORD is
-- only the bootstrap value, consulted while password_hash IS NULL — so once the
-- user has chosen their own password the env var is inert and cannot silently
-- override it on the next boot.
--
-- The row is seeded (with a NULL hash) rather than inserted lazily so that
-- SELECT ... FOR UPDATE always has a real row to lock, including on the very
-- first password change. Locking an absent row is a no-op, which would let two
-- concurrent first-time changes both validate against the env password.

CREATE TABLE IF NOT EXISTS auth_credential (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO auth_credential (id, password_hash) VALUES (1, NULL)
    ON CONFLICT (id) DO NOTHING;
