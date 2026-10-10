---
title: Saved lists and "showing saved data"
description: Pipeline, Contacts and Companies open on the rows you saw last time while the fresh list loads, and say so.
aliases: showing saved data, refreshing, couldn't refresh, cached list, stale list, slow list, list loading, offline list
admin: false
---
## What you see

Pipeline, Contacts and Companies load the whole list before you can filter it, which can take
a moment on a large install. So each one opens straight onto the rows it showed you last time,
with a line above them: **Refreshing… showing saved data from** a time. When the fresh list
arrives it replaces the saved rows all at once and the line goes away.

If the fresh list cannot be loaded, the line changes to **Couldn't refresh — showing saved data
from** a time, with a **Retry** button. The saved rows stay on screen so you can keep reading;
press Retry once the connection is back. Anything you see in that state may be out of date by
however old the time on the line is.

After the Dashboard loads, the app also fills these three lists quietly in the background, one
at a time, so the first time you open each one it is already fast.

## Who can see the saved rows

The saved rows are kept in this browser, for the person signed in, and only for that person.
They are erased when you sign out (in any tab), when your session expires, and when someone
else signs in on this browser. They are thrown away after seven days, and after an app update
that changes what a list row holds.

## On a pipeline link

A link to a deal can open that deal from the saved board straight away. If the saved board
does not have the deal, the page waits for the fresh board before it tells you the deal is not
there, because saved rows can be days old.
