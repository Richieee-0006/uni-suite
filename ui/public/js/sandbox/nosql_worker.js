/**
 * Phase 4 ready Tier 1 in-memory MongoDB-style Query & Aggregation Pipeline Worker (NoSQL).
 * Zero external dependencies, strict JSON input only (no dynamic script execution).
 * Supports: $match, $project, $addFields/$set, $group, $sort, $skip, $limit,
 *           $count, $unwind, $lookup, plus find() queries & schema introspection.
 */

const MAX_STDOUT_BYTES = 32 * 1024;
const MAX_REGEX_LEN = 128;
const MAX_DOCS_OUTPUT = 200;

function truncateUtf8(text, maxBytes = MAX_STDOUT_BYTES) {
  const encoder = new TextEncoder();
  const bytes = encoder.encode(String(text || ""));
  if (bytes.byteLength <= maxBytes) {
    return String(text || "");
  }
  const suffix = "\n...[zkráceno na limit 32 KB]";
  const suffixBytes = encoder.encode(suffix);
  const slice = bytes.slice(0, Math.max(0, maxBytes - suffixBytes.byteLength));
  return new TextDecoder("utf-8", { fatal: false }).decode(slice) + suffix;
}

function deepClone(val) {
  return val === undefined ? undefined : JSON.parse(JSON.stringify(val));
}

function isPlainObject(val) {
  return val !== null && typeof val === "object" && !Array.isArray(val);
}

export function getFieldPath(doc, path) {
  if (!doc || typeof path !== "string") {
    return undefined;
  }
  const parts = path.split(".");
  let cur = doc;
  for (const part of parts) {
    if (cur === null || cur === undefined) {
      return undefined;
    }
    if (Array.isArray(cur)) {
      const idx = Number(part);
      if (Number.isInteger(idx) && idx >= 0 && idx < cur.length) {
        cur = cur[idx];
      } else {
        // MongoDB implicit array traversal
        return cur
          .map((item) => (isPlainObject(item) ? item[part] : undefined))
          .filter((v) => v !== undefined);
      }
    } else if (typeof cur === "object") {
      cur = cur[part];
    } else {
      return undefined;
    }
  }
  return cur;
}

function setFieldPath(target, path, value) {
  const parts = String(path).split(".");
  let cur = target;
  for (let i = 0; i < parts.length - 1; i += 1) {
    const key = parts[i];
    if (key === "__proto__" || key === "constructor" || key === "prototype") {
      return;
    }
    if (!isPlainObject(cur[key])) {
      cur[key] = {};
    }
    cur = cur[key];
  }
  const last = parts[parts.length - 1];
  if (last === "__proto__" || last === "constructor" || last === "prototype") {
    return;
  }
  cur[last] = value;
}

function deleteFieldPath(target, path) {
  const parts = String(path).split(".");
  let cur = target;
  for (let i = 0; i < parts.length - 1; i += 1) {
    if (!isPlainObject(cur[parts[i]])) {
      return;
    }
    cur = cur[parts[i]];
  }
  delete cur[parts[parts.length - 1]];
}

export function deepEqual(a, b, tolerance = 1e-4) {
  if (a === b) {
    return true;
  }
  if (typeof a === "number" && typeof b === "number") {
    if (Number.isNaN(a) && Number.isNaN(b)) {
      return true;
    }
    return Math.abs(a - b) <= tolerance;
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) {
      return false;
    }
    return a.every((item, idx) => deepEqual(item, b[idx], tolerance));
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const keysA = Object.keys(a);
    const keysB = Object.keys(b);
    if (keysA.length !== keysB.length) {
      return false;
    }
    return keysA.every(
      (k) => Object.prototype.hasOwnProperty.call(b, k) && deepEqual(a[k], b[k], tolerance),
    );
  }
  return false;
}

function compareValues(a, b) {
  if (a === b) {
    return 0;
  }
  if (a === undefined || a === null) {
    return -1;
  }
  if (b === undefined || b === null) {
    return 1;
  }
  if (typeof a === "number" && typeof b === "number") {
    return a < b ? -1 : a > b ? 1 : 0;
  }
  return String(a).localeCompare(String(b));
}

