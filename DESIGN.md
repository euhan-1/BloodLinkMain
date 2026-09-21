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
    fontFamily: "Archivo, 'Plus Jakarta Sans', system-ui, sans-serif"
    fontSize: "clamp(1.5rem, 2.5vw, 1.875rem)"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body:
    fontFamily: "'Plus Jakarta Sans', system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 700
    lineHeight: 1.5
  label:
    fontFamily: "'JetBrains Mono', 'Courier New', monospace"
    fontSize: "0.8125rem"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "0.03em"
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

BloodLink's visual system takes its cues from the physical object at the center of the product: the printed label on a real blood bag. A donor identification number sets in wide-tracked mono type. An expiry date reads as a stamped, boxed callout rather than plain running text. Blood-type badges (O-, A+, AB-) are treated as the product's signature element — one shared, unmistakable shape used identically from a table row to a search-picker button, exactly the way a real bag's type label never changes shape depending on where you're standing.

That material honesty extends outward into warmth rather than sterility. The palette is a deliberate rejection of cold clinical white-and-blue hospital-software convention: warm stone neutrals instead of cool gray, an oxblood (not alert-red) brand identity for Blood Bank accounts and a deep, muted navy for Hospital accounts, and a status language whose safe/watch/critical states use teal, gold, and red-orange specifically so the system never leans on a red-green distinction. Typography carries the same conviction — body text sits at a 700 weight floor, the boldest static weight Plus Jakarta Sans ships, so nothing in the interface reads as tentative. Cards get genuine, layered depth (a tight contact shadow, a mid-distance layer, a wide ambient glow) so surfaces feel like they're actually resting on the page, not drawn on with a flat line.

Motion is restrained and one-shot: something happens, the interface responds once, and settles. Nothing loops or decorates — in a tool whose job is coordinating an actual blood shortage, motion is feedback, never atmosphere for its own sake.

