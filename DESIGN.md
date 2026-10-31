---
name: BloodLink
description: Real-time blood supply coordination between hospitals and blood banks — warm, bold, and grounded in physical blood-bag conventions.
colors:
  oxblood: "#8C1B3A"
  oxblood-hover: "#A8264C"
  deep-clinical-navy: "#1E3A5F"
  navy-hover: "#2C4F7C"
  warm-paper: "#FAF9F7"
  surface-white: "#FFFFFF"
  ink: "#1C1917"
  border-charcoal: "#58514B"
  status-safe: "#0F766E"
  status-watch: "#B8860B"
  status-critical: "#BD4024"
  info-blue: "#1D4ED8"
  synthetic-violet: "#7C3AED"
typography:
  display:
    fontFamily: "Archivo, 'IBM Plex Sans', system-ui, sans-serif"
    fontSize: "clamp(1.5rem, 2.5vw, 1.875rem)"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body:
    fontFamily: "'IBM Plex Sans', system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 700
    lineHeight: 1.5
  label:
    fontFamily: "'JetBrains Mono', 'Courier New', monospace"
    fontSize: "0.8125rem"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "0.03em"
  login-headline:
    fontFamily: "'IBM Plex Serif', Georgia, 'Times New Roman', serif"
    fontSize: "clamp(2.5rem, 3.6vw, 3.75rem)"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.01em"
rounded:
  sm: "0.125rem"
  md: "0.375rem"
  lg: "0.5rem"
  xl: "1.25rem"
components:
  button-primary:
    backgroundColor: "{colors.oxblood}"
    textColor: "#FFFFFF"
    rounded: "{rounded.md}"
    padding: "0 1.5rem"
    height: "2.5rem"
  button-primary-hover:
    backgroundColor: "{colors.oxblood-hover}"
  input-field:
    backgroundColor: "{colors.surface-white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "2.5rem"
    padding: "0 0.75rem"
  card:
    backgroundColor: "{colors.surface-white}"
    rounded: "{rounded.xl}"
    padding: "1.5rem"
  badge-blood-type:
    backgroundColor: "{colors.surface-white}"
    textColor: "{colors.oxblood}"
    rounded: "{rounded.md}"
    height: "1.75rem"
    padding: "0 0.375rem"
---

# Design System: BloodLink

## Overview

**Creative North Star: "The Blood Bag Label"**

BloodLink's visual system takes its cues from the physical object at the center of the product: the printed label on a real blood bag. A donor identification number sets in mono type, as it does on the bag. Blood-type badges (O-, A+, AB-) are treated as the product's signature element — one shared, unmistakable shape used identically from a table row to a search-picker button, exactly the way a real bag's type label never changes shape depending on where you're standing.

That material honesty extends outward into warmth rather than sterility. The palette is a deliberate rejection of cold clinical white-and-blue hospital-software convention: warm stone neutrals instead of cool gray, an oxblood (not alert-red) brand identity for Blood Bank accounts and a deep, muted navy for Hospital accounts, and a status language whose safe/watch/critical states use teal, gold, and red-orange specifically so the system never leans on a red-green distinction. Typography carries the same conviction — body text sits at a 700 weight floor, the boldest static weight IBM Plex Sans ships, so nothing in the interface reads as tentative. Cards get genuine, layered depth (a tight contact shadow, a mid-distance layer, a wide ambient glow) so surfaces feel like they're actually resting on the page, not drawn on with a flat line.

Motion is restrained and one-shot: something happens, the interface responds once, and settles. Nothing loops or decorates — in a tool whose job is coordinating an actual blood shortage, motion is feedback, never atmosphere for its own sake.

**Key Characteristics:**
- Physical-object material honesty: mono DIN codes, one unchanging blood-type badge shape everywhere
- Warm, not clinical: stone-gray neutrals and an oxblood/navy brand pair instead of cool gray-and-blue
- Bold by floor, not by exception: body text is set at 700 weight system-wide
- Genuinely layered elevation: cards carry real, structural depth, not a flat outline
- Colorblind-safe status language: teal/gold/red-orange, never red/green
- Motion as confirmation, not decoration: short, one-shot, never looping

## Colors

Warm, saturated, and decisive — nothing in the palette is a pale wash standing in for a real color; every tint is still visibly present, not a near-white suggestion of one.

