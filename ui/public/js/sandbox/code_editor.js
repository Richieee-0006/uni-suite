/**
 * Accessible line-numbered code editor with safe DOM syntax highlighting.
 * - Strict Trusted Types 'none' safe: builds DOM strictly via createElement/createTextNode.
 * - Ctrl+Enter / Cmd+Enter triggers execution.
 * - Tab / Shift+Tab indents 2 spaces; Escape temporarily releases Tab for focus navigation (WCAG 2.1.2).
 * - Reset kódu restores starter code cleanly.
 * - Mobile font-size >= 16px via sandbox.css to prevent iOS Safari auto-zoom.
 */

const KEYWORDS_BY_LANG = Object.freeze({
  r: new Set([
    "function",
    "if",
    "else",
    "for",
    "in",
    "while",
    "repeat",
    "break",
    "next",
    "return",
    "TRUE",
    "FALSE",
    "NULL",
    "NA",
    "NaN",
    "Inf",
    "library",
    "require",
  ]),
  python: new Set([
    "def",
    "return",
    "if",
    "elif",
    "else",
    "for",
    "in",
    "while",
    "break",
    "continue",
    "class",
    "import",
    "from",
    "as",
    "try",
    "except",
    "finally",
    "with",
    "lambda",
    "yield",
    "pass",
    "raise",
    "and",
    "or",
    "not",
    "is",
    "True",
    "False",
    "None",
    "assert",
  ]),
  sql_pg: new Set([
    "SELECT",
    "FROM",
    "WHERE",
    "JOIN",
    "INNER",
    "LEFT",
    "RIGHT",
    "FULL",
    "OUTER",
    "ON",
    "GROUP",
    "BY",
    "HAVING",
    "ORDER",
    "ASC",
    "DESC",
    "LIMIT",
    "OFFSET",
    "WITH",
    "AS",
    "DISTINCT",
    "UNION",
    "ALL",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "AND",
    "OR",
    "NOT",
    "IN",
    "IS",
    "NULL",
    "CREATE",
    "TABLE",
    "INSERT",
    "INTO",
    "VALUES",
    "UPDATE",
    "SET",
    "DELETE",
    "OVER",
    "PARTITION",
    "EXPLAIN",
  ]),
  csharp: new Set([
    "using",
    "namespace",
    "public",
    "private",
    "protected",
    "internal",
    "class",
    "interface",
    "struct",
    "record",
    "enum",
    "static",
    "readonly",
    "virtual",
    "override",
    "abstract",
    "sealed",
    "void",
    "return",
    "new",
    "if",
    "else",
    "for",
    "foreach",
    "in",
    "while",
    "switch",
    "case",
    "break",
    "var",
    "int",
    "double",
    "decimal",
    "string",
    "bool",
    "true",
    "false",
    "null",
    "this",
    "base",
    "async",
    "await",
    "throw",
    "try",
    "catch",
  ]),
  nosql: new Set(["true", "false", "null"]),
});

const TOKEN_REGEX_BY_LANG = Object.freeze({
  r: /(#[^\n]*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?L?\b)|(<-|->|%>%|\|>|==|!=|<=|>=|[+\-*/^:=])|(\b[A-Za-z._][A-Za-z0-9._]*\b)/g,
  python: /(#[^\n]*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(==|!=|<=|>=|->|[+\-*/%=<>:])|(\b[A-Za-z_][A-Za-z0-9_]*\b)/g,
  sql_pg: /(--[^\n]*)|('(?:''|[^'])*')|(\b\d+(?:\.\d+)?\b)|(<>|!=|<=|>=|::|[+\-*/=<>])|(\b[A-Za-z_][A-Za-z0-9_]*\b)/g,
  csharp: /(\/\/[^\n]*)|("(?:\\.|[^"\\])*")|(\b\d+(?:\.\d+)?[mfdl]?\b)|(=>|==|!=|<=|>=|\?\?|[+\-*/%=<>])|(\b[A-Za-z_][A-Za-z0-9_]*\b)/g,
  nosql: /()|("(?:\\.|[^"\\])*")|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|([{}[\]:,])|(\b(?:true|false|null)\b)/g,
});

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

