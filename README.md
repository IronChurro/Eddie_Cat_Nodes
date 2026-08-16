# Eddie_Cat_Nodes

A ComfyUI custom node pack for building auto-advancing, multi-segment
video render batches from a single set of image/prompt/video inputs. Six
nodes:

| Node | File | What it does |
|---|---|---|
| **Image Feeder** | `image_feeder.py` | Drag-and-drop grid of images; outputs a current/next pair by index |
| **Video Feeder** | `video_feeder.py` | Same idea, for video clips |
| **Prompt Feeder** | `prompt_feeder.py` | Same idea, for text prompts |
| **Long Scheduler** | `long_scheduler.py` | Reports per-segment image/timing info for a render batch |
| **Long Scheduler Advance** | `long_scheduler.py` | Sits at the end of the render chain; auto-requeues the workflow for the next segment, and advances Image/Video/Prompt Feeder and Video Merger to match |
| **Video Merger** | `video_merger.py` | Conforms each video in a batch to match the first one (size + frame rate), and hands off either each video's own audio or a sliced piece of a master track |

## Install

Copy the whole `Eddie_Cat_Nodes` folder into `ComfyUI/custom_nodes/` and
restart. No extra pip dependencies (everything used is already bundled
with ComfyUI: `torch`, `numpy`, `Pillow`, and ComfyUI's own `comfy_api`
video types).

```
Eddie_Cat_Nodes/
├── __init__.py            # merges all six nodes' registration into one package
├── image_feeder.py         # Image Feeder
├── video_feeder.py         # Video Feeder
├── long_scheduler.py       # Long Scheduler + Long Scheduler Advance
├── prompt_feeder.py        # Prompt Feeder
├── video_merger.py         # Video Merger
├── web/
│   ├── image_feeder.js     # drag/drop grid widget
│   ├── video_feeder.js     # drag/drop grid widget, video version
│   ├── long_scheduler.js   # hides Duration_N fields beyond Segment_Count
│   └── prompt_feeder.js    # hides Prompt_N fields beyond Prompt_Count
└── README.md
```

If you're extending this pack later: `__init__.py` merges each node
file's own `NODE_CLASS_MAPPINGS` / `NODE_DISPLAY_NAME_MAPPINGS` into one
combined dict. All nodes use this same classic registration style —
add a new node file the same way and merge its mappings in `__init__.py`.

## Quick-start wiring (images + prompts)

```
Image Feeder ──Image_Out_1──▶ Image_F ──┐
             └─Image_Out_2──▶ Image_L ──┤  Long Scheduler
                                         ├──Start_Image/End_Image──▶ your render pipeline
                                         └──Current_Segment, Total_Segments──▶ Long Scheduler Advance
                                                                                        ▲
Prompt Feeder ──Prompt_Out──▶ your text encoder            (save/output node) ──trigger┘
```

## Quick-start wiring (video merge batch)

```
Video Feeder ──Video_Out──▶ Video_in ──┐
                                        │  Video Merger
Long Scheduler ──Current_Segment───────┼──▶ Video_Number
               └─Total_Segments────────┼──▶ Total_Videos
                                        │
         Video (VIDEO) / Images (IMAGE) / Audio / Filename_Prefix / Merge_State_Out
                                        │                    │             │
                                        ▼                    ▼             ▼
               your Video Combine (Images+Audio+Frame_Rate, or Video)   Long Scheduler Advance
                              filename_prefix ◀──────────────┘          (Video_Merge_State input)
                                        │
                          (that Video Combine node's own output) ──trigger──▶ Long Scheduler Advance
```

Key rules:
- **Never** link `Current_Segment` into `Image_Index`, `Prompt_Index`, or
  `Output_Numb` — that creates a graph cycle. Leave all three unlinked;
  `Long Scheduler Advance` drives them directly.
- If you have more than one Long Scheduler / Advance pair in one
  workflow, give each pair (and its feeders / Video Merger) a matching
  `Scheduler_ID` so Advance updates the right ones.