### Primary
- **Oxblood** (#8C1B3A): The site's default brand identity — Blood Bank accounts, the logged-out login screen, and Admin. A deep, muted brownish-red, deliberately closer to dried blood than an alert-red. Used for primary buttons, the active nav item, links, and the blood-drop mark.
- **Oxblood Hover** (#A8264C): The lifted state for anything in Oxblood — buttons, selected pickers.

### Role Variant
- **Deep Clinical Navy** (#1E3A5F): Replaces Oxblood as the brand identity color the instant a logged-in account's facility type is Hospital (`[data-role="hospital"]` on `<html>`, set once at login). Muted and desaturated rather than a bright corporate blue — steady and institutional without going cold. Every place Oxblood appears, Navy appears instead for a Hospital account; the two never mix on one screen.
- **Navy Hover** (#2C4F7C): The lifted state for Navy, paired the same way Oxblood Hover pairs with Oxblood.

### Secondary
- **Info Blue** (#1D4ED8): "Here's a related option," never a status. Used for blood-type compatibility suggestions and reason-for-request tags — deliberately kept out of the safe/watch/critical system so blue is never mistaken for "OK."

### Tertiary
- **Synthetic Violet** (#7C3AED): "This is a stand-in, not real data." Reserved entirely for the synthetic reference model: the "Synthetic, not real data" note and its forecast line in Forecast details, and the one-line notice under the Dashboard's outlook table. The per-row tag that names where an outlook comes from ("Not from your records yet", "From its own history", "Simple trend line") is a plain neutral pill, not violet. Violet specifically because it reads as neither a status color nor Info's blue.

### Neutral
- **Warm Paper** (#FAF9F7): Page background.
- **Surface White** (#FFFFFF): Card, modal, and input background.
- **Ink** (#1C1917): Primary text — used almost everywhere, including as "muted" text, since this system doesn't dim secondary copy.
- **Border Charcoal** (#58514B): Every structural border — cards, table rows, tabs, inputs. Dark enough to read as real structure at a glance, not a hairline you have to look for.
- A nine-step warm "stone" gray scale (`#FAFAF9` → `#1C1917`) replaces Tailwind's default cool gray everywhere, so no neutral in the app reads as a mismatched off-the-shelf gray next to Oxblood or Navy.

### Status & Semantic
Reused identically everywhere status appears — expiry, stock thresholds, availability, forecast alerts, facility/account status — and, critically, identical on every account type. Role identity (Oxblood/Navy) never leaks into status color, so a Hospital and a Blood Bank account looking at the same shortage always see the same red.
- **Safe** (#0F766E, teal): Adequate stock, confirmed, live/active states.
- **Watch** (#B8860B, gold): "Below minimum" stock (under the minimum, not yet critical), pending confirmation, dev-mode indicator.
- **Critical** (#BD4024, red-orange): "Critical" stock (under 60% of the minimum), errors, deactivated state. Its hue sits ~12° from Oxblood's own ~344° specifically so a solid brand button next to a critical badge never blurs into one red blob.

### Named Rules
**The Four-Step Rule.** Every accent and status color (Primary, Safe, Watch, Critical, Info, Synthetic) ships as a family of four: a base tone for solid fills and icons, a *-hover* or *-text* tone for buttons/text-on-tint, a pale *-tint* for panel backgrounds, and a *-border* blended 66% of the way from the tint toward the base tone — the ratio that reliably clears ~3.2:1+ contrast without hand-tuning each one. Never introduce a fifth ad-hoc shade of an existing color family; derive it by the same blend instead.

**The No Red-Green Rule.** Status color never relies on a red/green distinction. Safe is teal, not green; Critical is red-orange, not pure red — chosen so the safe/watch/critical triad stays legible for red-green colorblindness (~8% of men), the single most common form, on the app's single most important recurring signal.

**The Three-State Rule (hospital dashboard and inventory).** The hospital dashboard uses three status colours and neutrals, one per state `stockStatus` already defines: Safe teal means adequate (at or above minimum), Watch gold means below minimum, and Critical red-orange means critical (under 60% of minimum). Each state also carries a word ("Adequate", "Below minimum", "Critical"), so colour is never the only signal. Info Blue means nothing there, so the chart's blue Maximum outline is gone. The tint appears on the one alert surface (Action Required, and only while something is actually short) and takes the colour of the worst shortage, so a surface with only gold shortages is gold-tinted, not peach. It is never a wash across a grid. The same three states are the Status column of the hospital Inventory table, where they are the reason the table exists. The role accent (Oxblood or Navy) still colours actions and links, because it is identity, not status.

## Typography

**Display Font:** Archivo (with IBM Plex Sans, system-ui fallback)
**Body Font:** IBM Plex Sans (with system-ui fallback)
**Label/Mono Font:** JetBrains Mono (with Courier New fallback)
**Login Headline Font:** IBM Plex Serif 700 (with Georgia fallback) — the login screen's maroon-panel headline and nothing else

**Character:** Archivo's geometric weight carries headlines and big numbers with real authority; IBM Plex Sans runs the interface at a floor most systems reserve for emphasis only, so nothing defaults to tentative, and shares a family with the login headline's IBM Plex Serif, so the two read as one voice rather than a pairing; JetBrains Mono marks anything that reads as a physical, printed identifier — a DIN, a date, a request ID — the way a real label would set it.

### Hierarchy
- **Display** (800, clamp(1.5rem, 2.5vw, 1.875rem), 1.2): Dashboard hero headline, big stat numbers, screen titles.
- **Headline** (700, 1.5rem / text-2xl, 1.5): Section headers, modal titles.
- **Title** (700, 1.125rem–1.1875rem, 1.5): Card titles, table section headers.
- **Body** (700, 0.875rem–0.9375rem, 1.5): Running interface copy — the effective floor for nearly all text in the app, including labels and secondary/"muted" copy.
- **Label** (600, 0.8125rem, 0.03em tracking): DIN codes, dates, request IDs.
- **Login Headline** (IBM Plex Serif 700, clamp(2.5rem, 3.6vw, 3.75rem), 1.05, -0.01em): The one line of copy on the login screen's maroon panel. Set large and tight; no word in it is accented by colour or weight.

### Named Rules
**The Bold Floor Rule.** Body text is set at font-weight 700 — the last real static weight IBM Plex Sans ships (400 → 500 → 600 → 700), raised there deliberately. Because the floor is already this high, `font-semibold` and `font-bold` utility classes on top of it read flatter than they would elsewhere; that's the intended effect, not a bug to "fix" by adding a heavier weight.

**The Login-Serif Rule.** IBM Plex Serif is loaded at 700 only and is used for the login headline (`font-login-serif`). It is a trial: the interface font is unchanged, and the serif is not used on any other screen until it has been judged on this one. Because body text everywhere else sits on the Bold Floor, the login screen is the one place the Bold Floor is lifted for supporting copy (explanatory paragraphs, the verification notice and the register line are set at 500, not 700) so the headline is the only bold thing on the page. Labels, buttons and inputs keep their usual 600.

**The Mono-Means-Physical Rule.** JetBrains Mono is reserved for anything that represents a physical, printed identifier on a real object (a blood bag's DIN, its expiry stamp) — never used as a generic "technical-looking" typeface for arbitrary UI chrome.

## Layout

Standard Tailwind spacing scale (4px base unit) — no custom spacing tokens are defined; density comes from consistent component sizing (a near-universal 2.5rem/40px control height for buttons and inputs) rather than a bespoke rhythm. Screens run in a `max-w-screen-2xl` centered container with generous card-to-card gaps (`gap-4`–`gap-6`). Cards commonly split into a 2-column (`lg:grid-cols-3`, 2/1 split) or 2-up grid on desktop, stacking to one column below `lg`. The top nav is sticky; a scrollbar gutter is permanently reserved (`scrollbar-gutter: stable`) so navigating between a tall and short page never shifts content sideways.

## Elevation & Depth

Elevation is structural, not decorative: every top-level card gets the same soft, layered lift by default, and that lift is how a surface reads as "this is a distinct thing," not an effect reserved for hover or interaction. It's a genuine multi-layer stack — a tight contact shadow at the card edge, a mid-distance layer, and a wide, soft ambient glow underneath — the combination that reads as a card actually resting above the page rather than a flat rectangle with a border. Primary buttons carry a second, separate shadow tier tinted with the active brand color (Oxblood or Navy, via an RGB triplet token) rather than flat black, so their lift always reinforces whichever role identity is active instead of looking like a generic UI-kit default.

### Shadow Vocabulary
- **Card** (`0 1px 2px rgba(28,25,23,.05), 0 3px 6px rgba(28,25,23,.06), 0 10px 20px rgba(28,25,23,.07), 0 22px 44px rgba(28,25,23,.06)`): Every top-level `rounded-xl` card, by default, at rest.
- **Button** (`0 1px 2px rgba(28,25,23,.06), 0 2px 6px -1px rgba(role-accent,.25)`): Solid primary buttons at rest.
- **Button Hover** (`0 2px 4px rgba(28,25,23,.07), 0 4px 12px -2px rgba(role-accent,.32)`): On hover.
- **Button Active** (`0 1px 1px rgba(28,25,23,.06), 0 1px 3px rgba(role-accent,.22)`): On press — a tighter, closer shadow reads as the button settling down under the cursor.
- **Raised** (`0 1px 1px rgba(28,25,23,.12), 0 3px 7px -1px rgba(28,25,23,.12), 0 16px 30px -8px rgba(28,25,23,.20)`): A borderless surface whose edge is the shadow. Three layers that each do one job: a tight contact line, a mid-distance layer, and a wide ambient layer pulled in with a negative spread. Used only for the hospital dashboard's two raised surfaces (`--shadow-raised`).
- Modals and dropdowns use Tailwind's own `shadow-lg`/`shadow-xl` instead of the card shadow, since they sit at a different z-plane above everything else.

### Named Rules
**The Structural Lift Rule.** Card elevation is never optional or state-gated — a card without `--shadow-card` reads as a bug, not a quieter variant. If a surface shouldn't look elevated, it shouldn't be a `rounded-xl` card in the first place.

**The Two-Raised-Surfaces Rule (hospital dashboard).** The hospital dashboard has exactly two raised surfaces, Action Required and the Your requests column, and they carry no border. Everything else on that screen (the summary line, the stock chart, the expiry list) sits flat on Warm Paper with no card and no shadow. This is a deliberate exception to the Structural Lift Rule: a hospital consumes blood and is on the screen to act, so the page has one place to act and one place to check, and depth marks exactly those two. The hospital Inventory goes further and has no raised surface at all: it is a table of hairline-separated rows on the page background, with Upload History as a flat list. The hospital Emergency Sourcing screen has exactly two: the map and the request panel, with the controls and the ranked list flat on the page. None of this has been rolled out to the blood-bank screens.

## Shapes

Two radius families with a clear division of labor: a tight `sm`/`md`/`lg` step (2px / 6px / 8px) for controls — buttons, inputs, small badges, table chips — and a distinctly larger, independently-set `xl` (20px) reserved for top-level cards. `xl` is deliberately not derived as "`lg` + a fixed offset," so bumping the card radius never ripples down into every small control's corners. Borders are a uniform `1px solid` in Border Charcoal across cards, inputs, and table dividers — never a two-tone or gradient border.

## Components

### Buttons
- **Shape:** 6px radius (`rounded-md`), never the larger card radius.
- **Primary:** Solid Oxblood (or Navy on a Hospital account) fill, white text, `h-10` (40px) height, generous horizontal padding. Carries the tinted Button shadow at rest.
- **Hover / Focus:** Background steps to the *-hover* tone; shadow steps to the Button Hover tier; a 150ms ease-out transition on shadow only (never a jarring instant swap, never a slow fade).
- **Secondary / Ghost:** Border-only or transparent-background variants inherit the same 40px height and `rounded-md`, and drop the tinted shadow entirely — elevation is reserved for the solid primary action.
- **Disabled:** Shadow removed entirely rather than dimmed — a disabled button reads as inert, not just quieter.

### Badges (signature component)
The product's one non-negotiable shared shape: a blood-type badge (O-, A+, AB-, …) that looks identical whether it's inline in a table cell or the label on a search-picker button — four sizes (`sm`→`xl`) of the same design, never a redesigned variant per context. Unselected: white surface, Oxblood/Navy text, a 25%-opacity brand-colored border, subtle shadow. Selected: solid brand fill, white text, no border. `rounded-md`, `font-display font-bold`, tight tracking.

Status badges (Adequate / Below minimum / Critical, the three stock states) follow the Four-Step color rule: tint background, border, and text all drawn from the same status family, `rounded-full` for pill-shaped inline badges.

### Cards / Containers
- **Corner Style:** 20px radius (`rounded-xl`), the system's one distinctly larger radius.
- **Background:** Surface White (Warm Paper in dark mode's near-black equivalent).
- **Shadow Strategy:** The structural Card shadow, always on — see Elevation & Depth.
- **Border:** 1px solid Border Charcoal.
- **Internal Padding:** Typically `1.5rem` (24px), occasionally `1.75rem` for a hero/lead card.

### Inputs / Fields
- **Style:** Surface White background, 1px Border Charcoal, `rounded-md`, 40px height, `1rem` label gap above.
- **Focus:** A 2px ring in Oxblood/Navy at 30% opacity plus a border-color shift to full brand color — never just a border change alone.
- **Keyboard focus (every control):** A global `:focus-visible` rule in `theme.css` draws a 2px solid outline in the role accent, offset 2px, on every link, button, tab and menu item. A field that draws its own ring opts out with `focus:outline-none`. Login uses the same outline on its fields, because the 30% ring is too faint against the charcoal border.
- **Error:** The field itself doesn't change; a Critical-family message box (tint background, border, text) appears beneath the form.

### Navigation
Sticky top bar, Surface White background, single bottom border (no shadow — the nav sits flush, cards below it carry the depth instead). Active item: Oxblood/Navy-tinted background pill with brand-colored text and a slight scale-up; inactive: muted text that brightens on hover with a soft background wash. Labels hide below the `md` breakpoint, leaving icon-only nav so the header shrinks rather than overflows on a narrow or split window. Below `sm` the wordmark is hidden (the drop alone is the logo) and the bar's gaps and padding tighten, so the three nav icons, the bell and the account button fit at 320px without touching.

### DIN and dates
A blood-unit ID (DIN) is plain mono text (JetBrains Mono, per the Mono-Means-Physical Rule); a date is plain text in the app's one date format ("30 Oct 2026"). There is no separate "label" or "stamp" component: an earlier design had a wide-tracked uppercase `DinLabel` and a boxed, status-colored `DateStamp` for expiry dates, but no screen used them once the bank screens were rebuilt as tables, and both were removed. An expiring unit is shown by the plain days-left text and the status colour of the row, not a stamp.

## Do's and Don'ts

### Do:
- **Do** derive every new accent shade from the Four-Step Rule (base → hover/text → tint → border at a 66% blend) instead of picking a new one-off color.
- **Do** give every top-level `rounded-xl` card the structural Card shadow — it's a default, not a per-component decision.
- **Do** use JetBrains Mono only for things that represent a physical printed identifier (DIN, dates, request IDs).
- **Do** keep status color (safe/watch/critical) completely independent of role-accent color (Oxblood/Navy) — a Hospital and a Blood Bank account must always see the same red for the same shortage.
- **Do** use the one shared `BloodTypeBadge` component at whatever size fits, never a bespoke inline badge for a blood type.
- **Do** keep the request reason unselected until the user chooses it. It decides where the request is listed in the supplier's pending requests (trauma and scheduled surgery ahead of restock), so a default would put every careless request at the top of that list. Send stays disabled until one is chosen.
- **Do** name the server's two priority tiers, Immediate use (trauma and scheduled surgery) and Restock, wherever requests are grouped or tagged by reason, not three reasons. The blood bank's incoming list shows them as numbered positions in the order to work in. Scheduled surgery is never blue.
- **Do** keep the clinical caveat ("always verify with clinical staff") attached to the compatible-types line on Emergency Sourcing. It is the one place that screen admits it is not a clinical authority.
- **Do** write hospital-dashboard labels in sentence case ("Total units", "Expiring soon") and give each fact its own sentence or clause.

### Don't:
- **Don't** introduce a second red-adjacent or green "success/danger" pair — Safe is teal, Critical is red-orange, by rule, to stay colorblind-safe.
- **Don't** use Info Blue or Synthetic Violet for anything status-shaped — they're reserved for "related option" and "stand-in data" respectively, never "OK" or "urgent."
- **Don't** add decorative or looping motion. Every animation in the system plays once, in response to a real event, and settles — never ambient, never repeating (the notification bell's attention pulse is the one deliberate exception, and even that plays once per new arrival, not on a loop).
- **Don't** flatten a card by removing its shadow "for a cleaner look" — in this system, no shadow reads as broken, not minimal.
- **Don't** use tracked-out capitals as labels, middle dots between facts, or arrow prefixes on button text on the hospital dashboard. The other screens still use them; they are to be reviewed when this treatment is rolled out.
- **Don't** add a third raised surface to the hospital dashboard, or put a border on a raised one.
