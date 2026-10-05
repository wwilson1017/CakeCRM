-- When the GTD weekly review was last marked done (#263). NULL = never, which the
-- Review page reads as "a review is due". Install-wide like the rest of the todo
-- store: one list, one review clock.
ALTER TABLE crm_meta ADD COLUMN IF NOT EXISTS todo_last_review_at TIMESTAMPTZ;
