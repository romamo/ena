# Pitch deck

## The deck on the official template

[Ena-pitch.pptx](Ena-pitch.pptx) is the stage deck: 11 slides on the organisers' template, 8 shown (interviews video, cover, the four jobs, how it works, live demo, built during the hackathon, how Bank of Cyprus ships it, thank you) and 3 backups (Jinius, architecture, brand). Both videos autoplay; each slide's speaker notes come from [script.md](script.md), the full spoken script with timings and the expected questions.

- Rebuild it from `docs/pitch/`: `python pptx/build_deck.py` (needs `python-pptx`, `lxml`, `Pillow`, `ffmpeg` and `gh`). It reads the commit, PR and issue counts for slide 6 from git and GitHub; update `TESTS` in the script after a test run.
- [brand/](brand/): the Ena mark and logo (SVG and PNG), the Canva logo and preview images of the brand and cover slides.
- [video/](video/): the interviews video for slide 1, the Ena closer for slide 8 and two alternative interview cuts.
- [web/](web/): the same deck as a Claude Slides artifact (slide HTML, `deck.css`, `deck.json`) and its compressed assets.

## The first draft (HTML)

The five-slide deck for the 4 minute pitch, with speaker notes under each slide. Open [deck.html](deck.html) in a browser from a checkout: it reads its styles from [deck.css](deck.css) and the phone screens from [../mockups/](../mockups/README.md).

| Slide | What it says |
|---|---|
| 1. Hook | “Pay the flour supplier €1,240?” on the approval card: the only thing the owner did today |
| 2. Problem | No back office: invoices from a phone, books at night, cash found short too late; the 3.6% SME non-performing loan ratio |
| 3. How Ena works | The loop over Jinius, bocAPI and JCC, and the gate: the owner’s limit and a receipt for every action |
| 4. The one number | The owner’s hours a month before, against about 6 minutes with Ena; the live demo goes here |
| 5. Business model and next step | Who pays, what Bank of Cyprus gains, a pilot with BoC business customers |

Bracketed placeholders such as `[€__]` and `[__] h` wait for the interview numbers. Check the Central Bank of Cyprus figure on slide 2 before the pitch.