function matchCondition(val, cond) {
  if (isPlainObject(cond)) {
    const keys = Object.keys(cond);
    const hasOperator = keys.some((k) => k.startsWith("$"));
    if (!hasOperator) {
      if (Array.isArray(val)) {
        return val.some((el) => deepEqual(el, cond, 0));
      }
      return deepEqual(val, cond, 0);
    }

    for (const op of keys) {
      const target = cond[op];
      switch (op) {
        case "$eq":
          if (Array.isArray(val)) {
            if (!val.some((el) => deepEqual(el, target, 0))) return false;
          } else if (!deepEqual(val, target, 0)) {
            return false;
          }
          break;
        case "$ne":
          if (Array.isArray(val)) {
            if (val.some((el) => deepEqual(el, target, 0))) return false;
          } else if (deepEqual(val, target, 0)) {
            return false;
          }
          break;
        case "$gt":
          if (!(typeof val === typeof target && val > target)) return false;
          break;
        case "$gte":
          if (!(typeof val === typeof target && val >= target)) return false;
          break;
        case "$lt":
          if (!(typeof val === typeof target && val < target)) return false;
          break;
        case "$lte":
          if (!(typeof val === typeof target && val <= target)) return false;
          break;
        case "$in":
          if (!Array.isArray(target)) return false;
          if (Array.isArray(val)) {
            if (!val.some((el) => target.some((t) => deepEqual(el, t, 0)))) return false;
          } else if (!target.some((t) => deepEqual(val, t, 0))) {
            return false;
          }
          break;
        case "$nin":
          if (!Array.isArray(target)) return false;
          if (Array.isArray(val)) {
            if (val.some((el) => target.some((t) => deepEqual(el, t, 0)))) return false;
          } else if (target.some((t) => deepEqual(val, t, 0))) {
            return false;
          }
          break;
        case "$exists": {
          const exists = val !== undefined;
          if (Boolean(target) !== exists) return false;
          break;
        }
        case "$size":
          if (!Array.isArray(val) || val.length !== Number(target)) return false;
          break;
        case "$all":
          if (!Array.isArray(val) || !Array.isArray(target)) return false;
          if (!target.every((t) => val.some((el) => deepEqual(el, t, 0)))) return false;
          break;
        case "$elemMatch":
          if (!Array.isArray(val) || !isPlainObject(target)) return false;
          if (!val.some((el) => matchDoc(el, target))) return false;
          break;
        case "$regex": {
          const pattern = String(target || "");
          if (pattern.length > MAX_REGEX_LEN) {
            throw new Error(`$regex přesahuje limit ${MAX_REGEX_LEN} znaků.`);
          }
          const flags = typeof cond.$options === "string" ? cond.$options.replace(/[^ims]/g, "") : "";
          const re = new RegExp(pattern, flags);
          if (typeof val !== "string" || !re.test(val)) return false;
          break;
        }
        case "$options":
          break;
        case "$not":
          if (matchCondition(val, target)) return false;
          break;
        default:
          throw new Error(`Nepodporovaný $match operátor: ${op}`);
      }
    }
    return true;
  }

  if (Array.isArray(val) && !Array.isArray(cond)) {
    return val.some((el) => deepEqual(el, cond, 0));
  }
  return deepEqual(val, cond, 0);
}

export function matchDoc(doc, filter) {
  if (!filter || !isPlainObject(filter) || Object.keys(filter).length === 0) {
    return true;
  }
  for (const [key, cond] of Object.entries(filter)) {
    if (key === "$and") {
      if (!Array.isArray(cond) || !cond.every((sub) => matchDoc(doc, sub))) return false;
      continue;
    }
    if (key === "$or") {
      if (!Array.isArray(cond) || !cond.some((sub) => matchDoc(doc, sub))) return false;
      continue;
    }
    if (key === "$nor") {
      if (!Array.isArray(cond) || cond.some((sub) => matchDoc(doc, sub))) return false;
      continue;
    }
    const val = getFieldPath(doc, key);
    if (!matchCondition(val, cond)) {
      return false;
    }
  }
  return true;
}

