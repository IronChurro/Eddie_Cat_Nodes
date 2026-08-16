import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const NODE_NAME = "VideoFeeder";
const DATA_WIDGET_NAME = "Dynamic_Video_Grid";

function viewUrl(entry) {
  const params = new URLSearchParams({
    filename: entry.filename,
    type: entry.type || "input",
    subfolder: entry.subfolder || "",
  });
  return `/view?${params.toString()}`;
}

async function uploadFile(file) {
  const body = new FormData();
  // Reusing ComfyUI's /upload/image endpoint for video files too: despite
  // the name, it just saves whatever file it's given into the input
  // folder and returns the resulting name/subfolder -- it isn't actually
  // validating image-ness. This matches how ComfyUI's own built-in Load
  // Video node's upload button is understood to work. If your ComfyUI
  // build rejects non-image files here, that assumption doesn't hold on
  // your version -- flag it and this is the first place to look.
  body.append("image", file);
  body.append("overwrite", "false");
  const resp = await api.fetchApi("/upload/image", { method: "POST", body });
  if (!resp.ok) {
    throw new Error(`Upload failed with status ${resp.status}`);
  }
  const data = await resp.json();
  return {
    filename: data.name,
    subfolder: data.subfolder || "",
    type: data.type || "input",
  };
}

