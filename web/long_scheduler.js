import { app } from "/scripts/app.js";

const NODE_NAME = "LongScheduler";
const MAX_SEGMENTS = 32; // must match MAX_SEGMENTS in nodes.py
const SEGMENT_COUNT_WIDGET = "Segment_Count";

// This is purely cosmetic. All 32 Duration_N widgets still exist and still
// get sent to the backend on every run (nodes.py only reads the first
// Segment_Count of them) — hiding a widget here doesn't remove it from the
// prompt, it just keeps it off screen.
function hideWidget(widget) {
  if (widget._lsHidden) return;
  widget._lsHidden = true;
  widget._lsOrigComputeSize = widget.computeSize;
  widget.computeSize = () => [0, -4];
  widget.hidden = true;
}

function showWidget(widget) {
  if (!widget._lsHidden) return;
  widget._lsHidden = false;
  widget.computeSize = widget._lsOrigComputeSize;
  delete widget._lsOrigComputeSize;
  widget.hidden = false;
}

function syncDurationVisibility(node, resize = true) {
  const countWidget = node.widgets?.find((w) => w.name === SEGMENT_COUNT_WIDGET);
  if (!countWidget) return;

  const count = Math.max(1, Math.min(MAX_SEGMENTS, parseInt(countWidget.value, 10) || 1));

  for (let i = 1; i <= MAX_SEGMENTS; i++) {
    const widget = node.widgets.find((w) => w.name === `Duration_${i}`);
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
  name: "custom.LongScheduler",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      const node = this;

      const countWidget = node.widgets?.find((w) => w.name === SEGMENT_COUNT_WIDGET);
      if (countWidget) {
        const origCallback = countWidget.callback;
        countWidget.callback = function (...args) {
          const r = origCallback?.apply(this, args);
          syncDurationVisibility(node);
          return r;
        };
      }

      syncDurationVisibility(node);

      // Re-sync when a saved workflow is loaded, so the correct Duration_N
      // count is hidden/shown to match whatever Segment_Count was saved --
      // but don't force a resize here, since ComfyUI has already restored
      // the saved size.
      const onConfigure = node.onConfigure;
      node.onConfigure = function () {
        const r = onConfigure?.apply(this, arguments);
        syncDurationVisibility(node, false);
        return r;
      };

      return result;
    };
  },
});
