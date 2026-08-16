"""
LongScheduler — drives a multi-segment video render batch.

This is split into two nodes, because a single node's execute() can only
run once per queued prompt — it can't both report a segment's info *and*
decide whether to continue, since that decision can only be made after the
render for this segment has actually finished (i.e. downstream of where
the segment info was used).

  - LongScheduler: an ordinary node, placed early in the graph. Given
    Image_F/Image_L and the segment settings, it reports which segment is
    "current" and its timing info. Wire Current_Segment back into
    ImageFeeder's Image_Index and you get a self-advancing loop with a
    single set of image inputs.

  - LongSchedulerAdvance: an OUTPUT_NODE placed at the very end of the
    render chain (after your save/output step). It re-queues the workflow
    for the next segment by copying the currently-running prompt, bumping
    the LongScheduler node's start_segment, and pushing that copy directly
    onto ComfyUI's own prompt queue. This is server-side and does not rely
    on any frontend/JS hooking, using the same pattern as ComfyUI's own
    "loop" style nodes (grab the running prompt, edit a copy, re-enqueue).

Because LongSchedulerAdvance edits the LongScheduler node's *inputs* in
the copied prompt (not its widget as drawn in the browser), the widget
shown on screen won't visually update between segments — only the
computed values flowing through the graph change. That's expected; see
the README.
"""

import copy
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid

import folder_paths
import server


MAX_SEGMENTS = 32


class AnyType(str):
    """A type that always compares unequal-to-nothing, so it matches any
    ComfyUI socket type. Used for LongSchedulerAdvance's `trigger` input,
    which just needs to accept whatever your final save/output node
    produces so execution order is forced without caring about the type.
    """

    def __ne__(self, other):
        return False


ANY_TYPE = AnyType("*")


def _normalize_prompt_keys(prompt):
    if prompt is None:
        return None
    return {str(key): value for key, value in prompt.items()}


def _get_current_queue_item():
    prompt_server = getattr(server.PromptServer, "instance", None)
    if prompt_server is None or getattr(prompt_server, "prompt_queue", None) is None:
        raise RuntimeError("PromptServer prompt queue is unavailable")

    currently_running = getattr(prompt_server.prompt_queue, "currently_running", {})
    if not currently_running:
        raise RuntimeError("No currently running prompt was found")

    current = next(iter(currently_running.values()))
    if len(current) == 6:
        (_, _, prompt, extra_data, outputs_to_execute, sensitive) = current
    else:
        (_, _, prompt, extra_data, outputs_to_execute) = current
        sensitive = {}
    return prompt, extra_data, outputs_to_execute, sensitive


def _infer_output_nodes(prompt):
    import nodes as comfy_nodes

    normalized_prompt = _normalize_prompt_keys(prompt) or {}
    outputs = []
    for node_id, node in normalized_prompt.items():
        class_type = node.get("class_type")
        class_def = comfy_nodes.NODE_CLASS_MAPPINGS.get(class_type)
        if class_def is not None and getattr(class_def, "OUTPUT_NODE", False):
            outputs.append(str(node_id))
    return outputs


def _enqueue_prompt(prompt, extra_data=None, outputs_to_execute=None, sensitive=None):
    prompt_server = getattr(server.PromptServer, "instance", None)
    if prompt_server is None or getattr(prompt_server, "prompt_queue", None) is None:
        raise RuntimeError("PromptServer prompt queue is unavailable")

    prompt_queue = prompt_server.prompt_queue
    prompt = _normalize_prompt_keys(prompt)

    try:
        _, current_extra_data, current_outputs_to_execute, current_sensitive = _get_current_queue_item()
    except Exception as exc:
        logging.warning("[LongScheduler] Falling back to inferred queue metadata: %s", exc)
        current_extra_data = None
        current_outputs_to_execute = None
        current_sensitive = None

    if extra_data is None:
        extra_data = current_extra_data if current_extra_data is not None else {}
    if sensitive is None:
        sensitive = current_sensitive if current_sensitive is not None else {}
    if outputs_to_execute is None:
        outputs_to_execute = current_outputs_to_execute
    if outputs_to_execute is None:
        outputs_to_execute = _infer_output_nodes(prompt)
    if not outputs_to_execute:
        raise RuntimeError("No output nodes were found for the requeued prompt")

    number = -prompt_server.number
    prompt_server.number += 1
    prompt_id = str(uuid.uuid4())
    logging.info("[LongScheduler] Queueing next segment prompt %s", prompt_id)
    prompt_queue.put((number, prompt_id, prompt, extra_data, outputs_to_execute, sensitive))