function evalExpr(doc, expr) {
  if (typeof expr === "string") {
    if (expr.startsWith("$")) {
      return getFieldPath(doc, expr.slice(1));
    }
    return expr;
  }
  if (Array.isArray(expr)) {
    return expr.map((item) => evalExpr(doc, item));
  }
  if (!isPlainObject(expr)) {
    return expr;
  }

  const keys = Object.keys(expr);
  if (keys.length === 1 && keys[0].startsWith("$")) {
    const op = keys[0];
    const arg = expr[op];
    switch (op) {
      case "$add": {
        const vals = evalExpr(doc, arg);
        return Array.isArray(vals)
          ? vals.reduce((acc, v) => acc + (Number(v) || 0), 0)
          : Number(vals) || 0;
      }
      case "$subtract": {
        const [a, b] = evalExpr(doc, arg);
        return (Number(a) || 0) - (Number(b) || 0);
      }
      case "$multiply": {
        const vals = evalExpr(doc, arg);
        return Array.isArray(vals)
          ? vals.reduce((acc, v) => acc * (Number(v) || 0), 1)
          : Number(vals) || 0;
      }
      case "$divide": {
        const [a, b] = evalExpr(doc, arg);
        return Number(b) === 0 ? null : (Number(a) || 0) / Number(b);
      }
      case "$concat": {
        const vals = evalExpr(doc, arg);
        return Array.isArray(vals)
          ? vals.map((v) => (v == null ? "" : String(v))).join("")
          : String(vals ?? "");
      }
      case "$toLower":
        return String(evalExpr(doc, arg) ?? "").toLowerCase();
      case "$toUpper":
        return String(evalExpr(doc, arg) ?? "").toUpperCase();
      case "$size": {
        const v = evalExpr(doc, arg);
        return Array.isArray(v) ? v.length : 0;
      }
      case "$ifNull": {
        const [a, fallback] = arg;
        const va = evalExpr(doc, a);
        return va === null || va === undefined ? evalExpr(doc, fallback) : va;
      }
      case "$cond": {
        if (Array.isArray(arg)) {
          return evalExpr(doc, arg[0]) ? evalExpr(doc, arg[1]) : evalExpr(doc, arg[2]);
        }
        return evalExpr(doc, arg.if) ? evalExpr(doc, arg.then) : evalExpr(doc, arg.else);
      }
      case "$eq": {
        const [a, b] = evalExpr(doc, arg);
        return deepEqual(a, b, 0);
      }
      case "$gt": {
        const [a, b] = evalExpr(doc, arg);
        return a > b;
      }
      case "$gte": {
        const [a, b] = evalExpr(doc, arg);
        return a >= b;
      }
      case "$lt": {
        const [a, b] = evalExpr(doc, arg);
        return a < b;
      }
      case "$lte": {
        const [a, b] = evalExpr(doc, arg);
        return a <= b;
      }
      default:
        throw new Error(`Nepodporovaný výraz: ${op}`);
    }
  }

  const out = {};
  for (const [k, v] of Object.entries(expr)) {
    if (k === "__proto__" || k === "constructor" || k === "prototype") {
      continue;
    }
    out[k] = evalExpr(doc, v);
  }
  return out;
}

function applyProject(docs, spec) {
  if (!isPlainObject(spec)) {
    throw new Error("$project očekává objekt.");
  }
  const entries = Object.entries(spec);
  const nonIdSpecs = entries.filter(([k]) => k !== "_id");
  const isExclusionOnly =
    nonIdSpecs.length > 0 && nonIdSpecs.every(([, v]) => v === 0 || v === false);

  return docs.map((doc) => {
    if (isExclusionOnly) {
      const copy = deepClone(doc);
      for (const [k, v] of entries) {
        if (v === 0 || v === false) {
          deleteFieldPath(copy, k);
        }
      }
      return copy;
    }

    const out = {};
    const includeId = spec._id !== 0 && spec._id !== false;
    if (includeId && doc._id !== undefined) {
      out._id = deepClone(doc._id);
    }
    for (const [k, v] of entries) {
      if (k === "_id") {
        if (v !== 0 && v !== false && v !== 1 && v !== true) {
          out._id = evalExpr(doc, v);
        }
        continue;
      }
      if (v === 1 || v === true) {
        const existing = getFieldPath(doc, k);
        if (existing !== undefined) {
          setFieldPath(out, k, deepClone(existing));
        }
      } else if (v === 0 || v === false) {
        // ignored in inclusion projection
      } else {
        setFieldPath(out, k, evalExpr(doc, v));
      }
    }
    return out;
  });
}

