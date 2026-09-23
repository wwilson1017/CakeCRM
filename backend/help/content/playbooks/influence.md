---
title: Influence: The Psychology of Persuasion
description: Cialdini's principles of persuasion — reciprocity, commitment, social proof, authority, liking, scarcity, unity — and the ethics of using them honestly.
aliases: Cialdini, persuasion, reciprocity, commitment, consistency, social proof, authority, liking, scarcity, unity, ethical persuasion, follow-up framing
admin: false
author: Robert B. Cialdini
---
## Core ideas

- **People take shortcuts, and the shortcuts are predictable.** Cialdini's research
  programme catalogued the automatic patterns that let humans decide quickly without
  thinking everything through. They are usually sensible. They are also exploitable, which
  is why the book spends as much time on defence as on use.
- **Reciprocity.** An unrequested favour creates a felt debt. In selling this means genuine
  help given before anything is asked for — an introduction, a piece of analysis, an honest
  warning about a risk in their plan.
- **Commitment and consistency.** People act in line with what they have already said,
  especially in public and in writing. A buyer who articulates their own problem is far
  more likely to act on it than one who merely agreed with yours.
- **Social proof.** Under uncertainty, people look at what similar others do. "Similar"
  does the work: a reference from an organization that looks like theirs, at their scale,
  with their constraints, is worth ten famous logos.
- **Authority.** Demonstrated expertise moves people, and the most credible form is
  specific and verifiable rather than asserted. Admitting a genuine weakness before
  presenting the strength raises perceived honesty, and lands the strength harder.
- **Liking.** We agree with people we like, and liking is driven by similarity, genuine
  compliments and having worked together on something. This is why real preparation about
  a person's business reads so differently from flattery.
- **Scarcity.** Things feel more valuable when they are limited, and people are moved more
  by what they stand to lose than by what they stand to gain. This is the principle most
  often faked, and faking it is the fastest way to destroy trust.
- **Unity.** The later addition to the list: shared identity — the same profession, the
  same region, the same hard experience — is stronger than mere similarity, because it is
  about who someone is rather than what they resemble.
- **The ethics are not a footnote.** Cialdini separates detecting what is genuinely there
  from manufacturing it. Pointing out real scarcity is information; inventing a deadline is
  a lie that works once. He calls the manufactured version smuggling, and argues it
  reliably costs more than it earns.
- **Defence matters as much as use.** Knowing the principles lets you notice when one is
  being used on you — a procurement deadline that appeared from nowhere, a concession
  designed to create a debt.

## When Baker reaches for it

- **A proposal is going out** — the framing of options, references and timing is where
  these principles apply most directly.
- **A follow-up has gone unanswered** — the reciprocity question is what genuine value you
  can give before asking again.
- **The buyer is comparing vendors** — social proof from a genuinely similar customer is
  more persuasive than any feature table.
- **The user is tempted to invent urgency** — a fake deadline to restart a stalled deal is
  exactly the misuse the book warns about, and it is worth saying so plainly.
- **A new relationship is starting** — liking and unity are built early, through
  preparation and shared ground, not through charm.
- **The user wants the buyer to commit to something small** — a pilot, a written summary
  they agree to, a date they name themselves.

## Applied to the CRM

- **Get the commitment in writing on the record** — when the buyer states their own
  problem or names their own date, capture the exact wording with `crm_add_note`. The
  point of consistency is that it is theirs, and paraphrase loses it.
- **Choose social proof from the CRM, not from memory** — `crm_search_companies` and
  `crm_analytics` can show which won deals actually resemble this one in size and
  situation, which is what makes the reference land.
- **A favour is a logged activity, not a feeling** — record what you gave and when with
  `crm_log_activity`, so the follow-up is grounded in something real rather than in "we
  have been very helpful".
- **Draft the follow-up rather than improvising it** — `gmail_create_draft` produces a
  reply for the user to review and put out themselves; Baker never delivers mail.
- **Check the history before claiming a relationship** — `crm_get_activity_log` shows what
  really happened on this deal. Authority built on a misremembered detail collapses the
  moment the buyer corrects it.
- **Never write a deadline into the deal that does not exist** — a date on the record
  implies a real constraint, and a manufactured one will be quoted back to you.

## Go read it

Robert B. Cialdini, *Influence: The Psychology of Persuasion* (first published 1984;
expanded edition 2021). Read it for the experiments rather than the tactics — the studies
are what make the principles stick, and the chapter on defending yourself is the reason
this belongs on a sales shelf rather than a manipulation one.
