import { app } from "/scripts/app.js";

const NODE_NAME = "PromptFeeder";
const MAX_PROMPTS = 32; // must match MAX_PROMPTS in nodes.py
const PROMPT_COUNT_WIDGET = "Prompt_Count";

// Purely cosmetic. All 32 Prompt_N widgets still exist and still get sent
// to the backend on every run (nodes.py only reads the first
// Prompt_Count of them) -- hiding a widget here doesn't remove it from
// the prompt, it just keeps it off screen.
function hideWidget(widget) {
  if (widget._pfHidden) return;
  widget._pfHidden = true;
  widget._pfOrigComputeSize = widget.computeSize;
  widget.computeSize = () => [0, -4];
  widget.hidden = true;
}

function showWidget(widget) {
  if (!widget._pfHidden) return;
  widget._pfHidden = false;
  widget.computeSize = widget._pfOrigComputeSize;
  delete widget._pfOrigComputeSize;
  widget.hidden = false;
}

function syncPromptVisibility(node, resize = true) {
  const countWidget = node.widgets?.find((w) => w.name === PROMPT_COUNT_WIDGET);
  if (!countWidget) return;

  const count = Math.max(1, Math.min(MAX_PROMPTS, parseInt(countWidget.value, 10) || 1));

  for (let i = 1; i <= MAX_PROMPTS; i++) {
    const widget = node.widgets.find((w) => w.name === `Prompt_${i}`);
    if (!widget) continue;
    if (i <= count) showWidget(widget);
    else hideWidget(widget);
  }

  // Only force a resize when actually reacting to a visibility change
  // (the count widget changing, or first-time node creation). On workflow
  // load, ComfyUI has already restored whatever size the user last saved
  // the node at -- calling setSize here would immediately overwrite that
  // back down to the computed minimum, undoing any manual resize every
  // single time the workflow is reopened.
  if (resize) {
    node.setSize(node.computeSize());
  }
  node.graph?.setDirtyCanvas(true, true);
}

app.registerExtension({
  name: "custom.PromptFeeder",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      const node = this;

      const countWidget = node.widgets?.find((w) => w.name === PROMPT_COUNT_WIDGET);
      if (countWidget) {
        const origCallback = countWidget.callback;
        countWidget.callback = function (...args) {
          const r = origCallback?.apply(this, args);
          syncPromptVisibility(node);
          return r;
        };
      }

      syncPromptVisibility(node);

      // Re-sync widget visibility when a saved workflow is loaded, so the
      // correct Prompt_N count is hidden/shown -- but don't force a
      // resize here, since ComfyUI has already restored the saved size.
      const onConfigure = node.onConfigure;
      node.onConfigure = function () {
        const r = onConfigure?.apply(this, arguments);
        syncPromptVisibility(node, false);
        return r;
      };

      return result;
    };
  },
});