function applyGroup(docs, spec) {
  if (!isPlainObject(spec) || !Object.prototype.hasOwnProperty.call(spec, "_id")) {
    throw new Error("$group musí obsahovat pole '_id'.");
  }
  const groups = new Map();
  for (const doc of docs) {
    const idVal = spec._id === null ? null : evalExpr(doc, spec._id);
    const mapKey = JSON.stringify(idVal);
    if (!groups.has(mapKey)) {
      groups.set(mapKey, { id: idVal, docs: [] });
    }
    groups.get(mapKey).docs.push(doc);
  }

  const results = [];
  for (const { id, docs: groupDocs } of groups.values()) {
    const out = { _id: id };
    for (const [field, accSpec] of Object.entries(spec)) {
      if (field === "_id") {
        continue;
      }
      if (!isPlainObject(accSpec)) {
        throw new Error(`$group akumulátor pro '${field}' musí být objekt.`);
      }
      const [op] = Object.keys(accSpec);
      const arg = accSpec[op];
      switch (op) {
        case "$sum":
          out[field] = groupDocs.reduce((acc, d) => {
            const v = evalExpr(d, arg);
            return acc + (typeof v === "number" ? v : 0);
          }, 0);
          break;
        case "$count":
          out[field] = groupDocs.length;
          break;
        case "$avg": {
          const nums = groupDocs
            .map((d) => evalExpr(d, arg))
            .filter((v) => typeof v === "number" && Number.isFinite(v));
          out[field] = nums.length
            ? nums.reduce((a, b) => a + b, 0) / nums.length
            : null;
          break;
        }
        case "$min": {
          const vals = groupDocs
            .map((d) => evalExpr(d, arg))
            .filter((v) => v !== undefined && v !== null);
          out[field] = vals.length
            ? vals.reduce((m, v) => (compareValues(v, m) < 0 ? v : m))
            : null;
          break;
        }
        case "$max": {
          const vals = groupDocs
            .map((d) => evalExpr(d, arg))
            .filter((v) => v !== undefined && v !== null);
          out[field] = vals.length
            ? vals.reduce((m, v) => (compareValues(v, m) > 0 ? v : m))
            : null;
          break;
        }
        case "$push":
          out[field] = groupDocs.map((d) => evalExpr(d, arg));
          break;
        case "$addToSet": {
          const seen = [];
          for (const d of groupDocs) {
            const v = evalExpr(d, arg);
            if (!seen.some((s) => deepEqual(s, v, 0))) {
              seen.push(v);
            }
          }
          out[field] = seen;
          break;
        }
        case "$first":
          out[field] = groupDocs.length ? evalExpr(groupDocs[0], arg) : null;
          break;
        case "$last":
          out[field] = groupDocs.length
            ? evalExpr(groupDocs[groupDocs.length - 1], arg)
            : null;
          break;
        default:
          throw new Error(`Nepodporovaný $group akumulátor: ${op}`);
      }
    }
    results.push(out);
  }
  return results;
}

function applySort(docs, spec) {
  if (!isPlainObject(spec)) {
    throw new Error("$sort očekává objekt.");
  }
  const entries = Object.entries(spec);
  return [...docs].sort((a, b) => {
    for (const [path, dir] of entries) {
      const cmp = compareValues(getFieldPath(a, path), getFieldPath(b, path));
      if (cmp !== 0) {
        return Number(dir) < 0 ? -cmp : cmp;
      }
    }
    return 0;
  });
}

