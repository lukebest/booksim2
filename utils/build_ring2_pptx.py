#!/usr/bin/env python3
"""Build the architecture-review deck as a real .pptx (Huawei corporate style).

Same content and same numbers as the HTML deck in ppt/, rebuilt with
python-pptx so the file can be opened and edited in PowerPoint / WPS /
Keynote. Every figure is read from ppt/images/, which is produced by
utils/ppt_ring2_figs.py from results/*.json.

Inline markup accepted by the text helpers:
    **bold**      bold run
    [[red]]       brand-red bold run

Usage:
    python3 utils/build_ring2_pptx.py            # writes ppt/ring2-write-fairness.pptx
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "ppt" / "images"
OUT = ROOT / "ppt" / "ring2-write-fairness.pptx"

# ---------------------------------------------------------------- palette
PAPER = RGBColor(0xF4, 0xF7, 0xFB)
CARD = RGBColor(0xFF, 0xFF, 0xFF)
INK = RGBColor(0x20, 0x24, 0x2C)
MUTED = RGBColor(0x66, 0x70, 0x80)
LINE = RGBColor(0xDC, 0xE3, 0xEA)
RED = RGBColor(0xD2, 0x0A, 0x2E)
RED_DARK = RGBColor(0x9C, 0x00, 0x1F)
GREY_BG = RGBColor(0xED, 0xF2, 0xF7)
DARK = RGBColor(0x14, 0x17, 0x1C)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

FONT = "Microsoft YaHei"
FONT_EN = "Arial"

W, H = 13.333, 7.5
ML = 0.62                      # left / right page margin
CW = W - 2 * ML                # content width
TOP = 1.06                     # first usable y below the chrome rule
BOT = 6.92                     # last usable y above the page number


# ---------------------------------------------------------------- helpers
def _units(s: str) -> float:
    """Width of a string in em, counting CJK as full width."""
    return sum(1.0 if ord(c) > 0x2E80 else 0.52 for c in s)


def _plain(s: str) -> str:
    return s.replace("**", "").replace("[[", "").replace("]]", "")


# A paragraph's line_spacing multiplies the font's own line height, which for
# the CJK faces used here is ~1.46 em rather than 1.0 em. Every height estimate
# below has to carry that factor or the text silently overruns its box.
LH = 1.46


def n_lines(lines: list[str], box_w: float, pt: float) -> int:
    per_line = box_w / (pt / 72)
    return sum(max(1, math.ceil(_units(_plain(t)) / per_line)) for t in lines)


def block_h(lines: list[str], box_w: float, pt: float, lsp: float,
            gap: float) -> float:
    return (n_lines(lines, box_w, pt) * pt * lsp * LH
            + (len(lines) - 1) * gap) / 72


def fit_pt(lines: list[str], box_w: float, box_h: float, base: float,
           floor: float = 8, lsp: float = 1.0, gap: float = 5.0) -> float:
    """Largest point size at which `lines` still fit in a box_w x box_h inch box."""
    pt = float(base)
    while pt > floor:
        if block_h(lines, box_w, pt, lsp, gap) <= box_h:
            return pt
        pt -= 0.5
    return floor


_TOKEN = re.compile(r"(\*\*.+?\*\*|\[\[.+?\]\])")


def _runs(par, text: str, pt: int, color: RGBColor, bold: bool = False,
          font: str = FONT) -> None:
    for chunk in _TOKEN.split(text):
        if not chunk:
            continue
        if chunk.startswith("**"):
            body, b, c = chunk[2:-2], True, color
        elif chunk.startswith("[["):
            body, b, c = chunk[2:-2], True, RED
        else:
            body, b, c = chunk, bold, color
        r = par.add_run()
        r.text = body
        r.font.size = Pt(pt)
        r.font.bold = b
        r.font.color.rgb = c
        r.font.name = font


def textbox(slide, x, y, w, h, lines, pt, color=INK, bold=False, lsp=1.0,
            gap=5.0, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, text in enumerate(lines):
        par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        par.alignment = align
        par.line_spacing = lsp
        if i:
            par.space_before = Pt(gap)
        _runs(par, text, pt, color, bold, font)
    return tb


def autotext(slide, x, y, w, h, lines, base, color=INK, bold=False, floor=8,
             lsp=1.0, gap=5.0, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
             font=FONT):
    pt = fit_pt(lines, w, h, base, floor, lsp, gap)
    return textbox(slide, x, y, w, h, lines, pt, color, bold, lsp, gap, align,
                   anchor, font)


def rect(slide, x, y, w, h, fill=None, line=None, lw=1.0,
         shape=MSO_SHAPE.RECTANGLE):
    sp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    sp.shadow.inherit = False
    if fill is None:
        sp.fill.background()
    else:
        sp.fill.solid()
        sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line
        sp.line.width = Pt(lw)
    sp.text_frame.word_wrap = True
    return sp


def image(slide, path: Path, x, y, w, h):
    """Place an image contained inside the x/y/w/h box, centred.

    Returns the box actually occupied, so captions can hug the figure instead
    of the (often much taller) slot reserved for it.
    """
    from PIL import Image as PILImage
    iw, ih = PILImage.open(path).size
    scale = min(w / iw, h / ih)
    dw, dh = iw * scale, ih * scale
    dx, dy = x + (w - dw) / 2, y + (h - dh) / 2
    slide.shapes.add_picture(str(path), Inches(dx), Inches(dy), Inches(dw),
                             Inches(dh))
    return dx, dy, dw, dh


# ------------------------------------------------------------ page furniture
def new_slide(prs, bg=PAPER):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    rect(s, 0, 0, W, H, fill=bg)
    return s


def brand(slide, on_dark=False):
    rect(slide, W - ML - 1.62, 0.30, 0.20, 0.20, fill=RED)
    textbox(slide, W - ML - 1.34, 0.27, 1.34, 0.28, ["HUAWEI"], 13,
            WHITE if on_dark else INK, bold=True, font=FONT_EN)


def chrome(slide, title: str):
    rect(slide, ML, 0.33, 0.15, 0.15, fill=RED)
    textbox(slide, ML + 0.26, 0.29, W - 2 * ML - 2.0, 0.26, [title], 11.5, MUTED,
            bold=True)
    rect(slide, ML, 0.70, CW, 0.011, fill=LINE)


def page_no(slide, i, total, on_dark=False):
    textbox(slide, ML, 7.06, 1.6, 0.24, [f"{i:02d} / {total:02d}"], 10,
            RGBColor(0x55, 0x5B, 0x66) if on_dark else RGBColor(0xA8, 0xB0, 0xBA),
            font=FONT_EN)


def kicker(slide, x, y, w, text, color=RED, pt=11):
    spaced = " ".join(text)
    return textbox(slide, x, y, w, 0.26, [spaced],
                   fit_pt([spaced], w, 0.26, pt, 7.5), color, bold=True,
                   font=FONT_EN)


def rule(slide, x, y, w=1.15):
    rect(slide, x, y, w, 0.045, fill=RED)


def band(slide, x, y, w, h, text, base=15):
    rect(slide, x, y, w, h, fill=RED)
    autotext(slide, x + 0.34, y + 0.10, w - 0.68, h - 0.20, [text], base,
             WHITE, floor=9, lsp=1.04, anchor=MSO_ANCHOR.MIDDLE)


def card_need(title, bodies, cw, tpt, bpt, pad, num=False):
    """Height a card needs to hold its text at full size, so a row of cards can
    be sized to its content instead of stretching to fill the slide."""
    h = 2 * pad + (0.58 if num else 0.0)
    if title:
        h += block_h([title], cw, tpt, 1.0, 0) + 0.13
    if bodies:
        h += block_h(bodies, cw, bpt, BODY_LSP, BODY_GAP)
    return h


def row_h(cells, cwid, tpt, bpt, pad, avail, num=False, floor_h=1.5):
    need = max(card_need(c.get("t", ""), c.get("b", []), cwid - 2 * pad, tpt,
                         bpt, pad, num) for c in cells)
    return min(avail, max(floor_h, need))


def card(slide, x, y, w, h, accent=False, flat=False):
    return rect(slide, x, y, w, h, fill=GREY_BG if flat else CARD,
                line=RED if accent else LINE, lw=1.5 if accent else 1.0)


BODY_LSP = 1.02
BODY_GAP = 6.0


def card_text(slide, x, y, w, h, title, bodies, accent=False, flat=False,
              tpt=14, bpt=11.5, pad=0.24, num=None, floor=8.5):
    card(slide, x, y, w, h, accent, flat)
    cx, cw = x + pad, w - 2 * pad
    inner = h - 2 * pad
    num_h = 0.58 if num is not None else 0.0

    th = tgap = 0.0
    tp = 0.0
    if title:
        tp = fit_pt([title], cw, 0.78, tpt, 9.5, lsp=1.0, gap=0)
        th = block_h([title], cw, tp, 1.0, 0)
        tgap = 0.13

    bh = 0.0
    bp = 0.0
    if bodies:
        bp = fit_pt(bodies, cw, inner - num_h - th - tgap, bpt, floor,
                    lsp=BODY_LSP, gap=BODY_GAP)
        bh = block_h(bodies, cw, bp, BODY_LSP, BODY_GAP)

    # Numbered cards keep their pill on a shared baseline across the row; only
    # the text below it is optically centred in whatever space is left.
    cy = y + pad
    if num is not None:
        rect(slide, cx, cy, 0.42, 0.42, fill=RED, shape=MSO_SHAPE.OVAL)
        textbox(slide, cx, cy + 0.09, 0.42, 0.26, [num], 11.5, WHITE, bold=True,
                align=PP_ALIGN.CENTER, font=FONT_EN)
        cy += num_h
    cy += max(0.0, inner - (num_h + th + tgap + bh)) / 2
    if title:
        textbox(slide, cx, cy, cw, th + 0.04, [title], tp,
                RED if accent else INK, bold=True)
        cy += th + tgap
    if bodies:
        textbox(slide, cx, cy, cw, bh + 0.04, bodies, bp, INK,
                lsp=BODY_LSP, gap=BODY_GAP)


# ------------------------------------------------------------- slide kinds
def s_cover(prs, d):
    s = new_slide(prs)
    brand(s)
    for cx, cy, cd, col in ((10.35, 1.20, 0.80, RED),
                            (11.60, 2.20, 1.90, GREY_BG),
                            (9.85, 3.95, 0.36, RED),
                            (9.30, 1.95, 0.70, GREY_BG)):
        rect(s, cx, cy, cd, cd, fill=col, shape=MSO_SHAPE.OVAL)
    rect(s, ML, 1.28, 0.95, 0.95, fill=CARD, line=LINE, shape=MSO_SHAPE.OVAL)
    rect(s, ML + 0.34, 1.62, 0.28, 0.28, fill=RED)
    kicker(s, ML, 2.62, 8.2, d["kicker"], pt=12)
    textbox(s, ML, 3.00, 8.6, 1.96, d["title"], 48, INK, bold=True, lsp=0.94)
    textbox(s, ML, 5.06, 8.0, 0.90, d["sub"], 14.5, MUTED, lsp=1.06)
    rect(s, 0, 6.30, W, 1.20, fill=RED)
    textbox(s, ML, 6.60, 9.6, 0.66, d["meta"], 11.5, WHITE, lsp=1.06)
    return s


def s_agenda(prs, d):
    s = new_slide(prs)
    brand(s)
    top, bottom = 1.05, 6.85
    mid = (top + bottom) / 2
    rect(s, ML, mid - 1.70, 4.05, 3.40, fill=RED)
    kicker(s, ML + 0.42, mid - 1.22, 3.2, "CONTENT", WHITE, pt=11)
    textbox(s, ML + 0.42, mid - 0.86, 3.2, 0.92, ["目录"], 40, WHITE, bold=True)
    textbox(s, ML + 0.42, mid + 0.34, 3.2, 0.30, ["7 SECTIONS"], 12, WHITE,
            bold=True, font=FONT_EN)
    x, w = ML + 4.45, CW - 4.45
    n = len(d["items"])
    gap = 0.16
    hh = (bottom - top - gap * (n - 1)) / n
    for i, t in enumerate(d["items"]):
        y = top + i * (hh + gap)
        rect(s, x, y, w, hh, fill=GREY_BG)
        rect(s, x, y, 0.66, hh, fill=RED)
        textbox(s, x, y, 0.66, hh, [f"{i + 1:02d}"], 12, WHITE, bold=True,
                align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, font=FONT_EN)
        autotext(s, x + 0.90, y, w - 1.20, hh, [t], 15, INK,
                 anchor=MSO_ANCHOR.MIDDLE)
    return s


def s_section(prs, d):
    s = new_slide(prs)
    brand(s)
    rect(s, ML, 2.06, 0.98, 0.98, fill=None, line=LINE, lw=1.4,
         shape=MSO_SHAPE.OVAL)
    textbox(s, ML, 2.38, 0.98, 0.40, [d["no"]], 21, RED, bold=True,
            align=PP_ALIGN.CENTER, font=FONT_EN)
    kicker(s, ML, 3.26, 4.4, d["kicker"], pt=11)
    textbox(s, ML, 3.60, 5.4, 1.56, d["title"], 38, INK, bold=True, lsp=0.98)
    autotext(s, ML, 5.30, 5.0, 0.86, [d["lead"]], 14, MUTED, lsp=1.06)
    rect(s, 6.55, 2.20, W - ML - 6.55, 3.10, fill=RED)
    kicker(s, 6.95, 2.62, 4.0, d["key_label"], WHITE, pt=11)
    autotext(s, 6.95, 3.02, W - ML - 6.95 - 0.40, 1.90, [d["key"]], 20, WHITE,
             bold=True, floor=12, lsp=1.04)
    return s


def s_closing(prs, d):
    s = new_slide(prs, bg=DARK)
    brand(s, on_dark=True)
    rect(s, ML, 1.90, 0.95, 0.95, fill=RGBColor(0x22, 0x26, 0x2D),
         shape=MSO_SHAPE.OVAL)
    rect(s, ML + 0.34, 2.24, 0.28, 0.28, fill=RED)
    kicker(s, ML, 3.20, 4.0, "THANK YOU", pt=12)
    textbox(s, ML, 3.58, 9.0, 1.80, d["title"], 44, WHITE, bold=True, lsp=0.96)
    textbox(s, ML, 5.60, 8.6, 0.90, d["lead"], 14,
            RGBColor(0x9A, 0xA3, 0xAF), lsp=1.10)
    return s


def s_media(prs, d):
    """Big stat column + figure on top, three takeaway cards underneath."""
    s = new_slide(prs)
    chrome(s, d["chrome"])
    brand(s)
    y = TOP + 0.06
    cards = d.get("cards", [])
    cwid = (CW - 2 * 0.26) / 3
    ch = row_h(cards, cwid, 13, 11, 0.22, 2.45, floor_h=1.5) if cards else 0.0
    fig_h = (BOT - y - ch - 0.78) if cards else (BOT - y - 0.40)
    sw = 2.60
    kicker(s, ML, y, sw, d["kicker"], pt=10.5)
    textbox(s, ML, y + 0.34, sw, 0.78, [d["stat"]], 40, RED, bold=True,
            font=FONT_EN)
    autotext(s, ML, y + 1.20, sw, 0.72, d["stat_sub"], 11.5, MUTED, lsp=1.06)
    rule(s, ML, y + 2.04, 0.90)
    fx = ML + sw + 0.42
    fw = W - ML - fx
    _, _, _, dh = image(s, IMG / d["img"], fx, y, fw, fig_h)
    autotext(s, fx, y + (fig_h + dh) / 2 + 0.10, fw, 0.32, [d["caption"]], 10,
             MUTED, floor=8)
    if cards:
        cy = BOT - ch
        for i, c in enumerate(cards):
            card_text(s, ML + i * (cwid + 0.26), cy, cwid, ch, c["t"], c["b"],
                      accent=c.get("accent", False), flat=True, tpt=13,
                      bpt=11, pad=0.22)
    return s


def s_figure_side(prs, d):
    """Figure on the left, a stack of note cards / bands on the right."""
    s = new_slide(prs)
    chrome(s, d["chrome"])
    brand(s)
    y = TOP + 0.10
    fw = d.get("fig_w", 8.05)
    fh = BOT - y - 0.52
    _, dy, _, dh = image(s, IMG / d["img"], ML, y, fw, fh)
    autotext(s, ML, min(dy + dh + 0.12, BOT - 0.40), fw, 0.42, [d["caption"]],
             10, MUTED, floor=8)
    x = ML + fw + 0.42
    w = W - ML - x
    kicker(s, x, y, w, d["kicker"], pt=10.5)
    cy = y + 0.38
    blocks = d["blocks"]
    avail = BOT - cy - 0.16 * (len(blocks) - 1)
    weights = [b.get("wt", 1.0) for b in blocks]
    for b, wt in zip(blocks, weights):
        bh = avail * wt / sum(weights)
        if b["kind"] == "band":
            band(s, x, cy, w, bh, b["text"], base=12)
        else:
            card_text(s, x, cy, w, bh, b.get("t", ""), b["b"],
                      accent=b.get("accent", False), flat=True, tpt=12,
                      bpt=10.5, pad=0.20, floor=8)
        cy += bh + 0.16
    return s


def s_process(prs, d):
    """Four numbered steps in a row, optional closing band."""
    s = new_slide(prs)
    chrome(s, d["chrome"])
    brand(s)
    y = TOP + 0.10
    kicker(s, ML, y, CW, d["kicker"], pt=10.5)
    textbox(s, ML, y + 0.32, CW, 0.72, [d["title"]], 30, INK, bold=True)
    top = y + 1.24
    bh = 0.92 if d.get("band") else 0.0
    avail = BOT - top - (bh + 0.30 if bh else 0)
    n = len(d["steps"])
    gap = 0.24
    cwid = (CW - gap * (n - 1)) / n
    ch = row_h(d["steps"], cwid, 15, 11.5, 0.24, avail, num=True, floor_h=2.2)
    top += (avail - ch) / 2
    for i, st in enumerate(d["steps"]):
        card_text(s, ML + i * (cwid + gap), top, cwid, ch, st["t"], st["b"],
                  accent=st.get("accent", False), tpt=15, bpt=11.5,
                  num=f"{i + 1:02d}")
    if bh:
        band(s, ML, BOT - bh, CW, bh, d["band"], base=14)
    return s


def s_scheme(prs, d):
    """Scheme page: four mechanism steps on top, three result cards below."""
    s = new_slide(prs)
    chrome(s, d["chrome"])
    brand(s)
    y = TOP + 0.06
    kicker(s, ML, y, CW, d["kicker"], pt=10)
    textbox(s, ML, y + 0.28, CW, 0.62, [d["title"]], 26, INK, bold=True)
    top = y + 1.06
    n, gap = 4, 0.22
    cwid = (CW - gap * (n - 1)) / n
    ph = row_h(d["steps"], cwid, 14, 10.5, 0.20, 2.60, num=True, floor_h=2.0)
    for i, st in enumerate(d["steps"]):
        card_text(s, ML + i * (cwid + gap), top, cwid, ph, st["t"], st["b"],
                  accent=st.get("accent", False), tpt=14, bpt=10.5, pad=0.20,
                  num=f"{i + 1:02d}")
    cy = top + ph + 0.30
    ch = BOT - cy
    cwid = (CW - 2 * 0.24) / 3
    for i, c in enumerate(d["cards"]):
        card_text(s, ML + i * (cwid + 0.24), cy, cwid, ch, c["t"], c["b"],
                  accent=c.get("accent", False), flat=True, tpt=12.5, bpt=10.5,
                  pad=0.20)
    return s


def s_matrix(prs, d):
    """2 x 4 card matrix, optional closing band."""
    s = new_slide(prs)
    chrome(s, d["chrome"])
    brand(s)
    y = TOP + 0.10
    kicker(s, ML, y, CW, d["kicker"], pt=10.5)
    textbox(s, ML, y + 0.32, CW, 0.72, [d["title"]], 30, INK, bold=True)
    top = y + 1.28
    bh = 0.94 if d.get("band") else 0.0
    total = BOT - top - (bh + 0.26 if bh else 0)
    gx, gy = 0.24, 0.24
    cwid = (CW - 3 * gx) / 4
    rows = math.ceil(len(d["cells"]) / 4)
    tpt, bpt = (15, 12) if rows == 1 else (13.5, 10.5)
    ch = row_h(d["cells"], cwid, tpt, bpt, 0.22,
               (total - gy * (rows - 1)) / rows, floor_h=1.8)
    top += (total - (rows * ch + gy * (rows - 1))) / 2
    for i, c in enumerate(d["cells"]):
        r, col = divmod(i, 4)
        card_text(s, ML + col * (cwid + gx), top + r * (ch + gy), cwid, ch,
                  c["t"], c["b"], accent=c.get("accent", False), tpt=tpt,
                  bpt=bpt, pad=0.22)
    if bh:
        band(s, ML, BOT - bh, CW, bh, d["band"], base=13)
    return s


def s_triple(prs, d):
    """Three tall cards, optional closing band."""
    s = new_slide(prs)
    chrome(s, d["chrome"])
    brand(s)
    y = TOP + 0.10
    kicker(s, ML, y, CW, d["kicker"], pt=10.5)
    textbox(s, ML, y + 0.32, CW, 0.72, [d["title"]], 30, INK, bold=True)
    top = y + 1.32
    bh = 0.94 if d.get("band") else 0.0
    avail = BOT - top - (bh + 0.26 if bh else 0)
    gap = 0.26
    cwid = (CW - 2 * gap) / 3
    ch = row_h(d["cards"], cwid, 15, 11.5, 0.24, avail,
               num=any(c.get("num") for c in d["cards"]), floor_h=2.4)
    top += (avail - ch) / 2
    for i, c in enumerate(d["cards"]):
        card_text(s, ML + i * (cwid + gap), top, cwid, ch, c["t"], c["b"],
                  accent=c.get("accent", False), tpt=15, bpt=11.5,
                  num=c.get("num"))
    if bh:
        band(s, ML, BOT - bh, CW, bh, d["band"], base=13)
    return s


def s_bars(prs, d):
    """Per-core bandwidth bar chart drawn as shapes."""
    s = new_slide(prs)
    chrome(s, d["chrome"])
    brand(s)
    y = TOP + 0.36
    kicker(s, ML, y, 4.6, d["kicker"], pt=10.5)
    textbox(s, ML, y + 0.36, 5.0, 1.40, d["title"], 29, INK, bold=True, lsp=0.98)
    autotext(s, ML, y + 1.94, 4.9, 1.00, [d["lead"]], 13, MUTED, lsp=1.06)
    textbox(s, ML, y + 3.10, 4.0, 0.74, [d["stat"]], 38, RED, bold=True,
            font=FONT_EN)
    textbox(s, ML, y + 3.84, 4.6, 0.30, [d["stat_sub"]], 11, MUTED)

    bx = ML + 5.40
    bw = W - ML - bx
    card(s, bx, TOP + 0.20, bw, BOT - TOP - 0.20)
    ix, iy = bx + 0.34, TOP + 0.52
    iw, ih = bw - 0.68, BOT - TOP - 1.30
    rows = d["rows"]
    rh = ih / len(rows)
    track_x = ix + 0.62
    track_w = iw - 0.62 - 0.86
    for i, (name, val, frac) in enumerate(rows):
        cy = iy + i * rh
        bar_h = min(0.26, rh * 0.62)
        textbox(s, ix, cy + (rh - 0.22) / 2, 0.56, 0.22, [name], 11, MUTED,
                bold=True, font=FONT_EN)
        rect(s, track_x, cy + (rh - bar_h) / 2, track_w, bar_h, fill=GREY_BG)
        rect(s, track_x, cy + (rh - bar_h) / 2, track_w * frac, bar_h,
             fill=RED if frac > 0.8 else RED_DARK)
        textbox(s, ix + iw - 0.80, cy + (rh - 0.22) / 2, 0.80, 0.22, [val], 11,
                INK, bold=True, align=PP_ALIGN.RIGHT, font=FONT_EN)
    autotext(s, ix, iy + ih + 0.18, iw, 0.46, [d["caption"]], 10, MUTED,
             floor=8)
    return s


def s_compare(prs, d):
    """Expected vs measured, plus three stat tiles."""
    s = new_slide(prs)
    chrome(s, d["chrome"])
    brand(s)
    y = TOP + 0.06
    kicker(s, ML, y, CW, d["kicker"], pt=10.5)
    textbox(s, ML, y + 0.30, CW, 0.86, [d["title"]], 26, INK, bold=True,
            lsp=1.22)
    autotext(s, ML, y + 1.24, CW, 0.34, [d["lead"]], 13.5, MUTED)
    top = y + 1.70
    sh = 1.16
    ch = BOT - top - sh - 0.26
    cwid = (CW - 0.30) / 2
    for i, col in enumerate(d["cols"]):
        x = ML + i * (cwid + 0.30)
        card(s, x, top, cwid, ch, accent=col.get("accent", False))
        textbox(s, x + 0.28, top + 0.24, cwid - 0.56, 0.32, [col["t"]], 14,
                RED if col.get("accent") else MUTED, bold=True)
        autotext(s, x + 0.28, top + 0.72, cwid - 0.56, ch - 1.00, col["b"],
                 12, INK, floor=9, lsp=BODY_LSP, gap=10)
    ty = BOT - sh
    twid = (CW - 2 * 0.26) / 3
    for i, (big, sub) in enumerate(d["stats"]):
        x = ML + i * (twid + 0.26)
        card(s, x, ty, twid, sh, flat=True)
        textbox(s, x, ty + 0.16, twid, 0.56, [big], 28, RED, bold=True,
                align=PP_ALIGN.CENTER, font=FONT_EN)
        autotext(s, x + 0.22, ty + 0.74, twid - 0.44, 0.32, [sub], 10.5, MUTED,
                 floor=8, align=PP_ALIGN.CENTER)
    return s


# ------------------------------------------------------------------ content
DECK = [
    ("cover", dict(
        kicker="NOC ARCHITECTURE REVIEW · 20-NODE BUFFERLESS RING",
        title=["满带宽下的", "多核带宽不均"],
        sub=["20 节点无缓存双向环上的瞬时写带宽公平性：",
             "问题定位 · 理论极限 · 拥塞控制选型"],
        meta=["汇报对象：芯片架构师",
              "数据口径：uniform tiled 写 · K = 20000 · 单 plane · 全部可复现"])),

    ("agenda", dict(items=[
        "问题背景：拓扑、节点、路由与关键 setup",
        "S0 的问题：带宽已打满，瞬时带宽不均",
        "S1 方案：检测 / 传递 / 反馈 / 控制与实测代价",
        "公平性与总带宽的 trade-off：形式化与曲线",
        "理论最优、理想拥塞控制与各方案 Pareto",
        "Pareto 前沿方案详述（免流量先验）",
        "结论与建议"])),

    ("section", dict(
        no="01", kicker="SECTION ONE", title=["问题背景"],
        lead="拓扑、节点角色、路由规则，以及决定结论的那几项关键 setup。",
        key_label="KEY MESSAGE",
        key="10 个 AI core 对 8 个 memory HA 做 CHI WriteNoSnp。总带宽已接近理论上限，"
            "问题出在「谁在什么时候拿到 slot」。")),

    ("figside", dict(
        chrome="拓扑与节点", img="04-topo.png", fig_w=5.90,
        caption="蓝 = AI core（10）· 橙 = memory HA（8）· 灰 = 非终端 N9 / N19；"
                "边上数字为 hop 时延（拍）。",
        kicker="TOPOLOGY & ROUTING",
        blocks=[
            dict(kind="card", t="20 节点单 plane 双向闭合 full ring",
                 b=["**节点角色**：10 个 AI core（C0–C18 偶数）发起写；"
                    "8 个 memory HA（M1–M17 奇数）为 completer；N9 / N19 只转发，不收发。",
                    "**路由：时延最短**。按链路时延之和选 CW / CCW，时延平局比跳数，"
                    "再平局取 CW；core→mem 与 mem→core 同一规则。"], wt=1.25),
            dict(kind="card", accent=True, t="几何后果：本次问题的起点",
                 b=["因为 N9 / N19 不是 mem，C0 / C8 / C10 / C18 的「邻接 mem 数」只有 1，"
                    "其余六核为 2。这一条几何差异，直接决定了后面看到的六快四慢。"], wt=1.0),
            dict(kind="band",
                 text="20 节点 / 1 plane · 10 core + 8 HA + 2 非终端 · "
                      "等速率理论上限 R* = 5.7143 flit/cycle", wt=0.62)])),

    ("process", dict(
        chrome="协议：CHI WriteNoSnp", kicker="PROTOCOL",
        title="一笔写 = 四拍握手，被计量的只有第三拍",
        steps=[
            dict(t="REQ", b=["core → HA，1 flit，走时延最短方向，占 REQ VC。"]),
            dict(t="DBIDResp",
                 b=["HA → core，1 flit，反方向，占 RSP VC。HA think time = 0。"]),
            dict(t="WriteData × 2", accent=True,
                 b=["core → HA，2 flit，占 DAT VC。",
                    "**per-core 写带宽 = 争用窗内成功上环的 WriteData flit / cycle。**"]),
            dict(t="Comp",
                 b=["HA → core，1 flit，占 RSP VC，释放该笔事务的 tracker 表项。"])],
        band="公平性主指标：50 拍宽的窗内对 10 个核的写带宽算 Jain，"
             "再对所有争用窗内的箱取平均。")),

    ("matrix", dict(
        chrome="关键 setup", kicker="FABRIC CONFIG", title="八项决定结论的配置",
        cells=[
            dict(t="上环端口",
                 b=["每方向一组 × REQ / RSP / DAT，每口 1 flit/cycle → 每节点 6 个上环口。"]),
            dict(t="下环端口",
                 b=["每 (node, VC) 一个「两写一读」buffer：两方向可同拍各写 1 flit，"
                    "PE 每拍只读 1 flit。"]),
            dict(t="上环队列",
                 b=["每 (node, VC) 12 深共享 FIFO + 每向 8 深 inject Q，各接一个上环口。"]),
            dict(t="下环队列",
                 b=["深度 12。DAT 侧平均占用 0.765 / 12（峰值 11），满的比例 0%，"
                    "从来不是瓶颈。"]),
            dict(t="在环绝对优先", accent=True,
                 b=["bufferless：transit flit 永不被本地注入打断，注入只能挤空隙。"]),
            dict(t="I-tag / E-tag",
                 b=["t_inj = 4 定向上游预约单槽；t_xfer = 1，首次下环失败即打标，"
                    "再到达时最高优先级下环。"]),
            dict(t="在飞与 tracker",
                 b=["core_outstanding = 128；HA tracker = 512，峰值占用 422，"
                    "重试 0 —— 已完全不绑定。"]),
            dict(t="专用流控总线", accent=True,
                 b=["广播，不占 NoC hop；**任何使用固定 30 拍延迟**（硬约束），"
                    "控制窗 64 拍。"])])),

    ("section", dict(
        no="02", kicker="SECTION TWO", title=["S0 的具体问题"],
        lead="总带宽已经打到理论上限的 95.69%，但十个核的瞬时带宽差 1.69 倍。",
        key_label="KEY MESSAGE",
        key="带宽这条线基本关死了，可争空间只剩 0.8 个点；真正的缺陷在公平性一侧，"
            "而且是几何决定的固定分组，不是随机抖动。")),

    ("media", dict(
        chrome="S0：总带宽已打满", kicker="BANDWIDTH IS SATURATED",
        stat="95.69%",
        stat_sub=["S0 = 5.4681 flit/cycle", "占等速率 R* = 5.7143"],
        img="07-totalbw.png",
        caption="细线 = 50 拍分箱，粗线 = 平滑。黑虚线 = fabric 等速率上限 "
                "R* = 5.7143 flit/cycle。",
        cards=[
            dict(t="4.31% 的缺口已逐拍闭合",
                 b=["makespan 73,152 = 70,000 名义载荷 + 1,056 绕环加价 + 2,096 空转，"
                    "三项相加逐拍相等。绑定项是 RSP 链路而不是 DAT。"]),
            dict(t="争用窗内均值 6.02 > R* 不是越界",
                 b=["恰恰是不公平的证据：领先核提前做完退出，尾部核独占剩下的容量，"
                    "所以窗内瞬时值高于严格等速率上限。"]),
            dict(t="出厂规格下可达上限约 96.5%", accent=True,
                 b=["留给拥塞控制去争的总带宽只有 0.8 个点 —— "
                    "带宽线应当靠「不弄丢」而不是「抬上去」来过。"])])),

    ("bars", dict(
        chrome="S0：各核写带宽", kicker="PER-CORE BANDWIDTH",
        title=["六快四慢，", "是固定分组不是抖动"],
        lead="最低 C8 = 0.42409，最高 C14 = 0.71803。慢的那四个正是"
             "「邻接 mem 数 = 1」的 C0 / C8 / C10 / C18。",
        stat="1.6931", stat_sub="整窗 MAX / MIN（S0，K = 20000）",
        rows=[("C0", "0.455", 0.607), ("C2", "0.715", 0.954),
              ("C4", "0.712", 0.949), ("C6", "0.713", 0.951),
              ("C8", "0.424", 0.565), ("C10", "0.443", 0.590),
              ("C12", "0.715", 0.954), ("C14", "0.718", 0.957),
              ("C16", "0.689", 0.919), ("C18", "0.436", 0.582)],
        caption="条长按 0.75 flit/cycle 归一。8 个 HA 收到的 WriteData 完全相等"
                "（50,000 / HA），所以这不是访存不均。")),

    ("media", dict(
        chrome="S0：50 拍瞬时公平性", kicker="INSTANTANEOUS FAIRNESS",
        stat="0.87865",
        stat_sub=["50 拍分箱平均 JAIN", "理想控制器 = 0.9997"],
        img="09-s0-instbal.png",
        caption="左 = 主指标随时间；右 = 同一份数据换观察窗宽度，实测始终低于理想控制器。",
        cards=[
            dict(t="不是个别坏箱",
                 b=["1,114 个箱里 p05 = 0.82121，最差箱 0.72226；"
                    "右图显示只有把观察窗放宽到 2^11 拍量级，max/min 才收敛。"]),
            dict(t="抖动抹平后的上限只有 0.95341", accent=True,
                 b=["给每核每箱都填上它自己的长期均值（只留速率差），Jain 上限就到顶了 —— "
                    "这是任何「只整时机、不搬份额」机制的天花板。"]),
            dict(t="所以要到 0.99 必须搬速率",
                 b=["核间长期速率 21.201 vs 35.901（比 1.6934）。核内抖动虽占方差 65.4%，"
                    "但封顶的恰恰是那 34.6% 的核间差 —— 50 拍窗内每核只有 30.1 个 flit。"])])),

    ("triple", dict(
        chrome="根因", kicker="ROOT CAUSE", title="几何 × 机制 = 固定的六快四慢",
        cards=[
            dict(num="01", t="几何：邻接 mem = 1",
                 b=["N9 / N19 是非终端，于是 C0 / C8 / C10 / C18 紧邻的 memory 只有一个。"
                    "它们的写必须挤到环上最忙的那几条 hop 上，而其余六核可以在两侧分流。",
                    "[[带宽与邻接 mem 数的相关系数 0.997。改路由也变不出第二个邻居。]]"]),
            dict(num="02", t="机制：在环绝对优先",
                 b=["无缓存环上 transit flit 永不被本地注入打断，注入只能挤空隙。"
                    "决定上环延迟的是空隙的**分布**而不是总量 —— "
                    "坐在热段起点的节点看到的空隙最少、最不规则。",
                    "[[官方 run 最忙 hop 占用 95.83%（DAT）/ 97.14%（RSP）；"
                    "K=2000 短探测把绑定 hop 的空槽逐拍分类，「端口空闲」一列合计 1，"
                    "实质为零 —— 没有一个空槽是仲裁失误。]]"]),
            dict(num="03", t="症状：只偏在失败尝试上",
                 b=["这四个核的 CW / CCW 上环失败比 2.42–2.58，其余六核只有 1.01–1.18。"
                    "而每核每方向**成功**上环数被路由钉死在 30,000 笔，逐位相同。",
                    "[[失败比不对称是在忠实报告「一个方向的 hop 更挤」，"
                    "抹平它本身不是目标。]]"])])),

    ("section", dict(
        no="03", kicker="SECTION THREE", title=["S1 方案与代价"],
        lead="拥塞检测、拥塞传递、拥塞反馈、流量控制，以及调参之后的两难。",
        key_label="KEY MESSAGE",
        key="S1 的公平性来自「把所有人一起压慢」：Jain +0.068，总带宽 −14.3%；"
            "调参把带宽拿回来之后，公平性也一起还了回去。")),

    ("process", dict(
        chrome="S1 机制：检测 / 传递 / 反馈 / 控制",
        kicker="SENDER-DRIVEN · RATE-BASED · BUS-TRIGGERED",
        title="S1：拥塞等级广播 + 节点级 AIMD",
        steps=[
            dict(t="拥塞检测",
                 b=["每节点每 64 拍窗统计**上环失败**与**下环偏转**，"
                    "分 total（任何原因）与 net（仅在环占用造成）两路。",
                    "等级 = min(7, 计数 ÷ 8)，量化成 3 bit：0–7→0，8–15→1，…，≥56→7。"]),
            dict(t="拥塞传递",
                 b=["每方向 3 bit 的**专用广播总线**，从不占用 NoC hop，"
                    "因此不会自我加剧拥塞。",
                    "本设计里任何总线使用固定 **30 拍**延迟；30 < 窗口 64，"
                    "所以反馈仍能赶在下一次 AIMD 之前送到。"]),
            dict(t="拥塞反馈",
                 b=["每节点维护一张**受控节点表** —— 自己的 flit 会经过的那些 "
                    "path nodes（20 项 × 6 bit）。",
                    "反馈值取这些节点等级的 **max**：路径上最堵的那一段说了算。"]),
            dict(t="流量控制", accent=True,
                 b=["最终等级 = level_of(自身 total 失败 − 8 × 收到的 max net 等级)。",
                    "对每窗**整型注入预算**做 AIMD：乘性减 α = 0.75 / 0.5 / 0.25，"
                    "加性增 β = +16 / +8 / +2，出口是令牌桶闸门。"])],
        band="四段本身都成立，问题出在第 ① 段的信号语义：在无缓存环上，"
             "一个核的上环失败大多由别人的 transit 造成，所以这条信号分不清"
             "「我太贪」和「我被挤」。")),

    ("media", dict(
        chrome="S1 效果：两个旋钮各拿一头", kicker="TUNING OUTCOME",
        stat="−14.3%", stat_sub=["S1 默认档的总写带宽代价", "换来 Jain +0.068"],
        img="12-s1-effect.png",
        caption="左 = 每核写带宽对比；右 = 三个工作点在带宽—Jain 平面上的位置与验收区。",
        cards=[
            dict(t="S1 默认（band = spec）",
                 b=["分箱 Jain 0.87865 → **0.94689**，整窗 max/min 1.6931 → 1.4161；"
                    "总写带宽 5.4681 → **4.6874（−14.3%）**。"
                    "公平性进步是真的，带宽代价也是真的。"]),
            dict(t="S1T 每向预算（调参后）", accent=True,
                 b=["62 组 AIMD 参数网格自己选出的最优点（dir_split、cap 0.5、w 64、"
                    "burst 1）：带宽回到 **5.4641（−0.07%）**，但 Jain 掉回 "
                    "**0.87294**，max/min 反升到 1.7879。"]),
            dict(t="方向失败比：没有改善",
                 b=["最坏核的 CW/CCW 失败比 S0 = 2.575、S1 = 2.463、S1T = 2.551，"
                    "三者实质相同。能压到 2.2 以下的配置一律要付 36–37% 带宽 —— "
                    "那是关机，不是调平。"])])),

    ("compare", dict(
        chrome="S1 为什么换不来公平", kicker="WHY IT DOES NOT TRANSFER RATE",
        title="期望是「把份额从快核搬给慢核」，实测是「把所有人一起压慢」",
        lead="极差变小，是因为上限被砍、下限也跟着掉，而不是速率发生了转移。",
        cols=[
            dict(t="期望的动作", b=[
                "**快核让速**：拥塞等级高的路径上，占优核先降预算。",
                "**让出的槽被慢核接手**：C0 / C8 / C10 / C18 拿到本来挤不上的 hop。",
                "**总量不变**：份额重新分配，总带宽守恒。"]),
            dict(t="实测发生的事", accent=True, b=[
                "**触发条件搞反了**：乘性减由「自己上环失败了」触发，"
                "而无缓存环上这些失败大多是别人的 transit 造成的 —— 等于惩罚受害者。",
                "**所有核一起下降**：慢核 C18 0.43642 → 0.39489（−9.5%），"
                "快核 C2 0.71521 → 0.54571（−23.7%）。max/min 变好，是上限被砍出来的。",
                "**闸门型执行器丢槽**：令牌桶「没额度就不上环」，"
                "而无缓存环上让出的槽不会被记账保留，沿途任何节点都能吃掉。"])],
        stats=[("−14.3%", "默认 S1 的总写带宽代价"),
               ("62 组", "AIMD 参数扫描，无一双线达标"),
               ("21,220", "S1 硬件 FF‑eq（总线 + 表 + 乘法器 + 令牌桶）")])),

    ("section", dict(
        no="04", kicker="SECTION FOUR", title=["公平性 — 总带宽", "的 trade-off"],
        lead="先把「要多一点公平到底该付多少带宽」这条曲线精确算出来。",
        key_label="KEY MESSAGE",
        key="Jain = 0.99 处的前沿是 5.9226 flit/cycle，比 S0 实测还快 9.81%。"
            "两条验收线不互斥 —— S0 根本不在前沿上。")),

    ("figside", dict(
        chrome="公平性 — 带宽交换曲线", img="16-tradeoff.png", fig_w=8.15,
        caption="红线 = 二阶锥规划精确解的 R(J) 前沿；绿区 = 两条验收线同时满足的区域。"
                "本图与 Pareto 图同属 K = 2000 的筛选轮，S0 = 5.3937；"
                "官方 K = 20000 的 S0 = 5.4681。",
        kicker="FORMAL DERIVATION",
        blocks=[
            dict(kind="card", t="形式化", b=[
                "决策变量：每核事务注入率 λc。每 (hop, 方向, VC) 一条容量约束 "
                "[[Σc a(r,c)·λc ≤ 1]]，λ ≥ 0。",
                "Jain(λ) ≥ J ⟺ ‖λ‖₂ ≤ (1′λ) / √(J·n)，是**二阶锥**约束 ⇒ "
                "R(J) 可**精确**求解，不是估计。等速率端有闭式 R* = W / max ā(r) = **40/7**。"],
                 wt=1.5),
            dict(kind="band",
                 text="① 公平本身的固有代价只有 −10.71%（R* 5.7143 vs R_max 6.4000）。",
                 wt=0.72),
            dict(kind="band",
                 text="② Jain 0.99 处前沿 5.9226 > 同曲线上的 S0 实测 5.3937"
                      "（+9.81%）—— 更公平和更快不冲突。", wt=0.86),
            dict(kind="band",
                 text="③ 最后 0.01 的 Jain，单价是前面一大段的 3.6 倍 ⇒ "
                      "线画在 0.99 而不是 1.0。", wt=0.80)])),

    ("section", dict(
        no="05", kicker="SECTION FIVE", title=["理论最优与", "各方案 Pareto"],
        lead="先钉死「无限聪明、无限快的控制器最多能做到什么」，"
             "再把 16 个实测方案摆进同一张图。",
        key_label="KEY MESSAGE",
        key="差距不在容量，在机制：S0 的带宽距理想仍有 4.3% 余量，"
            "卡住的是 30 拍的信号时延。")),

    ("triple", dict(
        chrome="理想拥塞控制器的上限",
        kicker="THE REFERENCE EVERYTHING IS DIVIDED BY",
        title="无限聪明、无限快，只受资源守恒约束",
        cards=[
            dict(t="LP 模型", b=[
                "目的地分布不是假设，是从真实事务表数出来的经验分布 p(c,h)。"
                "一笔事务占用的资源由四拍握手和固定的时延最短路由唯一确定，"
                "所以每条资源的占用是 λ 的线性函数。",
                "模型**不含**缓冲、不含在环优先、不含 I-tag / E-tag —— "
                "这是有意的：把机制造成的损失也算进待解释的差距里。"]),
            dict(t="两个界", accent=True, b=[
                "**最大吞吐** R_max = 6.4000 flit/cycle，但这一点的 LP 最优解**不唯一**"
                "（速率 Jain 0.80–0.914）；最差的那个把 C0 / C10 完全饿死，"
                "闭环批量下不是可行工作点。",
                "**等速率** λ* = 2/7 = 0.285714 txn/cycle/core，R* = 40/7 = **5.7143**。"
                "瓶颈是环上的 hop，不是注入或下环端口 —— 加深队列、加宽端口都抬不高它。",
                "max-min 公平恰好落在等速率点，α-公平 α=1 恰好落在 R_max。"]),
            dict(t="分箱 Jain 的理想上限", b=[
                "50 拍箱内全环只有 285.7 个写 flit，每核约 28.6 个。"
                "确定性控制器把箱内总数按整数尽可能均分，得 J_ideal = **0.9997**"
                "（突发 2 flit 不可拆时 0.9990）。",
                "两者都远高于 0.99，所以**验收线不是被整数粒度挡住的**。"])],
        band="适用范围：λ* = 2/7 是「fabric + 流量 pattern」的联合解，不是 fabric 常数。"
             "换成 hot 之后绑定资源从入环 hop 变成热簇的下环口，λ* 掉到 0.100、"
             "R* 掉到 2.0000 —— 任何把 λ* 写进硬件的设计只对它被推导时的那个 pattern 正确。")),

    ("figside", dict(
        chrome="收益 — 硬件开销 Pareto", img="18-pareto.png", fig_w=8.55,
        caption="η =（总带宽 × 分箱 Jain）/ 理想控制器同项 = ÷ 5.7126。"
                "理想 = 1.0，S0 = 0.8274。筛选轮 K = 2000。",
        kicker="FRONTIER",
        blocks=[
            dict(kind="card", t="可实现前沿（总线一律按 30 拍计）", b=[
                "S0（0 FF‑eq，η 0.8274）→ [[S16]]（900，0.8329）→ S19（5,840，0.8474）"
                "→ [[S20 DCTCP]]（5,840，0.8587）→ [[S22-stock]]（13,920，0.8678）"
                "→ S22 深队列（1,198,560，0.8822）",
                "注：S19 与 S20 同价 5,840 FF‑eq，S19 的 η 更低，"
                "它留在前沿链上只是并列成本的排序产物 —— 该价位上应选 S20。"], wt=1.30),
            dict(kind="band",
                 text="没有任何可实现方案同时满足 Jain > 0.99 与带宽差 < 1%。", wt=0.66),
            dict(kind="card", t="", b=[
                "最接近的是 S22 在**总线 1 拍**下的点：Jain 0.98914、带宽 −0.56%、"
                "η 0.9287 —— 但它违反「任何总线使用花 30 拍」这条硬约束，"
                "图上以灰 X 标出、不参与前沿。",
                "[[卡点已定位：30 拍 > 50 拍验收窗的一半，控制环的时间常数注定比验收窗宽。"
                "这是 Jain 停在 0.92 的直接原因，与容量无关。]]"], wt=1.30)])),

    ("figside", dict(
        chrome="固定非均匀流量下的带宽 Pareto", img="20-hot-pareto.png", fig_w=7.35,
        caption="hot：十个核全部写入 HA 11 / 13 两节点簇。R* 用它自己的目的地分布重解 "
                "= 2.0000，绑定资源变成热簇的下环口。",
        kicker="ROBUSTNESS TO TRAFFIC SHIFT",
        blocks=[
            dict(kind="card", accent=True, t="先修一个免费的错", b=[
                "core_outstanding 128 → 32：**零硬件、无流量先验**。"
                "带宽从 0.7401 R* 拉到 0.9876 R*（+33%），E-tag 绕环从 6,627 降到 4,384。"
                "倾斜度扫描显示 32 在 5 个倾斜度中 4 个最优 —— 它不是新的先验。"], wt=1.06),
            dict(kind="card", t="修正基线后前沿只剩两点", b=[
                "S0（0 FF‑eq，0.9876）→ [[S16]]（900 FF‑eq，**0.9985**，"
                "离理想只差 0.15%）。更贵的方案要么低于免费基线，"
                "要么只高出 0.3% 以内（S19 / S20 = 0.9908），全部被 S16 支配。",
                "原因：hot 上的拥塞在**目的地**，而 S16 是唯一把控制点放在拥塞发生地的方案；"
                "源端之间的公平化在这里毫无帮助，还因让位 / 门控白扔槽位而亏带宽。"], wt=1.44),
            dict(kind="band",
                 text="但公平性没解决：这里 R*(等速率) = R_max，公平不要钱，"
                      "而分箱 Jain 只有 0.3684（S16 0.8542）—— 这不是为带宽付的代价，"
                      "是纯缺陷。", wt=0.92)])),

    ("section", dict(
        no="06", kicker="SECTION SIX", title=["前沿方案详述"],
        lead="每方案一页，结构与 S1 对齐：检测 → 传递 → 反馈 → 执行，再加实测与硬件。",
        key_label="SELECTION RULE",
        key="入选标准：在可实现 Pareto 前沿上，且目标值全部来自测量、"
            "不含任何流量 pattern 先验。定速钉死的 S24 / S25 因此被撤下。")),

    ("scheme", dict(
        chrome="S16 授权保留（900 FF‑eq） · receiver-driven / window / 本地触发",
        kicker="SCHEME 1 / 4 · CHEAPEST POSITIVE RETURN",
        title="S16：把 CHI 本来就有的那张授权用起来",
        steps=[
            dict(t="检测", b=["completer 只数**自己的在飞授权数**。不需要任何拥塞信号 —— "
                              "拥塞就发生在它自己的下环口上。"]),
            dict(t="传递", b=["**零**。不用总线、不用带内标记。"
                              "控制信号是协议本来就要发的那条 DBIDResp，线格式完全不动。"]),
            dict(t="反馈", b=["REQ 到达后先**排队**而不是立即授权；"
                              "同时在飞的授权不超过 overcommit = 64（Homa 的过量承诺度）。"]),
            dict(t="执行", accent=True,
                 b=["在排队者中把授权给**迄今被服务最少**的那个。"
                    "定长写下 Homa 的 SRPT 退化成公平排队。"])],
        cards=[
            dict(t="uniform 实测",
                 b=["η 0.8329，带宽 0.9468 R*（**+0.31% vs S0**），分箱 Jain 0.87945。"
                    "它管的是「在飞总量」，不区分是谁的，所以公平性提升有限。"]),
            dict(t="非均匀流量：唯一不亏的方案", accent=True,
                 b=["hot 上拿到 R* 的 **99.85%**；倾斜度 f = 0 / 0.25 / 0.5 / 0.75 / 1.0 "
                    "五档相对免费基线依次 +0.0000 / +0.0280 / +0.0238 / +0.0184 / +0.0086，"
                    "**没有一档为负**。"]),
            dict(t="硬件 900 FF‑eq",
                 b=["每 HA 一个 10 bit 在飞计数器 + 2 个比较器 + 1 个加法器。"
                    "关键性质：**扣授权不会制造气泡** —— 预留 slot 没用上就浪费掉，"
                    "而扣授权只是让占优核少一点数据待发，能用这个 slot 的核照样拿走。"])])),

    ("scheme", dict(
        chrome="S20 DCTCP（5,840 FF‑eq） · sender-driven / window / ECN 触发",
        kicker="SCHEME 2 / 4 · THE ONLY MID-PRICE POINT WITH POSITIVE BANDWIDTH",
        title="S20：带内标记 + 比例式缩窗",
        steps=[
            dict(t="检测", b=["completer tracker 占用越过 k_min = 0.8 后，"
                              "按 RED 概率（p_max = 0.05）给经过的事务打标。"
                              "RetryAck 视同必标。"]),
            dict(t="传递", b=["**带内标记**，不用专有总线，因此不受 30 拍规则约束 —— "
                              "反馈随响应包一起回到源端。"]),
            dict(t="反馈", b=["源端对标记比例做 EWMA（g = 1/16）得到 α，"
                              "即「这条路径最近有多堵」的连续估计，而不是 0/1 判决。"]),
            dict(t="执行", accent=True,
                 b=["每个标记 epoch 做一次乘性缩窗 w ×= 1 − α/2，无标记时加性恢复；"
                    "窗口夹在 [8, 128] 且不超过 core_outstanding。"])],
        cards=[
            dict(t="uniform 实测", accent=True,
                 b=["η 0.8587（可实现前沿上性价比最好的中价位点），"
                    "带宽 0.9492 R*（**+0.56% vs S0，本轮唯一带宽为正的中价位方案**），"
                    "分箱 Jain 0.90441。"]),
            dict(t="为什么带宽为正",
                 b=["标记打在**真正拥塞的那个资源**上，所以它削掉的正是会导致 E-tag "
                    "绕环重试的那部分注入 —— 少注入换来更少的无效绕环。"]),
            dict(t="局限（必须讲清）",
                 b=["ECN 是**拥塞**信号而不是**公平**信号，Jain 只到 0.904。"
                    "hot 上（在飞上限 32）带宽 0.9908 R*、Jain 仅 0.3847；"
                    "早期看到的「+33.6%」全部来自 win_init = 16 这个静态在飞上限，"
                    "控制回路整轮一次都没触发（n_mark = 0）。"])])),

    ("scheme", dict(
        chrome="S19 Swift（5,840 FF‑eq） · sender-driven / window / 时延触发",
        kicker="SCHEME 3 / 4 · NO NEW WIRE AT ALL",
        title="S19：用端到端时延当信号，一根新线都不加",
        steps=[
            dict(t="检测", b=["端到端 RTT（REQ 发出到 Comp 返回）。基线取**全环最小** RTT "
                              "而不是每核 min，避免把一次幸运的邻跳采样当成基准。"]),
            dict(t="传递", b=["**无需任何新线**：时延本身就是带内信号，"
                              "交换机也不必支持标记。这是它与 S20 唯一的结构差别。"]),
            dict(t="反馈", b=["目标 = 8 × base RTT，base 带 20 拍下限"
                              "（fabric 尺度校准，不能照搬数据中心的绝对阈值）。"]),
            dict(t="执行", accent=True,
                 b=["rtt ≤ target 时窗口加性增 +1/w；否则 w ×= 1 − 0.4·(rtt − target)/rtt，"
                    "按超出比例缩，而不是固定砍半。"])],
        cards=[
            dict(t="uniform 实测",
                 b=["η 0.8474，带宽 0.9372 R*（−0.71% vs S0），分箱 Jain 0.90386。"
                    "硬件与 S20 完全同价：每核 24 bit 计数 + 1 个 EWMA + 2 加 + 2 比较。"]),
            dict(t="同价位被 S20 支配", accent=True,
                 b=["两者都是 5,840 FF‑eq，但 S20 的 η 高 0.0113、带宽还为正。"
                    "**如果只能选一个 ECN / delay 型窗口控制器，选 S20。**"
                    "S19 的价值在于它连交换机标记支持都不需要。"]),
            dict(t="非均匀流量",
                 b=["hot（在飞上限 32）带宽 0.9908 R*、Jain 0.3847，与 S20 逐位相同 —— "
                    "因为两个控制器在那里都**一次都没触发**（n_win_down = 0）。"
                    "时延信号在下环口拥塞上同样不灵敏。"])])),

    ("scheme", dict(
        chrome="S22 赤字触发的限域让路（13,920 FF‑eq） · receiver-driven / "
               "仲裁型 / 总线触发",
        kicker="SCHEME 4 / 4 · BEST BUILDABLE η UNDER 20K FF-EQ",
        title="S22：复用 S1 那条 6 bit 总线，但播进度、且不设闸门",
        steps=[
            dict(t="检测", b=["每节点只数**自己本窗成功上环的 flit 数**，饱和到 6 bit。"
                              "播的是进度，不是拥塞等级。"]),
            dict(t="传递", b=["复用 S1 那条 6 bit 广播总线，**位宽完全相同**，"
                              "只换了线上放什么。自己的进度也从总线读回："
                              "两边过同一个量化器、同一段延迟，开局瞬态不会留下永久偏置。"]),
            dict(t="反馈", b=["赤字 = 总线上 10 项计数的均值 − 自己那一项；"
                              "越过 thresh = 0.5 就举请求。"
                              "让路是**单向**的：落后的节点永不让路。"]),
            dict(t="执行：让位，不是门控", accent=True,
                 b=["不落后的节点只对「会从请求者出向 hop 骑过去」的 flit 让路"
                    "（scope = segment）；同时**前瞻改发**一个会在请求者之前下环的 flit，"
                    "让自己的 hop 不空转；margin = 4.0 拒掉对「差不多齐」的节点让路。"])],
        cards=[
            dict(t="30 拍总线 + stock 队列（推荐点）", accent=True,
                 b=["η **0.8678**，带宽 0.9425 R*（**−0.15% vs S0**），分箱 Jain 0.92046 "
                    "—— 所有 < 20k FF‑eq 的方案里 η 最高。硬件：6 bit 广播 × 20 + "
                    "10 项 8 bit 镜像表 + 10 bit 计数器 + 加法树 + 8 路前瞻比较器。"]),
            dict(t="若总线能做到 1 拍",
                 b=["Jain **0.98878**、带宽 −0.78%、整窗 max/min 1.6931 → **1.0002**，"
                    "核间长期速率差被抹到 1.0001（27.147 vs 27.151）。两条线几乎同时达成 "
                    "—— 但这违反 30 拍硬约束，只能作为架构决策的输入。"]),
            dict(t="两条结构性结论",
                 b=["① 深队列变体（12/8 → 32/32）η 只多 +0.0144，硬件贵 **86 倍**"
                    "（1,198,560 FF‑eq），**不推荐**。",
                    "② 同等公平度下**让位比门控省 10 倍带宽**：S23 门控付 1.50%，"
                    "S22 让位只付 0.15%。"])])),

    ("section", dict(
        no="07", kicker="SECTION SEVEN", title=["结论与建议"],
        lead="哪些已经关死，哪些还开着，以及下一步该由谁拍板。",
        key_label="BOTTOM LINE",
        key="带宽线已基本关死；公平线是真实缺陷但理论上不需要付带宽；"
            "真正的卡点是 30 拍的流控总线时延。")),

    ("matrix", dict(
        chrome="结论", kicker="CONCLUSIONS", title="四条",
        cells=[
            dict(t="① 带宽线已基本关死", b=[
                "S0 = 95.69% R*，4.31% 的缺口已逐拍闭合（1,056 绕环加价 + 2,096 空转），"
                "出厂规格下可达上限约 96.5%。",
                "[[拥塞控制的带宽目标应是「不弄丢」，不是「抬上去」。]]"]),
            dict(t="② 公平缺陷是真实的", b=[
                "不是采样噪声：把抖动完全抹平后的 Jain 上限只有 0.95341，"
                "六快四慢是几何决定的固定分组（核间速率比 1.6934）。",
                "[[要到 0.99 就必须真的搬速率，只整时机不够。]]"]),
            dict(t="③ 理论上两条线不互斥", b=[
                "Jain = 0.99 处的精确前沿是 5.9226 flit/cycle，"
                "比同一轮的 S0 实测 5.3937 还高 9.81%；公平本身的固有代价只有 −10.71%。",
                "[[差距全部在机制层，不在 fabric 容量。]]"]),
            dict(t="④ 卡点是信号时延", accent=True, b=[
                "可用信号必须来自测量，而测量要走 30 拍的总线；30 > 50 拍验收窗的一半，"
                "控制环时间常数注定比验收窗宽。",
                "[[这是可实现方案的 Jain 停在 0.92 的直接原因。]]"])],
        band="另一条贯穿全篇的机制结论：在无缓存环上「把速率从快核转移给慢核」本身是有损的 "
             "—— 让出的气泡只能向下游传递、沿途会被吃掉，而热点 hop 已经 90% 满。"
             "所以执行器必须选让位而不是门控。")),

    ("process", dict(
        chrome="建议", kicker="RECOMMENDATION",
        title="按成本递增的四步，前两步现在就能做",
        steps=[
            dict(t="立刻做 · 0 FF‑eq", b=[
                "**core_outstanding 128 → 32。**零硬件、无流量先验，"
                "uniform 带宽 +1.1%（顺带分箱 Jain 0.876 → 0.910），非均匀流量带宽 +33%。",
                "[[注意：本研究此前一直用 128，所以各章节的 S0 基线偏低，"
                "排序需在 32 上复核。]]"]),
            dict(t="要带宽鲁棒性 · 900 FF‑eq", b=[
                "**投 S16 授权保留。**唯一在任何倾斜度下都不亏带宽的方案，"
                "hot 上拿到 R* 的 99.85%，公平度同时改善。",
                "实现成本极低：不加总线、不加表，只改 completer 何时发 DBIDResp。"]),
            dict(t="要 uniform 公平性 · 13,920 FF‑eq", b=[
                "**投 S22-stock。**复用 S1 那条 6 bit 总线，"
                "去掉乘法器和令牌桶换成加法树。Jain 0.876 → 0.920，带宽只差 0.15%。",
                "[[不要买深队列变体：η 只多 0.014，硬件贵 86 倍。]]"]),
            dict(t="要闭合到 0.99 · 需架构拍板", accent=True, b=[
                "二选一，都超出「与 S1 同级硬件」的范围：",
                "**(a)** 把流控总线时延从 30 拍降到 ≤ 2 拍 —— S22 即可拿到 "
                "Jain 0.98878 / 带宽 −0.78% / max-min 1.0002。",
                "**(b)** 允许在环 flit 为入环 flit 让位，即打破「在环绝对优先」。"])],
        band="四步之间没有依赖：第 1 步可以单独上线，第 2、3 步可并行评估，"
             "第 4 步只决定能否从 Jain 0.92 闭合到 0.99。")),

    ("closing", dict(
        title=["汇报完毕", "请架构组决策"],
        lead=["需要拍板的一件事：流控总线时延能否从 30 拍降到 ≤ 2 拍。",
              "这决定 Jain 能停在 0.92 还是 0.99。"])),
]

BUILDERS = {
    "cover": s_cover, "agenda": s_agenda, "section": s_section,
    "closing": s_closing, "media": s_media, "figside": s_figure_side,
    "process": s_process, "scheme": s_scheme, "matrix": s_matrix,
    "triple": s_triple, "bars": s_bars, "compare": s_compare,
}


def main() -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)
    total = len(DECK)
    for i, (kind, data) in enumerate(DECK, 1):
        slide = BUILDERS[kind](prs, data)
        if kind != "cover":                      # cover footer is a red band
            page_no(slide, i, total, on_dark=(kind == "closing"))
    prs.save(OUT)
    print(f"wrote {OUT}  ({total} slides)")


if __name__ == "__main__":
    main()
