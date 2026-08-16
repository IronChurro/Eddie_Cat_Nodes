"""
VideoMerger — takes one video at a time from VideoFeeder (in a batch
driven by LongScheduler + LongSchedulerAdvance, same pattern as the rest
of this pack), checks it against the batch's reference size (locked from
the first video), and either passes it through or raises a clear error
if it doesn't match. Audio is either taken from the video itself or
sliced out of a separate master audio track so a full song can play
underneath the whole merged sequence without breaks at segment
boundaries.

WHY THERE'S NO PAD/CROP HERE:
There used to be one. It was removed on purpose. Conforming a
mismatched video's size means decoding it to raw float32 frames and
holding both the original and the resized copy in memory at once --
there's no way to avoid that once pixels need to be touched. Even a
short 5-10 second clip at 720p-1080p already sits at or past a couple of
GB doing this; a multi-minute clip is 25-200+ GB depending on
resolution. That's not a reasonable cost for what is, in effect, a
convenience feature. If your clips don't already share a resolution, use
an external tool to conform them before feeding them into Video Feeder.

Rejecting a mismatched video is nearly free: get_dimensions() is a cheap
metadata read that doesn't decode any frames, so a video that gets
rejected here never pays the decode cost at all -- only videos that
actually match the reference size get decoded (via get_components(),
which is unavoidable at that point since we need frame_rate and audio
regardless).

ARCHITECTURE NOTE -- why this needs its own locked state:
Each video in the batch is a separate queued prompt execution (same
constraint as everywhere else in this pack: ComfyUI runs a graph once
per Queue click, so nothing here can span multiple runs by itself). The
"reference" width/height/frame_rate get decided once, from the first
video, and need to stay fixed for every video after that -- and the "how
many seconds of the master track have already been used" figure needs
to keep advancing. Both live in Merge_State, a small JSON blob that this
node reads on every run and LongSchedulerAdvance copies forward (after
being updated by Merge_State_Out) into the next run, the exact same
one-directional mechanism already used for LongScheduler's own
Total_Segments_Lock.

Video_Number / Total_Videos are NOT independently tracked here --
they're meant to be wired straight from LongScheduler's Current_Segment /
Total_Segments outputs (or typed in manually for standalone use without
LongScheduler at all) and are just passed through as this node's own
outputs, matching the names requested for this node specifically.

FILENAME_PREFIX / RENDER_ID: to let LongSchedulerAdvance find and
concatenate all of this batch's segment files into one final movie once
the batch finishes, this node generates a unique render_id on video #1
(carried forward in Merge_State like everything else) and outputs
Filename_Prefix, which embeds that render_id plus the segment number.
Wire Filename_Prefix into your Video Combine node's filename_prefix
input -- this is what makes each segment's output file unambiguous and
in order, so LongSchedulerAdvance can glob for exactly this batch's
files (not some unrelated file, and not a previous batch's leftovers)
without needing any new links beyond Video_Merge_State, which you're
already wiring for the state-locking.

No VAE input: nothing here works with LATENT tensors -- video and audio
stay in pixel/waveform space throughout, so there's nothing for a VAE to
decode.
"""

import json
import logging
import uuid

import torch

try:
    from comfy_api.latest._input_impl.video_types import VideoFromComponents, VideoComponents
except ImportError:
    from comfy_api.input_impl.video_types import VideoFromComponents, VideoComponents


def _parse_state(raw: str) -> dict:
    try:
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _normalize_audio(audio):
    """Returns (waveform[1,C,T] tensor, sample_rate)."""
    if audio is None:
        return None, None
    waveform = audio.get("waveform") if isinstance(audio, dict) else None
    sample_rate = audio.get("sample_rate") if isinstance(audio, dict) else None
    if waveform is None or sample_rate is None:
        return None, None
    if not torch.is_tensor(waveform):
        waveform = torch.as_tensor(waveform)
    if waveform.ndim == 2:
        waveform = waveform.unsqueeze(0)
    return waveform, int(sample_rate)


