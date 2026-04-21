from __future__ import annotations

import re
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except Exception as exc:  # pragma: no cover
    raise RuntimeError("Pillow 가 없어 로컬 이미지 fallback 을 사용할 수 없습니다.") from exc


DEFAULT_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


PALETTE = {
    "navy": "#0F172A",
    "blue": "#2563EB",
    "sky": "#DBEAFE",
    "line": "#D5E6FF",
    "soft": "#F8FBFF",
    "text": "#334155",
    "muted": "#64748B",
}


def _pick_font_path() -> str:
    for candidate in DEFAULT_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("사용 가능한 한글 폰트를 찾지 못했습니다.")


def _fit_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = list(text.replace("\n", " ").split())
    if not words:
        return [""]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
    lines.append(current)
    return lines


def _summary_source(title: str, excerpt: str, article_markdown: str | None = None) -> str:
    normalized_excerpt = excerpt.strip()
    weak_markers = {"", "요약", "상단 요약", title.strip()}
    if normalized_excerpt not in weak_markers and len(normalized_excerpt) >= 18:
        return normalized_excerpt

    if article_markdown:
        for raw_line in article_markdown.splitlines():
            line = raw_line.strip().lstrip("-•# ").strip()
            if not line:
                continue
            if line in weak_markers:
                continue
            if len(line) >= 28:
                return line
    return title.strip()


def _trim_point(text: str, max_len: int = 62) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_len:
        return normalized
    trimmed = normalized[: max_len - 3].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed.rstrip(" ,") + "..."



def _is_meta_summary_candidate(text: str) -> bool:
    lowered = text.strip().lower()
    return any(marker in lowered for marker in ["이미지 세트", "image set", "thumbnail", "summary_card"])



def _summary_points(title: str, excerpt: str, article_markdown: str | None = None, *, limit: int = 3) -> list[str]:
    points: list[str] = []
    weak_markers = {"", "요약", "상단 요약", title.strip()}

    if article_markdown:
        for raw_line in article_markdown.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith(("-", "•")):
                candidate = stripped.lstrip("-• ").strip()
                if candidate and candidate not in weak_markers and len(candidate) >= 10 and not _is_meta_summary_candidate(candidate):
                    points.append(_trim_point(candidate))
            if len(points) >= limit:
                return points[:limit]

    source = _summary_source(title, excerpt, article_markdown=article_markdown)
    if source:
        fragments = [fragment.strip(" ·-•\t") for fragment in re.split(r"[.!?]\s+|\n+", source)]
        idx = 0
        while idx < len(fragments):
            candidate = fragments[idx]
            if candidate and candidate not in weak_markers and len(candidate) >= 10 and not _is_meta_summary_candidate(candidate):
                if len(candidate) < 18 and idx + 1 < len(fragments):
                    next_fragment = fragments[idx + 1].strip(" ·-•\t")
                    if next_fragment:
                        candidate = f"{candidate}. {next_fragment}"
                        idx += 1
                points.append(_trim_point(candidate))
            if len(points) >= limit:
                return points[:limit]
            idx += 1

    fallback = [
        "일반공급과 특별공급 기준을 분리해서 보셔야 합니다.",
        "통장, 거주요건, 무주택 여부를 먼저 점검해야 합니다.",
        "최종 일정과 자격은 공고문과 청약홈으로 확인해야 합니다.",
    ]
    for item in fallback:
        trimmed_item = _trim_point(item)
        if trimmed_item not in points:
            points.append(trimmed_item)
        if len(points) >= limit:
            break
    return points[:limit]


