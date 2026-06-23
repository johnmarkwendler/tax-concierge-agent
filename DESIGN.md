---
name: Tax Concierge Agent
description: A calm A2UI intake system that helps small business owners move from uncertainty to a tax entity recommendation.
colors:
  bg: "oklch(1.000 0.000 0)"
  surface: "oklch(0.985 0.006 353)"
  surface-raised: "oklch(0.970 0.010 353)"
  ink: "oklch(0.205 0.028 260)"
  muted: "oklch(0.455 0.030 260)"
  border: "oklch(0.895 0.014 353)"
  primary: "oklch(0.540 0.135 353)"
  primary-soft: "oklch(0.940 0.035 353)"
  accent: "oklch(0.470 0.095 195)"
  accent-soft: "oklch(0.930 0.035 195)"
  success: "oklch(0.500 0.100 150)"
  warning: "oklch(0.620 0.105 78)"
  danger: "oklch(0.520 0.135 25)"
typography:
  display:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "2.75rem"
    fontWeight: 650
    lineHeight: 1.05
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "1.5rem"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "1rem"
    fontWeight: 620
    lineHeight: 1.35
    letterSpacing: "0"
  body:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "0"
  label:
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif"
    fontSize: "0.875rem"
    fontWeight: 560
    lineHeight: 1.25
    letterSpacing: "0"
rounded:
  sm: "6px"
  md: "10px"
  lg: "14px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  xxl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.bg}"
    rounded: "{rounded.md}"
    padding: "12px 18px"
  button-secondary:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "12px 18px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "24px"
  input:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "14px 16px"
  status-confident:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    rounded: "{rounded.pill}"
    padding: "6px 10px"
  status-needs-clarification:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.primary}"
    rounded: "{rounded.pill}"
    padding: "6px 10px"
---

# Design System: Tax Concierge Agent

## 1. Overview

**Creative North Star: "The Guided Worktable"**

Tax Concierge should feel like sitting beside a careful expert at a clean worktable: the user brings scattered facts and documents, and the interface turns them into a small sequence of understandable next steps. The product is friendly, but it is not casual about tax decisions, document handling, or uncertainty.

The system is built around A2UI-generated UI. Agent output should render as coherent product surfaces: intake prompts, understanding summaries, document review cards, one-question follow-ups, and recommendation states. It must not look like a chatbot transcript, a tax-prep dashboard, or a giant form.

Use the combined direction of Guided Health Record and Soft Operating System: calm progress, clear status language, restrained color, and precise controls. The user should feel, "I do not have to know everything," and "I can always correct this."

**Key Characteristics:**
- Calm guided cards with one clear job each.
- Plain-language microcopy that explains why each question matters.
- Dynamic A2UI components that share one component vocabulary.
- Visible progress without percentages.
- Correction paths and editable review states by default.

## 2. Colors

The palette is restrained product UI: true white architecture, lightly rose-tinted surfaces, a muted rose primary, and a separate teal accent for review and confidence states.

### Primary
- **Guided Rose** (`oklch(0.540 0.135 353)`): Use for primary actions, selected controls, active journey states, and rare emphasis. It comes from the Impeccable seed but is muted enough for a careful tax product.
- **Rose Whisper** (`oklch(0.940 0.035 353)`): Use for soft selected backgrounds, low-pressure clarification badges, and gentle system emphasis.

### Secondary
- **Steady Teal** (`oklch(0.470 0.095 195)`): Use for confident findings, reviewed document details, and calm validation states. Do not use it as generic decoration.
- **Teal Wash** (`oklch(0.930 0.035 195)`): Use behind confident findings and document review chips where a filled saturated color would feel too loud.

### Neutral
- **True White** (`oklch(1.000 0.000 0)`): Main page background. Keep the architecture clean instead of turning the whole product beige or paper-toned.
- **Worktable Surface** (`oklch(0.985 0.006 353)`): Primary card background. The rose tint should be barely perceptible.
- **Raised Surface** (`oklch(0.970 0.010 353)`): Secondary panels, recent upload rows, disclosure bodies, and review-card interiors.
- **Deep Ink** (`oklch(0.205 0.028 260)`): Primary text and icon color. Body text must remain crisp against warm surfaces.
- **Soft Ink** (`oklch(0.455 0.030 260)`): Secondary text, helper text, and low-emphasis metadata. Do not go lighter for body-size copy.
- **Quiet Border** (`oklch(0.895 0.014 353)`): Dividers, control outlines, and inactive card boundaries.

