"""小组件皮肤：把背景图/动图处理成 tkinter 可用的帧（含圆角遮罩与透明色键）。

透明做法：圆角之外填 MAGIC 颜色，窗口用 `-transparentcolor` 把该颜色抠掉。
"""

from __future__ import annotations

MAGIC = "#010203"


def _fit(img, w: int, h: int, mode: str):
    from PIL import Image

    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if mode == "stretch":
        return img.resize((w, h), Image.LANCZOS)
    scale = min(w / iw, h / ih) if mode == "contain" else max(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)
    if mode == "contain":
        base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        base.paste(resized, ((w - nw) // 2, (h - nh) // 2))
        return base
    left, top = max(0, (nw - w) // 2), max(0, (nh - h) // 2)
    return resized.crop((left, top, left + w, top + h))


def load_frames(path: str, w: int, h: int, radius: int = 14, fit: str = "cover",
                animate: bool = True, limit: int = 150):
    """返回 (frames: list[PIL.Image(RGB)], durations: list[int], is_animated)。"""
    from PIL import Image, ImageChops, ImageDraw, ImageSequence

    img = Image.open(path)
    animated = bool(getattr(img, "is_animated", False)) and animate
    if animated:
        # 逐帧读 duration：容器级 img.info 只有最后一帧的值，统一套用会让变速动图节奏全错
        raw: list = []
        durations: list[int] = []
        for frame in ImageSequence.Iterator(img):
            raw.append(frame.copy().convert("RGBA"))
            durations.append(int((frame.info or {}).get("duration") or 100))
    else:
        raw = [img.convert("RGBA")]
        durations = [0]
    if limit and len(raw) > limit:
        raw, durations = raw[:limit], durations[:limit]

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=max(0, int(radius)), fill=255)

    frames = []
    for frame in raw:
        fitted = _fit(frame, w, h, fit)
        if fitted.size != (w, h):
            fitted = fitted.resize((w, h), Image.LANCZOS)
        alpha = ImageChops.multiply(fitted.getchannel("A"), mask)
        base = Image.new("RGB", (w, h), MAGIC)
        base.paste(fitted.convert("RGB"), (0, 0), alpha)
        frames.append(base)
    return frames, durations, animated