function applyUnwind(docs, spec) {
  const pathRaw = typeof spec === "string" ? spec : spec && spec.path;
  if (typeof pathRaw !== "string" || !pathRaw.startsWith("$")) {
    throw new Error("$unwind očekává cestu začínající '$'.");
  }
  const fieldPath = pathRaw.slice(1);
  const preserve = Boolean(isPlainObject(spec) && spec.preserveNullAndEmptyArrays);
  const idxField =
    isPlainObject(spec) && typeof spec.includeArrayIndex === "string"
      ? spec.includeArrayIndex
      : null;

  const out = [];
  for (const doc of docs) {
    const val = getFieldPath(doc, fieldPath);
    if (Array.isArray(val)) {
      if (val.length === 0) {
        if (preserve) {
          const copy = deepClone(doc);
          deleteFieldPath(copy, fieldPath);
          if (idxField) copy[idxField] = null;
          out.push(copy);
        }
      } else {
        for (let i = 0; i < val.length; i += 1) {
          const copy = deepClone(doc);
          setFieldPath(copy, fieldPath, deepClone(val[i]));
          if (idxField) copy[idxField] = i;
          out.push(copy);
        }
      }
    } else if (val === null || val === undefined) {
      if (preserve) {
        const copy = deepClone(doc);
        if (idxField) copy[idxField] = null;
        out.push(copy);
      }
    } else {
      const copy = deepClone(doc);
      if (idxField) copy[idxField] = null;
      out.push(copy);
    }
  }
  return out;
}

function applyLookup(docs, spec, collections) {
  if (!isPlainObject(spec) || !spec.from || !spec.localField || !spec.foreignField || !spec.as) {
    throw new Error("$lookup vyžaduje pole from, localField, foreignField, as.");
  }
  const foreignDocs = Array.isArray(collections[spec.from])
    ? collections[spec.from]
    : [];

  return docs.map((doc) => {
    const copy = deepClone(doc);
    const localVal = getFieldPath(doc, spec.localField);
    const localValues = Array.isArray(localVal) ? localVal : [localVal];
    const matched = foreignDocs
      .filter((fdoc) => {
        const fval = getFieldPath(fdoc, spec.foreignField);
        if (Array.isArray(fval)) {
          return fval.some((fv) =>
            localValues.some((lv) => deepEqual(lv, fv, 0)),
          );
        }
        return localValues.some((lv) => deepEqual(lv, fval, 0));
      })
      .map(deepClone);
    setFieldPath(copy, spec.as, matched);
    return copy;
  });
}

export function runPipeline(docs, pipeline, collections = {}) {
  if (!Array.isArray(pipeline)) {
    throw new Error("Aggregation pipeline musí být pole kroků.");
  }
  let current = docs.map(deepClone);
  for (const stage of pipeline) {
    if (!isPlainObject(stage)) {
      throw new Error("Každý krok pipeline musí být objekt.");
    }
    const keys = Object.keys(stage);
    if (keys.length !== 1) {
      throw new Error("Každý krok pipeline smí obsahovat právě jeden operátor.");
    }
    const op = keys[0];
    const arg = stage[op];
    switch (op) {
      case "$match":
        current = current.filter((d) => matchDoc(d, arg));
        break;
      case "$project":
        current = applyProject(current, arg);
        break;
      case "$addFields":
      case "$set":
        if (!isPlainObject(arg)) {
          throw new Error(`${op} očekává objekt.`);
        }
        current = current.map((d) => {
          const copy = deepClone(d);
          for (const [k, expr] of Object.entries(arg)) {
            setFieldPath(copy, k, evalExpr(d, expr));
          }
          return copy;
        });
        break;
      case "$group":
        current = applyGroup(current, arg);
        break;
      case "$sort":
        current = applySort(current, arg);
        break;
      case "$skip": {
        const n = Math.max(0, Number(arg) || 0);
        current = current.slice(n);
        break;
      }
      case "$limit": {
        const n = Math.max(0, Number(arg) || 0);
        current = current.slice(0, n);
        break;
      }
      case "$count":
        current = current.length ? [{ [String(arg || "count")]: current.length }] : [];
        break;
      case "$unwind":
        current = applyUnwind(current, arg);
        break;
      case "$lookup":
        current = applyLookup(current, arg, collections);
        break;
      default:
        throw new Error(`Nepodporovaný krok pipeline: ${op}`);
    }
  }
  return current.slice(0, MAX_DOCS_OUTPUT);
}