def _create_canvas(size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    width, height = size
    img = Image.new("RGB", (width, height), "#EAF4FF")
    draw = ImageDraw.Draw(img)

    for y in range(height):
        c1 = (234, 244, 255)
        c2 = (208, 229, 255)
        t = y / height
        color = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
        draw.line((0, y, width, y), fill=color)

    for box, color in [
        ((640, -20, 1180, 460), (96, 165, 250, 110)),
        ((-120, 660, 360, 1140), (191, 219, 254, 120)),
        ((720, 720, 1220, 1220), (37, 99, 235, 50)),
    ]:
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.ellipse(box, fill=color)
        overlay = overlay.filter(ImageFilter.GaussianBlur(38))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((52, 52, width - 52, height - 52), radius=42, fill="white")
    draw.rounded_rectangle((52, 52, width - 52, height - 52), radius=42, outline=PALETTE["line"], width=3)
    return img, draw


def _draw_badge(draw: ImageDraw.ImageDraw, *, font_path: str, text: str = "청약 A to Z") -> None:
    badge_font = ImageFont.truetype(font_path, 36)
    draw.rounded_rectangle((86, 86, 282, 140), radius=22, fill=PALETTE["navy"])
    draw.text((112, 96), text, font=badge_font, fill="white")


def _draw_thumbnail(
    *,
    title: str,
    excerpt: str,
    article_markdown: str | None,
    output_path: Path,
    size: tuple[int, int],
) -> str:
    width, _ = size
    font_path = _pick_font_path()
    img, draw = _create_canvas(size)
    _draw_badge(draw, font_path=font_path)

    headline_font = ImageFont.truetype(font_path, 86)
    body_font = ImageFont.truetype(font_path, 34)
    body_bold_font = ImageFont.truetype(font_path, 38)
    note_font = ImageFont.truetype(font_path, 28)

    title_lines = _fit_lines(draw, title, headline_font, 520)[:3]
    title_y = 188
    for index, line in enumerate(title_lines):
        color = PALETTE["blue"] if index == len(title_lines) - 1 else PALETTE["navy"]
        draw.text((92, title_y), line, font=headline_font, fill=color)
        title_y += 106

    draw.rounded_rectangle((92, 520, 628, 786), radius=30, fill=PALETTE["soft"], outline="#DCEBFF", width=2)
    draw.text((122, 560), "결론 먼저", font=body_bold_font, fill=PALETTE["blue"])

    excerpt_source = _summary_source(title, excerpt, article_markdown=article_markdown)
    if len(excerpt_source) > 90:
        excerpt_source = excerpt_source[:90].rstrip() + "..."
    excerpt_lines = _fit_lines(draw, excerpt_source, body_font, 440)[:4]
    point_y = 624
    for line in excerpt_lines:
        draw.text((122, point_y), line, font=body_font, fill=PALETTE["text"])
        point_y += 48

    draw.rounded_rectangle((92, 824, 628, 920), radius=28, fill=PALETTE["navy"])
    draw.text((126, 854), "공고문 + 청약홈으로 최종 확인", font=body_bold_font, fill="white")

    for x1, y1, x2, y2, radius, fill in [
        (688, 320, 950, 886, 28, "#DBEAFE"),
        (726, 250, 980, 886, 28, "#93C5FD"),
        (784, 194, 1004, 886, 28, "#60A5FA"),
    ]:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill)
        for wy in range(y1 + 28, y2 - 40, 58):
            for wx in range(x1 + 24, x2 - 34, 42):
                draw.rounded_rectangle((wx, wy, wx + 18, wy + 24), radius=4, fill="#EFF6FF")

    for cx, face_color, shirt_color in [
        (742, "#FDBA74", PALETTE["navy"]),
        (842, "#FBBF24", PALETTE["blue"]),
    ]:
        draw.ellipse((cx, 610, cx + 62, 672), fill=face_color)
        draw.rounded_rectangle((cx - 10, 670, cx + 72, 872), radius=30, fill=shirt_color)

    footer = "헷갈리면 일반공급과 특별공급을 분리해서 보셔야 합니다."
    draw.text((92, 956), footer, font=note_font, fill=PALETTE["muted"])

    img.save(output_path)
    return str(output_path)


def _draw_summary_card(
    *,
    title: str,
    excerpt: str,
    article_markdown: str | None,
    output_path: Path,
    size: tuple[int, int],
) -> str:
    width, _ = size
    font_path = _pick_font_path()
    img, draw = _create_canvas(size)
    _draw_badge(draw, font_path=font_path, text="핵심 요약 카드")

    title_font = ImageFont.truetype(font_path, 68)
    section_font = ImageFont.truetype(font_path, 34)
    bullet_font = ImageFont.truetype(font_path, 33)
    bullet_num_font = ImageFont.truetype(font_path, 30)
    footer_font = ImageFont.truetype(font_path, 30)

    title_lines = _fit_lines(draw, title, title_font, width - 200)[:3]
    title_y = 196
    for line in title_lines:
        draw.text((92, title_y), line, font=title_font, fill=PALETTE["navy"])
        title_y += 82

    draw.rounded_rectangle((92, 430, width - 92, 518), radius=28, fill=PALETTE["navy"])
    draw.text((124, 454), "핵심만 빠르게 보실 때", font=section_font, fill="white")

    points = _summary_points(title, excerpt, article_markdown=article_markdown, limit=3)
    top = 560
    for index, point in enumerate(points, start=1):
        box_top = top + (index - 1) * 138
        draw.rounded_rectangle((92, box_top, width - 92, box_top + 110), radius=30, fill=PALETTE["soft"], outline="#DCEBFF", width=2)
        draw.rounded_rectangle((122, box_top + 24, 174, box_top + 76), radius=18, fill=PALETTE["blue"])
        draw.text((141, box_top + 33), str(index), font=bullet_num_font, fill="white")
        point_lines = _fit_lines(draw, point, bullet_font, 760)[:2]
        text_y = box_top + 22
        for line in point_lines:
            draw.text((204, text_y), line, font=bullet_font, fill=PALETTE["text"])
            text_y += 38

    draw.rounded_rectangle((92, 936, width - 92, 1000), radius=24, fill="#EFF6FF")
    draw.text((124, 954), "최종 일정과 자격은 입주자모집공고, 청약홈에서 다시 확인", font=footer_font, fill=PALETTE["blue"])

    img.save(output_path)
    return str(output_path)


def render_fallback_thumbnail(
    *,
    title: str,
    excerpt: str,
    output_path: str | Path,
    image_role: str = "thumbnail",
    size: tuple[int, int] = (1080, 1080),
    article_markdown: str | None = None,
) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if image_role == "summary_card" or "summary" in image_role:
        return _draw_summary_card(
            title=title,
            excerpt=excerpt,
            article_markdown=article_markdown,
            output_path=output,
            size=size,
        )
    return _draw_thumbnail(
        title=title,
        excerpt=excerpt,
        article_markdown=article_markdown,
        output_path=output,
        size=size,
    )
