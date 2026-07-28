"""
Generates barcode and QR code images locally. Nothing here calls out to
any external service - python-barcode and qrcode both render images purely
from the input string using on-device drawing logic.
"""
import io
import random
import string

import barcode
from barcode.writer import ImageWriter
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer

SUPPORTED_TYPES = ["CODE128", "EAN13", "QR"]


def random_code(length=12, digits_only=False):
    if digits_only:
        return "".join(random.choices(string.digits, k=length))
    return "".join(random.choices(string.digits, k=length))


def generate_image(code_type, value):
    """Returns (PIL.Image, final_value_used)."""
    code_type = code_type.upper()

    if code_type == "QR":
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(value)
        qr.make(fit=True)
        img = qr.make_image(
            image_factory=StyledPilImage,
            module_drawer=RoundedModuleDrawer(),
            fill_color="#12203A",
            back_color="#FFFFFF",
        )
        return img.convert("RGB"), value

    if code_type == "EAN13":
        digits = "".join(ch for ch in value if ch.isdigit())
        digits = (digits + random_code(12, digits_only=True))[:12]
        writer = ImageWriter()
        writer.dpi = 300
        ean = barcode.get("ean13", digits, writer=writer)
        buf = io.BytesIO()
        ean.write(
            buf,
            options={
                "module_height": 18.0,
                "font_size": 9,
                "text_distance": 4,
                "quiet_zone": 4,
                "foreground": "#12203A",
            },
        )
        buf.seek(0)
        from PIL import Image

        return Image.open(buf).convert("RGB"), ean.get_fullcode()

    # default CODE128
    writer = ImageWriter()
    writer.dpi = 300
    code128 = barcode.get("code128", value, writer=writer)
    buf = io.BytesIO()
    code128.write(
        buf,
        options={
            "module_height": 15.0,
            "font_size": 9,
            "text_distance": 4,
            "quiet_zone": 4,
            "foreground": "#12203A",
        },
    )
    buf.seek(0)
    from PIL import Image

    return Image.open(buf).convert("RGB"), value
