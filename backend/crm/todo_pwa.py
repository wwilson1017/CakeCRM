"""Todo-GTD — shared web app manifest for the no-login surfaces (#70).

/capture and /todo both install as standalone home-screen apps; the only differences
are the name, description and icon set. Ported from chatty's `core/todo/pwa.py`.
"""

import json

from fastapi.responses import Response


def manifest_response(*, name: str, description: str, base_path: str) -> Response:
    """Web app manifest so Add to Home Screen installs a standalone app.

    start_url/scope carry the secret path when one is set, so the installed app
    always opens already "authorized" — the token is baked into the launch URL. That
    is also why the response is no-store: it must never outlive a regenerate.
    """
    manifest = {
        "name": name,
        "short_name": name,
        "description": description,
        # No trailing slash on purpose: the page is served (and the settings UI
        # copies the link) at exactly base_path, and a base_path + "/" scope would
        # put the install page itself outside its own manifest scope, degrading
        # installability.
        "start_url": base_path,
        "scope": base_path,
        "display": "standalone",
        "background_color": "#fffffa",
        "theme_color": "#e31d3b",
        # Maskable PWA icons: the brand mark at 70% on the card colour, so the safe zone
        # of a masked launcher never clips it (frontend/public/icon-*.png, from #163).
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any maskable"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }
    return Response(
        json.dumps(manifest),
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )
