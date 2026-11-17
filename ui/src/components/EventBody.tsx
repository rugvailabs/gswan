import { useState } from "react";

const LIMIT = 420;

/** Long agent messages (verifier reports, turn replies) are collapsed so the feed stays scannable. */
export function EventBody({ body }: { body: string }) {
  const [open, setOpen] = useState(false);
  const long = body.length > LIMIT;
  return (
    <span className="whitespace-pre-wrap break-words">
      {long && !open ? `${body.slice(0, LIMIT).trimEnd()}…` : body}
      {long && (
        <button type="button" className="ml-2 text-xs underline tone-info" onClick={() => setOpen(!open)}>
          {open ? "show less" : `show more (${body.length} chars)`}
        </button>
      )}
    </span>
  );
}
