export function formatBytes(bytes) {
  if (bytes == null) return "–";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export function formatDuration(sec) {
  if (sec == null) return "";
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function formatLocator(kind, locator) {
  if (locator == null) return "";
  if (["frame", "transcript"].includes(kind)) return `@ ${formatDuration(locator)}`;
  if (["page", "pdf_text", "ocr"].includes(kind)) return `p.${locator}`;
  return "";
}

export const SIGNAL_LABELS = { visual: "Visual", text: "Text meaning", keyword: "Keyword" };
