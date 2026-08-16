"""
PromptFeeder — a container node that holds a fixed-max set of text prompts
(Prompt_1 .. Prompt_32) and outputs one of them based on Prompt_Index,
looping or holding on the last prompt exactly like ImageFeeder does for
images.

Prompt_Count controls how many of the Prompt_N fields are actually in
play; web/prompt_feeder.js hides the rest, the same trick used for
LongScheduler's Duration_N list.

Prompt_Index is meant to be driven the same way ImageFeeder's Image_Index
is: NOT by linking Long Scheduler's Current_Segment into it directly
(that would create a graph cycle, same issue as before), but by
LongSchedulerAdvance editing it directly in the copied prompt each time it
requeues -- see the _advance_prompt_feeder addition in LongScheduler's
nodes.py. Prompt_Index can also just be typed in manually if you're not
using it as part of an auto-advancing batch at all.
"""

MAX_PROMPTS = 32


class PromptFeeder:
    @classmethod
    def INPUT_TYPES(cls):
        required = {
            "Prompt_Count": ("INT", {"default": 1, "min": 1, "max": MAX_PROMPTS, "step": 1}),
        }
        for i in range(1, MAX_PROMPTS + 1):
            required[f"Prompt_{i}"] = ("STRING", {"default": "", "multiline": True})

        optional = {
            "Prompt_Index": (
                "INT",
                {
                    "default": 0,
                    "min": 0,
                    "max": MAX_PROMPTS - 1,
                    "step": 1,
                    "tooltip": "Which prompt slot to output (0-indexed, matching ImageFeeder's Image_Index). Can be entered manually or advanced automatically by Long Scheduler Advance.",
                },
            ),
            "Loop_Prompts": (
                "BOOLEAN",
                {
                    "default": False,
                    "tooltip": "When the index reaches/passes the last prompt: loop back to the first (True) or hold on the last prompt (False). Same semantics as ImageFeeder's Loop_Images.",
                },
            ),
            "Scheduler_ID": (
                "STRING",
                {
                    "default": "",
                    "tooltip": "Only needed with multiple Long Scheduler pairs in one workflow -- set this to match the corresponding Long Scheduler's Scheduler_ID so Long Scheduler Advance updates the right Prompt Feeder.",
                },
            ),
        }

        return {"required": required, "optional": optional}

    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("Prompt_Out", "Current_Prompt_Index")
    FUNCTION = "select"
    CATEGORY = "video/scheduling"

    def select(self, Prompt_Count, Prompt_Index=0, Loop_Prompts=False, Scheduler_ID="", **prompt_kwargs):
        count = max(1, int(Prompt_Count))
        idx = max(0, int(Prompt_Index))

        if Loop_Prompts:
            idx = idx % count
        else:
            idx = min(idx, count - 1)

        prompt_text = str(prompt_kwargs.get(f"Prompt_{idx + 1}", ""))
        return (prompt_text, idx)


NODE_CLASS_MAPPINGS = {
    "PromptFeeder": PromptFeeder,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptFeeder": "Prompt Feeder",
}
