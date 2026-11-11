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
  status-watch: "#996F09"
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
- **Watch** (#996F09, gold): "Below minimum" stock (under the minimum, not yet critical), pending confirmation, dev-mode indicator.
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

**The Two-Raised-Surfaces Rule (hospital dashboard).** The hospital dashboard has exactly two raised surfaces, Action Required and the Your requests column, and they carry no border. Everything else on that screen (the summary line, the stock chart, the expiry list) sits flat on Warm Paper with no card and no shadow. This is a deliberate exception to the Structural Lift Rule: a hospital consumes blood and is on the screen to act, so the page has one place to act and one place to check, and depth marks exactly those two. The hospital Inventory goes further and has no raised surface at all: it is a table of hairline-separated rows on the page background, with Upload History as a flat list. It is laid out exactly like the blood bank's Inventory (same content width, same header block, same search box above the table, same row grid and spacing, Thresholds at the right edge), with the hospital's own columns (Units held, Status, Minimum, Maximum), and the two screens share the search box and its results frame (`components/UnitSearch.tsx`). The hospital Emergency Sourcing screen has exactly two: the map and the request panel, with the controls and the ranked list flat on the page. None of this has been rolled out to the blood-bank screens.

The default look above is what applies without the attribute. A second look, **the clay look, enabled by `data-surface="clay"` on `<html>`**, changes the shadows, radii and edges of surfaces and controls: see "The Clay Look" below. With the attribute absent nothing in this document changes.

## Shapes

*(Default look. In the clay look the radii are larger and a button's differs from the controls above: see "The Clay Look".)*

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
Sticky top bar, Surface White background, single bottom border (no shadow — the nav sits flush, cards below it carry the depth instead). **4rem tall (it was 3.5rem), and every control in it is at least 44 × 44 px** (the nav tabs, the bell, the account button): labels 15px, icons 20px (the bell 22px), the logo drop 32px and the wordmark 18px, the account avatar 32px. The row is one class, `HEADER_ROW_CLASS` in `components/AccountMenu.tsx` (`max-w-screen-2xl`, `px-4 sm:px-6`, the same gutter as every page), used by the facility `TopNav` and by the Admin screen's header, so the logo and the account button sit at the same x as the page content on every screen and every width (measured 2026-11-11 at 390, 1280 and 1920). Active item: Oxblood/Navy-tinted background pill with brand-colored text and a slight scale-up; inactive: muted text that brightens on hover with a soft background wash. Labels hide below the `md` breakpoint, leaving icon-only nav so the header shrinks rather than overflows on a narrow or split window. Below `sm` the wordmark is hidden (the drop alone is the logo) and the bar's gaps tighten, so the three nav icons, the bell and the account button fit at 320px without touching (measured: right-most control at 298 of 320 px). The bell's unread badge is anchored to the icon, 10px above and to the right of it, whatever the size of the target around it. Below `sm` the notification panel anchors to the header and spans the viewport with a 1rem gutter (anchored to the bell it ran off the left edge of a phone); from `sm` up it hangs from the bell, 20rem wide. The Admin header is the same bar (not sticky, no bell: an admin has no facility) with an `ADMIN` pill after the wordmark.

### Messages (the conversation screen)
The top nav tab is **Supply** (it was Requests); its sub-tabs are the role's sourcing name, **Messages** and Transfer history. Messages is a list of
conversations (one row per other facility: an initials circle, the name and the time, the last message, an unread count, and what is waiting
on you) beside one thread, and on a phone the list and then the thread with a back link. In the thread **everything is aligned by who did it:
mine on the right, theirs on the left, never centred and never full width**, each with a visible label ("You" or the facility's name, and the
time). A message is a bubble with an asymmetric corner (the tail toward its side); mine is the role's accent with white text (maroon on a bank,
navy on a hospital), theirs a neutral grey. A status event is a compact chip (an icon and one sentence naming its actor, such as "You accepted
request #38"; mine tinted with the accent, theirs neutral), with "Units released (9)" and "Units and outcome (9)" as expandable sections inside it
(the table scrolls inside the chip, the page never widens). A request is a compact card (at most 26rem wide) on the side of the facility that
made it: blood type, quantity, component, emergency type, a status pill, and the one button for its next step (Accept and Decline are the only
pair); a finished request is a one-line summary that expands in place. Names and times use the foreground and muted text colours: an
accent-coloured name was under 3:1 on a dark card. Every bubble and chip has a real 1px border (so forced colors still shows where each
begins); under the clay look its colour is transparent and the shading draws the edge. The composer is a pill-shaped field and a round accent send
button; an "About: request #N ×" chip sits above it on one line, also at 390px. Measured contrast of every text on the screen, in the browser
and for both accents, light and dark, clay on and off, is 4.5:1 or more (lowest: my bubble in the bank's dark theme, 4.5036:1).

### DIN and dates
A blood-unit ID (DIN) is plain mono text (JetBrains Mono, per the Mono-Means-Physical Rule); a date is plain text in the app's one date format ("30 Oct 2026"). There is no separate "label" or "stamp" component: an earlier design had a wide-tracked uppercase `DinLabel` and a boxed, status-colored `DateStamp` for expiry dates, but no screen used them once the bank screens were rebuilt as tables, and both were removed. An expiring unit is shown by the plain days-left text and the status colour of the row, not a stamp.

## The Clay Look (optional, enabled by `data-surface="clay"`)

**The look is a switch.** Set `data-surface="clay"` on `<html>` (it is in `index.html`; `document.documentElement.dataset.surface = "clay"` in DevTools tries it on a build where it is absent) and surfaces take a soft, molded finish. **With the attribute absent the default look above applies, unchanged, and nothing in this section does.** Whether the attribute ships is a one-word choice in `index.html`; this document describes both. The rules live in the last block of `src/styles/theme.css`. It changes **surfaces, controls and the stock gauges**; status and alert colours (safe, watch, critical, info, synthetic), the gauges' arc and track colours, chart colours, layout, spacing, copy and behaviour are untouched, and the hospital's tinted Action required keeps its fill and its colours.

**One recipe per kind, applied to every instance of that kind.** Eight kinds, each defined once in `theme.css`:

| Kind | Instances | Recipe |
|---|---|---|
| 1 Primary button | solid accent fill: maroon on a bank, navy on a hospital; a selected chip; the icon-only send button | outer drop in the accent colour, inner white light, inner dark-warm shade, a fill gradient, 0.875rem radius; pressed = the shadow goes inward |
| 2 Secondary button | outline or neutral fill, tabs and toggles (selected), Notify nearby hospitals, Cancel, Upload CSV; the danger outline button (Reject, Deactivate) keeps its critical border and text colour; an unselected tinted chip (blood type in Add unit) has no border | `--shadow-clay-sm`, the same gradient, a soft **boundary of 3:1 or better** (not on a chip, not on a danger button, which has its own colour) |
| 3 Pill / badge | status pills (Complete, Active, Trauma, Restock), position numbers, STAFF | inner light and inner shade only, **no gradient**, so a status colour is exactly its colour |
| 4 Field | input, select, textarea (the search box, the number box between the stepper buttons, selects, date inputs) | **raised** by default: the small raised recipe, a flat fill a step lighter than a card (no gradient, so it never reads as a button), a soft **3:1 boundary**, a text cursor, 0.875rem radius; `--clay-field-depth` switches it to the recessed field of the previous round |
| 5 Card and container | card, raised surface, big container; and a nested tile (the gauge tile on both Dashboards) | **no outline**: the edge is the shading. Card `md`, big container `lg`, 1.75rem radius; a tile is **raised** with the small recipe (`sm`), like a button, 1.25rem radius; the gauge's groove inside it is the sunken part |
| 6 Floating surface | modal, popover, tooltip | no outline, a stronger drop (`float`; a tooltip `sm`) |
| 7 Status-tinted panel | the hospital's Action required | the same radius and a light inner edge; its fill, text and outer drop are as before |
| 8 Flat controls | icon buttons, navigation, text links, the unselected half of a segmented control | unchanged on purpose: they are not objects |
| 9 Message bubble and status chip | the Messages thread (`.msg-bubble-own`, `.msg-bubble-other`, `.thread-chip`) | small raised look: `sm` for theirs and for chips, the accent recipe for mine; the border colour is transparent (it stays in the off look and in forced colors) |

A small control inside a list or table row (a button that is `h-6`, `h-7` or `h-8` in an `li`, `td` or `th`: Deactivate, Decline, Accept) gets the **inner shading only and no outer drop**, and a smaller radius, so a table of them stays calm; the same control outside a row has the full recipe. At most three shadow layers on any element.

**The edges.** A card, tile, modal, popover or tooltip has no visible outline: its border keeps its width (nothing moves) but is transparent, and the shading draws the edge. A control that has to be found by its edge (a field, an outline button, a tab) keeps a boundary, in a soft warm grey (`--clay-boundary`: `#857B70` in light theme, `#857868` in dark) instead of the default look's dark line: 3.4:1 against the page, 3.8:1 against a card and 3.9:1 against a raised field in light theme, and 4.3, 4.0 and 3.6 in dark (the default dark border is 1.6:1). **Dividers and rims inside a surface** (the rule under a section title, a table header band's edges, row separators, a `divide-y` list, the gray hairlines on Inventory, Upload history, Requests and Transfer history) are a soft warm line, `--border`: `#DDD3C4` in light theme (1.34:1 on a card, 1.21:1 on the page) and `#3A3029` in dark (1.33:1), against the default look's near-black line; they are decorative, so they carry no 3:1 requirement, and status borders are untouched.

**One light, from the upper left.** Every clay surface obeys it. A **raised** thing (card, big container, tile, button, chip, pill, modal, popover, tooltip, and by default a text field) throws a warm drop shadow to the **bottom right**, with next to nothing on its top and left, has a **white highlight** just inside its top-left edge and a **faint warm shade** well inside its bottom-right edge. A **sunken** thing (the gauge's groove, a pressed button, and a text field if `--clay-field-depth` is switched) is the reverse: shade inside the top left, light inside the bottom right. Nothing has a spread, and no raised drop has a zero or negative offset, so no shadow glows on the side the light comes from. The direction is two variables, `--clay-lx` and `--clay-ly` (+1 = the light is on the left / on top); every offset is multiplied by them, so flipping the light is one edit.

**The strength is one block of variables, at the top of the clay block, and so are the sizes.** A size is one depth `d` in px: `--clay-d-sm` 1.6 (button, chip, tile, tooltip, field), `-md` 2 (card), `-lg` 2.4 (big container: the Dashboard cards, Waiting for you / Your requests, the map, the Request card, the 600 px panels, and the edge of Action required), `-float` 4 (modal, popover), `-row` 1.6 and `-pill` 1 (the inner shading of a small control in a row, and of a status pill). **The outer drop** is `d` across and `d * --clay-ky` (1.25) down, blurred by `d * --clay-kb` (4.2): a large blur next to a small offset is what makes it fade out softly instead of drawing a line. **The inner light** is `d`, `d`, blurred by `d * --clay-kib` (2). **The inner shade** is `d * --clay-klo` (0.6) the other way, blurred by `d * --clay-ksb` (4): small, far inside and faint, so that it does not make a second dark band against the drop on the other side of the same edge. `--clay-hi-a`, `--clay-lo-a` and `--clay-drop-a` are the opacity of the light, the shade and the drop (medium, light theme: 0.95, 0.07, 0.12); `--clay-grad` is the button fill gradient; `--clay-acc-hi-a` and `--clay-acc-drop-a` are the light and the drop on an accent button. Two settings are written out, each for light and dark: **medium** (the default) and **strong** (`<html data-clay-strength="strong">`: depths about 1.25 times larger and higher opacities, same directions). **`--clay-field-depth`** is the field switch: `var(--clay-raised)` (the default) or `var(--clay-sunken)`, a one-line change; raised gives the small raised recipe on a flat `#FBF8F2` fill (`#2A231D` in dark), sunken the recessed `#E9E2D6` field (`#1A1510`). The warm colours are `--clay-hi-rgb`, `--clay-shade-rgb` and `--clay-drop-rgb`. The shadows by size are `--shadow-clay-sm`, `-md`, `-lg`, `-float`, `-row`, `-pill` (raised), `-field` (raised or sunken by the switch), `-pressed` (sunken), `-status` (Action required: the big container's drop and light, over its own fill) and the `-accent` set, all built from those variables.

**How far the shadow reaches (measured on the page background, light theme, medium; a 3-level colour change counts as visible, the 8-level reach is in brackets).** The big container (`lg`) reaches 10 px below and 9.5 px to the right (5 and 4.5), and 4 px above and 4.5 px to the left (0 and 0). The previous round's big container reached 10 / 9 below and right but 0.5 / 2 above and left: the larger blur that makes the edge soft leaks a faint tail on the light side, which is the trade. The card (`md`) reaches 8.5 and 8, the button, chip and tile (`sm`) 5.5 and 5, the modal and popover (`float`) 18 and 17. Strong's big container reaches 14 below and 12 right. The target for a big container is about 10 px or less below and to the right; medium meets it, strong does not by design.

**Edge softness: shading, not an outline.** An outline reads when the shadow outside an edge and the shade inside the same edge are both dark, because the eye sees a dark band on either side of the line. The recipe avoids it three ways: the outer drop has a large blur next to a small offset and a low opacity, so it fades out over 6 to 10 px with no step; the inner shade is faint and sits far inside; and the highlight is on the opposite (top-left) edge. Measured, shadow alone, each surface's real computed style replayed on an isolated box over its real backdrop: the largest per-pixel luminance step across the **bottom** edge (L\*, light theme) fell from 10.78 to 3.71 on a gauge tile, 6.57 to 3.11 on a big card, 11.41 to 3.71 on a secondary button and 11.99 to 3.74 on an Admin card, and across the **right** edge from 10.78 to 3.71, 5.61 to 2.74, 8.07 to 2.41 and 10.8 to 3.44: every one under half. The dark band inside the edge went from 6.1 to 1.3 L\* on a tile and the shadow outside from 10.8 to 3.7. A card's whole step, fill included, cannot fall below about 4.4 L\* (the card is a step lighter than the page): 9.09 to 6.31 on a big card. Retune with `edge_profile`-style measurements, not by eye: raising `--clay-lo-a` or `--clay-drop-a`, or lowering `--clay-kb`, brings the outline back.

**What the clay look changes in the default rules above** (so nobody reads one as a bug against the other). Where the default rules say to keep something flat, the clay look follows the owner's feedback instead:
- **Cards and tiles have no outline**, not a 1px Border Charcoal one; the gauge tiles are raised pieces and the hospital's controls are no longer flat.
- **Secondary and outline buttons carry a shadow**, and so do danger outline buttons, chips and pills; the default look gives elevation only to solid primary buttons. A disabled button carries none in either look.
- **The hospital's navy buttons, tiles and Action required** take the same recipe as the bank's; the Two-Raised-Surfaces Rule (everything else flat on the page) is a default-look rule.
- **A button's radius** is 0.875rem, not the 6 px `rounded-md`, and not the card radius.
- **The blood-type badge** keeps its shape and its border; its fill is the softer card (`#F8F3EA`), not white.
- **Fills**: `--card` is `#F8F3EA` (a step lighter than the page, so a card reads as raised), the page `#EEE8DD`, a raised field `#FBF8F2` (a flat step lighter than a card: it must read as a field, not a button, so it has no gradient). In dark theme the page and card are unchanged and a raised field is `#2A231D`.
- **Modals and dropdowns** use `--shadow-clay-float`, not `shadow-lg`/`shadow-xl`.
- **Placeholders** in clay fields use the muted text colour (5.5:1 on a raised field in light theme, 4.9:1 in dark); the default look's 50%-of-text placeholder is about 3.4:1.
- **Surface-versus-page and border contrast are lower than the default look's by design** (no outline on a card; a lighter boundary on a control): that is the point of the look, and it is why the fallbacks below exist.

**The gauge (`StockDial`, on both Dashboards).** The arc reads as a soft clay tube and the empty track as a gentle groove, drawn with a few extra thin strokes inside the gauge's original 10-unit band, never with a blur or filter (eight gauges repaint with the Dashboard). The arc's body keeps its status colour exactly (teal adequate, amber below minimum, red-orange critical) and so does the track (`--dial-track`). On the arc, a highlight stroke lies along the edge toward the light (top left) and a shade stroke along the edge away from it, each the arc's own colour mixed with white or black (`--clay-dial-hi`, `--clay-dial-lo`: 38% and 18% in light theme, 26% and 30% in dark), so a highlight or shade can never read as another status. On the track, a darker groove stroke (`--clay-dial-groove`) sits on the top-left inner wall and a faint lip (`--clay-dial-lip`) on the bottom-right. Everything stays inside the original band, so the gauge's silhouette, the threshold tick, the blood-type badge, the number and the "min" text keep their positions and sizes. The status word still carries the status (the gauge is `role="img"` and its label, with the status word, is unchanged); colour is never the only signal. The extra strokes exist in the markup as hidden groups (`display="none"` as a presentation attribute) that only the clay rules show, which is why the default look is unchanged. Contrast, from the token values: the arc's body against the track is 3.64:1 (adequate), 3.01:1 (below minimum) and 3.56:1 (critical) in light theme and 4.96, 5.66 and 3.66 in dark, unchanged; against the card 4.95, 4.10 and 4.84 in light (5.47, 4.53, 5.35 in the default look, against white) and unchanged in dark. The thin highlight and shade strokes are decoration inside the arc and fall below 3:1 against the track (highlights 1.6 to 1.8:1 in light; shades 2.0 to 2.9:1 in dark): the arc's outer edge, which is what meets the track and the card, is always the body colour.

**Fallbacks.** The clay look switches itself off, leaving exactly the default look, for `prefers-contrast: more`, `forced-colors: active` and **print** (a printed page has no clay shadow, inner shadow, tinted fill or larger radius, and its gauges are the plain ones; the printable Receipt prints byte-for-byte the same with the switch on or off).

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
