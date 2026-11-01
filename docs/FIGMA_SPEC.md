# CloudShop — Figma-ready design spec

A build sheet for recreating CloudShop in Figma. It lists the design tokens, the component
inventory, and the three screen layouts, all taken from the shipping app (`static/styles.css`,
`static/index.html`), so the Figma file and the running product stay in sync.

**Two ways to get it into Figma:**
1. **Fast import** — the interactive prototype is published as an HTML page. In Figma, install the
   free **html.to.design** plugin → *Import from URL* → paste the prototype link. It rebuilds the
   screens as editable frames, text, and shapes. Then replace raw hex fills with the Variables below
   so the file is themeable.
2. **Build by hand** — set up the Variables and Text styles below first, then lay out the three
   screens from the layout specs. Slower, but you get a clean, fully tokenised file.

Either way, create the **Variables and Text styles first** — everything else references them.

---

## 1. Design tokens (→ Figma Variables)

Create a variable collection **`cloudshop`** with two modes: **Light** and **Dark**. Add these
color variables; the app themes entirely through them.

| Variable | Light | Dark | Role |
|---|---|---|---|
| `bg` | `#F7F5F2` | `#161514` | Page background (warm cream / near-black) |
| `panel` | `#FFFFFF` | `#201F1D` | Cards, bars, inputs |
| `panel-2` | `#F1EDE7` | `#2A2825` | Recessed fills, chat bubbles, code |
| `ink` | `#1F1D1A` | `#EEEAE4` | Primary text |
| `muted` | `#6F6A63` | `#A39D94` | Secondary text, captions |
| `line` | `#E6E1DA` | `#34312D` | Borders, dividers |
| `accent` | `#2F5D50` | `#7FBFA8` | Pine green — brand, primary buttons, links |
| `accent-ink` | `#FFFFFF` | `#10231C` | Text on accent |
| `accent-soft` | `#E3EEE9` | `#23352E` | Active nav, summary panel, chips |

**Semantic colours** (verdict badges & checks — separate from the accent):

| Variable | Light fg / bg | Dark fg / bg | Meaning |
|---|---|---|---|
| `pass` | `#2E7D4F` / `#E5F3EA` | `#7FCF9C` / `#1D3025` | Verified, in stock, settled |
| `warn` | `#9A6400` / `#FDF1DC` | `#E8B454` / `#3A2E17` | Check a colourway, on sale, caution |
| `fail` | `#B3261E` / `#FBE6E4` | `#F08A82` / `#3B1F1D` | Failed a hard check, declined |

**Provenance colours** (Idea 1 — where a brief field came from):
`stated #2F5D50` · `inferred #B7791F` (dark `#E8B454`) · `edited #4A5FC1` (dark `#9FB0F0`) ·
`remembered #7A4FB0` (dark `#C4A0EA`).

**Scalar tokens:** radius `12px` (cards/panels), `8px` (buttons/inputs), `999px` (pills/chips);
shadow `0 1px 3px rgba(0,0,0,.06), 0 10px 30px rgba(31,29,26,.06)` (none in dark).
Spacing scale: `4 · 6 · 8 · 10 · 12 · 16 · 22 · 28` px.

---

## 2. Typography (→ Figma Text styles)

Two families. **Fraunces** (Google Fonts) for display/brand — a characterful modern serif that
matches the app's Georgia wordmark; **system UI** (`system-ui / -apple-system / Segoe UI`) for all
interface text; a mono stack for codes and IDs.

| Text style | Family / weight | Size / line | Used for |
|---|---|---|---|
| `Display/Hero` | Fraunces 600 | 33 / 36 | Screen H1 ("Picks for your client meeting") |
| `Display/Brand` | Fraunces 600 | 22 | Wordmark "CloudShop" |
| `Heading/Section` | system 600 | 15–16 / 22 | "Your request", gate questions |
| `Body` | system 400 | 15 / 22 | Lede, chat, descriptions |
| `Body/Strong` | system 600 | 14 | Card titles, totals |
| `Label/Caps` | system 600 | 11–12, +0.05em, uppercase | Field labels, brand eyebrow (`cbrand`) |
| `Caption` | system 400 | 12–13 | Muted helper text, ratings |
| `Mono` | mono 400 | 12–19 | OTP code, order/capture IDs, audit `code` |

Keep running text ≈ 60–65 characters wide. Headings use *balance* wrapping.

---

## 3. Component inventory