def _find_ffmpeg():
    forced = os.environ.get("VHS_FORCE_FFMPEG_PATH")
    if forced and os.path.isfile(forced):
        return forced

    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        ffmpeg_path = get_ffmpeg_exe()
        if ffmpeg_path and os.path.isfile(ffmpeg_path):
            return ffmpeg_path
    except Exception:
        pass

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    return None


def _find_segment_files(render_id, filename_prefix_base, total_videos):
    """Locate every segment file this batch's VideoMerger produced, in
    order. Matched by the render_id embedded in Filename_Prefix (see
    video_merger.py) -- this is why VideoMerger's Filename_Prefix output
    needs to be wired into your Video Combine node's filename_prefix.
    """
    output_dir = folder_paths.get_output_directory()
    base_name = os.path.basename(filename_prefix_base)
    pattern = os.path.join(output_dir, "**", f"*{base_name}_{render_id}_seg_*")
    candidates = glob.glob(pattern, recursive=True)
    # Only real files, not directories glob might also match.
    candidates = [path for path in candidates if os.path.isfile(path)]

    seg_number_re = re.compile(r"_seg_(\d{4})")
    by_segment = {}
    for path in candidates:
        match = seg_number_re.search(os.path.basename(path))
        if not match:
            continue
        seg_number = int(match.group(1))
        by_segment.setdefault(seg_number, []).append(path)

    def _preference_key(path):
        # VHS_VideoCombine can write more than one file per run when
        # audio is present (e.g. a silent preview plus an "-audio"
        # version) -- prefer the "-audio" one deterministically (its own
        # docs note this is "the most complete output"), then fall back
        # to whichever file was written most recently, instead of
        # depending on glob's/the filesystem's arbitrary listing order.
        has_audio_tag = "-audio" in os.path.basename(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        return (has_audio_tag, mtime)

    ordered_paths = []
    for seg_number in sorted(by_segment.keys()):
        best = max(by_segment[seg_number], key=_preference_key)
        ordered_paths.append(best)
    return ordered_paths


def _concat_segments(segment_paths, output_path):
    ffmpeg_path = _find_ffmpeg()
    if ffmpeg_path is None:
        raise RuntimeError("ffmpeg was not found, so segment videos could not be concatenated.")

    missing_paths = [p for p in segment_paths if not os.path.exists(p)]
    if missing_paths:
        missing_lines = "\n".join(missing_paths)
        raise RuntimeError(f"Segment merge aborted because these files are missing:\n{missing_lines}")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        list_path = handle.name
        for segment_path in segment_paths:
            escaped = segment_path.replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")

    # Primary attempt: copy the video stream (fast, no quality loss) but
    # re-encode audio -- straight stream-copying audio across a concat
    # boundary is what causes audible gaps/clicks at segment joins.
    primary_command = [
        ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        output_path,
    ]
    # Fallback if segments aren't stream-copy compatible (different
    # codecs/parameters between segments): full re-encode.
    fallback_command = [
        ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        subprocess.run(primary_command, check=True, capture_output=True)
    except subprocess.CalledProcessError as primary_exc:
        logging.warning(
            "[LongScheduler] Stream-copy concat failed, retrying with full re-encode: %s",
            primary_exc.stderr.decode("utf-8", errors="replace"),
        )
        try:
            subprocess.run(fallback_command, check=True, capture_output=True)
        except subprocess.CalledProcessError as fallback_exc:
            raise RuntimeError(fallback_exc.stderr.decode("utf-8", errors="replace")) from fallback_exc
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)


class LongScheduler:
    @classmethod
    def INPUT_TYPES(cls):
        required = {
            "Image_F": ("IMAGE", {"tooltip": "First frame of this segment."}),
            "Image_L": ("IMAGE", {"tooltip": "Last frame of this segment."}),
            "Segment_Count": ("INT", {"default": 1, "min": 1, "max": MAX_SEGMENTS, "step": 1}),
        }
        for i in range(1, MAX_SEGMENTS + 1):
            required[f"Duration_{i}"] = ("FLOAT", {"default": 5.0, "min": 0.01, "max": 3600.0, "step": 0.1})

        optional = {
            "Start_Segment": (
                "INT",
                {
                    "default": 1,
                    "min": 1,
                    "max": MAX_SEGMENTS,
                    "step": 1,
                    "tooltip": "Which segment this run starts on / is currently on. Set before pressing Run to start (or resume) partway through.",
                },
            ),
            # Written by LongSchedulerAdvance once a batch is underway, to
            # keep Total_Segments fixed for the rest of the batch even as
            # Start_Segment climbs. Leave at 0 to start a fresh batch.
            "Total_Segments_Lock": ("INT", {"default": 0, "min": 0, "max": MAX_SEGMENTS, "step": 1}),
            # Only needed if you have more than one LongScheduler node in
            # the same workflow — give each pair (LongScheduler +
            # LongSchedulerAdvance) a matching, unique Scheduler_ID so the
            # Advance node knows which one to update.
            "Scheduler_ID": ("STRING", {"default": ""}),
        }

        return {"required": required, "optional": optional}

    RETURN_TYPES = ("IMAGE", "IMAGE", "INT", "INT", "FLOAT", "FLOAT")
    RETURN_NAMES = (
        "Start_Image",
        "End_Image",
        "Current_Segment",
        "Total_Segments",
        "Segment_Duration_Secs",
        "Time_Position_Sec",
    )
    FUNCTION = "run"
    CATEGORY = "video/scheduling"

    def run(
        self,
        Image_F,
        Image_L,
        Segment_Count,
        Start_Segment=1,
        Total_Segments_Lock=0,
        Scheduler_ID="",
        **duration_kwargs,
    ):
        segment_count = max(1, int(Segment_Count))
        current_segment = max(1, min(int(Start_Segment), segment_count))

        durations = []
        for i in range(1, segment_count + 1):
            durations.append(max(0.0, float(duration_kwargs.get(f"Duration_{i}", 5.0))))

        if int(Total_Segments_Lock) > 0:
            total_segments = min(int(Total_Segments_Lock), segment_count)
        else:
            # Fresh batch: this is the run where Total_Segments gets
            # decided. LongSchedulerAdvance will copy this same value
            # forward into Total_Segments_Lock for the rest of the batch.
            total_segments = segment_count - current_segment + 1
        total_segments = max(1, total_segments)

        initial_start = max(1, segment_count - total_segments + 1)
        seg_idx = current_segment - 1
        start_idx = initial_start - 1
        segment_duration = durations[seg_idx] if 0 <= seg_idx < len(durations) else 0.0
        time_position = sum(durations[start_idx:seg_idx]) if seg_idx > start_idx else 0.0

        return (
            Image_F,
            Image_L,
            current_segment,
            total_segments,
            segment_duration,
            time_position,
        )


class LongSchedulerAdvance:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "trigger": (ANY_TYPE, {"tooltip": "Connect to whatever finishes last in your render/save chain."}),
                "Current_Segment": ("INT", {"forceInput": True}),
                "Total_Segments": ("INT", {"forceInput": True}),
            },
            "optional": {
                "Enabled": ("BOOLEAN", {"default": True, "tooltip": "Turn off to render a single segment without auto-continuing."}),
                "Scheduler_ID": ("STRING", {"default": ""}),
                "Video_Merge_State": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": "Wire from VideoMerger's Merge_State_Out, if you're using VideoMerger in this batch.",
                    },
                ),
                "Merge_Segments": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "On the final segment, concatenate all of this batch's Video Combine output files into one final video. Only applies if Video_Merge_State is wired.",
                    },
                ),
                "Keep_Segments": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "Keep the individual per-segment video files after merging them. Turn off to delete them once the final merged file is written.",
                    },
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "advance"
    OUTPUT_NODE = True
    CATEGORY = "video/scheduling"

    def advance(
        self,
        trigger,
        Current_Segment,
        Total_Segments,
        Enabled=True,
        Scheduler_ID="",
        Video_Merge_State=None,
        Merge_Segments=True,
        Keep_Segments=True,
        prompt=None,
    ):
        current_segment = int(Current_Segment)
        total_segments = int(Total_Segments)

        if not Enabled:
            return self._log_and_return([f"Rendered segment {current_segment}/{total_segments}. Auto-continue is off."])

        next_segment = current_segment + 1
        if next_segment > total_segments:
            status_lines = [f"Batch complete: rendered {total_segments} segment(s)."]
            if Video_Merge_State is not None and Merge_Segments:
                status_lines.extend(self._merge_video_segments(Video_Merge_State, total_segments, Keep_Segments))
            return self._log_and_return(status_lines)

        base_prompt = prompt
        if base_prompt is None:
            base_prompt, _, _, _ = _get_current_queue_item()
        prompt_copy = copy.deepcopy(_normalize_prompt_keys(base_prompt))

        # LongScheduler is optional here. If Current_Segment/Total_Segments
        # are being driven some other way (e.g. VideoMerger used
        # standalone, with Video_Number/Total_Videos typed in directly
        # instead of linked from a LongScheduler), there's simply nothing
        # to find here -- that's a valid setup, not an error.
        scheduler_node = None
        for node in prompt_copy.values():
            if node.get("class_type") != "LongScheduler":
                continue
            node_scheduler_id = node.get("inputs", {}).get("Scheduler_ID", "")
            if Scheduler_ID and node_scheduler_id != Scheduler_ID:
                continue
            scheduler_node = node
            break

        if scheduler_node is not None:
            scheduler_node.setdefault("inputs", {})["Start_Segment"] = next_segment
            scheduler_node.setdefault("inputs", {})["Total_Segments_Lock"] = total_segments

        # Advance the connected Image Feeder too, so the whole loop closes
        # with a single set of image inputs. This is *not* done by linking
        # Current_Segment back into Image_Index -- that would make a cycle
        # (Image Feeder -> Long Scheduler -> ... -> Image Feeder), which
        # ComfyUI's graph executor rejects outright. Instead we find the
        # Image Feeder by tracing the Image_F/Image_L link and edit its
        # Image_Index the same way we just edited Start_Segment above.
        # Only applicable if there's a LongScheduler to trace the link from.
        image_feeder_updated = (
            self._advance_linked_image_feeder(prompt_copy, scheduler_node, next_segment)
            if scheduler_node is not None
            else False
        )

        # Advance a Prompt Feeder too, if one's in the workflow. Unlike
        # Image Feeder, there's no link on LongScheduler itself to trace
        # back to a prompt node -- prompts typically feed a text encoder,
        # not the scheduler -- so this is matched by Scheduler_ID instead
        # (same field used to disambiguate multiple LongScheduler pairs).
        # This works with or without a LongScheduler present.
        prompt_feeder_updated = self._advance_prompt_feeder(prompt_copy, Scheduler_ID, next_segment)

        # Advance a Video Merger too (matched by Scheduler_ID, same reason
        # as Prompt Feeder). This also finds and advances whichever
        # VideoFeeder is linked to that VideoMerger's Video_in, mirroring
        # the Image Feeder trace above. Also works with or without a
        # LongScheduler present -- this directly sets VideoMerger's own
        # Video_Number, which matters most when nothing else is driving it.
        video_merger_updated, video_feeder_updated = self._advance_video_merger(
            prompt_copy, Scheduler_ID, next_segment, total_segments, Video_Merge_State
        )

        _enqueue_prompt(prompt_copy)
        status_lines = [f"Rendered segment {current_segment}/{total_segments}. Queued segment {next_segment}/{total_segments}."]
        if scheduler_node is None:
            status_lines.append(
                "Note: no LongScheduler node found -- if that's expected (e.g. a standalone VideoMerger "
                "setup), ignore this; Video_Number/Total_Videos are still being advanced directly."
            )
        if not image_feeder_updated:
            status_lines.append(
                "Note: no linked ImageFeeder node found on Image_F/Image_L -- its Image_Index was not advanced."
            )
        if not prompt_feeder_updated:
            status_lines.append(
                "Note: no matching PromptFeeder node found -- no Prompt_Index was advanced."
            )
        if Video_Merge_State is not None and not video_merger_updated:
            status_lines.append(
                "Note: no matching VideoMerger node found -- its Merge_State was not advanced."
            )
        if video_merger_updated and not video_feeder_updated:
            status_lines.append(
                "Note: no linked VideoFeeder node found on VideoMerger's Video_in -- its Output_Numb was not advanced."
            )
        return self._log_and_return(status_lines)

    @staticmethod
    def _log_and_return(status_lines):
        # Log server-side (console + comfyui.log) in addition to the UI
        # text, since the on-canvas text rendering is subject to whatever
        # your ComfyUI frontend build does with an OUTPUT_NODE's ui.text --
        # which has changed across versions (e.g. "Node 2.0" rendering).
        # The log line is not subject to any of that; it always shows up.
        for line in status_lines:
            logging.info("[LongSchedulerAdvance] %s", line)
        return {"ui": {"text": status_lines}}

    @staticmethod
    def _merge_video_segments(video_merge_state, total_segments, keep_segments):
        try:
            state = json.loads(video_merge_state) if video_merge_state else {}
        except (json.JSONDecodeError, TypeError):
            state = {}

        render_id = state.get("render_id")
        filename_prefix_base = state.get("filename_prefix_base")
        total_videos = int(state.get("total_videos", total_segments))

        if not render_id or not filename_prefix_base:
            return ["Note: Video_Merge_State didn't contain a render_id/filename_prefix_base -- skipped merging segments."]

        try:
            segment_paths = _find_segment_files(render_id, filename_prefix_base, total_videos)
        except Exception as exc:
            return [f"Segment merge failed while searching for files: {exc}"]

        if not segment_paths:
            return [
                f"Note: found no segment files matching '{os.path.basename(filename_prefix_base)}_{render_id}_seg_*' "
                f"in the output folder -- make sure VideoMerger's Filename_Prefix output is wired into your "
                f"Video Combine node's filename_prefix input."
            ]

        if len(segment_paths) != total_videos:
            logging.warning(
                "[LongScheduler] Expected %s segment files for render_id %s but found %s: %s",
                total_videos, render_id, len(segment_paths), segment_paths,
            )

        extension = os.path.splitext(segment_paths[0])[1] or ".mp4"
        output_dir = os.path.dirname(segment_paths[0])
        base_name = os.path.basename(filename_prefix_base)
        final_path = os.path.join(output_dir, f"{base_name}_{render_id}_final{extension}")

        try:
            _concat_segments(segment_paths, final_path)
        except Exception as exc:
            return [f"Found {len(segment_paths)} segment file(s) but merging them failed: {exc}"]

        status_lines = [f"Merged {len(segment_paths)} segment(s) into: {os.path.basename(final_path)}"]
        if len(segment_paths) != total_videos:
            status_lines.append(
                f"Note: expected {total_videos} segments but only found {len(segment_paths)} -- "
                f"the merged file may be missing some segments."
            )

        if not keep_segments:
            for path in segment_paths:
                try:
                    os.remove(path)
                except OSError as exc:
                    logging.warning("[LongScheduler] Could not remove segment file %s: %s", path, exc)
            status_lines.append(f"Removed {len(segment_paths)} individual segment file(s).")

        return status_lines

    @staticmethod
    def _advance_linked_image_feeder(prompt_copy, scheduler_node, next_segment):
        """Follow LongScheduler's Image_F / Image_L link back to an
        ImageFeeder node (if any) and set its Image_Index directly, so the
        next requeued run pulls the next image pair -- no link from
        Current_Segment back into Image_Index required or supported.
        """
        inputs = scheduler_node.get("inputs", {})
        for key in ("Image_F", "Image_L"):
            link = inputs.get(key)
            if not (isinstance(link, list) and len(link) == 2):
                continue
            source_id = str(link[0])
            source_node = prompt_copy.get(source_id)
            if source_node and source_node.get("class_type") == "ImageFeeder":
                # ImageFeeder is 0-indexed; LongScheduler's segments are 1-indexed.
                source_node.setdefault("inputs", {})["Image_Index"] = max(0, next_segment - 1)
                return True
        return False

    @staticmethod
    def _advance_prompt_feeder(prompt_copy, scheduler_id, next_segment):
        """Find a PromptFeeder node (matched by Scheduler_ID, same as
        LongScheduler itself) and set its Prompt_Index directly, so the
        next requeued run pulls the next prompt -- same one-directional
        prompt-editing approach as everything else here, not a link.
        """
        updated = False
        for node in prompt_copy.values():
            if node.get("class_type") != "PromptFeeder":
                continue
            node_scheduler_id = node.get("inputs", {}).get("Scheduler_ID", "")
            if scheduler_id and node_scheduler_id != scheduler_id:
                continue
            # PromptFeeder is 0-indexed, same convention as ImageFeeder.
            node.setdefault("inputs", {})["Prompt_Index"] = max(0, next_segment - 1)
            updated = True
        return updated

    @staticmethod
    def _advance_video_merger(prompt_copy, scheduler_id, next_segment, total_segments, video_merge_state):
        """Find a VideoMerger node (matched by Scheduler_ID) and advance
        it directly: its own Video_Number/Total_Videos (which matters
        most when nothing else -- e.g. no LongScheduler -- is driving
        them via a link) and its Merge_State. Also traces its OWN
        Video_in link back to a VideoFeeder node and advances that the
        same way Image Feeder is advanced from LongScheduler's
        Image_F/Image_L. Returns (video_merger_updated, video_feeder_updated).
        """
        video_merger_updated = False
        video_feeder_updated = False

        for node in prompt_copy.values():
            if node.get("class_type") != "VideoMerger":
                continue
            node_scheduler_id = node.get("inputs", {}).get("Scheduler_ID", "")
            if scheduler_id and node_scheduler_id != scheduler_id:
                continue

            # VideoMerger's segments are 1-indexed, same as LongScheduler's.
            # Setting these directly is what makes VideoMerger work in a
            # standalone setup (no LongScheduler at all) -- and is harmless
            # when a LongScheduler IS present and already driving these via
            # a link, since it recomputes to the same value either way.
            node.setdefault("inputs", {})["Video_Number"] = next_segment
            node.setdefault("inputs", {})["Fresh_Start"] = False
            node.setdefault("inputs", {})["Total_Videos"] = total_segments

            if video_merge_state is not None:
                node.setdefault("inputs", {})["Merge_State"] = video_merge_state
            video_merger_updated = True

            link = node.get("inputs", {}).get("Video_in")
            if isinstance(link, list) and len(link) == 2:
                source_id = str(link[0])
                source_node = prompt_copy.get(source_id)
                if source_node and source_node.get("class_type") == "VideoFeeder":
                    # VideoFeeder is 0-indexed, same convention as ImageFeeder.
                    source_node.setdefault("inputs", {})["Output_Numb"] = max(0, next_segment - 1)
                    video_feeder_updated = True
            break

        return video_merger_updated, video_feeder_updated


NODE_CLASS_MAPPINGS = {
    "LongScheduler": LongScheduler,
    "LongSchedulerAdvance": LongSchedulerAdvance,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LongScheduler": "Long Scheduler",
    "LongSchedulerAdvance": "Long Scheduler Advance",
}
