/**
 * Safe raster PNG / ImageBitmap Plot Viewer for R Sandbox.
 * - Raster PNG only; never injects SVG or raw HTML markup (Trusted Types 'none' safe).
 * - Verifies PNG magic bytes + IHDR before creating blob: URLs.
 * - Always revokes blob: object URLs on replace, download, and destroy.
 * - Supports 1 / N multi-plot paging, dark/light plot theme toggle,
 *   native fullscreen <dialog> zoom modal, and Stáhnout PNG download.
 */

const PNG_MAGIC = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

function createEl(tag, className, attrs) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v !== undefined && v !== null) {
        node.setAttribute(k, String(v));
      }
    }
  }
  return node;
}

function clearEl(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

export function validatePngBuffer(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < 24) {
    return null;
  }
  for (let i = 0; i < PNG_MAGIC.length; i += 1) {
    if (bytes[i] !== PNG_MAGIC[i]) {
      return null;
    }
  }
  if (
    bytes[12] !== 0x49 ||
    bytes[13] !== 0x48 ||
    bytes[14] !== 0x44 ||
    bytes[15] !== 0x52
  ) {
    return null;
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint32(16, false);
  const height = view.getUint32(20, false);
  if (width < 1 || height < 1 || width > 8192 || height > 8192) {
    return null;
  }
  return { width, height };
}

function base64ToUint8Array(b64) {
  try {
    const bin = atob(String(b64));
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) {
      arr[i] = bin.charCodeAt(i);
    }
    return arr;
  } catch {
    return null;
  }
}

async function bitmapToPngBytes(bitmap) {
  if (typeof OffscreenCanvas !== "undefined") {
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(bitmap, 0, 0);
    const blob = await canvas.convertToBlob({ type: "image/png" });
    return new Uint8Array(await blob.arrayBuffer());
  }
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(bitmap, 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob) {
    return null;
  }
  return new Uint8Array(await blob.arrayBuffer());
}

export class SandboxPlotViewer {
  constructor(container, options = {}) {
    if (!container) {
      throw new Error("SandboxPlotViewer requires a mount container.");
    }
    this.container = container;
    this.plotTheme = options.theme === "light" ? "light" : "dark";
    this.onThemeChange =
      typeof options.onThemeChange === "function" ? options.onThemeChange : null;
    this._plots = []; // { url, bytes, width, height, caption }
    this._index = 0;

    this._buildDom();
    this._render();
  }

  _buildDom() {
    clearEl(this.container);
    this.container.classList.add("sbx-plot-viewer");
    this.container.dataset.plotTheme = this.plotTheme;

    this.toolbar = createEl("div", "sbx-plot-toolbar");

    const navGroup = createEl("div", "sbx-plot-pager");
    this.prevBtn = createEl("button", "sbx-btn sbx-btn-ghost", {
      type: "button",
      "aria-label": "Předchozí graf",
    });
    this.prevBtn.textContent = "◀";

    this.counterEl = createEl("span", "sbx-plot-counter", {
      "aria-live": "polite",
    });
    this.counterEl.textContent = "0 / 0";

    this.nextBtn = createEl("button", "sbx-btn sbx-btn-ghost", {
      type: "button",
      "aria-label": "Další graf",
    });
    this.nextBtn.textContent = "▶";

    navGroup.appendChild(this.prevBtn);
    navGroup.appendChild(this.counterEl);
    navGroup.appendChild(this.nextBtn);

    const actionsGroup = createEl("div", "sbx-plot-actions");

    this.themeBtn = createEl("button", "sbx-btn sbx-btn-ghost", {
      type: "button",
    });

    this.zoomBtn = createEl("button", "sbx-btn sbx-btn-ghost", {
      type: "button",
    });
    this.zoomBtn.textContent = "Zvětšit";

    this.downloadBtn = createEl("button", "sbx-btn sbx-btn-ghost", {
      type: "button",
    });
    this.downloadBtn.textContent = "Stáhnout PNG";

    actionsGroup.appendChild(this.themeBtn);
    actionsGroup.appendChild(this.zoomBtn);
    actionsGroup.appendChild(this.downloadBtn);

    this.toolbar.appendChild(navGroup);
    this.toolbar.appendChild(actionsGroup);

    this.emptyEl = createEl("p", "sbx-empty-note");
    this.emptyEl.textContent =
      "Tento běh nevytvořil žádný graf. Použijte plot(), hist(), curve() nebo boxplot().";

    this.figure = createEl("figure", "sbx-plot-figure");
    this.img = createEl("img", "sbx-plot-img", {
      alt: "Vygenerovaný rastrový graf z R",
      decoding: "async",
    });
    this.caption = createEl("figcaption", "sbx-plot-caption");
    this.figure.appendChild(this.img);
    this.figure.appendChild(this.caption);

    // Native fullscreen zoom <dialog> modal.
    this.dialog = createEl("dialog", "sbx-plot-dialog", {
      "aria-label": "Zvětšený náhled grafu",
    });
    const dialogBar = createEl("div", "sbx-plot-dialog-bar");
    this.dialogTitle = createEl("span", "sbx-plot-dialog-title");
    const closeForm = createEl("form", "sbx-plot-dialog-form", {
      method: "dialog",
    });
    const closeBtn = createEl("button", "sbx-btn sbx-btn-ghost", {
      type: "submit",
    });
    closeBtn.textContent = "Zavřít";
    closeForm.appendChild(closeBtn);
    dialogBar.appendChild(this.dialogTitle);
    dialogBar.appendChild(closeForm);

    const dialogBody = createEl("div", "sbx-plot-dialog-body");
    this.dialogImg = createEl("img", "sbx-plot-dialog-img", {
      alt: "Zvětšený rastrový graf",
    });
    dialogBody.appendChild(this.dialogImg);
    this.dialog.appendChild(dialogBar);
    this.dialog.appendChild(dialogBody);

    this.container.appendChild(this.toolbar);
    this.container.appendChild(this.emptyEl);
    this.container.appendChild(this.figure);
    this.container.appendChild(this.dialog);

    this.prevBtn.addEventListener("click", () => this.goTo(this._index - 1));
    this.nextBtn.addEventListener("click", () => this.goTo(this._index + 1));
    this.themeBtn.addEventListener("click", () => this.toggleTheme());
    this.zoomBtn.addEventListener("click", () => this.openZoom());
    this.downloadBtn.addEventListener("click", () => this.downloadCurrent());
    this.img.addEventListener("click", () => this.openZoom());
  }

