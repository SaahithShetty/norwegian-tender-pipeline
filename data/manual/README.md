# Tender annexes (optional input)

Drop LIS tender annex spreadsheets here to populate the pack-level columns
(`itemNumber`, `productName`, `strength`, `packSize`, `supplier`,
`packsSoldLast12m`).

**The pipeline runs fine without them.** If this directory is empty, those columns
are simply left empty and everything else is unaffected — no configuration, no
credentials, no failure.

## Where they come from

Each notice on Doffin links to its tender documents via `competitionDocsUrl`, which
points at Mercell. The **document list is public**, but downloading a file requires a
signed-in Mercell account:

```
https://www.mercell.com/en/tender/176511702/lis-2207-onkologi-tender.aspx
  -> LIS 2207 - Vedlegg 02 Kravspesifikasjon.xlsx   (requirement specification)
  -> LIS 2207 - Vedlegg 03 Prisskjema v 2.xlsx      (price form - the one used here)
```

An anonymous request to `GetFile.ashx?id=...` returns **HTTP 403**, including from
inside a real browser session, so the file cannot be fetched programmatically without
an account. The assignment lists "register as a supplier" under *You do not need to*,
which is why this is a manual, optional input rather than a pipeline step.

## Naming

Keep the original filename. The tender number in it (`LIS 2207`) is what attaches the
packs to the right notice — a file named arbitrarily will parse but match nothing.

## What the file actually contains

Worth knowing before reading too much into it: the Prisskjema is the **blank bidding
template** suppliers fill in. Its `TILBUDT GIP` (offered price) column is empty in
every row, so `maxPrice` cannot be sourced from it. What it does carry is item
numbers, product names, strengths, pack sizes, suppliers and — usefully — historical
consumption in `PAKNINGER <year>`.