- **Do** wire `Video Merger`'s `Merge_State_Out` into `Long Scheduler
  Advance`'s `Video_Merge_State` input — this is a real link (unlike
  `Scheduler_ID`, which is just a matching label), because `Merge_State`
  carries the locked reference size/frame-rate/elapsed-audio-time forward
  between segments and only exists as a runtime value.
- **Do** wire `Video Merger`'s `Filename_Prefix` output into your Video
  Combine node's `filename_prefix` input, and wire whatever Video
  Combine outputs (e.g. `VHS_VideoCombine`'s `Filenames`) into `Long
  Scheduler Advance`'s `trigger`. Together these are what let Advance
  find and stitch all of this batch's segment files into one final movie
  once the last segment finishes — see "Merging segments into one final
  video" below.

## Image Feeder

- Drag image files onto the node's grid to add them; click a thumbnail
  and hit its **×** button to remove it; drag one thumbnail onto another
  to reorder.
- `Image_Index` selects the current slot; `Image_Out_1`/`Image_Out_2` are
  that image and the next one. Past the last image: `Loop_Images` wraps
  to the start, or holds on the last image.
- Column count and thumbnail size scale with the node's width. Drag the
  small grip in the bottom-right corner of the grid (not the node's own
  corner) to change its height.
- Images are uploaded through ComfyUI's own `/upload/image` endpoint and
  referenced by filename — not embedded in the workflow file. Moving a
  saved workflow to another machine requires those files to exist in its
  `input` folder too.

## Video Feeder

Mirrors Image Feeder as closely as the underlying media allows:

- Drag video files onto the grid to add them; **×** to remove; drag to
  reorder — identical mechanics to Image Feeder, same code adapted.
- Shows a first-frame still as the thumbnail (seeks to just after time 0
  once the browser loads the video's metadata — no server-side thumbnail
  generation involved).
- `Output_Numb` selects the current slot; `Loop_Videos` wraps to the
  start or holds on the last video, same semantics as `Loop_Images`.
- **Does not decode video at all.** The selected file is wrapped in
  ComfyUI's `VideoFromFile` — a lazy reference to the file path — so
  holding videos in the grid costs nothing in memory regardless of how
  long they are. Actual decoding only happens downstream, in whatever
  node consumes the video (typically Video Merger).
- **Assumption worth knowing**: uploads reuse ComfyUI's `/upload/image`
  endpoint (same one Image Feeder uses), since it saves whatever file
  it's given regardless of the field name — this matches how ComfyUI's
  own built-in Load Video node's upload button is understood to work. If
  your ComfyUI build rejects video files there, that's the first thing
  to check; tell me what error you see and I'll adjust.

## Prompt Feeder

- `Prompt_Count` shows/hides `Prompt_1..Prompt_32` text fields.
- `Prompt_Index` selects which one becomes `Prompt_Out`. Same
  loop/hold-on-last behavior as Image Feeder, via `Loop_Prompts`.

## Long Scheduler

- Feed it `Image_F`/`Image_L` (typically from Image Feeder) plus
  `Segment_Count` and a `Duration_N` per segment (seconds).
- Outputs `Start_Image`/`End_Image` (passthrough), `Current_Segment`,
  `Total_Segments` (fixed for the whole batch), `Segment_Duration_Secs`,
  and `Time_Position_Sec` (elapsed time from the batch's start segment,
  for audio sync).
- `Start_Segment` is both "where this run starts" (editable before
  pressing Run) and "which segment this run is currently on" (advanced
  automatically between segments) as long as the auto-continuing chain
  keeps succeeding. **This does not mean pressing Run again after an
  error or interruption resumes where you left off** — Advance's edits
  only ever live in an in-memory copy of the prompt for the next queued
  run; they're never written back to the widgets shown on the canvas. If
  a segment fails partway through a batch, `Start_Segment` on the node
  you're looking at still shows whatever it was *before* the batch
  started, not wherever it actually got to. To resume by hand, check the
  console/log for the last `[LongScheduler]`/`[VideoMerger]` line before
  the failure and copy those values in yourself — see "Resuming after an
  error" below. To start a genuinely fresh batch, reset `Start_Segment`
  to `1` and `Total_Segments_Lock` back to `0`.

### Resuming after an error or interruption

If a segment fails (a mismatched video, closing ComfyUI, anything), the
batch doesn't pick back up automatically — there's no queued copy left
holding "where we got to," only the canvas widgets, which were never
touched. To resume by hand:

1. Check the console/log (or `comfyui.log`) for the last successful
   segment's log line — `Video Merger` logs a line like `Completed video
   #2/3. To resume from the next segment by hand: set Video_Number to 3
   and Merge_State to: {...}` after every run, and `Long Scheduler
   Advance` logs its own status the same way.
2. Fix whatever caused the failure (e.g. conform the mismatched clip).
3. Manually set the values that line gave you — `Video_Number`/
   `Merge_State` on Video Merger (and set its `Fresh_Start` to `False`,
   since that now defaults to `True` and would otherwise ignore the
   values you just pasted in), or `Start_Segment`/`Total_Segments_Lock`
   on Long Scheduler — directly on the node's widgets.
4. Press Run.

This is a manual step, not automatic — flagging that plainly rather than
implying otherwise, since automatically restoring canvas widget state
from a failed run isn't something this pack currently does.

## Long Scheduler Advance

Place at the very end of your render/save chain. After each segment
renders, it:

1. Grabs the currently-running prompt (the exact graph ComfyUI just
   executed), makes an in-memory copy.
2. If a matching Long Scheduler node exists, bumps its
   `Start_Segment`/`Total_Segments_Lock`. **Long Scheduler is optional**
   — if `Current_Segment`/`Total_Segments` are being driven some other
   way (e.g. Video Merger used standalone, with its own
   `Video_Number`/`Total_Videos` typed in directly instead of linked
   from a Long Scheduler), that's a valid setup and this step is simply
   skipped, not an error.
3. Finds a linked Image Feeder (by tracing Long Scheduler's
   `Image_F`/`Image_L` — only applicable if a Long Scheduler was found)
   and/or a Prompt Feeder (by matching `Scheduler_ID`) and advances their
   indexes.
4. Finds a matching Video Merger (by `Scheduler_ID`) and advances its
   `Video_Number`/`Total_Videos` directly (this is what makes Video
   Merger work with or without a Long Scheduler in the loop), carries
   its `Merge_State` forward, and traces *its* `Video_in` link to
   advance a linked Video Feeder the same way.
5. Pushes the modified copy directly onto ComfyUI's own prompt queue --
   *unless* this was the last segment, in which case it merges all of
   this batch's video segments into one final file instead (see below).

This is entirely server-side — no frontend hooking, no relying on
clicking Run again. Its status text after each run confirms what it
found and updated, including whether any feeder or Video Merger couldn't
be matched, so wiring problems show up immediately instead of silently
not advancing.

`trigger` accepts any type — connect it to whatever finishes last in your
save/output chain, purely to force execution order. In a video-merge
batch, that's whatever your Video Combine node outputs (e.g.
`VHS_VideoCombine`'s `Filenames`) -- it only produces a value once the
file has actually finished writing, which is exactly the ordering
guarantee `trigger` needs. `Enabled` lets you render a single segment
without auto-continuing, for testing.

## Merging segments into one final video

Each video in the batch is a separate queued execution, so your Video
Combine node writes one file per segment -- there's no way around that
given how the batching works. To turn those separate files into the one
final movie you actually want, `Long Scheduler Advance` does the merging
itself once the last segment finishes, using ffmpeg (the same tool your
Video Combine node almost certainly already uses).

**Setup**: wire `Video Merger`'s `Filename_Prefix` output into your
Video Combine node's `filename_prefix` input. That's the only extra
wiring needed -- `Video_Merge_State` (which you're already wiring for
the size/frame-rate locking) carries everything else Advance needs to
find the files.

**What `Filename_Prefix` actually is**: `{your base name}_{render_id}_seg_{0001, 0002, ...}`.
The `render_id` is a random ID generated once for the whole batch (on
segment #1, carried forward the same way reference size is), so files
from this batch can never be confused with files from a previous batch
or an unrelated node -- Advance globs specifically for
`*{base}_{render_id}_seg_*` in your output folder.

**On the final segment**, Advance:
1. Globs for every file matching this batch's pattern and sorts them by
   the segment number embedded in the filename (not alphabetically --
   VHS_VideoCombine sometimes appends its own suffix after the segment
   number, e.g. a frame count, which alphabetical sorting could get
   wrong).
2. Concatenates them with ffmpeg into `{base}_{render_id}_final.{ext}`
   in the same folder, in the same directory as the segments. The
   video stream is copied (no re-encode, no quality loss); audio is
   re-encoded, because straight-copying audio across a concat boundary
   is what causes audible clicks/gaps at segment joins. If segments
   turn out not to be stream-copy compatible, it automatically falls
   back to a full re-encode.
3. If `Keep_Segments` is off, deletes the individual segment files
   after a successful merge.

If it can't find the expected number of files, or ffmpeg fails, the
status text says exactly what happened rather than silently producing a
partial or missing result -- check that text after the batch finishes.

`Merge_Segments` (default on) and `Keep_Segments` (default on) control
this; both are ignored if `Video_Merge_State` isn't wired at all (i.e.
you're not using Video Merger in this workflow).

## Video Merger

Takes one video per run (from Video Feeder, in a Long-Scheduler-driven
batch), checks it against the batch's reference size (locked from video
#1), and either passes it through or raises a clear error:

- **Two ways to feed your Video Combine node**, since different
  ComfyUI versions/node packs expect different things here:
  - **`Images` (IMAGE batch) + `Audio` + `Frame_Rate`** — this is what
    ComfyUI-VideoHelperSuite's `VHS_VideoCombine` node actually wants
    (its `images` input is a raw frame batch, not a `VIDEO`-typed
    socket). If that's what you have, wire these three, plus
    `Filename_Prefix` → `filename_prefix` if you want segments merged
    automatically (see above).
  - **`Video` (VIDEO type)** — for newer core nodes (e.g. `SaveVideo`)
    that take a single `VIDEO`-typed input instead. If that's what you
    have, wire this one and ignore `Images`.

  Both come from the exact same decoded frame tensor — `Images` isn't a
  separate copy or extra decode, it's the same data ComfyUI's `VIDEO`
  object already wraps, just exposed as a second socket so it matches
  whichever schema your actual Video Combine node uses.
- **No automatic resizing.** If a video's dimensions don't match the
  locked reference size, Video Merger raises an error naming both sizes
  and telling you to conform the clip externally before feeding it into
  Video Feeder. This was a deliberate choice, not a limitation to work
  around: pad/crop was in an earlier version of this node and was
  removed because conforming a mismatched video means decoding it to raw
  float32 frames — there's no way around that once pixels need to be
  touched. Even a short 5-10 second clip at 720p-1080p already needs a
  couple of GB doing this; a multi-minute clip needs 25-200+ GB
  depending on resolution. Rejecting a mismatch is nearly free by
  comparison: the size check reads video metadata only, so a rejected
  clip never gets decoded at all — only clips that actually match pay
  the decode cost, which is unavoidable anyway since frame rate and
  audio are needed regardless of size.
- **`Custom_Audio`**: `False` keeps each video's own audio track as-is.
  `True` instead pulls the matching time-slice out of `Custom_Aud_In` —
  the exact window of the master track that corresponds to this video's
  position in the sequence — so a full song plays underneath the merged
  result without gaps or restarts at each segment boundary.
- **`Frame_Rate`** output is locked from video #1's native frame rate
  and held fixed for the batch. Note: clips with a different native
  frame rate than video #1 are *not* resampled to match. If your clips
  already share a frame rate (typical when they're all rendered by the
  same pipeline) this doesn't matter; if they don't, the reported
  `Frame_Rate` may not exactly match every clip's real playback speed.
- **No VAE input.** Nothing here decodes LATENT tensors — video and
  audio stay in pixel/waveform space throughout, so there's nothing for
  a VAE to do.
- **`Merge_State`**: the mechanism that makes the locked reference size/
  frame-rate/elapsed-audio-time/render-id survive from one queued run to
  the next. Wire its `Merge_State_Out` into `Long Scheduler Advance`'s
  `Video_Merge_State` input — Advance copies it forward into the next
  run's `Merge_State` the same way it locks `Total_Segments` on Long
  Scheduler itself.
- **`Filename_Prefix`**: wire this into your Video Combine node's
  `filename_prefix` input if you want Long Scheduler Advance to
  automatically merge all segments into one final video -- see
  "Merging segments into one final video" above.
- **`Video_Number` / `Total_Videos`**: these are meant to be wired
  straight from Long Scheduler's `Current_Segment` / `Total_Segments`
  (or typed in manually if you're using Video Merger standalone, without
  Long Scheduler at all) — Video Merger just passes them through as its
  own outputs, matching the names you asked for.
- **`Fresh_Start`** (default `True`): every run ignores whatever
  `Video_Number`/`Merge_State` currently show and starts clean at video
  #1, generating a brand-new render ID — no manual reset needed, ever,
  after an error, a completed batch, or anything else. `Long Scheduler
  Advance` is the only thing that ever sets this to `False`, and only on
  its own internal continuation edits while actively auto-advancing a
  batch — you should essentially never need to touch this yourself.
- **Logs a resume line every run** — after each video, it logs the
  `Video_Number`/`Merge_State` you'd need if you ever wanted to force a
  manual resume anyway (set `Fresh_Start` to `False` yourself and paste
  these in) — see "Resuming after an error or interruption" under Long
  Scheduler above. This is now an opt-in fallback rather than something
  you need to remember to do.

