const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const rupees = (v, exact = false) => (exact ? inr2 : inr0).format(Number(v || 0));

export const signedRupees = (v) => {
  const n = Number(v || 0);
  return (n < 0 ? "−" : "") + inr0.format(Math.abs(n));
};

export const qty = (v) => {
  const n = Number(v);
  return n.toLocaleString("en-IN", { maximumFractionDigits: n < 1 ? 8 : 4 });
};

const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const day = (iso) => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `${Number(d)} ${months[Number(m) - 1]} ${y}`;
};

export const fyLabel = (fy) => (fy ? `FY ${fy.replace("-", "–")}` : "");

export const pct = (rate) => `${(Number(rate) * 100).toLocaleString("en-IN", { maximumFractionDigits: 1 })}%`;

export const TERM_LABEL = { ST: "Short-term", LT: "Long-term", VDA: "Crypto (VDA)" };

export function transfersToCsv(transfers, fy) {
  const head = ["FY", "Asset class", "Instrument", "ISIN", "Sale date", "Purchase date", "Quantity", "Sale value (INR)", "Cost (INR)", "Gain (INR)", "Treatment"];
  const lines = [head];
  for (const t of transfers) {
    for (const p of t.parts) {
      lines.push([fy, t.asset_class === "crypto" ? "Crypto (VDA)" : "Equity", t.symbol, t.isin || "", t.sell_date, p.buy_date, p.quantity, p.proceeds, p.cost, p.gain, TERM_LABEL[p.term]]);
    }
    if (t.unmatched_quantity) {
      lines.push([fy, t.asset_class, t.symbol, t.isin || "", t.sell_date, "UNMATCHED", t.unmatched_quantity, "", "", "", "Not included"]);
    }
  }
  return lines.map((r) => r.map((c) => (/[",\n]/.test(String(c)) ? `"${String(c).replace(/"/g, '""')}"` : c)).join(",")).join("\n");
}

export function download(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