def _slice_audio(audio, start_seconds: float, duration_seconds: float):
    waveform, sample_rate = _normalize_audio(audio)
    if waveform is None:
        return None
    start_frame = max(0, int(round(start_seconds * sample_rate)))
    end_frame = min(waveform.shape[-1], start_frame + max(1, int(round(duration_seconds * sample_rate))))
    if start_frame >= waveform.shape[-1]:
        # Master track has run out -- hand back silence for this segment
        # rather than erroring, so a slightly-short custom track doesn't
        # crash the whole batch.
        silent_frames = max(1, int(round(duration_seconds * sample_rate)))
        return {"waveform": torch.zeros((1, waveform.shape[1], silent_frames), dtype=waveform.dtype), "sample_rate": sample_rate}
    return {"waveform": waveform[..., start_frame:end_frame], "sample_rate": sample_rate}


class VideoMerger:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "Video_in": ("VIDEO", {"tooltip": "One video for this run, typically from VideoFeeder."}),
                "Video_Number": (
                    "INT",
                    {
                        "default": 1,
                        "min": 1,
                        "max": 999999,
                        "step": 1,
                        "tooltip": "Which video this run represents. Wire from LongScheduler's Current_Segment, or type manually for standalone use.",
                    },
                ),
                "Total_Videos": (
                    "INT",
                    {
                        "default": 1,
                        "min": 1,
                        "max": 999999,
                        "step": 1,
                        "tooltip": "Total videos in this batch. Wire from LongScheduler's Total_Segments, or type manually for standalone use.",
                    },
                ),
                "Custom_Audio": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "False: use each video's own audio. True: use Custom_Aud_In instead, sliced to match this video's position in the batch so a full track plays without gaps.",
                    },
                ),
            },
            "optional": {
                "Custom_Aud_In": ("AUDIO", {"tooltip": "Master audio track, only used when Custom_Audio is True."}),
                "Filename_Prefix_Base": (
                    "STRING",
                    {
                        "default": "video/EddieCatMerge",
                        "tooltip": "Base name for segment output files. Wire this node's Filename_Prefix output into your Video Combine node's filename_prefix so LongSchedulerAdvance can find and concatenate all segments at the end.",
                    },
                ),
                "Merge_State": (
                    "STRING",
                    {
                        "default": "{}",
                        "multiline": True,
                        "tooltip": "Internal JSON state (reference size, frame rate, elapsed time, render id) — managed by LongSchedulerAdvance, not meant to be hand-edited.",
                    },
                ),
                "Scheduler_ID": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Only needed with multiple Long Scheduler pairs in one workflow -- must match the corresponding Long Scheduler's Scheduler_ID.",
                    },
                ),
                # IMPORTANT: new inputs must always be added at the END of
                # this optional list, never inserted earlier. ComfyUI
                # stores saved widget values as a plain positional array,
                # not by name -- inserting something in the middle shifts
                # every later value onto the wrong widget when an existing
                # saved workflow is reloaded. This one bug already caused
                # exactly that (Filename_Prefix_Base/Merge_State/
                # Scheduler_ID all landing one slot off) -- don't repeat it.
                "Fresh_Start": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "True (default): always start a new batch from video #1, ignoring whatever Video_Number/Merge_State currently show -- no manual reset needed after an error or between runs. Long Scheduler Advance sets this to False internally while auto-continuing a batch; you generally never need to touch it yourself.",
                    },
                ),
            },
        }

    RETURN_TYPES = ("VIDEO", "IMAGE", "AUDIO", "INT", "INT", "FLOAT", "STRING", "STRING")
    RETURN_NAMES = ("Video", "Images", "Audio", "Video_Number", "Total_Videos", "Frame_Rate", "Filename_Prefix", "Merge_State_Out")
    FUNCTION = "merge"
    CATEGORY = "video/scheduling"

    def merge(
        self,
        Video_in,
        Video_Number,
        Total_Videos,
        Custom_Audio=False,
        Custom_Aud_In=None,
        Filename_Prefix_Base="video/EddieCatMerge",
        Merge_State="{}",
        Scheduler_ID="",
        Fresh_Start=True,
    ):
        total_videos = max(1, int(Total_Videos))

        # Fresh_Start is the whole fix: it's an explicit signal, not a
        # guess based on Video_Number/Merge_State's current contents (a
        # stale Video_Number=1 combined with a stale non-empty Merge_State
        # from an old, different batch would be genuinely ambiguous to
        # detect otherwise). Defaulting True means a plain manual Run
        # always starts clean with zero chance of accidentally inheriting
        # a previous batch's locked size/render_id/elapsed time --
        # Long Scheduler Advance is the only thing that ever sets this to
        # False, and only on its own internal continuation edits.
        is_first = bool(Fresh_Start)
        video_number = 1 if is_first else max(1, int(Video_Number))

        state = _parse_state(Merge_State) if not is_first else {}

        # Cheap metadata read -- does not decode any frames. Checking size
        # here means a mismatched video is rejected before it ever pays
        # the decode cost.
        native_w, native_h = Video_in.get_dimensions()

        if is_first:
            ref_w, ref_h = native_w, native_h
        else:
            ref_w = int(state.get("ref_w", native_w))
            ref_h = int(state.get("ref_h", native_h))
            if (native_w, native_h) != (ref_w, ref_h):
                raise ValueError(
                    f"VideoMerger: video #{video_number} is {native_w}x{native_h}, but this batch is "
                    f"locked to {ref_w}x{ref_h} (from video #1). Conform this clip to {ref_w}x{ref_h} "
                    f"with an external tool before feeding it into Video Feeder -- automatic pad/crop "
                    f"was removed here because it requires decoding the full clip to raw frames, which "
                    f"gets expensive fast (a 5-10 second 1080p clip alone can need several GB)."
                )

        # From here on the video matches the locked reference size, so
        # decoding is worthwhile -- frame_rate and audio are needed
        # regardless of size, and no resize work happens on the frames.
        components = Video_in.get_components()
        images = components.images
        native_frame_rate = float(getattr(components, "frame_rate", 0) or 0) or 24.0
        this_video_duration = images.shape[0] / native_frame_rate

        if is_first:
            ref_frame_rate = native_frame_rate
            elapsed_seconds = 0.0
            render_id = uuid.uuid4().hex[:10]
            filename_prefix_base = Filename_Prefix_Base
        else:
            ref_frame_rate = float(state.get("frame_rate", native_frame_rate))
            elapsed_seconds = float(state.get("elapsed_seconds", 0.0))
            # render_id and the base prefix are locked from video #1, same
            # as ref_w/ref_h/frame_rate -- every segment in a batch needs
            # to share them for LongSchedulerAdvance to find them all
            # together at the end.
            render_id = state.get("render_id") or uuid.uuid4().hex[:10]
            filename_prefix_base = state.get("filename_prefix_base") or Filename_Prefix_Base

        if Custom_Audio:
            if Custom_Aud_In is None:
                raise ValueError("VideoMerger: Custom_Audio is True but Custom_Aud_In was not provided.")
            chosen_audio = _slice_audio(Custom_Aud_In, elapsed_seconds, this_video_duration)
        else:
            chosen_audio = components.audio

        out_components = VideoComponents(images=images, audio=chosen_audio, frame_rate=ref_frame_rate)
        out_video = VideoFromComponents(out_components)

        filename_prefix = f"{filename_prefix_base}_{render_id}_seg_{video_number:04d}"

        next_state = {
            "ref_w": ref_w,
            "ref_h": ref_h,
            "frame_rate": ref_frame_rate,
            "elapsed_seconds": elapsed_seconds + this_video_duration,
            "total_videos": total_videos,
            "render_id": render_id,
            "filename_prefix_base": filename_prefix_base,
        }

        next_state_json = json.dumps(next_state)

        # If a later segment fails (a size mismatch, ComfyUI closing, any
        # interruption), the widgets on this node stay at whatever they
        # were before the batch started -- Advance's edits only ever live
        # in an in-memory copy of the prompt, never written back to the
        # canvas. This is the only durable record of "how far did we get"
        # -- to resume by hand, set Video_Number to the number shown here
        # for the NEXT segment (this logged value + 1) and paste this
        # Merge_State in.
        logging.info(
            "[VideoMerger] Completed video #%d/%d. To resume from the next segment by hand: "
            "set Video_Number to %d and Merge_State to: %s",
            video_number, total_videos, video_number + 1, next_state_json,
        )

        return (
            out_video,
            images,
            chosen_audio,
            video_number,
            total_videos,
            ref_frame_rate,
            filename_prefix,
            next_state_json,
        )


NODE_CLASS_MAPPINGS = {
    "VideoMerger": VideoMerger,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "VideoMerger": "Video Merger",
}
