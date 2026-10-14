"""QR codes as PNG data URIs — the 2FA enrolment code and the todo app's sign-in code.

`qrcode` draws black modules on an opaque white field with a quiet-zone border, so the
image scans on a dark card without any wrapper colour.
"""

import base64
import io

import qrcode


def qr_png_data_uri(text: str) -> str:
    img = qrcode.make(text, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
