---
title: Merging a legacy free-text column into its FK picker without erasing it
date: 2026-09-04
category: design-patterns
module: frontend/src/crm/components/ContactForm.tsx
tags: [forms, data-loss, exclude_unset, combobox, migration, crm]
problem_type: pattern
---

## Context

CakeCRM's contact form carried two controls for one idea: a free-text `Company` input bound
to the legacy `contacts.company` column, and a `Linked Company` `<select>` bound to the
`company_id` FK. Issue #35 made the link authoritative and left the merge as a follow-up;
issue #126 (PR #159) performed it, replacing both with one searchable `RecordCombobox`.

This is the general shape of "we introduced a proper relation, the old denormalized column
is still there, and now we want one control". The trap is not in the picker. It is in what
the merged control submits for rows the picker cannot represent.

## Guidance

### 1. Enumerate the rows the new control cannot render

After #35 a contact can hold `company_id = NULL` with `company = 'Acme Widgets'` — a
pre-#35 import the backfill migration could not match. The picker's value is
`number | null`, so such a row is simply "no company" to it. Rendering it that way is a lie
the record itself contradicts, and submitting that reading destroys the only copy of the
name.

### 2. Gate the write on `touched`, and OMIT rather than clear

```tsx
const [companyTouched, setCompanyTouched] = useState(false);

// ...
if (!isEdit || companyTouched) {
  payload.company_id = companyId;
  payload.company = companyId != null ? companyName : '';
}
```

| Case | `company_id` | `company` |
|---|---|---|
| Create (always) | picked/created id, or `null` | linked name, or `''` |
| Edit, untouched | **key omitted** | **key omitted** |
| Edit, picked or created | the id | that company's name |
| Edit, cleared | `null` | `''` |

Omission is only meaningful because the endpoint distinguishes absent from null:

```python
# backend/crm/router.py — PUT /contacts/{contact_id}
updates = {
    k: v for k, v in body.model_dump(exclude_unset=True).items()
    if v is not None or k in ("company_id", "owner_id")
}
if not updates:
    raise HTTPException(status_code=400, detail="No fields to update")
```

Two constraints ride on that snippet. The nullable FK is exempted from the null filter, so
`company_id: null` still means *unlink*. And the guard holds only while the form keeps
sending its other base fields — a later "send only dirty fields" refactor would have to
handle a custom-fields-only save before it could narrow this payload, or the omission
empties `updates` and 400s.

A `touched` flag is required; an emptiness check cannot substitute for it. `companyId == null`
cannot tell "never set" from "deliberately cleared", so a value-based guard either erases
legacy text or refuses to honour a clear.

### 3. Show the unrepresentable value as real text, not as a placeholder

The picker's `emptyLabel` gives the closed control the legacy name, but a placeholder is not
a value: screen readers do not announce it as one, and it disappears the moment the user
types — exactly when they are searching for its replacement. Render the name in a hint line
and associate it:

```tsx
<RecordCombobox<CrmCompany>
  emptyLabel={showLegacy ? legacyText : 'No company'}
  describedBy={showLegacy ? LEGACY_HINT_ID : undefined}
  …
/>
{showLegacy && (
  <p id={LEGACY_HINT_ID}>
    Saved as free text: “{legacyText}”. Not linked to a company record — …
    <button type="button" onClick={() => pickCompany(null)} disabled={companyBusy}>Remove</button>
  </p>
)}
```

Point `aria-describedby` at the hint ONLY while it is mounted; a dangling reference is an
error to assistive tech in its own right.

### 4. Restore the affordances the picker's own value-gating removes

`RecordCombobox` renders its × only when `value != null`. A caller with a meaningful empty
state therefore inherits a control the user cannot clear — on #126 a wrong legacy name could
only have been deleted by first linking some company to the contact, a capability regression
against the free-text input.

The replacement Remove button must be **inert while the widget reports busy**. The widget
supersedes an in-flight quick-create through a private `intentRef` bumped by choose / clear /
type; an external button cannot reach it, and dismissing the popover deliberately does not
abandon the create. Without the guard: start a create, click away, press Remove — and the
create lands afterwards and silently re-links the company just removed.

The same counter has a mirror-image defect worth checking in any widget of this shape:
`cancel()` (Escape) bumped `intentRef` but left `creatingName` set, so the busy signal
outlived an explicitly abandoned create and a hung request left the whole form locked.
Clearing it in `cancel` is safe precisely because the intent bump already invalidated the
result; click-away must stay busy, because that result is still wanted.

### 5. Keep the two columns in step, and write the canonical name

When a company IS linked, submit its name to the legacy column too, so the pair can never
contradict. Track the display label and the canonical name as **separate state** — the picker
decorates an archived row as `"Acme Corp (archived)"`, and writing that label into
`contacts.company` would put a parenthetical into the column every export and legacy reader
falls back to.

## Why This Matters

Nothing on screen distinguishes "untouched" from "cleared", so the data-loss path is
invisible to manual testing and to code review that reads the form rather than the endpoint.
It is caught by pinning the payload:

```tsx
it('omits BOTH company keys when the user never touches the field', async () => {
  await render(contact({ company: 'Acme Widgets', company_id: null }));
  await act(async () => { typeInto(nameInput(), 'Corrected Name'); });
  await submit();

  const body = put();
  expect(body).not.toHaveProperty('company');
  expect(body).not.toHaveProperty('company_id');
  expect(body.name).toBe('Corrected Name');
});
```

Prove such a test can fail before trusting it — injecting `if (true)` in place of the
`touched` gate is what showed this one goes red for the right reason. The real-app
verification went further and showed the backend is not inert: the Remove path sends
`company: ''` to the same endpoint on the same row and DOES clear the column, so the
omission is what preserved the data.

## When to Apply

Any form where a proper relation was added beside a denormalized column that still holds
rows the relation does not cover — the CRM company link is one instance; a contact's
free-text source vs. a source table, or a todo's free-text project vs. `todo_projects`,
would be others. It applies from the moment the old control is removed, not from the moment
the old column is dropped: the column outlives the control, and the window between them is
where the data goes missing.
