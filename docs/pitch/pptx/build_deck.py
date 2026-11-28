"""Build the Ena pitch deck on the official BoC Fintech Hackathon 7.0 template.

Run from this folder:  python build_deck.py
Reads template.pptx (a copy of the organisers' template), the Ena mark and the app
screenshots from the team repo; writes ../Ena-pitch.pptx.
"""
import io
from pathlib import Path

from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

HERE = Path(__file__).parent       # docs/pitch/pptx
ROOT = HERE.parent                 # docs/pitch
SCREENS = HERE.parents[1] / "mockups/screens"
MARK = ROOT / "brand/ena-mark.png"

WHITE, MUTED = "FFFFFF", "C9D3E6"
CYAN, AMBER, TEAL = "89FFFE", "E8A33D", "0F6E6E"
CARD, CARD_LINE = "0A0E26", "2B3A7A"
DEEP, CREAM, MINT = "102A29", "F6F4EF", "8FD3C8"
BODY_FONT = "Arial"
REPO = HERE.parents[2]             # the team repo, romamo/ena
SCRIPT = HERE.parent / "script.md"  # the spoken script; each "## N ·" section becomes slide N's notes
TESTS = 433  # passing on 28 Nov 17:30 (uv run --with tzdata pytest); rerun and update before the pitch


def repo_stats():
    """Commits on main, merged PRs and closed issues, read live so a rebuild stays current."""
    import subprocess as sp
    sp.run(["git", "-C", str(REPO), "fetch", "-q", "origin"], check=False)
    commits = sp.run(["git", "-C", str(REPO), "rev-list", "--count", "origin/main"],
                     check=True, capture_output=True, text=True).stdout.strip()

    def gh_count(kind, state):
        out = sp.run(["gh", kind, "list", "-R", "romamo/ena", "--state", state, "--limit", "1000",
                      "--json", "number"], check=True, capture_output=True, text=True).stdout
        return len(__import__("json").loads(out))
    return {"commits": int(commits), "prs": gh_count("pr", "merged"), "issues": gh_count("issue", "closed")}


def script_sections():
    """{slide number: notes text} from script.md's '## N · title · time' sections."""
    import re
    sections, cur = {}, None
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"## (\d+) · (.*)", line)
        if m:
            cur = int(m.group(1))
            sections[cur] = [m.group(2)]
        elif line.startswith("# "):
            cur = None
        elif cur is not None and line.strip():
            sections[cur].append(re.sub(r"[*_>]", "", line).strip())
    return {n: "\n".join(v) for n, v in sections.items()}


def rgb(h):
    return RGBColor.from_string(h)


def drop(shape):
    shape._element.getparent().remove(shape._element)


def by_id(slide, shape_id):
    return next(s for s in slide.shapes if s.shape_id == shape_id)


def text(slide, x, y, w, h, runs, size=12, color=WHITE, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, font=BODY_FONT, italic=False, spacing=None):
    """Add a text box. `runs` is a string, or a list of paragraphs; a paragraph is a
    string or a list of (text, {overrides}) runs."""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    fill_text(box.text_frame, runs, size, color, bold, align, anchor, font, italic, spacing)
    return box


def fill_text(tf, runs, size=12, color=WHITE, bold=False, align=PP_ALIGN.LEFT,
              anchor=MSO_ANCHOR.TOP, font=BODY_FONT, italic=False, spacing=None):
    tf.clear()
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, side, 0)
    paras = [runs] if isinstance(runs, str) else runs
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = 1.05
        if spacing:
            p.space_after = Pt(spacing)
        parts = [(para, {})] if isinstance(para, str) else para
        for t, o in parts:
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = o.get("font", font)
            f.size = Pt(o.get("size", size))
            f.bold = o.get("bold", bold)
            f.italic = o.get("italic", italic)
            f.color.rgb = rgb(o.get("color", color))


def box(slide, x, y, w, h, fill=CARD, line=CARD_LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
        radius=0.08, dash=False):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill:
        s.fill.solid()
        s.fill.fore_color.rgb = rgb(fill)
    else:
        s.fill.background()
    if line:
        s.line.color.rgb = rgb(line)
        s.line.width = Pt(1)
        if dash:
            s.line.dash_style = 4  # dash
    else:
        s.line.fill.background()
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    s.shadow.inherit = False
    return s