export function introspectCollections(seedDocs) {
  const collections = normalizeCollections(seedDocs);
  return Object.entries(collections).map(([name, docs]) => {
    const fieldTypes = new Map();
    for (const doc of docs.slice(0, 50)) {
      if (!isPlainObject(doc)) continue;
      for (const [k, v] of Object.entries(doc)) {
        const t = v === null ? "null" : Array.isArray(v) ? "array" : typeof v;
        if (!fieldTypes.has(k)) fieldTypes.set(k, new Set());
        fieldTypes.get(k).add(t);
      }
    }
    return {
      name,
      count: docs.length,
      fields: Array.from(fieldTypes.entries()).map(([field, types]) => ({
        name: field,
        types: Array.from(types),
      })),
    };
  });
}

function normalizeCollections(seedDocs) {
  if (Array.isArray(seedDocs)) {
    return { items: seedDocs };
  }
  if (isPlainObject(seedDocs)) {
    const out = {};
    for (const [k, v] of Object.entries(seedDocs)) {
      if (Array.isArray(v)) {
        out[k] = v;
      }
    }
    return out;
  }
  return { items: [] };
}

function docsToTable(docs) {
  const cols = [];
  const seen = new Set();
  for (const doc of docs) {
    if (!isPlainObject(doc)) continue;
    for (const k of Object.keys(doc)) {
      if (!seen.has(k)) {
        seen.add(k);
        cols.push(k);
      }
    }
  }
  const rows = docs.map((doc) =>
    cols.map((c) => {
      const v = isPlainObject(doc) ? doc[c] : undefined;
      if (v === undefined) return null;
      if (typeof v === "object" && v !== null) return JSON.stringify(v);
      return v;
    }),
  );
  return { columns: cols, rows };
}

function buildTableDiff(expectedTable, actualDocs) {
  const act = docsToTable(actualDocs);
  const cols =
    expectedTable && Array.isArray(expectedTable.columns) && expectedTable.columns.length
      ? expectedTable.columns.map(String)
      : act.columns;
  const expRows =
    expectedTable && Array.isArray(expectedTable.rows)
      ? expectedTable.rows
      : act.rows;
  const maxLen = Math.max(expRows.length, act.rows.length);
  const rowMatches = [];
  for (let i = 0; i < maxLen; i += 1) {
    if (i >= expRows.length || i >= act.rows.length) {
      rowMatches.push(false);
    } else {
      rowMatches.push(deepEqual(act.rows[i], expRows[i], 1e-4));
    }
  }
  return {
    columns: cols,
    expected_rows: expRows,
    actual_rows: act.rows,
    row_matches: rowMatches,
    matched: expRows.length === act.rows.length && rowMatches.every(Boolean),
  };
}

