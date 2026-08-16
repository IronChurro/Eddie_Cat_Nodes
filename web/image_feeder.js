import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const NODE_NAME = "ImageFeeder";
const DATA_WIDGET_NAME = "Dynamic_Image_Grid";

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
  name: "custom.ImageFeeder",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_NAME) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      const node = this;

      const dataWidget = node.widgets?.find((w) => w.name === DATA_WIDGET_NAME);
      if (!dataWidget) return result;

      // Hide the raw JSON text box. It still exists and still gets
      // serialized/sent to the backend — it's just not drawn.
      dataWidget.computeSize = () => [0, -4];
      dataWidget.hidden = true;
      if (dataWidget.inputEl) {
        dataWidget.inputEl.style.display = "none";
      }

      let images = [];
      try {
        images = JSON.parse(dataWidget.value || "[]");
        if (!Array.isArray(images)) images = [];
      } catch {
        images = [];
      }
      let selectedIndex = -1;

      const MIN_TILE_SIZE = 96; // px — doubled from the original 48px per request
      const GRID_GAP = 4; // px

      const wrapper = document.createElement("div");
      Object.assign(wrapper.style, {
        position: "relative",
        width: "100%",
        height: "100%",
      });

      const container = document.createElement("div");
      container.tabIndex = 0;
      container.className = "image-feeder-grid";
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

      // Recomputes column count and tile size from the container's actual
      // rendered width, so thumbnails scale up/down as the node is resized
      // instead of staying pinned at a fixed pixel size. Tiles stay square.
      //
      // Columns are capped at the actual image count. Without that cap,
      // a wide container with only a few images would reserve extra
      // empty columns sized at MIN_TILE_SIZE instead of letting the few
      // real tiles grow to fill the space -- which is what made resizing
      // look like it wasn't doing anything.
      function updateGridLayout() {
        const width = container.clientWidth;
        if (!width) return;
        const maxColumnsForWidth = Math.max(1, Math.floor((width + GRID_GAP) / (MIN_TILE_SIZE + GRID_GAP)));
        const columns = Math.max(1, Math.min(maxColumnsForWidth, images.length || 1));
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
      emptyHint.textContent = "Drop images here";
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
        dataWidget.value = JSON.stringify(images);
        node.graph?.setDirtyCanvas(true, true);
      }

      function render() {
        updateGridLayout();
        container.innerHTML = "";

        if (images.length === 0) {
          container.appendChild(emptyHint);
          return;
        }

        images.forEach((entry, i) => {
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
          });

          const img = document.createElement("img");
          img.src = viewUrl(entry);
          img.draggable = false;
          Object.assign(img.style, {
            width: "100%",
            height: "100%",
            objectFit: "cover",
            display: "block",
          });
          tile.appendChild(img);

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
          removeButton.title = "Remove image";
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
            images.splice(i, 1);
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
            e.dataTransfer.setData("application/x-imagefeeder-index", String(i));
            e.dataTransfer.effectAllowed = "move";
          });

          tile.addEventListener("dragover", (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
          });

          tile.addEventListener("drop", (e) => {
            e.preventDefault();
            e.stopPropagation();
            // Reordering an existing tile takes priority over file drops.
            const srcRaw = e.dataTransfer.getData("application/x-imagefeeder-index");
            if (srcRaw !== "") {
              const src = parseInt(srcRaw, 10);
              if (!Number.isNaN(src) && src !== i) {
                const [moved] = images.splice(src, 1);
                images.splice(i, 0, moved);
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
        const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("image/"));
        for (const file of files) {
          try {
            const entry = await uploadFile(file);
            images.push(entry);
          } catch (err) {
            console.error("ImageFeeder: upload failed for", file.name, err);
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
        // Only handle here if it wasn't already handled by a tile drop.
        const srcRaw = e.dataTransfer.getData("application/x-imagefeeder-index");
        if (srcRaw !== "") return;
        handleFileDrop(e);
      });

      container.addEventListener("keydown", (e) => {
        if ((e.key === "Delete" || e.key === "Backspace") && selectedIndex >= 0) {
          e.preventDefault();
          e.stopPropagation();
          // Without stopPropagation, this same keypress can also reach
          // ComfyUI's own canvas shortcut for "delete the selected node" --
          // which is presumably what was deleting the whole node, or at
          // least fighting with this handler, instead of just the image.
          images.splice(selectedIndex, 1);
          selectedIndex = -1;
          sync();
          render();
        }
      });

      const domWidget = node.addDOMWidget(`${DATA_WIDGET_NAME}_UI`, "div", wrapper, {
        serialize: false,
        hideOnZoom: false,
      });

      // gridHeight is the ONLY thing that determines the widget's height,
      // and the ONLY way it ever changes is through the drag handle below
      // -- never by reading node.size back. That one-directional flow
      // (drag -> gridHeight -> computeSize -> node grows to fit) is what
      // makes this safe. Reading node.size inside computeSize (or inside
      // anything called every draw frame, like onDrawForeground) is NOT
      // safe here: node.size is itself partly determined by what this
      // widget reports through computeSize, so feeding it back in --
      // even indirectly, even once per frame instead of synchronously --
      // creates a loop that slowly (or not so slowly) grows without bound.
      // That was the actual cause of the "endless vertical length" bug.
      let gridHeight = 320;

      domWidget.computeSize = function (width) {
        const w = width ?? node.size?.[0] ?? 300;
        return [w, gridHeight];
      };

      const resizeGrip = document.createElement("div");
      resizeGrip.title = "Drag to resize the image grid";
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
          images = JSON.parse(dataWidget.value || "[]");
          if (!Array.isArray(images)) images = [];
        } catch {
          images = [];
        }
        selectedIndex = -1;
        render();
        return r;
      };

      return result;
    };
  },
});
