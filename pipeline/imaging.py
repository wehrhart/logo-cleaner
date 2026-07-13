"""Image download, decoding (incl. SVG), quality analysis, square-PNG normalization."""
import base64
import io
import re

from PIL import Image, ImageOps

from . import config, net

Image.MAX_IMAGE_PIXELS = 60_000_000

try:
    import cairosvg
    HAVE_SVG = True
except Exception:  # pragma: no cover
    HAVE_SVG = False


def fetch_image(url: str):
    """Return (bytes, content_type, error)."""
    if url.startswith("data:"):
        m = re.match(r"data:([^;,]+)?(;base64)?,(.*)", url, re.S)
        if not m:
            return None, "", "bad data uri"
        ct = m.group(1) or "application/octet-stream"
        payload = m.group(3)
        try:
            data = base64.b64decode(payload) if m.group(2) else payload.encode()
        except Exception as e:
            return None, "", f"data uri decode: {e}"
        return data, ct, None
    res = net.fetch(url)
    if not res.ok:
        return None, "", res.error or f"http {res.status}"
    return res.content, res.content_type, None


def load_image(data: bytes, content_type: str, url: str):
    """Decode bytes into RGBA PIL image. Returns (img, kind, error)."""
    is_svg = ("svg" in (content_type or "")) or url.lower().split("?")[0].endswith(".svg") \
             or data[:300].lstrip().startswith((b"<svg", b"<?xml"))
    if is_svg and b"<svg" in data[:5000].lower():
        if not HAVE_SVG:
            return None, "svg", "cairosvg unavailable"
        try:
            png = cairosvg.svg2png(bytestring=data, output_width=1024)
            return Image.open(io.BytesIO(png)).convert("RGBA"), "svg", None
        except Exception as e:
            return None, "svg", f"svg render: {repr(e)[:120]}"
    try:
        img = Image.open(io.BytesIO(data))
        if getattr(img, "is_animated", False):
            img.seek(0)
        if img.format == "ICO":
            # pick the largest frame
            sizes = getattr(img, "ico", None)
            if sizes:
                try:
                    biggest = max(img.ico.sizes())
                    img = img.ico.getimage(biggest)
                except Exception:
                    pass
        img.load()
        return img.convert("RGBA"), (img.format or "raster").lower(), None
    except Exception as e:
        return None, "raster", f"decode: {repr(e)[:120]}"


def trim(img: Image.Image) -> Image.Image:
    """Crop uniform transparent or near-white borders."""
    # transparent border
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    if bbox and bbox != (0, 0, img.width, img.height):
        img = img.crop(bbox)
    # near-white border (flatten on white first)
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(bg, img).convert("RGB")
    from PIL import ImageChops
    diff = ImageChops.difference(flat, Image.new("RGB", flat.size, (255, 255, 255)))
    bbox = diff.convert("L").point(lambda p: 255 if p > 12 else 0).getbbox()
    if bbox and bbox != (0, 0, img.width, img.height):
        img = img.crop(bbox)
    return img


def analyze(img: Image.Image):
    """Quality metrics for a trimmed candidate image."""
    w, h = img.size
    if w == 0 or h == 0:
        return {"reject": "empty"}
    aspect = max(w / h, h / w)
    small = img.copy()
    small.thumbnail((128, 128))
    rgb = small.convert("RGB")
    colors = rgb.getcolors(maxcolors=6000)
    n_colors = len(colors) if colors else 6000
    # blank detection: almost no visible variation
    import statistics
    px = list(rgb.getdata())
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px[:: max(1, len(px) // 2000)]]
    std = statistics.pstdev(lum) if lum else 0.0
    alpha = small.getchannel("A")
    visible = sum(1 for a in alpha.getdata() if a > 16) / (small.width * small.height)

    # white-variant logos (white-on-transparent) look blank on light UIs
    white_frac = 0.0
    vis_px = [p for p in small.getdata() if p[3] > 16]
    if vis_px:
        white_frac = sum(1 for p in vis_px if p[0] > 235 and p[1] > 235 and p[2] > 235) / len(vis_px)

    m = {"w": w, "h": h, "aspect": round(aspect, 2), "n_colors": n_colors,
         "lum_std": round(std, 1), "visible_frac": round(visible, 3),
         "white_frac": round(white_frac, 3)}
    m["white_on_transparent"] = bool(white_frac > 0.92 and visible < 0.98)
    if visible < 0.02 or std < 4.0:
        m["reject"] = "blank_or_near_blank"
    elif aspect > config.MAX_ASPECT:
        m["reject"] = f"extreme_aspect_{aspect:.1f}"
    elif max(w, h) < config.MIN_SOURCE_DIM:
        m["reject"] = f"too_small_{w}x{h}"
    m["photo_like"] = bool(n_colors >= 5000 and min(w, h) >= 500 and visible > 0.95 and std > 40)
    return m


def quality_factor(m: dict, source: str) -> float:
    """0..1 multiplier from image quality metrics."""
    if m.get("reject"):
        return 0.0
    size = max(m["w"], m["h"])
    size_f = min(1.0, size / 300.0)
    if size < 128:
        size_f *= 0.7
    photo_pen = 0.15 if (m.get("photo_like") and source in ("og_image", "twitter_image")) else 1.0
    aspect_pen = 1.0 if m["aspect"] <= 6 else 0.85
    white_pen = 0.25 if m.get("white_on_transparent") else 1.0
    return round(size_f * photo_pen * aspect_pen * white_pen, 3)


def normalize_to_square(img: Image.Image) -> Image.Image:
    """Center the trimmed logo on a square canvas with padding, >=250px."""
    w, h = img.size
    content_max = max(w, h)
    pad = config.PAD_RATIO
    pref = config.PREFERRED_OUTPUT_SIZE
    target_content = int(pref * (1 - 2 * pad))            # 420 for 500px canvas
    scale = min(target_content / content_max, config.MAX_UPSCALE)
    if scale < 1 or content_max >= config.MIN_OUTPUT_SIZE:
        canvas_size = pref
    else:
        # small source: upscale only as far as MAX_UPSCALE allows
        canvas_size = max(config.MIN_OUTPUT_SIZE, int(content_max * scale / (1 - 2 * pad)))
        canvas_size = min(canvas_size, pref)
        target_content = int(canvas_size * (1 - 2 * pad))
        scale = min(target_content / content_max, config.MAX_UPSCALE)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    # transparent canvas if the source has real transparency, else white
    alpha = img.getchannel("A")
    has_alpha = any(a < 250 for a in alpha.resize((32, 32)).getdata())
    bg = (255, 255, 255, 0) if has_alpha else (255, 255, 255, 255)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), bg)
    canvas.paste(resized, ((canvas_size - new_w) // 2, (canvas_size - new_h) // 2), resized)
    return canvas


def save_png(img: Image.Image, path) -> None:
    img.save(path, format="PNG", optimize=True)


def validate_file(path) -> list:
    """Return list of problems with a final PNG (empty list = OK)."""
    problems = []
    try:
        img = Image.open(path)
        img.load()
    except Exception as e:
        return [f"corrupt: {e}"]
    if (img.format or "").upper() != "PNG":
        problems.append(f"not_png:{img.format}")
    if img.width != img.height:
        problems.append(f"not_square:{img.width}x{img.height}")
    if img.width < config.MIN_OUTPUT_SIZE:
        problems.append(f"too_small:{img.width}")
    m = analyze(img.convert("RGBA"))
    if m.get("reject") == "blank_or_near_blank":
        problems.append("blank")
    if m.get("white_on_transparent"):
        problems.append("white_on_transparent")
    return problems
