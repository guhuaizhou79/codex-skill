# Comic and sequential art workflow

Load this reference when the request is for comics, manga, webtoons, comic strips, knowledge comics, character sheets, or any multi-image set where the same character must stay visually consistent.

## Choose the scope

| Scope | Use when | Primary output |
|------|----------|----------------|
| Character sheet | The same character will appear more than once | One reference sheet or turnaround |
| Comic panel | One decisive beat or splash image is enough | One panel or cover-like image |
| Comic page | Multiple beats must appear in one image | One strip, page, or webtoon slice |
| Knowledge comic | Source material must be adapted into pages | Storyboard, prompts, and page images |

## Recommended production order

1. Define the story goal, audience, and output scope.
2. Lock style, tone, and layout before writing page prompts.
3. Write canonical character anchors for every recurring character.
4. Generate a character reference sheet if the same character appears in more than one final image.
5. Write page briefs with panel beats and text plan.
6. Render one page at a time, always reusing the same character sheet or anchor block.
7. Save the selected finals and the intermediate planning files in the workspace if the comic is project-bound.

## Style, tone, and layout shortcuts

| Content signal | Recommended direction |
|----------------|-----------------------|
| Tutorial, how-to, technical explainer | `manga` or clean-line educational style, neutral tone, dense or webtoon layout |
| Biography, balanced storytelling | `ligne-claire`, neutral tone, mixed layout |
| Personal journey, mentor story | `ligne-claire`, warm tone, standard layout |
| Historical or pre-1950 subject | `realistic`, vintage tone, cinematic layout |
| Romance, school-life, emotional beats | `manga`, romantic tone, standard layout |
| Martial arts, wuxia, intense action | `ink-brush`, action tone, splash-heavy layout |

Use these as defaults only. Explicit user choices override them.

## Character anchor template

Keep a short canonical block for each recurring character and repeat it verbatim across prompts:

```text
Character: <name>
Role: <protagonist / mentor / rival / narrator>
Face and hair: <face shape, hair color/style, notable features>
Eyes and expression baseline: <shape, default affect>
Build and silhouette: <height, body type, posture>
Outfit and palette: <default clothing, key colors>
Signature prop: <glasses, satchel, weapon, notebook, etc.>
Must keep: <features that should never drift>
Allowed variation: <expressions, pose changes, weather, damage, etc.>
```

## Character reference sheet guidance

- Use a plain or lightly textured background.
- Prefer front, 3/4, side, and back views for design-heavy characters.
- Add an expression row when emotion range matters.
- Keep costume details, palette, silhouette, and accessories identical across views.
- If the built-in path supports reference-image reuse for later generations, use the finished character sheet as the reference image on every page.
- If you cannot rely on reference-image reuse, paste the same character anchor block into every page prompt.

## Page brief template

Draft the page before rendering:

```text
Page goal: <core message or dramatic beat>
Layout: <standard / cinematic / dense / splash / mixed / webtoon>
Panel count: <N>
Reading order: <left-to-right, top-to-bottom / vertical scroll>
Panel 1: <scene, shot type, action, emotional beat>
Panel 2: <scene, shot type, action, emotional beat>
Panel 3: <scene, shot type, action, emotional beat>
Text plan: <blank balloons / verbatim text / no text>
Character anchors: <which recurring characters appear>
Consistency reminders: <what must stay stable across panels>
```

## Text strategy

- If exact dialogue or typography matters, prefer blank balloons or minimal placeholder text and add final lettering later.
- If the model must render text, keep it short, quote it verbatim, and separate narration from dialogue.
- For educational comics, reserve exact labels for the most important terms only.

## Workflow modes for iteration

Use these modes conceptually even when the tooling is manual:

- `storyboard-only`: when the story structure is still moving.
- `prompts-only`: when the outline is stable but art direction needs review.
- `pages-only`: when prompts are locked and only rendering remains.
- `regenerate page N`: when a specific page needs fixes without redoing the whole comic.

## Suggested project layout

```text
comic/<topic-slug>/
  storyboard.md
  characters.md
  characters.png
  prompts/
    00-cover.md
    01-page.md
    02-page.md
  00-cover.png
  01-page.png
  02-page.png
```

Keep filenames stable. Save the planning files alongside final images so later edits remain reproducible.