export function executeNoSqlTask(payload) {
  const started = performance.now();
  const collections = normalizeCollections(payload.seedDocs || payload.dataset);
  const defaultCollectionName = Object.keys(collections)[0] || "items";

  let parsedInput;
  try {
    parsedInput = JSON.parse(String(payload.code || ""));
  } catch (err) {
    return {
      ok: false,
      status: "error",
      stdout: "",
      stderr: `Neplatný JSON dotaz/pipeline: ${String(err && err.message ? err.message : err)}`,
      tests: [],
      table_diff: null,
      plots: [],
      elapsed_ms: Math.round(performance.now() - started),
    };
  }

  let outputDocs;
  try {
    if (Array.isArray(parsedInput)) {
      const baseDocs = collections[defaultCollectionName] || [];
      outputDocs = runPipeline(baseDocs, parsedInput, collections);
    } else if (isPlainObject(parsedInput)) {
      const colName = String(parsedInput.collection || defaultCollectionName);
      const baseDocs = collections[colName] || [];
      if (parsedInput.operation === "find" || parsedInput.filter !== undefined) {
        const stages = [];
        if (parsedInput.filter) stages.push({ $match: parsedInput.filter });
        if (parsedInput.sort) stages.push({ $sort: parsedInput.sort });
        if (parsedInput.skip) stages.push({ $skip: parsedInput.skip });
        if (parsedInput.limit) stages.push({ $limit: parsedInput.limit });
        if (parsedInput.projection) stages.push({ $project: parsedInput.projection });
        outputDocs = runPipeline(baseDocs, stages, collections);
      } else if (Array.isArray(parsedInput.pipeline)) {
        outputDocs = runPipeline(baseDocs, parsedInput.pipeline, collections);
      } else {
        throw new Error(
          "Zadejte buď JSON pole pipeline kroků, nebo objekt s polem 'pipeline' či 'filter'.",
        );
      }
    } else {
      throw new Error("Vstup musí být JSON pole nebo objekt.");
    }
  } catch (err) {
    return {
      ok: false,
      status: "error",
      stdout: "",
      stderr: String(err && err.message ? err.message : err),
      tests: [],
      table_diff: null,
      plots: [],
      elapsed_ms: Math.round(performance.now() - started),
    };
  }

  const tableDiff = buildTableDiff(payload.expectedTable, outputDocs);
  const allTests = [
    ...(Array.isArray(payload.visibleTests)
      ? payload.visibleTests.map((t) => ({ ...t, hidden: false }))
      : []),
    ...(Array.isArray(payload.hiddenTests)
      ? payload.hiddenTests.map((t) => ({ ...t, hidden: true }))
      : []),
  ];

  const tests = allTests.map((tc) => {
    const tol = Number.isFinite(Number(tc.tolerance)) ? Number(tc.tolerance) : 1e-4;
    let passed = false;
    let actual = outputDocs;
    if (tc.code === "__COUNT__") {
      actual = outputDocs.length;
      passed = actual === Number(tc.expected);
    } else if (tc.code === "__DOCS__" || !tc.code) {
      passed = deepEqual(outputDocs, tc.expected, tol);
    } else if (tc.code.startsWith("field:")) {
      const path = tc.code.slice("field:".length);
      actual = outputDocs.map((d) => getFieldPath(d, path));
      passed = deepEqual(actual, tc.expected, tol);
    } else {
      passed = deepEqual(outputDocs, tc.expected, tol);
    }
    return {
      name: String(tc.name || "NoSQL Test"),
      passed,
      actual: tc.hidden ? (passed ? "ověřeno" : "neshoda") : actual,
      expected: tc.hidden ? null : tc.expected,
      message: "",
      elapsed_ms: Math.round(performance.now() - started),
      hidden: Boolean(tc.hidden),
    };
  });

  const allTestsPass = tests.every((t) => t.passed);
  const diffPass = payload.expectedTable ? tableDiff.matched : true;

  return {
    ok: allTestsPass && diffPass,
    status: "ok",
    stdout: truncateUtf8(JSON.stringify(outputDocs, null, 2)),
    stderr: "",
    tests,
    table_diff: tableDiff,
    plots: [],
    elapsed_ms: Math.round(performance.now() - started),
  };
}

if (
  typeof self !== "undefined" &&
  typeof self.postMessage === "function" &&
  typeof document === "undefined"
) {
  self.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg) return;
    if (msg.type === "schema") {
      const schema = introspectCollections(
        msg.payload && (msg.payload.seedDocs || msg.payload.dataset),
      );
      self.postMessage({
        type: "result",
        requestId: msg.requestId,
        result: {
          ok: true,
          status: "ok",
          stdout: JSON.stringify(schema, null, 2),
          stderr: "",
          tests: [],
          table_diff: null,
          plots: [],
          schema,
          elapsed_ms: 0,
        },
      });
      return;
    }
    if (msg.type === "run") {
      const result = executeNoSqlTask(msg.payload || {});
      self.postMessage({
        type: "result",
        requestId: msg.requestId,
        result,
      });
    }
  });
}