export function tokenizeCode(code, language = "r") {
  const lang = String(language || "r").toLowerCase();
  const baseRe = TOKEN_REGEX_BY_LANG[lang] || TOKEN_REGEX_BY_LANG.r;
  const re = new RegExp(baseRe.source, "g");
  const keywords = KEYWORDS_BY_LANG[lang] || KEYWORDS_BY_LANG.r;
  const isSql = lang === "sql_pg";
  const isNoSql = lang === "nosql";

  const tokens = [];
  let lastIndex = 0;
  let match;

  while ((match = re.exec(code)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ type: "plain", text: code.slice(lastIndex, match.index) });
    }
    const [full, comment, str, num, op, ident] = match;
    if (comment) {
      tokens.push({ type: "comment", text: full });
    } else if (str) {
      const isOperatorKey = isNoSql && str.startsWith('"$');
      tokens.push({ type: isOperatorKey ? "keyword" : "string", text: full });
    } else if (num) {
      tokens.push({ type: "number", text: full });
    } else if (op) {
      tokens.push({ type: "operator", text: full });
    } else if (ident) {
      const lookup = isSql ? ident.toUpperCase() : ident;
      const nextChar = code[re.lastIndex];
      if (keywords.has(lookup)) {
        tokens.push({ type: "keyword", text: full });
      } else if (nextChar === "(") {
        tokens.push({ type: "function", text: full });
      } else {
        tokens.push({ type: "plain", text: full });
      }
    } else {
      tokens.push({ type: "plain", text: full });
    }
    lastIndex = re.lastIndex;
  }

  if (lastIndex < code.length) {
    tokens.push({ type: "plain", text: code.slice(lastIndex) });
  }
  return tokens;
}

let editorCounter = 0;

export class SandboxCodeEditor {
  constructor(container, options = {}) {
    if (!container) {
      throw new Error("SandboxCodeEditor requires a mount container.");
    }
    this.container = container;
    this.language = String(options.language || "r").toLowerCase();
    this.starterCode = String(options.starterCode || "");
    this.onRun = typeof options.onRun === "function" ? options.onRun : null;
    this.onChange = typeof options.onChange === "function" ? options.onChange : null;
    this.onReset = typeof options.onReset === "function" ? options.onReset : null;
    this.ariaLabel = String(options.ariaLabel || "Editor zdrojového kódu");
    this._escapeFocusMode = false;
    this._id = `sbx-code-input-${++editorCounter}`;

    this._buildDom();
    this.setValue(
      options.initialCode !== undefined ? String(options.initialCode) : this.starterCode,
      { silent: true },
    );
  }

  _buildDom() {
    clearEl(this.container);
    this.container.classList.add("sbx-editor-host");

    const label = createEl("label", "visually-hidden", { for: this._id });
    label.textContent =
      `${this.ariaLabel} (Ctrl+Enter spustí kód, Escape pak Tab přesune fokus mimo editor)`;

    const frame = createEl("div", "sbx-editor-frame");
    this.gutter = createEl("div", "sbx-editor-gutter", { "aria-hidden": "true" });

    const viewport = createEl("div", "sbx-editor-viewport");
    this.highlightPre = createEl("pre", "sbx-editor-highlight", {
      "aria-hidden": "true",
    });
    this.highlightCode = createEl("code");
    this.highlightPre.appendChild(this.highlightCode);

    this.textarea = createEl("textarea", "sbx-editor-textarea", {
      id: this._id,
      spellcheck: "false",
      autocapitalize: "off",
      autocomplete: "off",
      autocorrect: "off",
      wrap: "off",
      "aria-label": this.ariaLabel,
    });

    viewport.appendChild(this.highlightPre);
    viewport.appendChild(this.textarea);
    frame.appendChild(this.gutter);
    frame.appendChild(viewport);
    this.container.appendChild(label);
    this.container.appendChild(frame);

    this._onInput = () => {
      this._escapeFocusMode = false;
      this._renderMirror();
      this._syncScroll();
      if (this.onChange) {
        this.onChange(this.textarea.value);
      }
    };

    this._onScroll = () => {
      this._syncScroll();
    };

    this._onKeyDown = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        if (this.onRun) {
          this.onRun(this.textarea.value);
        }
        return;
      }

      if (event.key === "Escape") {
        this._escapeFocusMode = true;
        return;
      }

