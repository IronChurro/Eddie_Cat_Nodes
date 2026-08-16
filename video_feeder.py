"""
VideoFeeder — a container node that holds a dynamic, reorderable grid of
videos (managed by the accompanying JS widget, mirroring ImageFeeder's
grid as closely as possible) and outputs one of them based on an integer
index:

  Video_Out = video at Output_Numb

If Output_Numb reaches or passes the last video:
  - Loop_Videos = True  -> wraps back around to the start
  - Loop_Videos = False -> keeps outputting the last video

Unlike ImageFeeder, this does NOT decode the video into frames at all.
VideoFromFile is a lazy wrapper around the file path -- decoding only
happens later, whenever something downstream (e.g. VideoMerger) actually
asks for pixel data. This matters: eagerly decoding every video in the
grid just to hold/preview it would scale memory use with total video
length, which this design avoids entirely.

The video grid itself (order, filenames, subfolders) is stored as a JSON
string in the "Dynamic_Video_Grid" input, written and maintained by the
JS widget in web/video_feeder.js -- the Python side just reads it.
"""

import json
import os

import folder_paths

try:
    from comfy_api.latest._input_impl.video_types import VideoFromFile
except ImportError:
    # Alternate import path seen in some ComfyUI versions/forks.
    from comfy_api.input_impl.video_types import VideoFromFile


def _base_dir_for_type(vid_type: str) -> str:
    if vid_type == "output":
        return folder_paths.get_output_directory()
    if vid_type == "temp":
        return folder_paths.get_temp_directory()
    return folder_paths.get_input_directory()


def _resolve_video_path(entry: dict) -> str:
    filename = entry.get("filename")
    subfolder = entry.get("subfolder", "") or ""
    vid_type = entry.get("type", "input") or "input"

    base_dir = _base_dir_for_type(vid_type)
    path = os.path.join(base_dir, subfolder, filename) if subfolder else os.path.join(base_dir, filename)

    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"VideoFeeder: could not find grid video on disk: {path}\n"
            f"(It may have been deleted, or this workflow was moved to a "
            f"machine that doesn't have it in its input folder.)"
        )
    return path


class VideoFeeder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "Output_Numb": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 999999,
                        "step": 1,
                        "tooltip": "Which grid slot to output. Can be entered manually or driven by a link.",
                    },
                ),
                "Loop_Videos": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "When the index runs past the last video: loop back to the start (True) or hold on the last video (False).",
                    },
                ),
                "Dynamic_Video_Grid": (
                    "STRING",
                    {
                        "default": "[]",
                        "multiline": True,
                        "tooltip": "Internal JSON storage for the video grid — managed by the node's UI widget, not meant to be hand-edited.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("Video_Out",)
    FUNCTION = "execute"
    CATEGORY = "video/utils"

    def execute(self, Output_Numb, Loop_Videos, Dynamic_Video_Grid):
        try:
            videos = json.loads(Dynamic_Video_Grid) if Dynamic_Video_Grid else []
            if not isinstance(videos, list):
                videos = []
        except (json.JSONDecodeError, TypeError):
            videos = []

        if not videos:
            raise ValueError(
                "VideoFeeder: the grid is empty. Drag at least one video onto the node before running."
            )

        total = len(videos)
        idx = max(0, int(Output_Numb))

        if Loop_Videos:
            idx = idx % total
        else:
            idx = min(idx, total - 1)

        path = _resolve_video_path(videos[idx])
        return (VideoFromFile(path),)


NODE_CLASS_MAPPINGS = {
    "VideoFeeder": VideoFeeder,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "VideoFeeder": "Video Feeder",
}