def dot(slide, x, y, d, fill, label=None, size=11, color=WHITE):
    s = box(slide, x, y, d, d, fill=fill, line=None, shape=MSO_SHAPE.OVAL)
    if label:
        fill_text(s.text_frame, label, size, color, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    return s


def arrow(slide, x1, y1, x2, y2, color=CYAN, both=False):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(1.5)
    ln = c.line._get_or_add_ln()
    if both:  # a:headEnd must come before a:tailEnd
        etree.SubElement(ln, qn("a:headEnd")).set("type", "triangle")
    etree.SubElement(ln, qn("a:tailEnd")).set("type", "triangle")
    return c


def screenshot(slide, name, x, y, w, ratio):
    """Insert the top of an app screenshot cropped to width/height = ratio."""
    im = Image.open(SCREENS / name)
    crop = im.crop((0, 0, im.width, round(im.width / ratio)))
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    buf.seek(0)
    pic = slide.shapes.add_picture(buf, Inches(x), Inches(y), Inches(w), Inches(w / ratio))
    pic.line.color.rgb = rgb(CARD_LINE)
    pic.line.width = Pt(0.75)
    return pic


def logo(slide, x, y, d, word_size=None, word_color=WHITE):
    slide.shapes.add_picture(str(MARK), Inches(x), Inches(y), Inches(d), Inches(d))
    if word_size:
        text(slide, x + d + 0.18, y, 3, d, "ena", word_size, word_color, True,
             anchor=MSO_ANCHOR.MIDDLE)


def title(slide, shape, words, size=24):
    """Restyle a template title box to the deck's one title position."""
    shape.left, shape.top, shape.width, shape.height = Inches(0.48), Inches(0.2), Inches(7.0), Inches(0.6)
    tf = shape.text_frame
    # keep the template's own title font (Manrope) on the first run, change only words
    r0 = tf.paragraphs[0].runs[0]
    font_name = r0.font.name
    fill_text(tf, words, size, WHITE, True, anchor=MSO_ANCHOR.MIDDLE, font=font_name)


def notes(slide, words):
    slide.notes_slide.notes_text_frame.text = words


prs = Presentation(HERE / "template.pptx")
src = list(prs.slides)

# ---------- order: keep 11 template slides, drop the organisers' instruction slides
ORDER = [7, 9, 5, 17, 15, 13, 12, 18, 6, 10, 11]  # 1-based template slide numbers
id_lst = prs.slides._sldIdLst
entries = list(id_lst)
keep = [entries[n - 1] for n in ORDER]
for e in entries:
    id_lst.remove(e)
    if e not in keep:
        prs.part.drop_rel(e.get(qn("r:id")))
for e in keep:
    id_lst.append(e)
S = {n: src[n - 1] for n in ORDER}

# ---------- 1. Interviews video (embedded at the end, under Videos)
s = S[7]
drop(by_id(s, 113))
text(s, 0.48, 1.7, 2.75, 0.8, "We asked local business owners", 20, WHITE, True)
text(s, 0.48, 2.55, 2.75, 0.6, "How long do your books take?", 13, CYAN, True)
text(s, 0.48, 3.1, 2.75, 0.6, "Real interviews, filmed during the hackathon", 10, MUTED, italic=True)

# ---------- 2. Cover
s = S[9]
logo(s, 0.48, 0.95, 0.85, word_size=40)
ph = by_id(s, 127)
ph.left, ph.top, ph.width, ph.height = Inches(0.48), Inches(2.0), Inches(6.2), Inches(1.4)
fill_text(ph.text_frame, ["Ena is the", "back office"], 36, WHITE, True,
          anchor=MSO_ANCHOR.BOTTOM, font=ph.text_frame.paragraphs[0].runs[0].font.name or "Manrope")
text(s, 0.48, 3.8, 6.6, 0.75,
     "One AI agent for invoices, books, cash forecast and loans, on the owner's Bank of Cyprus "
     "account. Below the owner's limit it acts. Above it, it asks.", 13, MUTED)
text(s, 0.48, 4.85, 5.8, 0.3, "Fintech for SMEs  ·  bocAPI  ·  Jinius  ·  JCC", 11, CYAN, True)

# ---------- 3. Problem: Ena is the back office
s = S[5]
title(s, by_id(s, 99), "Ena is the back office")
text(s, 0.48, 0.95, 5.3, 0.55,
     "A bakery, a barber, a photo studio: no staff for the paperwork, so the owner does it "
     "between customers.", 13, MUTED)
pains = [("Invoices", "Paid one by one from a phone"),
         ("Books", "Done at night, or paid to an accountant"),
         ("Cash forecast", "None: cash is found short when a payment bounces"),
         ("Loans", "The instalment falls due in a lean month")]
for i, (k, v) in enumerate(pains):
    y = 1.55 + i * 0.63
    dot(s, 0.48, y, 0.42, TEAL, str(i + 1), 13)
    text(s, 1.08, y - 0.02, 4.6, 0.3, k, 15, WHITE, True)
    text(s, 1.08, y + 0.27, 4.6, 0.3, v, 12, MUTED)
text(s, 0.48, 4.2, 5.3, 0.4,
     [[("Our challenge: ", {"bold": True, "color": CYAN}),
       ("Fintech for SMEs, on bocAPI, Jinius and JCC", {"color": WHITE})]], 12)
box(s, 6.15, 1.05, 3.35, 3.5)
text(s, 6.4, 1.2, 2.9, 0.4, "What owners told us", 18, WHITE, True)
quotes = [("\"So it probably takes like two hours every day to do all the accounting stuff on my own.\"", "Shop owner"),
          ("\"There are three people who work on our bookkeeping.\"", "Business owner"),
          ("\"Maybe two or three hours, or four.\"", "Food business owner")]
for i, (q, who) in enumerate(quotes):
    y = 1.68 + i * 0.8
    text(s, 6.4, y, 2.9, 0.5, q, 10.5, WHITE, italic=True)
    text(s, 6.4, y + 0.52, 2.9, 0.2, who, 9, CYAN, True)
text(s, 6.4, 4.1, 2.9, 0.3, "Self-reported by the owners we interviewed", 9, MUTED)

# ---------- 4. How it works
s = S[17]
for sid in (249, 250, 251, 253, 256):  # the template's phone frame, picture, text and store badges
    drop(by_id(s, sid))
title(s, by_id(s, 252), "How it works")
# The owner, left: a simple person, as in the flow the team liked
dot(s, 0.66, 1.68, 0.34, WHITE)
box(s, 0.56, 2.06, 0.54, 0.62, fill=WHITE, line=None, radius=0.35)
text(s, 0.33, 2.76, 1.0, 0.25, "Owner", 11, WHITE, True, PP_ALIGN.CENTER)
text(s, 0.23, 3.0, 1.2, 0.4, "sets the limit, approves above it", 8.5, MUTED, align=PP_ALIGN.CENTER)
arrow(s, 1.18, 2.22, 1.52, 2.22, both=True)
# The four features in one row, then the outcome
FX, FW, FY, FH = [1.55, 3.18, 4.81, 6.44], 1.35, 1.75, 0.95
feats = [("Invoices", "Pays within the limit"), ("Books", "Matches every line"),
         ("Cash forecast", "8 weeks ahead"), ("Loans", "Reserve, or a loan offer")]
for x, (k, v) in zip(FX, feats):
    box(s, x, FY, FW, FH, line=CYAN)
    text(s, x, FY + 0.2, FW, 0.3, k, 13, WHITE, True, PP_ALIGN.CENTER)
    text(s, x, FY + 0.52, FW, 0.3, v, 9.5, MUTED, align=PP_ALIGN.CENTER)
for a, b in zip(FX, FX[1:]):
    arrow(s, a + FW + 0.03, FY + FH / 2, b - 0.03, FY + FH / 2)
arrow(s, FX[-1] + FW + 0.03, FY + FH / 2, 8.05, FY + FH / 2)
box(s, 8.07, FY, 1.43, FH, fill="13A89E", line=None)
text(s, 8.07, FY + 0.17, 1.43, 0.45, ["Paid, booked,", "covered"], 11.5, WHITE, True, PP_ALIGN.CENTER)
text(s, 8.07, FY + 0.62, 1.43, 0.25, "a receipt for each", 9, WHITE, align=PP_ALIGN.CENTER)
# Partners in from above, the bank's API below
for x, name in [(FX[0], "Jinius"), (FX[1], "JCC")]:
    c = x + FW / 2
    box(s, c - 0.5, 1.0, 1.0, 0.36, fill=None, line=CYAN, radius=0.5)
    text(s, c - 0.5, 1.0, 1.0, 0.36, name, 10.5, CYAN, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    arrow(s, c, 1.38, c, FY - 0.03)
box(s, FX[0], 3.12, FX[-1] + FW - FX[0], 0.48, fill="0E1A3A", line=CYAN)
text(s, FX[0], 3.12, FX[-1] + FW - FX[0], 0.48,
     [[("BoC's API (bocAPI)", {"bold": True}), ("   balance, statement, payments", {"color": MUTED})]],
     11, WHITE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
for x in FX[:3]:
    c = x + FW / 2
    arrow(s, c - 0.08, FY + FH + 0.03, c - 0.08, 3.1)
    arrow(s, c + 0.08, 3.1, c + 0.08, FY + FH + 0.03)
text(s, FX[3], FY + FH + 0.1, FW, 0.3, "simulated: no lending API", 8.5, AMBER, align=PP_ALIGN.CENTER)
# The gate, one line
box(s, 0.48, 3.92, 9.02, 0.62, fill="1E1A10", line=AMBER)
text(s, 0.68, 3.92, 8.7, 0.62,
     [[("The gate  ", {"bold": True, "color": AMBER}),
       ("Below the owner's limit Ena acts on its own. Above it, Ena asks. Every action leaves a receipt. "
        "Code decides every amount; the AI explains.", {})]], 11, WHITE, anchor=MSO_ANCHOR.MIDDLE)

# ---------- 5. Live demo
s = S[15]
t = by_id(s, 230)
t.left, t.top, t.width, t.height = Inches(0.48), Inches(0.95), Inches(4.6), Inches(0.7)
fill_text(t.text_frame, "Live demo", 36, WHITE, True, font=t.text_frame.paragraphs[0].runs[0].font.name or "Manrope")
text(s, 0.48, 1.65, 4.6, 0.3, "A synthetic bakery's November, on the real app", 13, MUTED)
path = [("\"Pay the flour invoice\"", "€1,240 is above the €500 limit, so Ena asks"),
        ("Approve", "The receipt appears (simulated: no money moves)"),
        ("\"Will I be short of cash this month?\"", "The week of 16 November is lean"),
        ("\"Can I get an advance for the Georgiou wedding?\"", "A simulated offer, repaid from the event's final payment")]
for i, (k, v) in enumerate(path):
    y = 2.15 + i * 0.66
    dot(s, 0.48, y + 0.02, 0.32, TEAL, str(i + 1), 11)
    text(s, 0.95, y, 4.2, 0.25, k, 12, WHITE, True)
    text(s, 0.95, y + 0.25, 4.2, 0.35, v, 10.5, MUTED)
for i, name in enumerate(["1-chat-approval.png", "2-chat-receipt.png", "3-chat-forecast.png"]):
    screenshot(s, name, 5.45 + i * 1.4, 1.05, 1.25, 0.462)

# ---------- 6. Progress made during the hackathon
s = S[13]
title(s, by_id(s, 179), "Built during the hackathon")
stats = repo_stats()
for i, (num, label) in enumerate([(stats["commits"], "commits"), (stats["prs"], "pull requests merged"),
                                  (stats["issues"], "issues closed"), (TESTS, "tests passing"),
                                  (3, "sandboxes connected")]):
    x = 0.48 + i * 1.855
    box(s, x, 0.95, 1.6, 0.78)
    text(s, x, 1.0, 1.6, 0.42, str(num), 24, CYAN, True, PP_ALIGN.CENTER)
    text(s, x, 1.43, 1.6, 0.25, label, 9.5, MUTED, align=PP_ALIGN.CENTER)
sub = by_id(s, 178)
sub.left, sub.top, sub.width, sub.height = Inches(0.48), Inches(1.84), Inches(9.0), Inches(0.3)
fill_text(sub.text_frame, [
    [("Live on the sandboxes: ", {"bold": True, "color": WHITE}),
     ("a bocAPI payment, Jinius invoices, JCC card callbacks.   ", {}),
     ("Simulated, and labelled so: ", {"bold": True, "color": AMBER}),
     ("the loan offer and the demo's payments.", {})]], 9.5, MUTED)
cards = [  # card shape, icon, label, date, body
    (190, 194, 192, 193, 191, "Plan and connect", "Fri 27 Nov",
     "Owner interviews guide, architecture and data model. The bocAPI sandbox connector."),
    (180, 184, 182, 183, 181, "Build the loop", "Sat 28 Nov",
     "The gate and receipts. Books, forecast, reserve, loan and advance rules. Jinius and JCC "
     "clients. The agent, chat app and dashboard."),
    (185, 189, 187, 188, 186, "Ship", "Sun 29 Nov",
     "The two-laptop live demo with a backup video, rehearsal, and the submission to the "
     "hackathon repo."),
]
for card_id, icon_id, lab_id, date_id, body_id, lab, date, body in cards:
    c = by_id(s, card_id)
    cx, cw = c.left / 914400, c.width / 914400
    c.top, c.height = Inches(2.25), Inches(2.55)
    ic = by_id(s, icon_id)
    ic.left, ic.top = Inches(cx + 0.18), Inches(2.38)
    for sid, y, h, words, size, color, bold in [(lab_id, 2.9, 0.25, lab, 12, WHITE, True),
                                                (date_id, 3.13, 0.22, date, 9, CYAN, True),
                                                (body_id, 3.4, 1.3, body, 9.5, MUTED, False)]:
        b = by_id(s, sid)
        b.left, b.top, b.width, b.height = Inches(cx + 0.18), Inches(y), Inches(cw - 0.32), Inches(h)
        fill_text(b.text_frame, words, size, color, bold)

# ---------- 7. How Bank of Cyprus ships it
s = S[12]
title(s, by_id(s, 156), "How Bank of Cyprus ships it")
drop(by_id(s, 155))
model = [("Who it's for", "Micro-businesses and the self-employed who bank with BoC and have no back-office staff."),
         ("Who pays", "A monthly subscription below the back office it replaces, or BoC bundles it into a premium business account."),
         ("What BoC gains", "Instalments paid on time, Digital Business Loans sold the week they're needed, payments that stay at BoC.")]
for i, (k, v) in enumerate(model):
    x = 0.48 + i * 3.05
    box(s, x, 0.95, 2.85, 1.45)
    text(s, x + 0.2, 1.08, 2.45, 0.25, k, 12, CYAN, True)
    text(s, x + 0.2, 1.36, 2.45, 0.95, v, 10.5, WHITE)
ship = [(162, 161, 170, "1  Pilot",
         "Three months, a handful of business customers in one vertical. Measure owner hours and missed instalments."),
        (160, 159, 169, "2  Payment mandate",
         "Production bocAPI with a standing payment consent, capped at the owner's limit."),
        (158, 157, 168, "3  Lending API",
         "The pre-filled Digital Business Loan goes from simulated to a real application."),
        (164, 163, 171, "4  Inside 1bank",
         "Ena as a feature of the business account, with Jinius and JCC merchants as channels.")]
for lab_id, body_id, icon_id, lab, body in ship:
    ic = by_id(s, icon_id)
    c = (ic.left + ic.width / 2) / 914400
    for sid, y, h, words, size, color, bold in [(lab_id, 3.3, 0.25, lab, 11.5, WHITE, True),
                                                (body_id, 3.58, 1.15, body, 9.5, MUTED, False)]:
        b = by_id(s, sid)
        b.left, b.top, b.width, b.height = Inches(c - 0.98), Inches(y), Inches(1.96), Inches(h)
        fill_text(b.text_frame, words, size, color, bold, PP_ALIGN.CENTER)

# ---------- 8. Thank you
s = S[18]
logo(s, 0.53, 1.3, 0.95, word_size=40)
t = by_id(s, 265)
t.left, t.top, t.width, t.height = Inches(0.53), Inches(2.45), Inches(4.2), Inches(0.65)
fill_text(t.text_frame, "Thank you", 36, WHITE, True, font=t.text_frame.paragraphs[0].runs[0].font.name or "Manrope")
text(s, 0.53, 3.15, 4.2, 0.35, "Ena is the back office", 15, MUTED)
text(s, 0.53, 3.6, 4.2, 0.3, "Code, sample data and deck: github.com/romamo/ena", 11, CYAN, True)

# ---------- 11. Appendix: the brand
s = S[11]
for sid in (144, 146, 147, 148):
    drop(by_id(s, sid))
t = by_id(s, 145)
t.left, t.top, t.width, t.height = Inches(0.69), Inches(0.45), Inches(3.8), Inches(0.3)
fill_text(t.text_frame, "APPENDIX  ·  THE BRAND", 10, TEAL, True)
logo(s, 0.69, 1.0, 1.55)
text(s, 0.69, 2.7, 3.8, 0.8, "ena", 48, DEEP, True)
text(s, 0.69, 3.5, 3.8, 0.35, [[("ένα", {"bold": True, "color": TEAL}), ("  ·  Greek for \"one\"", {})]], 14, "4A5654")
text(s, 5.2, 0.95, 4.3, 0.7, "One agent. One account. A receipt for every action.", 16, WHITE, True)
box(s, 5.2, 1.88, 0.42, 0.13, fill=TEAL, line=None, radius=0.5)
text(s, 5.8, 1.75, 3.7, 0.25, "One stroke", 12, WHITE, True)
text(s, 5.8, 2.0, 3.7, 0.5, "The \"e\" is one line that closes into a loop: invoices, books, cash forecast, loans.", 10.5, MUTED)
dot(s, 5.29, 2.62, 0.24, AMBER)
text(s, 5.8, 2.55, 3.7, 0.25, "One dot", 12, WHITE, True)
text(s, 5.8, 2.8, 3.7, 0.5, "The owner: the moment Ena stops and asks, above the limit she set.", 10.5, MUTED)
for i, (name, hexv) in enumerate([("Teal", TEAL), ("Deep", DEEP), ("Cream", CREAM), ("Mint", MINT), ("Amber", AMBER)]):
    x = 5.2 + i * 0.82
    box(s, x, 3.5, 0.66, 0.5, fill=hexv, line="3A3A3A", radius=0.15)
    text(s, x, 4.06, 0.8, 0.4, [[(name, {"bold": True, "color": WHITE})], "#" + hexv], 8, MUTED)

# ---------- 9. Appendix: how Ena uses Jinius
s = S[6]
title(s, next(sh for sh in s.shapes if sh.has_text_frame), "Appendix  ·  How Ena uses Jinius")
flow = [("Supplier", "Ena Flour Mill Ltd sends the invoice on Jinius", CYAN),
        ("Read", "GET /v3/invoices/payable", CYAN),
        ("Decide", "The gate: pay, or ask the owner", AMBER),
        ("Pay", "From the BoC account, through bocAPI", CYAN),
        ("Report back", "POST approve, then markAsPaid with the bank's reference", CYAN)]
for i, (k, v, col) in enumerate(flow):
    x = 0.48 + i * 1.855
    box(s, x, 1.0, 1.6, 1.15, line=col)
    text(s, x + 0.12, 1.1, 1.36, 0.28, k, 12, col, True)
    text(s, x + 0.12, 1.42, 1.36, 0.7, v, 9.5, WHITE)
    if i:
        arrow(s, x - 0.235, 1.575, x - 0.02, 1.575)
facts = [("Live", CYAN, "Two companies on the Jinius integration environment: the bakery as buyer, and a supplier "
          "we registered, approved by Jinius. Its €180 and €1,240 invoices and a €119 credit note, read through the API."),
         ("Built and tested", WHITE, "Approve and markAsPaid, to the Jinius API 3.0 specification. When Jinius is "
          "offline, Ena reads its saved responses."),
         ("Next", AMBER, "Jinius callbacks instead of polling, and paying through Jinius's own Pay Others with 1bank.")]
for i, (tag, col, words) in enumerate(facts):
    y = 2.45 + i * 0.7
    box(s, 0.48, y, 1.45, 0.34, fill=None, line=col, radius=0.5)
    text(s, 0.48, y, 1.45, 0.34, tag, 10, col, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    text(s, 2.15, y - 0.02, 7.3, 0.6, words, 10.5, WHITE)

# ---------- 10. Appendix: how it's built
s = S[10]
t = by_id(s, 136)
title(s, t, "Appendix  ·  How it's built")
for i, (k, v) in enumerate([("Jinius", "Invoices in, paid status out"), ("JCC", "Card sales, callback"),
                            ("Owner", "Chat and dashboard (React)")]):
    y = 1.1 + i * 1.05
    box(s, 0.48, y, 2.2, 0.75)
    text(s, 0.65, y + 0.1, 1.9, 0.25, k, 12, CYAN, True)
    text(s, 0.65, y + 0.37, 1.9, 0.3, v, 10, WHITE)
    arrow(s, 2.72, y + 0.375, 3.12, y + 0.375)
box(s, 3.15, 0.95, 3.6, 3.15, fill="0E1A3A", line=CYAN)
text(s, 3.35, 1.05, 3.2, 0.3, "Ena server  ·  Python, FastAPI", 12, WHITE, True)
for i, (k, v) in enumerate([("The gate", "Limit check, receipts in SQLite"),
                            ("Rules", "Books, forecast, reserve, loan, advance"),
                            ("Claude agent", "MCP tools: explains and asks")]):
    y = 1.5 + i * 0.83
    box(s, 3.35, y, 3.2, 0.68, fill=CARD)
    text(s, 3.5, y + 0.08, 2.9, 0.25, k, 11, AMBER if k == "The gate" else CYAN, True)
    text(s, 3.5, y + 0.33, 2.9, 0.3, v, 10, WHITE)
arrow(s, 6.78, 1.85, 7.18, 1.85)
arrow(s, 6.78, 3.0, 7.18, 3.0)
box(s, 7.2, 1.45, 2.3, 0.8)
text(s, 7.35, 1.55, 2.0, 0.25, "bocAPI", 12, CYAN, True)
text(s, 7.35, 1.8, 2.0, 0.4, "Accounts, statement, payments", 10, WHITE)
box(s, 7.2, 2.6, 2.3, 0.8, fill=None, line=AMBER, dash=True)
text(s, 7.35, 2.7, 2.0, 0.25, "Digital Business Loan", 11, AMBER, True)
text(s, 7.35, 2.95, 2.0, 0.4, "Simulated: no lending API", 10, WHITE)
text(s, 0.48, 4.3, 6.3, 0.4,
     "Code decides every amount; the model only restates numbers the code gives it.", 11, CYAN, True)

# ---------- Videos: embedded here, set to start on their own when the slide appears
import json
import subprocess
import sys

VIDEOS = ROOT / "video"
AUTOPLAY = """<p:timing xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:tnLst><p:par>
<p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>
<p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>
<p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/><p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst><p:childTnLst>
<p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>
<p:par><p:cTn id="5" presetID="1" presetClass="mediacall" presetSubtype="0" fill="hold" nodeType="afterEffect"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>
<p:cmd type="call" cmd="playFrom(0.0)"><p:cBhvr><p:cTn id="6" dur="{dur}" fill="hold"/><p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cBhvr></p:cmd>
</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>
</p:childTnLst></p:cTn>
<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>
<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>
<p:video><p:cMediaNode vol="80000"><p:cTn id="7" fill="hold" display="0"><p:stCondLst><p:cond delay="indefinite"/></p:stCondLst></p:cTn><p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cMediaNode></p:video>
</p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>"""


def video(slide, name, x, y, h, poster_at):
    """Embed a vertical (1080x1920) video that plays on its own when the slide appears."""
    path = VIDEOS / name
    poster = HERE / f"poster-{path.stem}.png"
    if not poster.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(poster_at), "-i", str(path),
                        "-frames:v", "1", str(poster)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                            str(path)], check=True, capture_output=True, text=True)
    dur = round(float(json.loads(probe.stdout)["format"]["duration"]) * 1000)
    w = h * 1080 / 1920
    pic = slide.shapes.add_movie(str(path), Inches(x), Inches(y), Inches(w), Inches(h),
                                 poster_frame_image=str(poster), mime_type="video/mp4")
    pic.name = path.stem
    sld = slide._element
    for old in sld.findall(qn("p:timing")):
        sld.remove(old)
    timing = etree.fromstring(AUTOPLAY.replace("\n", "").format(dur=dur, spid=pic.shape_id))
    ext = sld.find(qn("p:extLst"))
    if ext is not None:
        ext.addprevious(timing)
    else:
        sld.append(timing)


video(S[7], "interviews-main-27s.mp4", (10 - 5.625 * 1080 / 1920) / 2, 0, 5.625, 0.5)
video(S[18], "ena-closer-4s.mp4", 4.95, 0.3, 4.55, 0.2)

# Speaker notes for the main slides come from script.md
ordered = list(prs.slides)
for n, words in script_sections().items():
    notes(ordered[n - 1], words)

out = HERE.parent / (sys.argv[1] if len(sys.argv) > 1 else "Ena-pitch.pptx")
prs.save(out)
print("saved", out)
