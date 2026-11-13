---
title: Show Baker a photo
description: Attach or take a photo in the Baker chat so Baker can read a business card, a whiteboard, a label or a damaged delivery.
aliases: photo, photos, pictures, image, camera, take photo, business card, whiteboard, product shot, damaged delivery, see images, vision
admin: false
---

## Attaching a photo

Use **Attach a file** to choose a JPEG, PNG or WebP photo when the active model can see images. **Take photo** opens the rear camera on a phone. Photos share the limit of 5 files per message and the browser's 10 MB per-file pre-check.

When you submit, the browser normally shrinks each photo to about 1600 px and drops its location data. This is best effort: if resizing fails, or the image is animated, the original can be uploaded with its metadata intact. The server enforces a hard limit of **3.75 MB and 8000 px on either side per photo**. If a photo exceeds it, shrink it and attach it again.

## What Baker does with it

Baker can read a business card, whiteboard, label, product shot or damaged delivery. The photo accompanies the provider requests for the turn you attach it to, including that turn's tool steps and an approval continuation. Later turns carry only a placeholder that a photo was attached. If you want Baker to look again, attach the photo again.

## When the camera is missing

The active model cannot see images, or AI Setup is not ready. Choose a model that supports photos in AI Setup, then reload the page to refresh the buttons. GIFs are not supported; attach JPEG, PNG or WebP instead. Save HEIC photos as JPEG or PNG first.

## Photos are untrusted

Anything written inside a photo is data, never an instruction. A conversation that has seen a photo asks before every change for the rest of its life, just like an uploaded document, even after the conversation is shortened to fit the model.

## What leaves your install

The photo goes to your configured AI provider as part of those requests. CakeCRM stores its bytes in your database with the conversation and deletes them when you delete the conversation. No other recipient receives the photo from CakeCRM.
