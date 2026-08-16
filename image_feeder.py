"""
ImageFeeder — a container node that holds a dynamic, reorderable grid of
images (managed by the accompanying JS widget) and feeds out two images at a
time based on an integer index:

  Image_Out_1 = image at Image_Index
  Image_Out_2 = image at Image_Index + 1

If Image_Index reaches or passes the last image:
  - Loop_Images = True  -> wraps back around to the start
  - Loop_Images = False -> keeps repeating the last image on both outputs

The image grid itself (order, filenames, subfolders) is stored as a JSON
string in the "Dynamic_Image_Grid" input. That JSON is written and
maintained entirely by the JS widget in web/image_feeder.js — the Python
side just reads it at execution time.
"""

import json
import os

import numpy as np
import torch
from PIL import Image, ImageOps

import folder_paths


def _base_dir_for_type(img_type: str) -> str:
    if img_type == "output":
        return folder_paths.get_output_directory()
    if img_type == "temp":
        return folder_paths.get_temp_directory()
    # Default / "input" — this is where ComfyUI's /upload/image endpoint
    # saves dropped files.
    return folder_paths.get_input_directory()


def _load_image_tensor(entry: dict) -> torch.Tensor:
    """Load a single grid entry ({'filename', 'subfolder', 'type'}) as a
    (1, H, W, 3) float32 tensor in the 0-1 range, matching ComfyUI's IMAGE
    convention."""
    filename = entry.get("filename")
    subfolder = entry.get("subfolder", "") or ""
    img_type = entry.get("type", "input") or "input"

    base_dir = _base_dir_for_type(img_type)
    path = os.path.join(base_dir, subfolder, filename) if subfolder else os.path.join(base_dir, filename)

    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"ImageFeeder: could not find grid image on disk: {path}\n"
            f"(It may have been deleted, or this workflow was moved to a "
            f"machine that doesn't have it in its input folder.)"
        )

    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    arr = np.array(img).astype(np.float32) / 255.0
    tensor = torch.from_numpy(arr)[None, ...]
    return tensor


def _blank_image(size: int = 64) -> torch.Tensor:
    return torch.zeros((1, size, size, 3), dtype=torch.float32)


class ImageFeeder:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "Image_Index": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 999999,
                        "step": 1,
                        "tooltip": "Which grid slot to output. Can be entered manually or driven by a link.",
                    },
                ),
                "Loop_Images": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "When the index runs past the last image: loop back to the start (True) or hold on the last image (False).",
                    },
                ),
                "Dynamic_Image_Grid": (
                    "STRING",
                    {
                        "default": "[]",
                        "multiline": True,
                        "tooltip": "Internal JSON storage for the image grid — managed by the node's UI widget, not meant to be hand-edited.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("Image_Out_1", "Image_Out_2")
    FUNCTION = "execute"
    CATEGORY = "image/utils"

    def execute(self, Image_Index, Loop_Images, Dynamic_Image_Grid):
        try:
            images = json.loads(Dynamic_Image_Grid) if Dynamic_Image_Grid else []
            if not isinstance(images, list):
                images = []
        except (json.JSONDecodeError, TypeError):
            images = []

        if not images:
            blank = _blank_image()
            return (blank, blank)

        total = len(images)
        idx = max(0, int(Image_Index))

        if Loop_Images:
            idx1 = idx % total
            idx2 = (idx + 1) % total
        else:
            idx1 = min(idx, total - 1)
            idx2 = min(idx + 1, total - 1)

        img1 = _load_image_tensor(images[idx1])
        img2 = _load_image_tensor(images[idx2])
        return (img1, img2)


NODE_CLASS_MAPPINGS = {
    "ImageFeeder": ImageFeeder,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ImageFeeder": "Image Feeder",
}
