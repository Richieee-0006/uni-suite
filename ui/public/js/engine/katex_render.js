/**
 * Trusted-Types-safe ('trusted-types: none') KaTeX MathML renderer & rich text builder.
 * ZERO innerHTML, ZERO DOMParser.parseFromString, ZERO eval, ZERO inline style=.
 *
 * Uses window.katex.renderToString(tex, { output: "mathml", throwOnError: false })
 * and parses the resulting clean MathML XML via a pure-JS allowlisted tokenizer
 * directly into document.createElementNS(MATHML_NS, ...).
 */
(() => {
  "use strict";

  const MATHML_NS = "http://www.w3.org/1998/Math/MathML";

  const ALLOWED_MATHML_TAGS = new Set([
    "math",
    "semantics",
    "mrow",
    "mi",
    "mn",
    "mo",
    "mtext",
    "mspace",
    "ms",
    "mfrac",
    "msqrt",
    "mroot",
    "msub",
    "msup",
    "msubsup",
    "munder",
    "mover",
    "munderover",
    "mtable",
    "mtr",
    "mtd",
    "mstyle",
    "mpadded",
    "mphantom",
    "mfenced",
    "menclose",
    "mmultiscripts",
    "mprescripts",
    "none",
    "annotation",
  ]);

  const ALLOWED_HTML_TAGS = new Set(["span"]);

  const ALLOWED_ATTRS = new Set([
    "class",
    "display",
    "mathvariant",
    "stretchy",
    "symmetric",
    "fence",
    "separator",
    "lspace",
    "rspace",
    "accent",
    "accentunder",
    "columnalign",
    "rowalign",
    "columnspacing",
    "rowspacing",
    "linethickness",
    "width",
    "height",
    "depth",
    "encoding",
    "aria-hidden",
  ]);

  const ENTITY_MAP = Object.freeze({
    amp: "&",
    lt: "<",
    gt: ">",
    quot: '"',
    apos: "'",
    nbsp: "\u00a0",
  });

  function decodeXmlEntities(text) {
    return String(text).replace(
      /&(?:#x([0-9a-fA-F]+)|#([0-9]+)|([a-zA-Z]+));/g,
      (match, hex, dec, named) => {
        if (hex) {
          const code = Number.parseInt(hex, 16);
          return Number.isFinite(code) ? String.fromCodePoint(code) : match;
        }
        if (dec) {
          const code = Number.parseInt(dec, 10);
          return Number.isFinite(code) ? String.fromCodePoint(code) : match;
        }
        return Object.prototype.hasOwnProperty.call(ENTITY_MAP, named)
          ? ENTITY_MAP[named]
          : match;
      },
    );
  }

  /**
   * Parse simple well-formed KaTeX MathML XML string into pure DOM nodes
   * without touching innerHTML or DOMParser (100% Trusted Types 'none' safe).
   */
  function buildDomFromMathMlString(xml) {
    const fragment = document.createDocumentFragment();
    const stack = [{ node: fragment, inMath: false }];
    const tokenRe = /<(\/?)([a-zA-Z][a-zA-Z0-9:-]*)([^>]*?)(\/?)>|([^<]+)/g;
    const attrRe = /([a-zA-Z_:][a-zA-Z0-9_.:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;

    let match;
    while ((match = tokenRe.exec(xml)) !== null) {
      const [, isClosing, rawTag, rawAttrs, isSelfClosing, textChunk] = match;

      if (textChunk !== undefined) {
        const parent = stack[stack.length - 1];
        if (parent && parent.node) {
          parent.node.appendChild(document.createTextNode(decodeXmlEntities(textChunk)));
        }
        continue;
      }

      const tag = rawTag.toLowerCase();
      if (isClosing) {
        if (stack.length > 1) {
          stack.pop();
        }
        continue;
      }

      const parentCtx = stack[stack.length - 1];
      const enteringMath = tag === "math" || parentCtx.inMath;
      let element = null;

      if (enteringMath && ALLOWED_MATHML_TAGS.has(tag)) {
        element = document.createElementNS(MATHML_NS, tag);
      } else if (!enteringMath && ALLOWED_HTML_TAGS.has(tag)) {
        element = document.createElement(tag);
      }

      if (element && rawAttrs) {
        let attrMatch;
        attrRe.lastIndex = 0;
        while ((attrMatch = attrRe.exec(rawAttrs)) !== null) {
          const attrName = attrMatch[1].toLowerCase();
          const attrVal = decodeXmlEntities(
            attrMatch[2] !== undefined ? attrMatch[2] : attrMatch[3] || "",
          );
          if (attrName === "xmlns" || attrName === "style" || attrName.startsWith("on")) {
            continue;
          }
          if (ALLOWED_ATTRS.has(attrName)) {
            element.setAttribute(attrName, attrVal);
          }
        }
      }

      if (element && parentCtx && parentCtx.node) {
        parentCtx.node.appendChild(element);
      }

      if (!isSelfClosing) {
        stack.push({
          node: element || (parentCtx ? parentCtx.node : fragment),
          inMath: enteringMath,
        });
      }
    }

    return fragment;
  }

  function renderMathNode(tex, displayMode = false) {
    const cleanTex = String(tex || "").trim();
    const wrapper = document.createElement(displayMode ? "div" : "span");
    wrapper.className = displayMode ? "eng-math-scroll" : "eng-math-inline";
    if (displayMode) {
      wrapper.setAttribute("tabindex", "0");
      wrapper.setAttribute("role", "region");
      wrapper.setAttribute("aria-label", "Matematický vzorec");
    }

    if (!cleanTex) return wrapper;

    if (window.katex && typeof window.katex.renderToString === "function") {
      try {
        const xml = window.katex.renderToString(cleanTex, {
          output: "mathml",
          displayMode: Boolean(displayMode),
          throwOnError: false,
          strict: "ignore",
        });
        const domFrag = buildDomFromMathMlString(xml);
        if (domFrag.childNodes.length > 0) {
          wrapper.appendChild(domFrag);
          return wrapper;
        }
      } catch {
        // Fall through to safe text fallback.
      }
    }

    const fallback = document.createElement("span");
    fallback.className = "eng-math-fallback";
    fallback.textContent = cleanTex;
    wrapper.appendChild(fallback);
    return wrapper;
  }

  /**
   * Render Czech/technical text containing $$...$$, $...$, **bold**, and `code`
   * safely into a target container with zero innerHTML.
   */
  function renderRichText(container, text) {
    if (!container) return;
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }
    const source = String(text || "");
    if (!source) return;

    const pattern = /(`[^`]+?`|\$\$[\s\S]+?\$\$|\$(?:\\.|[^$\n])+?\$|\*\*[^*]+?\*\*|\n)/g;
    let cursor = 0;
    let match;

    while ((match = pattern.exec(source)) !== null) {
      if (match.index > cursor) {
        container.appendChild(document.createTextNode(source.slice(cursor, match.index)));
      }
      const token = match[0];
      if (token === "\n") {
        container.appendChild(document.createElement("br"));
      } else if (token.startsWith("$$") && token.endsWith("$$")) {
        container.appendChild(renderMathNode(token.slice(2, -2), true));
      } else if (token.startsWith("$") && token.endsWith("$")) {
        container.appendChild(renderMathNode(token.slice(1, -1), false));
      } else if (token.startsWith("**") && token.endsWith("**")) {
        const strong = document.createElement("strong");
        renderRichText(strong, token.slice(2, -2));
        container.appendChild(strong);
      } else if (token.startsWith("`") && token.endsWith("`")) {
        const code = document.createElement("code");
        code.textContent = token.slice(1, -1);
        container.appendChild(code);
      }
      cursor = pattern.lastIndex;
    }

    if (cursor < source.length) {
      container.appendChild(document.createTextNode(source.slice(cursor)));
    }
  }

  window.UniSuite = window.UniSuite || {};
  window.UniSuite.katex = {
    renderMathNode,
    renderRichText,
    buildDomFromMathMlString,
  };
})();