### Tertiary
- **Care Green** (`oklch(0.500 0.100 150)`): Success and confirmed-safe states.
- **Review Amber** (`oklch(0.620 0.105 78)`): Needs-review and still-learning markers.
- **Risk Red** (`oklch(0.520 0.135 25)`): Security warnings, blocked uploads, and true risk states only.

### Named Rules

**The No Percentages Rule.** Never map model confidence to visible percentages. Use `Confident`, `Needs clarification`, and `Still learning`.

**The One Accent Per Meaning Rule.** Rose means action or convergence. Teal means understood or reviewed. Amber means review needed. Red means real risk.

**The White Architecture Rule.** Warmth comes from the primary color, motion, spacing, and language. The page background stays true white.

## 3. Typography

**Display Font:** Inter with system UI fallbacks.
**Body Font:** Inter with system UI fallbacks.
**Label/Mono Font:** Use the same family; this product does not need a separate display or mono voice.

**Character:** The typography should be familiar, legible, and quietly premium. It should support a guided product workflow, not editorial drama.

### Hierarchy
- **Display** (650, `2.75rem`, `1.05`): Hero prompt only, especially "Tell us about your business." Keep letter spacing no tighter than `-0.025em`.
- **Headline** (650, `1.5rem`, `1.2`): Major card headings such as "We think we understand the following" and "We have a recommendation."
- **Title** (620, `1rem`, `1.35`): Question titles, document finding titles, upload names, and recommendation subsections.
- **Body** (400, `1rem`, `1.55`): Explanations, generated summaries, and helper copy. Cap prose at 65-75ch.
- **Label** (560, `0.875rem`, `1.25`): Control labels, status labels, journey steps, and compact metadata. Use sentence case, not all caps.

### Named Rules

**The Plain First Rule.** Say "Two owners" before "partnership classification." Say "S-Corp election status" only when that exact term must be reviewed.

**The Question Has One Job Rule.** A follow-up card title should ask one thing. Put context in "Why we're asking," not in a long title.

## 4. Elevation

Depth is conveyed through tonal layering first, then very small state shadows. Cards are mostly flat at rest. This keeps the product calm and avoids the bordered-card-plus-soft-shadow pattern that makes interfaces feel generated.

### Shadow Vocabulary
- **Focus Ring** (`0 0 0 3px oklch(0.940 0.035 353)`): Keyboard focus and active input states. Pair with a primary border shift.
- **Lifted State** (`0 6px 8px oklch(0.205 0.028 260 / 0.08)`): Hovered upload rows, active disclosure panels, and selected follow-up cards only.
- **Overlay Sheet** (`0 12px 24px oklch(0.205 0.028 260 / 0.12)`): Use only for temporary overlays or mobile bottom sheets.

### Named Rules

**The Flat At Rest Rule.** Default cards should use tonal surfaces and borders, not decorative shadows.

**The No Ghost Card Rule.** Do not combine a 1px border with a large soft shadow. If a component has a border, any shadow must stay at or below 8px blur.

## 5. Components

### Buttons
- **Shape:** Soft rectangle with `10px` radius. Full pill only for compact chips and status labels.
- **Primary:** Guided Rose background with white text, `12px 18px` padding, medium weight label. Use for `Continue`, upload confirmation, and recommendation continuation.
- **Hover / Focus:** Hover deepens primary slightly and may use Lifted State. Focus uses the Focus Ring and must be visible without color alone.
- **Secondary:** Raised Surface background, Deep Ink text, Quiet Border outline. Use for `Edit`, `Review`, and `Upload another document`.
- **Text action:** Use for low-emphasis correction links such as `Change this answer`. Keep sentence case.

### Chips
- **Style:** Pill shape, `6px 10px`, label typography, soft semantic background, readable semantic text.
- **Confident:** Teal Wash background with Steady Teal text.
- **Needs clarification:** Rose Whisper background with Guided Rose text.
- **Still learning:** Raised Surface background with Soft Ink text and a small Review Amber dot when needed.
- **Do not:** Use raw percentages, all-caps labels, or multiple colors for the same meaning.

### Cards / Containers
- **Corner Style:** `14px` radius for main cards, `10px` for nested-but-not-card controls such as upload rows and editable fields.
- **Background:** Main cards use Worktable Surface. Subpanels use Raised Surface. Inputs remain True White.
- **Shadow Strategy:** Flat at rest; Lifted State only on interaction.
- **Border:** Quiet Border, 1px, full perimeter only. No colored side stripes.
- **Internal Padding:** `24px` on desktop, `20px` on tablet, `16px` on mobile. Hero card can use `32px` desktop padding.