  _revokeAllUrls() {
    for (const plot of this._plots) {
      if (plot.url) {
        URL.revokeObjectURL(plot.url);
      }
    }
    this._plots = [];
  }

  async setPlots(rawItems = []) {
    this._revokeAllUrls();
    this._index = 0;

    const list = Array.isArray(rawItems) ? rawItems.slice(0, 8) : [];
    for (let i = 0; i < list.length; i += 1) {
      const item = list[i];
      let bytes = null;
      let caption = `Graf ${i + 1}`;

      if (typeof ImageBitmap !== "undefined" && item instanceof ImageBitmap) {
        bytes = await bitmapToPngBytes(item);
      } else if (item instanceof Uint8Array) {
        bytes = item;
      } else if (item instanceof ArrayBuffer) {
        bytes = new Uint8Array(item);
      } else if (typeof item === "string") {
        bytes = base64ToUint8Array(item);
      } else if (item && typeof item === "object") {
        if (typeof item.caption === "string" && item.caption.trim()) {
          caption = item.caption.trim();
        }
        if (item.bytes instanceof Uint8Array) {
          bytes = item.bytes;
        } else if (item.bytes instanceof ArrayBuffer) {
          bytes = new Uint8Array(item.bytes);
        } else if (typeof item.base64 === "string") {
          bytes = base64ToUint8Array(item.base64);
        } else if (
          typeof ImageBitmap !== "undefined" &&
          item.bitmap instanceof ImageBitmap
        ) {
          bytes = await bitmapToPngBytes(item.bitmap);
        }
      }

      if (!bytes) {
        continue;
      }
      const dims = validatePngBuffer(bytes);
      if (!dims) {
        continue;
      }
      const blob = new Blob([bytes], { type: "image/png" });
      const url = URL.createObjectURL(blob);
      this._plots.push({
        url,
        bytes,
        width: dims.width,
        height: dims.height,
        caption,
      });
    }

    this._render();
    return this._plots.length;
  }

  getCount() {
    return this._plots.length;
  }

  goTo(index) {
    if (!this._plots.length) {
      return;
    }
    this._index = Math.max(0, Math.min(this._plots.length - 1, index));
    this._render();
  }

  toggleTheme() {
    this.plotTheme = this.plotTheme === "dark" ? "light" : "dark";
    this.container.dataset.plotTheme = this.plotTheme;
    this._updateThemeLabel();
    if (this.onThemeChange) {
      this.onThemeChange(this.plotTheme);
    }
  }

  _updateThemeLabel() {
    this.themeBtn.textContent =
      this.plotTheme === "dark" ? "Pozadí: Tmavé" : "Pozadí: Světlé";
    this.themeBtn.setAttribute(
      "aria-pressed",
      this.plotTheme === "light" ? "true" : "false",
    );
  }

  openZoom() {
    const active = this._plots[this._index];
    if (!active) {
      return;
    }
    this.dialogTitle.textContent = `${active.caption} (${this._index + 1} / ${this._plots.length})`;
    this.dialogImg.src = active.url;
    this.dialogImg.alt = active.caption;
    this.dialogImg.width = active.width;
    this.dialogImg.height = active.height;
    if (typeof this.dialog.showModal === "function" && !this.dialog.open) {
      this.dialog.showModal();
    }
  }

  downloadCurrent() {
    const active = this._plots[this._index];
    if (!active) {
      return;
    }
    const blob = new Blob([active.bytes], { type: "image/png" });
    const dlUrl = URL.createObjectURL(blob);
    const link = createEl("a", "visually-hidden", {
      href: dlUrl,
      download: `uni-graf-${this._index + 1}.png`,
    });
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    // Clean up temporary download blob URL promptly.
    setTimeout(() => URL.revokeObjectURL(dlUrl), 1500);
  }

  _render() {
    this._updateThemeLabel();
    const total = this._plots.length;
    const hasPlots = total > 0;

    this.emptyEl.hidden = hasPlots;
    this.figure.hidden = !hasPlots;
    this.zoomBtn.disabled = !hasPlots;
    this.downloadBtn.disabled = !hasPlots;
    this.prevBtn.disabled = !hasPlots || this._index <= 0;
    this.nextBtn.disabled = !hasPlots || this._index >= total - 1;
    this.counterEl.textContent = hasPlots ? `${this._index + 1} / ${total}` : "0 / 0";

    if (hasPlots) {
      const active = this._plots[this._index];
      this.img.src = active.url;
      this.img.alt = active.caption;
      this.img.width = active.width;
      this.img.height = active.height;
      this.caption.textContent = `${active.caption} · ${active.width} × ${active.height} px (PNG)`;
    } else {
      this.img.removeAttribute("src");
      this.caption.textContent = "";
    }
  }

  destroy() {
    if (this.dialog && this.dialog.open) {
      this.dialog.close();
    }
    this._revokeAllUrls();
    clearEl(this.container);
  }
}
