import { day, qty, rupees } from "../format.js";

const FIELDS = [
  { key: "date", label: "Trade date", required: true },
  { key: "symbol", label: "Instrument", required: true, hint: "Stock symbol, fund name or crypto pair" },
  { key: "side", label: "Buy or sell", hint: "If empty, negative quantities are read as sells" },
  { key: "quantity", label: "Quantity", required: true },
  { key: "price", label: "Price per unit" },
  { key: "total_inr", label: "Total value in ₹", hint: "Needed for crypto pairs not priced in rupees" },
  { key: "isin", label: "ISIN", equityOnly: true },
  { key: "fees", label: "Brokerage", equityOnly: true, hint: "Not STT or GST" },
];

const SOURCE = {
  ai: { label: "Columns read by AI", tone: "ai" },
  template: { label: "Matched a saved layout", tone: "template" },
  keywords: { label: "Matched by column names", tone: "keywords" },
};

export default function MappingReview({ item, onChange, onConfirm, onDiscard }) {
  const { mapping, headers, sampleRows } = item;
  const source = SOURCE[item.source] || SOURCE.keywords;
  const isCrypto = mapping.asset_class === "crypto";

  const setColumn = (field, header) =>
    onChange({
      ...mapping,
      columns: { ...mapping.columns, [field]: header || null },
      confidence: { ...mapping.confidence, [field]: header ? 1 : 0 },
    });

  const sample = (header) => {
    if (!header) return "";
    const i = headers.indexOf(header);
    return sampleRows[0]?.[i] ?? "";
  };

  const sides = mapping.side_values || { buy: [], sell: [] };
  const readable = item.legCount > 0 ? item.rowCount - item.errorCount : 0;
  const canImport = readable > 0 && !item.busy;

  return (
    <section className="review" aria-labelledby={`review-${item.importId}`}>
      <header className="review__head">
        <div>
          <h3 id={`review-${item.importId}`} className="review__file">{item.filename}</h3>
          <p className="review__meta">{item.rowCount} rows found</p>
        </div>
        <span className={`source source--${source.tone}`}>{source.label}</span>
      </header>

      {item.sourceNotes?.map((n, i) => (
        <p key={i} className="note note--quiet">{n}</p>
      ))}
      {mapping.notes?.map((n, i) => (
        <p key={i} className="note">{n}</p>
      ))}

      <div className="review__toggles">
        <fieldset className="seg">
          <legend>This file contains</legend>
          <label className={!isCrypto ? "is-on" : ""}>
            <input type="radio" name={`ac-${item.importId}`} checked={!isCrypto} onChange={() => onChange({ ...mapping, asset_class: "equity" })} />
            Stocks and mutual funds
          </label>
          <label className={isCrypto ? "is-on" : ""}>
            <input type="radio" name={`ac-${item.importId}`} checked={isCrypto} onChange={() => onChange({ ...mapping, asset_class: "crypto" })} />
            Crypto
          </label>
        </fieldset>
        <fieldset className="seg">
          <legend>Dates are written</legend>
          <label className={mapping.day_first ? "is-on" : ""}>
            <input type="radio" name={`df-${item.importId}`} checked={mapping.day_first} onChange={() => onChange({ ...mapping, day_first: true })} />
            Day first
          </label>
          <label className={!mapping.day_first ? "is-on" : ""}>
            <input type="radio" name={`df-${item.importId}`} checked={!mapping.day_first} onChange={() => onChange({ ...mapping, day_first: false })} />
            Month first
          </label>
        </fieldset>
      </div>

      <div className="fields" role="table" aria-label="Column mapping">
        <div className="fields__row fields__row--head" role="row">
          <span role="columnheader">Lotwise needs</span>
          <span role="columnheader">Column in your file</span>
          <span role="columnheader">First value</span>
        </div>
        {FIELDS.filter((f) => !(f.equityOnly && isCrypto)).map((f) => {
          const header = mapping.columns[f.key];
          const conf = mapping.confidence?.[f.key] ?? 0;
          const unsure = header && conf < 0.8;
          const missing = f.required && !header;
          return (
            <div key={f.key} className={`fields__row ${unsure ? "is-unsure" : ""} ${missing ? "is-missing" : ""}`} role="row">
              <span role="cell" className="fields__label">
                {f.label}
                {f.required && <span className="req" aria-label="required">*</span>}
                {f.hint && <small>{f.hint}</small>}
              </span>
              <span role="cell">
                <select value={header || ""} onChange={(e) => setColumn(f.key, e.target.value)} aria-label={`Column for ${f.label}`}>
                  <option value="">Not in this file</option>
                  {headers.map((h) => (
                    <option key={h} value={h}>{h}</option>
                  ))}
                </select>
                {unsure && <small className="flag">Check this one</small>}
              </span>
              <span role="cell" className="fields__sample">{sample(header)}</span>
            </div>
          );
        })}
      </div>

      {mapping.columns.side && (sides.buy.length > 0 || sides.sell.length > 0) && (
        <p className="review__sides">
          Buys are marked {sides.buy.map((v) => `“${v}”`).join(", ") || "—"}; sells are marked {sides.sell.map((v) => `“${v}”`).join(", ") || "—"}.
        </p>
      )}

      <div className="preview">
        <h4>How Lotwise reads the first trades</h4>
        {item.preview?.length ? (
          <div className="scroll-x">
            <table className="table table--compact">
              <thead>
                <tr><th>Date</th><th>Asset</th><th>Type</th><th className="num">Quantity</th><th className="num">Value</th></tr>
              </thead>
              <tbody>
                {item.preview.map((l, i) => (
                  <tr key={i}>
                    <td>{day(l.date)}</td>
                    <td>{l.symbol}</td>
                    <td><span className={`side side--${l.side.toLowerCase()}`}>{l.side === "BUY" ? "Buy" : "Sell"}</span></td>
                    <td className="num">{qty(l.quantity)}</td>
                    <td className="num">{rupees(l.value_inr)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="note">No trades can be read yet. Pick the columns marked with an asterisk.</p>
        )}
        {item.errorCount > 0 && (
          <details className="errors">
            <summary>{item.errorCount} {item.errorCount === 1 ? "row" : "rows"} can't be read and will be skipped</summary>
            <ul>
              {item.errors.map((e, i) => (
                <li key={i}>{e.row ? `Row ${e.row}: ` : ""}{e.message}</li>
              ))}
            </ul>
          </details>
        )}
      </div>

      {item.error && <p className="note note--error" role="alert">{item.error}</p>}

      <footer className="review__actions">
        <button className="btn btn--primary" disabled={!canImport} onClick={onConfirm}>
          {item.busy ? "Importing…" : `Import ${readable} ${readable === 1 ? "trade" : "trades"}`}
        </button>
        <button className="btn btn--ghost" onClick={onDiscard} disabled={item.busy}>Discard file</button>
      </footer>
    </section>
  );
}
