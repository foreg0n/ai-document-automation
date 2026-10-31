# Product design system

AI Document Automation is a quiet workspace for three decisions: upload the
right document, check its extracted values, and export the saved result.
The interface uses brightness levels and thin boundaries for depth. There are
no gradients, glass effects, decorative illustrations, or shadow stacks.

## Open the working reference

Start the normal workspace, then visit
[http://127.0.0.1:8765/design-system](http://127.0.0.1:8765/design-system).

```powershell
.\.venv\Scripts\python.exe -m document_automation serve --demo --port 8765
```

The reference shares production CSS, fonts, icons, and tokens. It contains all
16 requested components and their eight states, four screen-state patterns,
button variants, and explicitly illustrative confidence examples. It never
changes real documents. Download the
[machine-readable tokens](../document_automation/web_static/design-tokens.json)
from the reference footer or read the file directly.

## Color and contrast

| Token | Value | Use |
| --- | --- | --- |
| Canvas | `#0B0C0E` | Page and source-text canvas |
| Surface | `#121316` | Tables, cards, and form panels |
| Raised | `#17191D` | Menus, dialogs, and hover surfaces |
| Border | `#24272D` | Quiet structural boundaries |
| Text | `#ECEDEF` | Main reading content |
| Secondary | `#A1A6B0` | Supporting labels |
| Muted | `#7C828D` | Hints and metadata; never dimmed with opacity |
| Control border | `#666D7A` | Inputs that need a perceptible boundary |
| Accent | `#8B7CFF` | Main action, current navigation, and keyboard focus |
| Success | `#3DD68C` | Completed or confirmed status with a check icon |
| Warning | `#F5A524` | Review needed, missing values, or ambiguous examples |
| Error | `#FF6B6B` | Failed actions and invalid formats with recovery text |

All seven text/semantic colors exceed 4.5:1 on the three surface levels.
The lowest pair is muted on raised: **4.5546:1**. Main text on the page is
**16.70:1**. Primary buttons use the dark canvas color for text on indigo,
producing **5.9869:1**; pale text on that button would not meet the requirement.
The control boundary is a separate neutral because the subtle structural
border alone does not provide enough contrast to identify an input.

These are computed color ratios, not a claim of full WCAG certification.
The check uses the W3C relative-luminance formula and does not round a failing
ratio up to a passing value. See the
[contrast criterion](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).
Original PDFs keep their own document colors inside the browser preview.

## Type, spacing, and motion

| Foundation | Rule |
| --- | --- |
| Interface | Self-hosted Inter, weights 400 / 500 / 600 |
| Data | Self-hosted JetBrains Mono for dates, amounts, IDs, and source text |
| Type scale | 12 / 14 / 16 / 20 / 28 / 40px |
| Body | 16px, line-height 1.5, paragraph measure up to 70ch |
| Supporting text | 12px metadata and 14px labels, with the same contrast floor |
| Spacing | 8px grid; 4px adjustments for icons and small groups |
| Radius | 8px controls; 12px cards, menus, and dialogs |
| Targets | At least 44px; primary buttons are 48px high |
| Focus | 2px indigo outline, 3px offset |
| Motion | 150–200ms ease-out fades and state transitions |
| Reduced motion | Instant state changes; static skeletons |

The original font licenses ship with the files. No Google Fonts/CDN request
is made when opening the interface. See [font provenance](../document_automation/web_static/fonts/README.md).

## Components and states

The catalogue has five groups, with no more than four component choices in a
group. Each selected component renders default, hover, focus-visible, pressed,
disabled, loading, error, and success specimens. Static data components apply
action states to their container or surrounding region.

| Component | Production behavior |
| --- | --- |
| Button | Primary, secondary, ghost, and destructive variants; a named busy state |
| Input | Label above, format hint below, recoverable inline errors on blur |
| Select | Native keyboard behavior; disabled choices explain availability |
| Checkbox | 44px label target; source acknowledgement gates confirmation |
| Switch | Component reference for optional preferences; no invented product setting |
| Tabs | Text/PDF preview; selected state, arrow keys, Home, and End |
| Table | Eight rows per page; real data only, no placeholder invoices |
| Card | Surface and border grouping; no decorative shadow |
| Badge / Status | Icon plus text, never color alone |
| Dropzone | Choose, drag, selected-file, busy, and recovery states |
| Progress | Actual upload bytes and server-reported processing stages |
| Toast | Named result; Undo remains available for 10 seconds and pauses on focus/hover |
| Modal | Native dialog focus containment, Escape/Cancel, concrete action verbs |
| Tooltip | Native product titles and accessible reference examples |
| Sidebar | Three edge-aligned navigation choices; mobile bottom navigation |
| Empty state | Clear next step, contextual copy, no decorative artwork |

Disabled actions explain their cause in adjacent text or a title. Loading
buttons name the work rather than showing a spinner without context. Error
states keep saved data and provide a retry or a concrete correction.

## Screens and decision hierarchy

### Documents

At 1440px the sidebar is 232px wide, the top bar is 80px high, and the content
gutter is 40px. Search is in the top bar; document status, amount, and date live
in a single table. Three overview controls are also useful filters. The only
primary action is **Upload document**. Empty and failed list states are distinct.
Six skeleton rows hold the table structure while loading. At most eight actual
documents are displayed; the included demo has four unique records.

### Upload

The centered dropzone offers **Choose PDF**. After selecting a file, that
button becomes secondary and **Extract data** becomes the primary action.
The file can be removed and restored with Undo. Dragging multiple files gets
a clear one-file-at-a-time message.

Upload progress uses real transferred bytes. Subsequent stages use the actual
job state: queued, reading, extracting, validating/reusing, and ready. The UI
does not invent percentages for an external model request. After six minutes,
polling exposes **Check status**, preserving the job ID to avoid another upload.
No fictional OCR stage is presented for the current text-only reader.

### Review

The source is on the left and five fields are on the right. Source text uses
16px mono type. Each field has a label, hint, and **Find in source** action.
The first literal text match is highlighted; common amount/date formatting
variants are tried. The caption names the page and tells the user to check
context. A match is not proof that the right fact was extracted. No PDF
bounding boxes or text coordinates are fabricated. Input focus scrolls only the
source panel, preserving the form position. On stacked layouts, an explicit
lookup provides **Back to field** to return from the source.

The footer keeps **Save draft** secondary and **Confirm & export** primary.
The latter checks acknowledgement, saves a revision, and downloads only that
document as CSV by default. **More → Download format** can choose JSON without
leaving the review. Remaining checks require a note. A download failure clearly
states that the review was saved. Undo appends a new revision restoring the
previous values and confirmation flag, with the same optimistic revision guard.
Restoring initially unreviewed values leaves an unconfirmed audit revision. It does not
remove an already downloaded file.

History and the untouched original extraction are available through **More**.
Switching to another document with unsaved edits asks before discarding them.

### Export

Three format choices are visible: CSV, JSON, and unavailable API integration.
CSV and JSON work. The API choice states that no external connection is
configured. The default scope contains confirmed documents; all completed
records and the current document are explicit alternatives.

The server returns the eligible count, unconfirmed count, and up to five preview
rows. Downloading multiple records opens **Export N documents?**, with the
format and unconfirmed count. Cancel performs no download. A single confirmed
review can complete upload → review → export without an extra export screen.
Document deletion is not implemented; its destructive confirmation exists only
as a clearly marked component specimen.

## Confidence is not a formatting result

The current extraction contract contains five nullable strings and does not
return calibrated confidence scores. Production fields therefore say
**Not scored**, or **Check source** when a rule needs attention. The 98% and
61% examples are isolated in the component catalogue and labelled illustrative.
Never convert a regex check, text match, or duplicate cache hit into confidence.
OCR, contract extraction, and external push integrations are outside the
current application capabilities.

## Responsive behavior

| Width | Behavior |
| --- | --- |
| 1440px desktop | Sidebar, five-column document table, source and fields side by side |
| 1000–1200px | Narrower sidebar/gutters; secondary date column may hide |
| Below 1000px | Review source and fields stack; source remains directly above the form |
| Below 760px | Three-item bottom navigation; full-width upload; compact table with document and labelled status |
| 320px minimum | Controls wrap; long filenames and source lines break; core actions keep their target size |

Secondary amount/date columns are hidden on narrow lists; the full values
remain available in the review. There is no hover-only workflow. A skip link,
visible focus, native dialogs, labelled controls, and live status regions support
keyboard and assistive-technology use.

## Verification checklist

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m tools.check_design --output output/design-qa/accessibility.json
npm install --ignore-scripts
npm run test:ui
```

Node is only a development test tool; the product has no frontend build step.
The DOM tests use LinkeDOM and a controlled transport. They check primary-action
count, review acknowledgement, field recovery, source highlighting, save/Undo,
document-scoped download, bulk confirmation/cancel, JSON selection, upload stage
transitions, failure recovery, and all component/state specimens.

| Check | Evidence |
| --- | --- |
| First action is clear | Upload is the sole visible primary action on the dashboard; covered by a DOM check |
| Text contrast | All specified text/semantic pairs pass 4.5:1; primary action passes 5.98:1 |
| Body sizing and measure | 16px body/source; declared 70ch paragraph limit |
| Response to actions | Loading, success, retry, and Undo flows exercised with controlled requests |
| Concrete bulk confirmation | Count-specific label; Cancel verified to make no download |
| Consistent screens | Shared tokens, components, form actions, and state catalogue |
| Responsive layout and 44px geometry | CSS rules supplied; actual browser geometry remains to be checked |
| Keyboard/dialog and screen-reader experience | Semantics supplied; real assistive-technology QA remains to be run |
| Browser screenshots | Chrome/Edge cannot render successfully in this restricted environment; no screenshot QA is claimed |

The static checker validates computed contrast, local assets, icon references,
unique IDs, labels, CSP-compatible markup, and font headers. It does not measure
layout. Run an actual browser at 1440px, 1024px, 768px, and 390px to complete
visual and keyboard checks. The existing 14 HTTP tests also remain skipped
where loopback sockets are blocked. See [the recorded design audit](results/design-audit.json).

Verification recorded on 2026-11-01: 167 Python tests, with 153 passed and
14 HTTP tests skipped; all 15 DOM interaction tests passed. The local DOM run
used an offline copy of LinkeDOM 0.18.14 because package downloads are restricted
in this environment. The standard development command uses the same pinned
package. Edge exited with an access error before creating a screenshot, so
native browser layout and assistive-technology verification remain open.
