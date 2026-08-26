-- Baker is a permanent product brand, not a setting (issue #71).
--
-- The application no longer reads or writes assistant_identity.name: get_identity()
-- returns the NAME constant and update_identity() has no name parameter. This
-- migration exists so the STORED value agrees with that truth as well, which matters
-- in exactly one place — an install that renamed its assistant before #71 and then
-- rolls back to a pre-#71 binary would otherwise resurrect the old name.
--
-- The column is deliberately NOT dropped. Dropping it would make the upgrade a
-- one-way door (a rolled-back binary still does `SELECT name, personality`, and a
-- missing column breaks the identity read outright — a dead assistant, not merely a
-- misnamed one). A vestigial column that nothing reads is the cheaper trade, and it
-- follows the same call this repo already made for `auth_credential`. A later
-- release may drop it.

UPDATE assistant_identity SET name = 'Baker' WHERE name IS DISTINCT FROM 'Baker';