Build these as Figma components with variants; props in **bold**.

- **Button** — variants **primary** (accent fill, accent-ink), **ghost** (transparent, line border),
  **danger** (fail text + border). Radius 8 (14 on hero/composer). Padding 8×14.
- **Pill / Chip** — radius 999. Chip variants **default** / **selected** (accent fill). Pill is a
  status tag (e.g. `mock · offline`) in accent-soft.
- **Verdict badge** — variants **pass / warn / fail**, each `{semantic}-bg` fill + `{semantic}` text,
  radius 3, uppercase 10px. Sits top-right on a product tile.
- **Swatch dot** — 14px circle, 1px `#0002` ring + 1.5px white halo; row sits bottom-left of a tile.
  Catalogue filter swatch is 26px with an **on** variant (accent ring + ✓).
- **Product card** — tile (3:4, `accent/colour @ ~13% ` fill, 60px emoji, badge, swatches) +
  body (eyebrow brand, title, rating with gold ★, price, why-line, expandable checklist, colour
  select + Add button). Hover: lift 3px + tile shadow.
- **Check row** — `st` status tag (pass/warn/fail) + evidence text. Lives in the card's "Why this
  pick" disclosure.
- **Brief field** — label (caps) + provenance square + value; **inferred** variant gets an amber
  border. Grid of `minmax(210px, 1fr)`.
- **Clarification** — panel with left warn border (4px), question + gap tag (capability / instruction
  / intentionality) + chip row.
- **Secure-gate stepper** — 5 pills `1 Cart → 2 Card → 3 Review → 4 2FA → 5 Pay`, variants
  **done** (pass-bg) / **now** (accent) / **upcoming** (line). Arrows between.
- **Gate card** — accent 2px border panel: stepper + 🔒 question + reliance caption (panel-2) +
  gate body + right-aligned actions.
- **Chat bubble** — variants **user** (accent fill, right, tight bottom-right corner) and
  **assistant** (panel-2, left). Max width 78%.
- **Receipt** — pass-bg panel, ✅ + order id + settled capture id (mono).
- **Top bar** — brand + segmented screen switcher (shop / checkout / catalogue) + pill + theme
  toggle + New chat.
- **Sidebar step** — dot (states: idle / working-pulse / done) + bold title + small subtitle;
  **active** variant in accent-soft.

---

## 4. Screen layouts (desktop 1120–1280px; stack < 860px)

### A. Shop (chat + recommendations)
`Top bar` / two columns: **230px sidebar** (4 numbered steps · "Shopping from" box · bag mini) and a
**fluid main** (hero H1 + lede → accent-soft summary strip → clarification → "Your request" brief
grid with the provenance legend → toolbar [show-failed toggle · refine input] → product grid
`minmax(225px, 1fr)`, 28/22 gap). A **sticky chat dock** spans the bottom: scrollable log of
user/assistant bubbles + input + Send. The example transcript uses the real flow: *"meeting with a
client… don't wanna wear black and white"* → navy/sage picks, no black or white.

### B. Secure checkout (5 gates)
Single **760px centred column**. H1 + one-line intro, then the **gate card** which advances through
the states: **① Cart** (review items, approve) → **② Card** (pick Visa/Mastercard) → **③ Review**
(cart + card together + cap-override checkbox) → **④ 2FA** (OTP input, sandbox code shown) →
**⑤ Pay** (hold placed → capture). Ends in a **receipt** + an open **audit trail** listing every
human and agent step. Footer line maps the gates to the Week-2 reliance principles.

### C. Catalogue (data / admin)
Two columns: **250px filter rail** (search · department radios · category checkboxes · colour swatch
grid [multi] · price range + live value · rating chips · show-only toggles) and a **product grid**
`minmax(180px, 1fr)` with a result-count bar ("1,201 products · 20 categories · 12 retailers").
Cards reuse the tile/emoji/swatch component; a `sale` warn badge appears on reduced items.

---

## 5. Notes for the Figma file
- Make the three screens three top-level **frames** (or pages). Keep the top bar as one shared
  component instance across all three.
- The prototype is themeable: duplicate each frame and switch the variable collection to **Dark**
  mode to produce the dark-theme boards with zero extra work.
- Product imagery is intentionally **category emoji on a tinted tile**, not photos — the app is a
  licence-free prototype; swap in licensed photography for a real deployment.
- Everything here is live in the published prototype — open it beside Figma while you build, or
  import it via html.to.design and restyle against these tokens.
