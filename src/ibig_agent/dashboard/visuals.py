"""Visuels aux couleurs IBIG pour les publications (section 14 : « modèles aux couleurs
IBIG »). Gabarit SVG généré sans service extérieur ; le navigateur le convertit en PNG
pour la publication. Le brief visuel de l'agent reste disponible pour un visuel sur mesure.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

# Couleur d'accent par pôle (fond bleu IBIG commun)
ACCENTS = {
    "GROUPE": "#0ea5c6", "EDUFORM": "#f59e0b", "IMMOTRUST": "#10b981", "SOFT": "#38bdf8",
    "MARKET": "#f43f5e", "DIGITAL": "#8b5cf6", "CONSEIL": "#eab308", "PARTNERS": "#14b8a6",
    "MULTISERVICES": "#fb923c",
}
# Formats par réseau (largeur, hauteur)
FORMATS = {"linkedin": (1200, 675), "x": (1200, 675), "facebook_page": (1080, 1080),
           "facebook_groupe": (1080, 1080), "instagram": (1080, 1350), "threads": (1080, 1350),
           "tiktok": (1080, 1920), "whatsapp_chaine": (1080, 1920)}
SLOGAN = "L'excellence est notre passion"


def wrap(text: str, width: int, max_lines: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;:") + "…"
    return lines


def first_sentence(text: str, limit: int = 170) -> str:
    text = " ".join(text.split())
    for sep in (". ", " ! ", "! ", " ? ", "? "):
        if sep in text[: limit + 1]:
            return text[: text.index(sep) + 1].strip()
    return text[:limit].rsplit(" ", 1)[0] + ("…" if len(text) > limit else "")


def render_svg(headline: str, message: str, pole: str, reseau: str,
               pole_name: str = "") -> str:
    w, h = FORMATS.get(reseau, (1080, 1080))
    accent = ACCENTS.get(pole, ACCENTS["GROUPE"])
    k = min(w, h) / 1080  # tailles calculées sur le petit côté (paysage comme portrait)
    pad = int(min(w, h) * 0.08)
    tall = h / w > 1.2
    text_w = (w - 2 * pad) * (0.78 if w > h else 1.0)
    title_size = int((88 if tall else 76) * k)
    msg_size = int((40 if tall else 36) * k)
    title_lines = wrap(headline, int(text_w / (title_size * 0.55)), 4 if tall else 3)
    msg_lines = wrap(message, int(text_w / (msg_size * 0.5)), 5 if tall else 3)
    bar_y = pad + int(64 * k) + int(56 * k)
    if tall:  # formats verticaux : bloc de texte vers le tiers de l'image
        bar_y = max(bar_y, int(h * 0.30))
    y = bar_y + int(24 * k)
    footer_y = h - pad
    brand = "IBIG " + (pole if pole != "GROUPE" else "SARL")
    subtitle = pole_name if pole_name and pole_name.upper() != brand.upper() else ""
    ln = f"{pad + 20 * k:.0f} {pad + 46 * k:.0f}V{pad + 18 * k:.0f}"
    out = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
         f'viewBox="0 0 {w} {h}" font-family="Inter, Segoe UI, Arial, sans-serif">'),
        "<defs>",
        ('<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
         '<stop offset="0" stop-color="#0b1830"/><stop offset="0.6" stop-color="#12306b"/>'
         '<stop offset="1" stop-color="#1257c4"/></linearGradient>'),
        (f'<radialGradient id="glow" cx="0.85" cy="0.1" r="0.7"><stop offset="0" '
         f'stop-color="{accent}" stop-opacity="0.55"/><stop offset="1" stop-color="{accent}" '
         'stop-opacity="0"/></radialGradient>'),
        "</defs>",
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
        f'<rect width="{w}" height="{h}" fill="url(#glow)"/>',
        (f'<circle cx="{w * 0.92:.0f}" cy="{h * 0.88:.0f}" r="{min(w, h) * 0.3:.0f}" '
         f'fill="none" stroke="{accent}" stroke-opacity="0.25" stroke-width="{3 * k:.1f}"/>'),
        (f'<rect x="{pad}" y="{pad}" width="{64 * k:.0f}" height="{64 * k:.0f}" '
         f'rx="{16 * k:.0f}" fill="{accent}"/>'),
        (f'<path d="M{ln}M{pad + 32 * k:.0f} {pad + 46 * k:.0f}V{pad + 18 * k:.0f}'
         f'M{pad + 44 * k:.0f} {pad + 46 * k:.0f}V{pad + 18 * k:.0f}" stroke="#fff" '
         f'stroke-width="{6 * k:.1f}" stroke-linecap="round"/>'),
        (f'<text x="{pad + 84 * k:.0f}" y="{pad + (30 if subtitle else 42) * k:.0f}" '
         f'fill="#fff" font-size="{30 * k:.0f}" font-weight="800">{escape(brand)}</text>'),
    ]
    if subtitle:
        out.append(f'<text x="{pad + 84 * k:.0f}" y="{pad + 60 * k:.0f}" fill="#fff" '
                   f'fill-opacity="0.7" font-size="{22 * k:.0f}">{escape(subtitle[:60])}</text>')
    out.append(f'<rect x="{pad}" y="{bar_y}" width="{90 * k:.0f}" height="{8 * k:.0f}" '
               f'rx="{4 * k:.0f}" fill="{accent}"/>')
    for line in title_lines:
        y += title_size
        out.append(f'<text x="{pad}" y="{y}" fill="#fff" font-size="{title_size}" '
                   f'font-weight="800" letter-spacing="-1">{escape(line)}</text>')
        y += int(title_size * 0.16)
    y += int(34 * k)
    for line in msg_lines:
        if y + msg_size > footer_y - int(56 * k):
            break  # jamais sur le slogan
        y += msg_size
        out.append(f'<text x="{pad}" y="{y}" fill="#dbe7ff" font-size="{msg_size}">'
                   f'{escape(line)}</text>')
        y += int(msg_size * 0.42)
    out += [
        (f'<text x="{pad}" y="{footer_y}" fill="#fff" fill-opacity="0.75" '
         f'font-size="{24 * k:.0f}" font-style="italic">{escape(SLOGAN)}</text>'),
        "</svg>",
    ]
    return "\n".join(out)