      if (event.key === "Tab") {
        if (this._escapeFocusMode) {
          this._escapeFocusMode = false;
          return;
        }
        event.preventDefault();
        this._handleTabIndent(event.shiftKey);
        return;
      }

      this._escapeFocusMode = false;
    };

    this.textarea.addEventListener("input", this._onInput);
    this.textarea.addEventListener("scroll", this._onScroll, { passive: true });
    this.textarea.addEventListener("keydown", this._onKeyDown);
  }

  _handleTabIndent(isShift) {
    const ta = this.textarea;
    const val = ta.value;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;

    if (!isShift && start === end) {
      ta.value = val.slice(0, start) + "  " + val.slice(end);
      ta.selectionStart = start + 2;
      ta.selectionEnd = start + 2;
      this._onInput();
      return;
    }

    const lineStart = val.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const selectedBlock = val.slice(lineStart, end);
    const lines = selectedBlock.split("\n");

    let deltaStart = 0;
    let deltaEnd = 0;
    const updated = lines.map((line, idx) => {
      if (isShift) {
        if (line.startsWith("  ")) {
          if (idx === 0) deltaStart -= Math.min(2, start - lineStart);
          deltaEnd -= 2;
          return line.slice(2);
        }
        if (line.startsWith("\t") || line.startsWith(" ")) {
          if (idx === 0) deltaStart -= Math.min(1, start - lineStart);
          deltaEnd -= 1;
          return line.slice(1);
        }
        return line;
      }
      if (idx === 0) deltaStart += 2;
      deltaEnd += 2;
      return "  " + line;
    });

    ta.value = val.slice(0, lineStart) + updated.join("\n") + val.slice(end);
    ta.selectionStart = Math.max(lineStart, start + deltaStart);
    ta.selectionEnd = Math.max(ta.selectionStart, end + deltaEnd);
    this._onInput();
  }

  _syncScroll() {
    this.highlightPre.scrollTop = this.textarea.scrollTop;
    this.highlightPre.scrollLeft = this.textarea.scrollLeft;
    this.gutter.scrollTop = this.textarea.scrollTop;
  }

  _renderMirror() {
    const code = this.textarea.value;

    // 1. Update line numbers gutter safely via textContent.
    const lineCount = Math.max(1, code.split("\n").length);
    const existingLines = this.gutter.childElementCount;
    if (existingLines !== lineCount) {
      const frag = document.createDocumentFragment();
      for (let i = 1; i <= lineCount; i += 1) {
        const ln = createEl("span", "sbx-gutter-line");
        ln.textContent = String(i);
        frag.appendChild(ln);
      }
      clearEl(this.gutter);
      this.gutter.appendChild(frag);
    }

    // 2. Render syntax highlight overlay using ONLY Text nodes and <span> elements.
    const tokens = tokenizeCode(code, this.language);
    const frag = document.createDocumentFragment();
    for (const tok of tokens) {
      if (tok.type === "plain") {
        frag.appendChild(document.createTextNode(tok.text));
      } else {
        const span = createEl("span", `sbx-tok-${tok.type}`);
        span.textContent = tok.text;
        frag.appendChild(span);
      }
    }
    // Preserve trailing empty line height so textarea & pre stay aligned.
    if (code.endsWith("\n") || code.length === 0) {
      frag.appendChild(document.createTextNode("\n"));
    }
    clearEl(this.highlightCode);
    this.highlightCode.appendChild(frag);
  }

  getValue() {
    return this.textarea.value;
  }

  setValue(code, { silent = false } = {}) {
    this.textarea.value = String(code || "");
    this._renderMirror();
    this._syncScroll();
    if (!silent && this.onChange) {
      this.onChange(this.textarea.value);
    }
  }

  setLanguage(language) {
    this.language = String(language || "r").toLowerCase();
    this._renderMirror();
  }

  setStarterCode(code) {
    this.starterCode = String(code || "");
  }

  reset() {
    this.setValue(this.starterCode);
    this.textarea.focus();
    if (this.onReset) {
      this.onReset(this.starterCode);
    }
  }

  focus() {
    this.textarea.focus();
  }

  destroy() {
    this.textarea.removeEventListener("input", this._onInput);
    this.textarea.removeEventListener("scroll", this._onScroll);
    this.textarea.removeEventListener("keydown", this._onKeyDown);
    clearEl(this.container);
  }
}
