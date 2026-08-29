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
              "数据口径：uniform tiled 写 · K = 20000 · 单 plane · 每核在飞上限 32 · "
              "瞬时均衡度按 100 拍窗 · 全部可复现"])),

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
        band="公平性主指标：100 拍宽的窗内对 10 个核的写带宽算 Jain，"
             "再对所有争用窗内的箱取平均。全篇瞬时均衡度都用这个口径。")),

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
                 b=["深度 12。整轮里满的比例 0%，从来不是瓶颈 —— "
                    "所以带宽不是被下环侧卡住的。"]),
            dict(t="在环绝对优先", accent=True,
                 b=["bufferless：transit flit 永不被本地注入打断，注入只能挤空隙。"]),
            dict(t="I-tag / E-tag",
                 b=["t_inj = 4 定向上游预约单槽；t_xfer = 1，首次下环失败即打标，"
                    "再到达时最高优先级下环。"]),
            dict(t="每核在飞上限 = 32", accent=True,
                 b=["最坏情况下一笔写的空载 RTT 是 89 拍，等速率份额 2/7 笔/拍 → "
                    "覆盖 RTT 只需约 26 笔，取 32。HA tracker 512 远大于 10 × 32，不绑定。"]),
            dict(t="专用流控总线",
                 b=["广播，不占 NoC hop；**任何使用固定 30 拍延迟**（物理约束，"
                    "不是可调项），控制窗 64 拍。"])])),

    ("section", dict(
        no="02", kicker="SECTION TWO", title=["S0 的具体问题"],
        lead="总带宽已经打到理论上限的 96.97%，但十个核的瞬时带宽差 1.57 倍。",
        key_label="KEY MESSAGE",
        key="带宽这条线基本关死了；真正的缺陷在公平性一侧，"
            "而且是几何决定的固定分组，不是随机抖动。")),

    ("media", dict(
        chrome="S0：总带宽已打满", kicker="BANDWIDTH IS SATURATED",
        stat="96.97%",
        stat_sub=["S0 = 5.5412 flit/cycle", "占等速率上限 R* = 5.7143"],
        img="07-totalbw.png",
        caption="左 = 每个 VC 的 40 条有向链路按占用率排序，天花板是「每拍 1 flit」；"
                "右 = 绑定链路的拍数预算。两幅都不把分箱带宽和理论上限放在同一根轴上。",
        cards=[
            dict(t="判据：链路已经快满了",
                 b=["最忙的 8 条链路（RSP 的 1→0 / 11→10 / 7→8 / 17→18 与对应的 4 条 "
                    "DAT）占用都是 **96.98%**。它们就是 R* 的绑定项 —— "
                    "带宽是被链路卡住的，不是被队列或端口卡住的。"]),
            dict(t="差的 3.03% 是什么", accent=True,
                 b=["makespan 72,186 = 70,000 载有效载荷 + 5 绕环重发 + "
                    "**2,181 空转**，三项逐拍相加相等。"
                    "core_outstanding 固定为 32、覆盖最坏 RTT；在这个前提下，"
                    "绕环重发仅 5 拍，剩余缺口来自已分类的空转。"]),
            dict(t="空转是什么组成的",
                 b=["逐拍归因、无残项：**I-tag 主动让位 49.1%**（槽被留给上游预约）、"
                    "**dry 42.0%**（四段握手串行，HA 手里还没有 RSP 可发）、"
                    "在环 flit 同拍抢走 8.6%、队头阻塞 0.2%。",
                    "[[能回收的不到 1 个点，且要拿公平性换：I-tag 调强可到 97.59% R*，"
                    "但 Jain 掉 0.029；加深下环队列也不能改善这一结论。]]"])])),

    ("bars", dict(
        chrome="S0：各核写带宽", kicker="PER-CORE BANDWIDTH",
        title=["六快四慢，", "是固定分组不是抖动"],
        lead="最低 C8 = 0.44978，最高 C14 = 0.70449。慢的那四个正是"
             "「邻接 mem 数 = 1」的 C0 / C8 / C10 / C18。",
        stat="1.5663", stat_sub="整窗 MAX / MIN（S0，K = 20000）",
        rows=[("C0", "0.455", 0.607), ("C2", "0.687", 0.916),
              ("C4", "0.688", 0.917), ("C6", "0.698", 0.930),
              ("C8", "0.450", 0.600), ("C10", "0.458", 0.610),
              ("C12", "0.690", 0.920), ("C14", "0.704", 0.939),
              ("C16", "0.674", 0.898), ("C18", "0.466", 0.621)],
        caption="条长按 0.75 flit/cycle 归一。8 个 HA 收到的 WriteData 完全相等"
                "（50,000 / HA），所以这不是访存不均。")),

    ("media", dict(
        chrome="S0：100 拍瞬时公平性", kicker="INSTANTANEOUS FAIRNESS",
        stat="0.92818",
        stat_sub=["100 拍分箱平均 JAIN", "理想控制器 = 0.99997"],
        img="09-s0-instbal.png",
        caption="左 = 主指标随时间；右 = 同一份数据换观察窗宽度，实测始终低于理想控制器。",
        cards=[
            dict(t="不是个别坏箱",
                 b=["567 个箱里 p05 = 0.88533，最差箱 0.84551。右图把观察窗一路放宽："
                    "实测只收敛到 0.96458 的天花板，永远追不上理想控制器。"]),
            dict(t="抖动抹平后的上限只有 0.96458", accent=True,
                 b=["给每核每箱都填上它自己的长期均值（抖动全抹掉、只留速率差），"
                    "Jain 上限就到顶了 —— 这是任何「只整时机、不搬份额」机制的天花板。"]),
            dict(t="所以要提高均衡度，必须真的搬速率",
                 b=["核间长期速率 44.95 vs 70.46 flit/箱（比 1.5673）。"
                    "核内抖动占方差 53.4%，但封顶的是另外那 46.6% 的核间差 —— "
                    "100 拍窗内每核有 59.7 个 flit，整数粒度不构成限制。"])])),

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
                    "[[官方 run 最忙 hop 占用 96.98%（RSP）；K=2000 短探测把绑定 hop 的"
                    "空槽逐拍分类，「端口空闲」一列合计 1，实质为零 —— "
                    "没有一个空槽是仲裁失误。]]"]),
            dict(num="03", t="症状：差距全在「等着上环」这一段",
                 b=["8 个 HA 收到的 WriteData 完全相等，每核每方向**成功**上环数也被路由"
                    "钉死在 30,000 笔、逐位相同。服务侧没有任何不均。",
                    "[[所以差的不是「谁被服务得多」，而是「谁等得久」—— "
                    "决策点在注入排队，不在存储侧。]]"])])),

    ("section", dict(
        no="03", kicker="SECTION THREE", title=["S1 方案与代价"],
        lead="拥塞检测、拥塞传递、拥塞反馈、流量控制，以及调参之后的两难。",
        key_label="KEY MESSAGE",
        key="S1 的公平性来自「把所有人一起压慢」：Jain +0.029，总带宽 −16.3%；"
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
                    "本设计里任何总线使用固定 **30 拍**延迟（物理约束）；30 < 窗口 64，"
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
        stat="−16.3%", stat_sub=["S1 默认档的总写带宽代价", "换来 Jain +0.029"],
        img="12-s1-effect.png",
        caption="左 = 每核写带宽对比；右 = 三个工作点在带宽—Jain 平面上的位置。",
        cards=[
            dict(t="S1 默认（band = spec）",
                 b=["100 拍分箱 Jain 0.92818 → **0.95675**，整窗 max/min "
                    "1.5663 → 1.3586；总写带宽 5.5412 → **4.6397（−16.3%）**。"
                    "公平性进步是真的，带宽代价也是真的。"]),
            dict(t="S1T 每向预算（调参后）", accent=True,
                 b=["62 组 AIMD 参数网格自己选出的最优点（dir_split、cap 0.5、w 64、"
                    "burst 1）：带宽回到 **5.5426（+0.03%）**，但 Jain 掉回 "
                    "**0.92596**，max/min 反升到 1.5815 —— 和 S0 实质相同。"]),
            dict(t="两档之间没有中间地带",
                 b=["从 S1 走到 S1T：带宽 +19.5%，Jain −0.031，一分不少地还回去。"
                    "62 组扫描里没有出现「带宽基本不掉、Jain 明显更好」的配置 —— "
                    "这不是没调好，是机制本身的形状。"])])),

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
                "**所有核一起下降**：慢核 C18 0.46574 → 0.42582（−8.6%），"
                "快核 C14 0.70449 → 0.54936（−22.0%）。max/min 变好，是上限被砍出来的。",
                "**闸门型执行器丢槽**：令牌桶「没额度就不上环」，"
                "而无缓存环上让出的槽不会被记账保留，沿途任何节点都能吃掉。"])],
        stats=[("−16.3%", "默认 S1 的总写带宽代价"),
               ("62 组", "AIMD 参数扫描，无一组两头都好"),
               ("21,220", "S1 硬件 FF‑eq（总线 + 表 + 乘法器 + 令牌桶）")])),

    ("section", dict(
        no="04", kicker="SECTION FOUR", title=["公平性 — 总带宽", "的 trade-off"],
        lead="先把「要多一点公平到底该付多少带宽」这条曲线精确算出来。",
        key_label="KEY MESSAGE",
        key="总带宽和公平性确实不可兼得：把十个核压到完全等速率，"
            "结构上就要放弃 10.71% 的峰值带宽 —— 但这是全部的代价，S1 付的远不止这些。")),

    ("figside", dict(
        chrome="公平性 — 带宽交换曲线", img="16-tradeoff.png", fig_w=8.15,
        caption="红线 = 每个公平目标下的理论最高带宽；点 = 官方 K = 20000 实测。"
                "其中 I-tag 点只是 S0 的 t_inj / hold 调参，不是独立机制。",
        kicker="HOW TO READ THE CURVE",
        blocks=[
            dict(kind="card", t="先认四个量", b=[
                "**λc**：第 c 个核的事务速率；十个核就是十个待选择的速率。",
                "**链路占用表**：每个核发一笔事务，会占哪些链路、各占几拍。"
                "路由和握手固定后，这张表也是固定的。",
                "**J**：本次要求的最低 Jain 公平度，例如 0.95。",
                "**R(J)**：在公平度不低于 J 的前提下，所有核合计最多能跑多快。"],
                 wt=1.44),
            dict(kind="band",
                 text="一个红线点怎么来：先固定 J，再尝试十个核的所有速率组合；"
                      "任何链路都不能超过每拍 1 flit，最后保留总带宽最高的组合。", wt=1.02),
            dict(kind="band",
                 text="把 J 从低到高逐点重算，就得到整条红线。"
                      "这是标准凸优化的全局上界（只有数值求解误差），不是对仿真点做拟合。", wt=0.92),
            dict(kind="band",
                 text="读图：红线上 = 该公平度下理论能做到的最好结果；"
                      "红线下 = 实际机制损失；红线上方 = 资源容量不允许。"
                      "J = 1 的最右端就是等速率上限 R* = 5.7143。", wt=1.00)])),

    ("section", dict(
        no="05", kicker="SECTION FIVE", title=["理论最优与", "各方案 Pareto"],
        lead="先钉死「无限聪明、无限快的控制器最多能做到什么」，"
             "再把实测方案摆进同一张图。",
        key_label="KEY MESSAGE",
        key="一个无限聪明的拥塞控制器，最好也就是 5.7143 flit/cycle（S0 的 103.1%）"
            "配 Jain ≈ 1.0。可争的总带宽只有 3 个点，公平性那一侧才是主战场。")),

    ("triple", dict(
        chrome="理想拥塞控制器的上限",
        kicker="THE REFERENCE EVERYTHING IS DIVIDED BY",
        title="无限聪明、无限快，只受资源守恒约束",
        cards=[
            dict(t="什么是 LP 模型", b=[
                "LP = 线性规划：**变量**是十个核各自的注入速率，"
                "**约束**是每条链路每拍最多搬 1 flit，**目标**是把总带宽做到最大。"
                "因为路由和握手都固定，「一笔事务占几条链路各几拍」是一张常数表，"
                "占用量随速率**线性**增长，所以这个最大值能被精确解出来、不需要仿真。",
                "模型里**没有**缓冲、没有在环优先、没有 I-tag / E-tag —— "
                "这是有意的：让机制造成的损失也留在待解释的差距里。"]),
            dict(t="两个界", accent=True, b=[
                "**最大吞吐** R_max = 6.4000 flit/cycle。但这一点的解**不唯一**，"
                "其中最差的那个把 C0 / C10 完全饿死 —— 不是可交付的工作点。",
                "**等速率点** = 强行要求十个核速率**完全相同**时能达到的最大总带宽。"
                "此时每核 λ* = 2/7 = 0.2857 笔/拍，合计 R* = 40/7 = **5.7143**。"
                "它就是「最公平」那一端的天花板，全篇的分母都是它。",
                "瓶颈在环上的链路，不在注入或下环端口 —— 加深队列、加宽端口都抬不高它。"]),
            dict(t="理想控制器能做到什么", b=[
                "100 拍箱内全环有 571.4 个写 flit，每核约 57.1 个。"
                "一个确定性的理想控制器把箱内总量按整数尽量均分，得 "
                "J_ideal = **0.99997**。",
                "也就是说：**理论最优 = 5.7143 flit/cycle（S0 的 103.1%）配 Jain ≈ 1.0**。"
                "整数粒度不构成任何限制。"])],
        band="适用范围：λ* = 2/7 是「fabric + 流量 pattern」的联合解，不是 fabric 常数。"
             "换成 hot 之后绑定资源从入环 hop 变成热簇的下环口，λ* 掉到 0.100、"
             "R* 掉到 2.0000 —— 任何把 λ* 写进硬件的设计只对它被推导时的那个 pattern 正确。")),

    ("figside", dict(
        chrome="收益 — 硬件开销 Pareto", img="18-pareto.png", fig_w=8.55,
        caption="η =（总带宽 × 分箱 Jain）/ 理想控制器的同一乘积。理想 = 1.0。"
                "筛选轮 K = 2000，100 拍窗。",
        kicker="FRONTIER",
        blocks=[
            dict(kind="card", t="横轴的 FF‑eq 是什么", b=[
                "**FF‑eq = 等效触发器数**（flip-flop equivalent）：把方案新增的寄存器、"
                "比较器、加法器、乘法器、总线线宽都折算成「相当于多少个 D 触发器」，"
                "得到一个可以横向比较的面积代价。S1 = 21,220 FF‑eq 就是这么算出来的。"],
                 wt=1.02),
            dict(kind="card", t="严格前沿只剩两个点", b=[
                "S0（0 FF‑eq，η 0.8894）→ [[S16]]（900 FF‑eq，η **0.9395**）。"
                "900 FF‑eq 买到离理想控制器最近的位置，"
                "**比 S1 便宜 24 倍、η 高 0.174**。",
                "S22-stock（13,920，η 0.8883）保留为事务层不可改时的环仲裁备选；"
                "S19 / S20（5,840，η 0.8992）只作为 requester 窗口控制的研究对照。",
                "I-tag t_inj=2 / hold=2 只是 S0 参数重调，不再作为独立方案计成本或入选。"],
                 wt=1.34),
            dict(kind="band",
                 text="判据是「离理想控制器有多近」，不是任何一条固定验收线："
                      "S16 把 η 从 0.8894 推到 0.9395，走完了 S0 与理想之间 45% 的距离。",
                 wt=1.00)])),

    ("figside", dict(
        chrome="固定非均匀流量下的带宽 Pareto", img="20-hot-pareto.png", fig_w=8.55,
        caption="hot：十个核全部写入 HA 11 / 13 两节点簇。R* 用它自己的目的地分布重解 "
                "= 2.0000，绑定资源变成热簇的下环口。此处**只看总带宽**。",
        kicker="ROBUSTNESS TO TRAFFIC SHIFT",
        blocks=[
            dict(kind="card", accent=True, t="为什么这里不看瞬时 Jain", b=[
                "十个核全部打同一个两节点热簇，谁快谁慢由「离热簇几跳」直接决定，"
                "拥塞点也不在注入侧。这种流量下的瞬时均衡度既不是设计目标、"
                "也不反映控制器好坏 —— 这一页只考察**总带宽是否被换掉**。"], wt=1.10),
            dict(kind="card", t="前沿只剩两点", b=[
                "固定实验配置下，S0 本身就已经拿到 R* 的 **98.76%**。"
                "在此之上 [[S16]]（900 FF‑eq）到 **99.74%**，E-tag 绕环降到 **35**。",
                "更贵的方案全部更差：S19 / S20 = 99.08%，S22-stock = 98.75%，"
                "S1 = 98.35%。原因是 hot 的拥塞在**目的地**，"
                "而 S16 是唯一把控制点放在拥塞发生地的方案；"
                "源端之间的公平化在这里毫无帮助，还因让位 / 门控白扔槽位而亏带宽。"],
                 wt=1.52),
            dict(kind="band",
                 text="结论：换流量 pattern 之后，S16 仍然不亏带宽 —— "
                      "它不含任何 pattern 先验。", wt=0.80)])),

    ("section", dict(
        no="06", kicker="SECTION SIX", title=["前沿方案详述"],
        lead="两个落地候选、两个研究对照：机制示意 + 与 S0 / S1 的同口径实测。",
        key_label="SELECTION RULE",
        key="S16 是主方案，S22 是事务层不可改时的环仲裁备选；"
            "S19 / S20 用于验证 requester 动态窗口的边界，不进入最终架构建议。")),

    ("figside", dict(
        chrome="S16 授权保留（900 FF‑eq） · receiver-driven / 本地触发 / 零新报文",
        img="22-s16-diagram.png", fig_w=8.75,
        caption="上 = 写路径：把协议本来就要发的 DBIDResp 当授权用；"
                "下 = 读路径：同一套状态可以照搬，但实测不需要（见下页）。",
        kicker="FRONTIER · COMPLETER MECHANISM",
        blocks=[
            dict(kind="card", t="四段机制（与 S1 同格式）", b=[
                "**① 检测**：HA 只数自己的在飞授权数。不需要任何拥塞信号 —— "
                "拥塞就发生在它自己的下环口上。",
                "**② 传递**：零。不加总线、不加带内标记，线格式完全不动。",
                "**③ 反馈**：REQ 到达后先排队而不是立即授权，"
                "同时在飞授权 ≤ overcommit = 16。",
                "**④ 执行**：在排队者里把授权给**迄今被服务最少**的那个核。"],
                 wt=1.62),
            dict(kind="card", accent=True, t="改的是事务层，不是链路层", b=[
                "四段全部落在 completer 内部：它决定**什么时候、先给谁**发 DBIDResp。"
                "线格式、协议状态机都不动，但**动了 HA 的事务调度** —— "
                "这一条决定它能不能落地，见结论页。"], wt=1.06),
            dict(kind="band",
                 text="overcommit 要跟着在飞上限走：上限 32 时取 16（12–16 是一段平台）。"
                      "取 64 就永远扣不住任何东西，S16 会退化成 S0。", wt=0.72)])),

    ("media", dict(
        chrome="S16 实测：写侧要做，读侧不用做", kicker="MEASURED · UNIFORM",
        stat="0.98466",
        stat_sub=["S16 写侧 100 拍 Jain（S0 = 0.92818）",
                  "总写带宽 −0.39%，整窗 max/min = 1.0001"],
        img="23-s16-compare.png",
        caption="左两幅 = 写（K = 20000），右两幅 = 读（K = 5000）；"
                "每幅内三根柱依次 S0 / S1 / S16，橙虚线 = 理论上限 R*。"
                "注意读侧 S0 与 S16-R 两根柱几乎一样高。",
        cards=[
            dict(t="写：几乎白拿的公平性", accent=True,
                 b=["Jain 0.92818 → **0.98466**（S1 只到 0.95675），"
                    "整窗 max/min 1.5663 → **1.0001**，速率差实质抹平；"
                    "总带宽只让 0.39%，而 S1 要让 16.3%。"]),
            dict(t="读：S0 本来就是齐的",
                 b=["十个核的读带宽只差 **0.36%**（0.5603–0.5623），"
                    "Jain 0.99067、max/min 1.0035，带宽已到 R* 的 97.96%。"
                    "**读侧没有待解决的问题。**"]),
            dict(t="所以读侧建议不做",
                 b=["S16-R 把带宽推到 98.41%（+0.47%）、Jain 到 0.99449，"
                    "[[收益在噪声量级，不值一次事务层改动。]]",
                    "顺带一条反证：同样管读、但把控制放在请求端的 S1-R 掉 20.5% 带宽、"
                    "Jain 反而更低（0.97474）。控制点必须在 HA 侧。"])])),

    ("figside", dict(
        chrome="S19 Swift / S20 DCTCP（各 5,840 FF‑eq） · requester 动态窗口",
        img="28-window-diagram.png", fig_w=8.75,
        caption="两者共用每核动态 outstanding 窗口；S19 用协议天然 RTT，"
                "S20 用 HA tracker 产生的 1 bit ECN 标记。",
        kicker="REFERENCE · WINDOW CC",
        blocks=[
            dict(kind="card", t="共同执行器：动态 outstanding", b=[
                "每个 core 维护窗口 Wc：只有当前在飞事务数 < Wc，才允许发新的 REQ。"
                "初值 16、下限 8、硬上限仍是 core_outstanding = 32。",
                "事务完成归还名额；Retry 重发不受窗口限制。"
                "窗口有名额时允许突发，因此比逐拍 rate pacer 更贴近 CHI 的事务模型。"],
                 wt=1.25),
            dict(kind="card", accent=True, t="S19：RTT 驱动", b=[
                "REQ 上环到 DBIDResp 回到 core 就是一份现成 RTT 样本，"
                "Retry 往返也被计入。RTT 低于 target 就加窗，高于 target 就按超额比例缩窗。"
                "**不加报文、不加标记位。**"], wt=1.00),
            dict(kind="card", t="S20：ECN 驱动", b=[
                "HA 根据 request tracker 占用率在 DBIDResp 上附 1 bit mark，RetryAck 直接视为 mark。"
                "core 对标记比例做 EWMA；有标记按 α/2 缩窗，无标记按 1/W 加窗。"], wt=1.00)])),

    ("media", dict(
        chrome="S19 / S20 实测：均匀写", kicker="MEASURED · UNIFORM",
        stat="≤0.28%",
        stat_sub=["S19 / S20 的总带宽与 S0 的最大差异",
                  "两者各 5,840 FF-eq · requester 动态窗口"],
        img="29-window-compare.png",
        caption="三幅依次比较总带宽、100 拍瞬时 Jain、整窗最快 / 最慢核带宽比；"
                "每幅内四根柱都是 S0 / S1 / S19 / S20。",
        cards=[
            dict(t="信号不同，执行器相同", accent=True,
                 b=["S19 看端到端 RTT，能覆盖 ring 与 completer 等待；"
                    "S20 只看 HA tracker 压力，信号更直接但需要 DBIDResp 的 1 bit mark。"]),
            dict(t="当前工作点结果接近",
                 b=["**S19**：带宽 5.5255（vs S0 −0.28%）、Jain 0.93324、"
                    "max/min 1.5515。",
                    "**S20**：带宽 5.5478（vs S0 +0.12%）、Jain 0.93017、"
                    "max/min 1.5514。"]),
            dict(t="参考结论",
                 b=["两者都基本保住总带宽，但 Jain 与长期速率差几乎没有离开 S0。"
                    "这说明 requester 窗口能限制注入量，却没有把瓶颈 hop 的服务机会"
                    "从快核搬给慢核。",
                    "[[仅保留为 source-side 控制对照，不进入最终架构建议。]]"])])),

    ("figside", dict(
        chrome="S22 赤字触发的限域让路（13,920 FF‑eq） · 仲裁型 / 总线触发",
        img="24-s22-diagram.png", fig_w=8.75,
        caption="复用 S1 那条 6 bit 广播总线，位宽完全相同，只换了线上放什么："
                "从「我有多堵」换成「我这窗发成了多少」。",
        kicker="RING-ONLY · MECHANISM",
        blocks=[
            dict(kind="card", t="四段机制", b=[
                "**① 检测**：每节点只数自己本窗成功上环的 flit 数，饱和到 6 bit。"
                "播的是进度，不是拥塞等级。",
                "**② 传递**：复用 S1 的 6 bit 广播总线（30 拍）。"
                "自己的进度也从总线读回，两边过同一个量化器、同一段延迟。",
                "**③ 反馈**：赤字 = 总线上 10 项的均值 − 自己那一项，越过 0.5 就举请求。"
                "让路是**单向**的：落后的节点永不让路。",
                "**④ 执行：让位，不是门控**。只对「会从请求者出向 hop 骑过去」的 flit "
                "让路，同时前瞻改发一个会先下环的 flit，让自己的 hop 不空转。"],
                 wt=1.90),
            dict(kind="band",
                 text="与 S1 的关键差别：S1 的执行器是令牌桶闸门（没额度就不上环，"
                      "让出的槽被沿途吃掉）；S22 只在具体某一拍上让出具体某个位置。",
                 wt=0.86),
            dict(kind="band",
                 text="同等公平度下让位比门控省 10 倍带宽：S23 门控付 1.50%，"
                      "S22 让位只付 0.15%（K = 2000 同轮对比）。", wt=0.70)])),

    ("media", dict(
        chrome="S22 实测：均匀流量", kicker="MEASURED · UNIFORM",
        stat="0.94799",
        stat_sub=["S22 写侧 100 拍 Jain（S0 = 0.92818）", "总写带宽 −2.21%"],
        img="25-s22-compare.png",
        caption="四根柱依次 S0 / S1 / S16 / S22，橙虚线 = 理论上限 R*；写侧 K = 20000。"
                "后两幅是公平性的两个不同问题，见右侧第三张卡。",
        cards=[
            dict(t="比 S1 全面更好", accent=True,
                 b=["Jain 0.94799 vs S1 的 0.95675 基本持平，"
                    "但带宽只让 **2.21%**，S1 要让 **16.3%**；"
                    "max/min 1.2230 vs 1.3586。硬件还便宜 34%（13,920 vs 21,220 FF‑eq）。"]),
            dict(t="数字上被 S16 支配，但层次不同",
                 b=["S16 用 **1/15 的硬件**拿到更高的 Jain（0.98466）和更小的带宽代价"
                    "（0.39%）。S22 唯一的、也是决定性的优势："
                    "[[它只改环上仲裁，一点事务层都不碰。]]"]),
            dict(t="两个公平性指标分别在问什么",
                 b=["**瞬时均衡度**（每 100 拍算一次 Jain 再平均）问的是："
                    "在任意一小段时间里，十个核是不是同时都在被服务。",
                    "**长期速率差**（整窗最快核带宽 ÷ 最慢核带宽）问的是："
                    "把整个争用窗平均下来，有没有哪个核被系统性地拖慢。"
                    "S0 = 1.5663 意味着最快的核长期比最慢的核快 57%。"])])),

    ("section", dict(
        no="07", kicker="SECTION SEVEN", title=["结论与建议"],
        lead="从现象、根因、理论上界到可实现机制，形成完整证据闭环。",
        key_label="BOTTOM LINE",
        key="问题已经收敛为一个架构决策：能改 HA 授权调度就验证 S16；"
            "事务层不能改，则验证只动环上仲裁的 S22。")),

    ("matrix", dict(
        chrome="结论", kicker="CONCLUSIONS", title="三项确定结论 + 一个架构决策",
        cells=[
            dict(t="① 容量问题已闭环", b=[
                "S0 最忙链路利用率 96.98%；相对 R* 的 3.03% 缺口已按拍拆成"
                "有效载荷、绕环重发和四类空转，账目无残项。",
                "[[结论：当前问题不是带宽没打满，而是满载时服务机会分配不均。]]"]),
            dict(t="② 公平性是结构问题", b=[
                "六快四慢随时间稳定存在；即使完全抹平瞬时抖动，Jain 上限仍只有 0.96458，"
                "核间长期速率比为 1.5673。",
                "[[结论：只整形发包时机不够，必须在瓶颈处重新分配服务机会。]]"]),
            dict(t="③ 上界与评价口径已建立", b=[
                "由逐链路占用约束求得 R(J) 理论边界与等速率上限 R* = 5.7143；"
                "再用 100 拍 Jain、长期 max/min、硬件 FF‑eq 和流量迁移统一评估。",
                "[[结论：方案优劣来自同一上界下的可量化差距，不依赖经验判断。]]"]),
            dict(t="④ 控制点决定方案", accent=True, b=[
                "S16 在 HA 授权点直接重排服务：900 FF‑eq，Jain 0.98466，"
                "max/min 1.0001，总带宽仅 −0.39%。",
                "若事务层不可改，S22 是只动环仲裁的备选；S19 / S20 仅作对照。",
                "[[架构决策只剩：HA 授权调度能不能改。]]"])],
        band="本研究完成了「现象复现 → 逐拍归因 → 理论上界 → 机制设计 → "
             "硬件代价 → 流量迁移」闭环，并把开放问题压缩为一项可决策的架构边界。")),

    ("process", dict(
        chrome="建议", kicker="RECOMMENDATION",
        title="只保留一主一备，明确研究边界",
        steps=[
            dict(t="主方案 · S16 写侧授权保留", accent=True, b=[
                "**目标**：在 HA / memory controller 内验证 DBIDResp 授权排队与最少服务优先。",
                "**价值**：当前所有可实现点里，S16 离理论上界最近、硬件代价最低，"
                "并在 uniform / hot 两类流量下都不显著损失带宽。",
                "**门槛**：确认 HA 授权时序、CHI 合规性和现有 tracker 接口可支持。"]),
            dict(t="备选 · S22 环上赤字让路", b=[
                "**适用条件**：HA 事务调度不可改，但允许调整 ring arbitration。",
                "**验证重点**：6 bit 进度总线复用、30 拍反馈稳定性、"
                "让路范围和现有注入队列深度下的收益。",
                "**定位**：性能次于 S16，但控制面完全留在环层。"]),
            dict(t="边界 · 明确不进入建议", b=[
                "**S19 / S20**：仅保留为 requester-side 研究对照；实测公平性几乎不变，"
                "不作为第三层落地路径。",
                "**读侧**：S0 已达到 Jain 0.99067、max/min 1.0035，维持现状。",
                "**outstanding**：由最坏 RTT 覆盖要求固定，不作为拥塞控制旋钮。"])],
        band="请架构组只决策一件事：是否允许 HA 改变授权的时机与对象。"
             "允许则进入 S16 微架构验证；不允许则转入 S22 环仲裁原型。")),

    ("closing", dict(
        title=["汇报完毕", "请架构组决策"],
        lead=["第一问仍是：HA / 内存控制器的授权调度能不能改？",
              "允许修改：进入 S16 微架构验证；不允许修改：进入 S22 环仲裁原型。"])),
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
