import { day, qty, signedRupees, TERM_LABEL } from "../format.js";

/**
 * One sale, drawn as the purchase lots it consumed.
 * Segment width = share of quantity; colour = tax treatment.
 */
export default function LotStrip({ parts, unmatched, size = "sm", showLabels = false }) {
  const total = parts.reduce((s, p) => s + Number(p.quantity), 0) + Number(unmatched || 0);
  if (!total) return null;
  const description = parts
    .map((p) => `${qty(p.quantity)} bought ${day(p.buy_date)}, ${TERM_LABEL[p.term].toLowerCase()}, ${signedRupees(p.gain)}`)
    .join("; ");

  const strip = (
    <div className={`lotstrip lotstrip--${size}`} role="img" aria-label={`Matched lots: ${description}${unmatched ? `; ${qty(unmatched)} unmatched` : ""}`}>
      {parts.map((p, i) => {
        const share = (Number(p.quantity) / total) * 100;
        return (
          <span
            key={i}
            className={`lot lot--${p.term}`}
            style={{ flexBasis: `${share}%` }}
            title={`${qty(p.quantity)} from ${day(p.buy_date)}, ${TERM_LABEL[p.term]}, ${signedRupees(p.gain)}`}
          />
        );
      })}
      {unmatched && (
        <span className="lot lot--unmatched" style={{ flexBasis: `${(Number(unmatched) / total) * 100}%` }} title={`${qty(unmatched)} with no matching purchase`} />
      )}
    </div>
  );
  if (!showLabels) return strip;

  return (
    <div className="lotstrip-labelled">
      {strip}
      <div className="lotstrip__labels" aria-hidden="true">
        {parts.map((p, i) => (
          <span key={i} className={`lotstrip__label lotstrip__label--${p.term}`} style={{ flexBasis: `${(Number(p.quantity) / total) * 100}%` }}>
            <strong>{qty(p.quantity)}</strong>
            bought {day(p.buy_date)}
            <em>{TERM_LABEL[p.term]}</em>
          </span>
        ))}
      </div>
    </div>
  );
}
