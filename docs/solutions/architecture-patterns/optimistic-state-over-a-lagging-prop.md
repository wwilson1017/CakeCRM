---
title: Optimistic state over a prop that lags — version-ordered adoption
date: 2026-09-04
category: architecture-patterns
module: frontend/src/crm/gtd/components/TriageCard.tsx
tags: [react, optimistic-ui, concurrency, useReducer, updated_at, stale-closure]
problem_type: pattern
---

## Context

A component renders a record it receives as a prop, and also writes to that record. The parent
refetches asynchronously, so between a write being sent and the refetch landing the prop still
describes the record as it was. CakeCRM's GTD triage card (#150) hit this with three optimistic
values at once — a title, a due date and a notes draft — plus fields it writes but never renders
optimistically (star, project).

The naive shape (render the prop, keep a `pending*` override beside it, clear the override when
the prop changes) produced five distinct defects across four review stages. They look unrelated
and have one cause.

## Guidance

**Do not render the prop. Render the component's own view of the record, and advance it from
whichever source is NEWER — the prop, or the row a write of yours answered with.**

```tsx
const isNewer = (a: string, b: string): boolean => {
  const na = Date.parse(a), nb = Date.parse(b);
  if (Number.isNaN(na) || Number.isNaN(nb)) return false;
  return na !== nb ? na > nb : subMs(a) > subMs(b);   // see "Version, not content"
};

function adopt(s: CardState, r: Row): CardState {
  if (!isNewer(r.updated_at, s.row.updated_at)) return s;
  return {
    ...s,
    row: r,
    // Unsaved text of the user's own is never overwritten.
    draft: s.draft === s.row.notes ? r.notes : s.draft,
  };
}
```

Four rules make it work:

**1. Version, not content.** A write's response is newer than the prop the parent is still
holding. Comparing CONTENT reads that lagging prop as an outside change, so the draft rewinds
the instant a save succeeds and the next blur writes the pre-save text back over it. Order on
the row's version column.

Watch the precision: `Date.parse` truncates at the millisecond, and a Postgres `TIMESTAMPTZ`
rendered by `datetime.isoformat()` carries microseconds. Break the tie on the FRACTIONAL SECONDS,
not by comparing the whole string — `Z` sorts after `+`, so a lexical compare calls the same
instant written two ways a newer version and adopts your own echo.

**2. Every write path adopts its own response.** Fields written straight through and never
rendered optimistically (a star toggle, a project assignment) are only current because of this.
Without it, an editor opened after the write acknowledges but before the refetch lands sees
pre-write values, and its full-row save reverts them. Miss ONE write path and that path alone
regresses — on #150 it was the create-a-project-inline call.

**3. Release an override when its OWN write settles, never on disagreement.** A row is not
evidence about a write still in flight. "The adopted row disagrees with my override" cannot
distinguish an outside change from a row committed before your write was — and an earlier write
of your own, answering first, carries exactly that disagreement.

**4. Decide against state as it is NOW, not as the handler captured it.** These handlers run
from async callbacks, and a callback closes over the render that created it. Put the state in a
`useReducer` and mirror it into a ref that one `apply()` advances with the same pure reducer
before dispatching:

```tsx
const [state, dispatch] = useReducer(reduce, initial, init);
const stateRef = useRef(state);
const apply = useCallback((a: Action) => {
  stateRef.current = reduce(stateRef.current, a);   // synchronous, for continuations
  dispatch(a);                                      // scheduled, for rendering
}, []);
```

The reducer half stops a title save that resolves after the user started typing from comparing
against the empty draft it captured, calling the box clean, and **overwriting what was typed**.
The ref half lets a continuation read the component before React commits — build any payload
handed to a child editor from `stateRef.current` at the moment it is needed, never captured at
click time.

## Why This Matters

Each of the five defects was found by a different reviewer and each looked like its own bug:
a notes box that rewound on save, a due-date field pinned to the first date picked, a stale star
in an edit sheet, a duplicate write from a stacked promise queue, and typed notes silently
discarded by a concurrent title save. All five are the same mistake — treating a lagging prop as
authoritative, or treating a captured render as current.

The cost of the naive shape is not one bug you can fix; it is a bug per field per interleaving.

## When to Apply

When a component both renders and writes the same record, AND the parent refetches
asynchronously rather than handing back the write's result. If the parent applies the write's
response directly, none of this is needed — prefer that.

Not needed for a single field with no concurrent writer. The machinery earns itself at two
optimistic fields or one plus a background writer (another tab, an assistant tool, a second
device).

Adjacent: `docs/solutions/design-patterns/silent-refresh-over-optimistic-ui.md` covers the
neighbouring case — guarding a background GET against in-flight optimistic writes.