### Inputs / Fields
- **Text Area:** Large, calm, and inviting. Minimum desktop height `180px`; mobile height `150px`. Placeholder text must meet body contrast and should read like an invitation, not a command.
- **Focus:** Primary border plus Focus Ring. The field should feel selected, not alarmed.
- **Error / Disabled:** Errors use Risk Red text with a plain-language recovery path. Disabled fields should explain why they are disabled when the reason is not obvious.
- **Editable Findings:** Low-confidence document findings render as editable cards, each with one field, current value, source label, and `Looks right` / `Edit` actions.

### Navigation
- **Style:** Very little chrome. Use a narrow top area for product name, session status, and a calm save indicator if needed.
- **Active State:** Use text weight and a small semantic chip, not heavy nav bars.
- **Mobile Treatment:** Collapse supporting context under the main guided flow. Never hide the active question behind a dashboard navigation layer.

### Hero Intake Card
- **Purpose:** First screen anchor. It should make starting feel easy.
- **Copy:** Heading `Tell us about your business.` Subtext `Come as you are. We'll figure it out together.`
- **Structure:** Heading, subtext, large story text area, document upload button, recent uploads row/list.
- **Behavior:** Users can type first, upload first, or do both. Recent uploads stay visible but secondary.

### Understanding Card
- **Purpose:** Show what the system believes it has understood without sounding final too early.
- **Copy:** Heading `We think we understand the following`.
- **Rows:** One fact per row, paired with a status chip: `Confident`, `Needs clarification`, or `Still learning`.
- **Behavior:** Each row can be corrected. Clarification rows can lead directly into one focused follow-up card.

### Follow-up Question Card
- **Purpose:** Ask one concept at a time.
- **Controls:** Prefer radio buttons, segmented controls, and select menus. Avoid giant forms.
- **Required Element:** Collapsed `Why we're asking` disclosure with plain-language context.
- **Submit Copy:** `Continue`, never `Submit`.

### Recommendation Journey
- **Purpose:** Make convergence visible without false precision.
- **Steps:** `Still learning` -> `Getting clearer` -> `Ready for recommendation`.
- **Behavior:** Current step is highlighted with Guided Rose. Completed steps use Steady Teal. Future steps use Soft Ink.
- **Do not:** Show confidence percentages, score dials, or technical model language.

### Document Review
- **Purpose:** Convert uploaded documents into reviewable facts.
- **Heading:** `We found these details`.
- **Structure:** Separate `Confident findings` from `Needs review`.
- **Behavior:** Never dump raw OCR text. Low-confidence fields become editable cards with source hints and correction controls.

### Recommendation Screen
- **Purpose:** Give the user a clear next step without judgment.
- **Headline:** `We have a recommendation.`
- **Content:** Business type, why, assumptions, and questions that influenced the recommendation.
- **Primary Action:** `Continue`.
- **Tone:** Guided and careful. Mention assumptions and correction paths near the recommendation, not hidden at the bottom.

## 6. Do's and Don'ts

### Do:
- **Do** make the hero intake card the first-viewport focus with `Tell us about your business.` and `Come as you are. We'll figure it out together.`
- **Do** render A2UI output as product components: cards, controls, review rows, disclosures, and journey steps.
- **Do** ask one follow-up concept per card and use `Continue` for progression.
- **Do** include `Why we're asking` on every follow-up question.
- **Do** separate document findings into `Confident findings` and `Needs review`.
- **Do** make low-confidence document fields editable.
- **Do** support reduced motion with instant or crossfade alternatives.
- **Do** keep body text at or above WCAG AA contrast and use Deep Ink for most readable text.

### Don't:
- **Don't** use jargon-heavy tax software language or dense IRS-style phrasing.
- **Don't** make the interface look like a chatbot transcript, show chat bubbles, or add avatars.
- **Don't** show giant forms. Break complexity into focused A2UI cards.
- **Don't** show confidence percentages such as `67% confidence`.
- **Don't** dump raw OCR text into the UI.
- **Don't** use `Submit` as the primary progression label.
- **Don't** use decorative glassmorphism, gradient text, colored side stripes, or repeating stripe backgrounds.
- **Don't** over-round cards beyond `14px` or use full-pill shapes for cards and fields.
- **Don't** hide assumptions, missing facts, or correction paths.
- **Don't** use color alone to communicate status.
