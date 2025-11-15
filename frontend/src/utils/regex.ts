export const pythonToJsFlags = (flags?: string) => {
  const allow = new Set(["i", "m", "s", "u"]);
  let out = "";
  for (const ch of (flags || "")) if (allow.has(ch) && !out.includes(ch)) out += ch;
  return out || "i";
};

export const makeRegex = (pattern?: string, flags?: string): RegExp | null => {
  console.log("makeRegex called with pattern:", pattern, "and flags:", flags);
  if (!pattern) return null;
  try {
    return new RegExp(pattern, pythonToJsFlags(flags));
  } catch {
    return null;
  }
};

export const isMatchAllRegex = (pattern?: string | null) =>
  !!pattern && /^\s*\^?\.\*\$?\s*$/.test(pattern);
