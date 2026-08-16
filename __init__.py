"""
Eddie_Cat_Nodes -- a ComfyUI custom node pack bundling:

  - ImageFeeder            (image_feeder.py)
  - VideoFeeder            (video_feeder.py)
  - LongScheduler          (long_scheduler.py)
  - LongSchedulerAdvance   (long_scheduler.py)
  - PromptFeeder           (prompt_feeder.py)
  - VideoMerger            (video_merger.py)

All six register the same way: classic NODE_CLASS_MAPPINGS /
NODE_DISPLAY_NAME_MAPPINGS.

WEB_DIRECTORY points at one shared web/ folder containing JS files for
ImageFeeder, VideoFeeder, LongScheduler, and PromptFeeder. VideoMerger
has no custom widget, so it has no JS file of its own.
"""

from .image_feeder import (
    NODE_CLASS_MAPPINGS as _IMAGE_FEEDER_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _IMAGE_FEEDER_NAMES,
)
from .video_feeder import (
    NODE_CLASS_MAPPINGS as _VIDEO_FEEDER_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _VIDEO_FEEDER_NAMES,
)
from .long_scheduler import (
    NODE_CLASS_MAPPINGS as _LONG_SCHEDULER_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _LONG_SCHEDULER_NAMES,
)
from .prompt_feeder import (
    NODE_CLASS_MAPPINGS as _PROMPT_FEEDER_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _PROMPT_FEEDER_NAMES,
)
from .video_merger import (
    NODE_CLASS_MAPPINGS as _VIDEO_MERGER_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as _VIDEO_MERGER_NAMES,
)

NODE_CLASS_MAPPINGS = {
    **_IMAGE_FEEDER_MAPPINGS,
    **_VIDEO_FEEDER_MAPPINGS,
    **_LONG_SCHEDULER_MAPPINGS,
    **_PROMPT_FEEDER_MAPPINGS,
    **_VIDEO_MERGER_MAPPINGS,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    **_IMAGE_FEEDER_NAMES,
    **_VIDEO_FEEDER_NAMES,
    **_LONG_SCHEDULER_NAMES,
    **_PROMPT_FEEDER_NAMES,
    **_VIDEO_MERGER_NAMES,
}

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