**Key Characteristics:**
- Physical-object material honesty: mono DIN codes, stamped expiry dates, one unchanging blood-type badge shape everywhere
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
- **Synthetic Violet** (#7C3AED): "This is a stand-in, not real data." Reserved entirely for the synthetic-forecast-model badge and its matching forecast-line color on the Dashboard. Violet specifically because it reads as neither a status color nor Info's blue.

### Neutral
- **Warm Paper** (#FAF9F7): Page background.
- **Surface White** (#FFFFFF): Card, modal, and input background.
- **Ink** (#1C1917): Primary text — used almost everywhere, including as "muted" text, since this system doesn't dim secondary copy.
- **Border Charcoal** (#58514B): Every structural border — cards, table rows, tabs, inputs. Dark enough to read as real structure at a glance, not a hairline you have to look for.
- A nine-step warm "stone" gray scale (`#FAFAF9` → `#1C1917`) replaces Tailwind's default cool gray everywhere, so no neutral in the app reads as a mismatched off-the-shelf gray next to Oxblood or Navy.

### Status & Semantic
Reused identically everywhere status appears — expiry, stock thresholds, availability, forecast alerts, facility/account status — and, critically, identical on every account type. Role identity (Oxblood/Navy) never leaks into status color, so a Hospital and a Blood Bank account looking at the same shortage always see the same red.
- **Safe** (#0F766E, teal): Adequate stock, confirmed, live/active states.
- **Watch** (#B8860B, gold): Marginal stock, pending confirmation, dev-mode indicator.
- **Critical** (#BD4024, red-orange): Below minimum, errors, deactivated state. Its hue sits ~12° from Oxblood's own ~344° specifically so a solid brand button next to a critical badge never blurs into one red blob.

### Named Rules
**The Four-Step Rule.** Every accent and status color (Primary, Safe, Watch, Critical, Info, Synthetic) ships as a family of four: a base tone for solid fills and icons, a *-hover* or *-text* tone for buttons/text-on-tint, a pale *-tint* for panel backgrounds, and a *-border* blended 66% of the way from the tint toward the base tone — the ratio that reliably clears ~3.2:1+ contrast without hand-tuning each one. Never introduce a fifth ad-hoc shade of an existing color family; derive it by the same blend instead.

**The No Red-Green Rule.** Status color never relies on a red/green distinction. Safe is teal, not green; Critical is red-orange, not pure red — chosen so the safe/watch/critical triad stays legible for red-green colorblindness (~8% of men), the single most common form, on the app's single most important recurring signal.

## Typography

**Display Font:** Archivo (with Plus Jakarta Sans, system-ui fallback)
**Body Font:** Plus Jakarta Sans (with system-ui fallback)
**Label/Mono Font:** JetBrains Mono (with Courier New fallback)

**Character:** Archivo's geometric weight carries headlines and big numbers with real authority; Plus Jakarta Sans runs the interface at a floor most systems reserve for emphasis only, so nothing defaults to tentative; JetBrains Mono marks anything that reads as a physical, printed identifier — a DIN, a date, a request ID — the way a real label would set it.

### Hierarchy
- **Display** (800, clamp(1.5rem, 2.5vw, 1.875rem), 1.2): Dashboard hero headline, big stat numbers, screen titles.
- **Headline** (700, 1.5rem / text-2xl, 1.5): Section headers, modal titles.
- **Title** (700, 1.125rem–1.1875rem, 1.5): Card titles, table section headers.
- **Body** (700, 0.875rem–0.9375rem, 1.5): Running interface copy — the effective floor for nearly all text in the app, including labels and secondary/"muted" copy.
- **Label** (600, 0.8125rem, 0.03em tracking): DIN codes, dates, request IDs — uppercase where the physical convention calls for it (see DinLabel).

### Named Rules
**The Bold Floor Rule.** Body text is set at font-weight 700 — the last real static weight Plus Jakarta Sans ships (400 → 500 → 600 → 700), raised there deliberately. Because the floor is already this high, `font-semibold` and `font-bold` utility classes on top of it read flatter than they would elsewhere; that's the intended effect, not a bug to "fix" by adding a heavier weight.

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
- Modals and dropdowns use Tailwind's own `shadow-lg`/`shadow-xl` instead of the card shadow, since they sit at a different z-plane above everything else.

### Named Rules
**The Structural Lift Rule.** Card elevation is never optional or state-gated — a card without `--shadow-card` reads as a bug, not a quieter variant. If a surface shouldn't look elevated, it shouldn't be a `rounded-xl` card in the first place.

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

Status badges (Adequate / Marginal / Low, etc.) follow the Four-Step color rule: tint background, border, and text all drawn from the same status family, `rounded-full` for pill-shaped inline badges.

### Cards / Containers
- **Corner Style:** 20px radius (`rounded-xl`), the system's one distinctly larger radius.
- **Background:** Surface White (Warm Paper in dark mode's near-black equivalent).
- **Shadow Strategy:** The structural Card shadow, always on — see Elevation & Depth.
- **Border:** 1px solid Border Charcoal.
- **Internal Padding:** Typically `1.5rem` (24px), occasionally `1.75rem` for a hero/lead card.

### Inputs / Fields
- **Style:** Surface White background, 1px Border Charcoal, `rounded-md`, 40px height, `1rem` label gap above.
- **Focus:** A 2px ring in Oxblood/Navy at 30% opacity plus a border-color shift to full brand color — never just a border change alone.
- **Error:** The field itself doesn't change; a Critical-family message box (tint background, border, text) appears beneath the form.

### Navigation
Sticky top bar, Surface White background, single bottom border (no shadow — the nav sits flush, cards below it carry the depth instead). Active item: Oxblood/Navy-tinted background pill with brand-colored text and a slight scale-up; inactive: muted text that brightens on hover with a soft background wash. Labels hide below the `md` breakpoint, leaving icon-only nav so the header shrinks rather than overflows on a narrow or split window.

### DIN / Date Stamp (signature component)
Two small, deliberately literal components that carry the "printed on a real label" metaphor: `DinLabel` sets a blood-unit ID in uppercase, wide-tracked mono. `DateStamp` boxes an expiry date in a bordered, status-colored callout — visually heavier than the plain collection-date text beside it, mirroring how a real bag's printed expiry is the one date meant to catch your eye.

## Do's and Don'ts

### Do:
- **Do** derive every new accent shade from the Four-Step Rule (base → hover/text → tint → border at a 66% blend) instead of picking a new one-off color.
- **Do** give every top-level `rounded-xl` card the structural Card shadow — it's a default, not a per-component decision.
- **Do** use JetBrains Mono only for things that represent a physical printed identifier (DIN, dates, request IDs).
- **Do** keep status color (safe/watch/critical) completely independent of role-accent color (Oxblood/Navy) — a Hospital and a Blood Bank account must always see the same red for the same shortage.
- **Do** use the one shared `BloodTypeBadge` component at whatever size fits, never a bespoke inline badge for a blood type.

### Don't:
- **Don't** introduce a second red-adjacent or green "success/danger" pair — Safe is teal, Critical is red-orange, by rule, to stay colorblind-safe.
- **Don't** use Info Blue or Synthetic Violet for anything status-shaped — they're reserved for "related option" and "stand-in data" respectively, never "OK" or "urgent."
- **Don't** add decorative or looping motion. Every animation in the system plays once, in response to a real event, and settles — never ambient, never repeating (the notification bell's attention pulse is the one deliberate exception, and even that plays once per new arrival, not on a loop).
- **Don't** flatten a card by removing its shadow "for a cleaner look" — in this system, no shadow reads as broken, not minimal.