app.registerExtension({
  name: "custom.VideoFeeder",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      const node = this;

      const dataWidget = node.widgets?.find((w) => w.name === DATA_WIDGET_NAME);
      if (!dataWidget) return result;

      dataWidget.computeSize = () => [0, -4];
      dataWidget.hidden = true;
      if (dataWidget.inputEl) {
        dataWidget.inputEl.style.display = "none";
      }

      let videos = [];
      try {
        videos = JSON.parse(dataWidget.value || "[]");
        if (!Array.isArray(videos)) videos = [];
      } catch {
        videos = [];
      }
      let selectedIndex = -1;

      const MIN_TILE_SIZE = 96; // px
      const GRID_GAP = 4; // px

      const wrapper = document.createElement("div");
      Object.assign(wrapper.style, {
        position: "relative",
        width: "100%",
        height: "100%",
      });

      const container = document.createElement("div");
      container.tabIndex = 0;
      container.className = "video-feeder-grid";
      Object.assign(container.style, {
        display: "grid",
        gap: `${GRID_GAP}px`,
        width: "100%",
        height: "100%",
        overflowY: "auto",
        boxSizing: "border-box",
        padding: "4px",
        background: "rgba(0,0,0,0.25)",
        border: "1px dashed rgba(255,255,255,0.25)",
        borderRadius: "4px",
        outline: "none",
      });
      wrapper.appendChild(container);

      // Same column/tile-size math as ImageFeeder: columns capped at the
      // actual video count, so a few videos in a wide node grow to fill
      // the space instead of leaving empty reserved columns.
      function updateGridLayout() {
        const width = container.clientWidth;
        if (!width) return;
        const maxColumnsForWidth = Math.max(1, Math.floor((width + GRID_GAP) / (MIN_TILE_SIZE + GRID_GAP)));
        const columns = Math.max(1, Math.min(maxColumnsForWidth, videos.length || 1));
        const tileSize = (width - (columns - 1) * GRID_GAP) / columns;
        container.style.gridTemplateColumns = `repeat(${columns}, 1fr)`;
        container.style.gridAutoRows = `${Math.max(MIN_TILE_SIZE, tileSize)}px`;
      }

      if (typeof ResizeObserver !== "undefined") {
        const resizeObserver = new ResizeObserver(() => updateGridLayout());
        resizeObserver.observe(container);

        const onRemoved = node.onRemoved;
        node.onRemoved = function () {
          resizeObserver.disconnect();
          return onRemoved?.apply(this, arguments);
        };
      }

      const emptyHint = document.createElement("div");
      emptyHint.textContent = "Drop videos here";
      Object.assign(emptyHint.style, {
        gridColumn: "1 / -1",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "rgba(255,255,255,0.4)",
        fontSize: "12px",
        pointerEvents: "none",
      });

      function sync() {
        dataWidget.value = JSON.stringify(videos);
        node.graph?.setDirtyCanvas(true, true);
      }

      function render() {
        updateGridLayout();
        container.innerHTML = "";

        if (videos.length === 0) {
          container.appendChild(emptyHint);
          return;
        }

        videos.forEach((entry, i) => {
          const tile = document.createElement("div");
          tile.draggable = true;
          Object.assign(tile.style, {
            position: "relative",
            width: "100%",
            height: "100%",
            border: i === selectedIndex ? "2px solid #4af" : "1px solid rgba(255,255,255,0.3)",
            borderRadius: "3px",
            overflow: "hidden",
            cursor: "grab",
            background: "#000",
          });

          // preload="metadata" + a tiny seek once metadata is ready gives
          // a first-frame still without needing a server-side thumbnail
          // endpoint. Muted + no controls keeps it looking like a static
          // preview tile rather than a playable video.
          const video = document.createElement("video");
          video.src = viewUrl(entry);
          video.muted = true;
          video.playsInline = true;
          video.preload = "metadata";
          video.draggable = false;
          Object.assign(video.style, {
            width: "100%",
            height: "100%",
            objectFit: "cover",
            display: "block",
            pointerEvents: "none",
          });
          video.addEventListener("loadedmetadata", () => {
            try {
              video.currentTime = Math.min(0.05, (video.duration || 1) / 2);
            } catch {
              // Some codecs/browsers don't like an explicit seek this
              // early; the video just shows its default first frame instead.
            }
          });
          tile.appendChild(video);

          const badge = document.createElement("div");
          badge.textContent = String(i + 1);
          Object.assign(badge.style, {
            position: "absolute",
            top: "2px",
            left: "2px",
            fontSize: "10px",
            lineHeight: "1",
            padding: "1px 4px",
            background: "rgba(0,0,0,0.75)",
            color: "#fff",
            borderRadius: "3px",
            pointerEvents: "none",
          });
          tile.appendChild(badge);

          const removeButton = document.createElement("div");
          removeButton.textContent = "×";
          removeButton.title = "Remove video";
          Object.assign(removeButton.style, {
            position: "absolute",
            top: "2px",
            right: "2px",
            width: "16px",
            height: "16px",
            lineHeight: "16px",
            textAlign: "center",
            fontSize: "12px",
            fontWeight: "bold",
            background: "rgba(0,0,0,0.75)",
            color: "#fff",
            borderRadius: "3px",
            cursor: "pointer",
          });
          removeButton.addEventListener("mouseenter", () => {
            removeButton.style.background = "rgba(200,40,40,0.9)";
          });
          removeButton.addEventListener("mouseleave", () => {
            removeButton.style.background = "rgba(0,0,0,0.75)";
          });
          removeButton.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            videos.splice(i, 1);
            if (selectedIndex === i) selectedIndex = -1;
            else if (selectedIndex > i) selectedIndex -= 1;
            sync();
            render();
          });
          tile.appendChild(removeButton);

          tile.addEventListener("click", (e) => {
            e.stopPropagation();
            selectedIndex = selectedIndex === i ? -1 : i;
            container.focus();
            render();
          });

          tile.addEventListener("dragstart", (e) => {
            e.dataTransfer.setData("application/x-videofeeder-index", String(i));
            e.dataTransfer.effectAllowed = "move";
          });

          tile.addEventListener("dragover", (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
          });

          tile.addEventListener("drop", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const srcRaw = e.dataTransfer.getData("application/x-videofeeder-index");
            if (srcRaw !== "") {
              const src = parseInt(srcRaw, 10);
              if (!Number.isNaN(src) && src !== i) {
                const [moved] = videos.splice(src, 1);
                videos.splice(i, 0, moved);
                selectedIndex = i;
                sync();
                render();
              }
              return;
            }
            handleFileDrop(e);
          });

          container.appendChild(tile);
        });
      }

      async function handleFileDrop(e) {
        if (!e.dataTransfer?.files?.length) return;
        const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("video/"));
        for (const file of files) {
          try {
            const entry = await uploadFile(file);
            videos.push(entry);
          } catch (err) {
            console.error("VideoFeeder: upload failed for", file.name, err);
          }
        }
        sync();
        render();
      }

      container.addEventListener("dragover", (e) => {
        e.preventDefault();
      });

      container.addEventListener("drop", (e) => {
        e.preventDefault();
        const srcRaw = e.dataTransfer.getData("application/x-videofeeder-index");
        if (srcRaw !== "") return;
        handleFileDrop(e);
      });

      container.addEventListener("keydown", (e) => {
        if ((e.key === "Delete" || e.key === "Backspace") && selectedIndex >= 0) {
          e.preventDefault();
          e.stopPropagation();
          videos.splice(selectedIndex, 1);
          selectedIndex = -1;
          sync();
          render();
        }
      });

      const domWidget = node.addDOMWidget(`${DATA_WIDGET_NAME}_UI`, "div", wrapper, {
        serialize: false,
        hideOnZoom: false,
      });

      // Same one-directional sizing rule as ImageFeeder: gridHeight only
      // ever changes via the drag handle below, never by reading
      // node.size back. See ImageFeeder's comments for why that matters.
      let gridHeight = 320;

      domWidget.computeSize = function (width) {
        const w = width ?? node.size?.[0] ?? 300;
        return [w, gridHeight];
      };

      const resizeGrip = document.createElement("div");
      resizeGrip.title = "Drag to resize the video grid";
      Object.assign(resizeGrip.style, {
        position: "absolute",
        bottom: "2px",
        right: "2px",
        width: "14px",
        height: "14px",
        cursor: "nwse-resize",
        background: "linear-gradient(135deg, transparent 50%, rgba(255,255,255,0.5) 50%)",
        zIndex: "10",
      });
      wrapper.appendChild(resizeGrip);

      let dragStartY = 0;
      let dragStartHeight = 0;

      function onDragMove(e) {
        const delta = e.clientY - dragStartY;
        gridHeight = Math.max(100, dragStartHeight + delta);
        updateGridLayout();
        node.setSize(node.computeSize());
        node.graph?.setDirtyCanvas(true, true);
      }

      function onDragEnd() {
        document.removeEventListener("mousemove", onDragMove);
        document.removeEventListener("mouseup", onDragEnd);
      }

      resizeGrip.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        dragStartY = e.clientY;
        dragStartHeight = gridHeight;
        document.addEventListener("mousemove", onDragMove);
        document.addEventListener("mouseup", onDragEnd);
      });

      render();
      node.setSize(node.computeSize());

      const onConfigure = node.onConfigure;
      node.onConfigure = function () {
        const r = onConfigure?.apply(this, arguments);
        try {
          videos = JSON.parse(dataWidget.value || "[]");
          if (!Array.isArray(videos)) videos = [];
        } catch {
          videos = [];
        }
        selectedIndex = -1;
        render();
        return r;
      };

      return result;
    };
  },
});
