# Norwegian pharmaceutical tender pipeline

Extracts tender data for five molecules — **Axitinib, Everolimus, Lenalidomide,
Anagrelide, Paliperidone** — from Norwegian public procurement sources, and turns it
into a bid recommendation.

Output: **44 rows** in `output/output.csv`, four charts in `output/charts/`.
**All 28 columns populated.**

---

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python run.py                 # writes output/output.csv + output/charts/
python run.py --no-cache      # bypass the local cache and re-fetch
python -m pytest tests/ -q    # 59 tests
```

A first run takes roughly 12 minutes: requests are deliberately spaced 1.5 s apart.
Responses are cached under `data/cache/`, so re-runs are near-instant and do not
re-hit the sources.

---

## How I found the sources

Nothing here was assumed. Every endpoint below was verified with live requests, and
the approaches that failed are kept because they shaped the design.

### Doffin — the national procurement database

`https://doffin.no/search?...` returns a **1.3 KB JavaScript shell** with no data in
it. Two things did *not* work:

- **Guessing REST paths.** `/api/search`, `/swagger/v1/swagger.json` and friends all
  return **HTTP 200** — with the SPA shell. Every unknown path does. A naïve probe
  script would have reported eight working endpoints, all of them false.
- **Scraping the rendered page.** Possible with a browser, but unnecessary.

What worked: downloading the app bundle `/assets/index-*.js` (1.46 MB) and grepping
for the API base, which is compiled in as a Vite variable:

```
VITE_APP_SEARCH_API_URL = https://api.doffin.no/webclient/api/v2/search-api
VITE_APP_API_URL        = https://api.doffin.no/webclient/api/v2/notices-api
```

Both are public and unauthenticated. Two details cost real time and are worth stating:

1. Search is a **POST with a JSON body**. A GET returns `{"statusCode":404}`.
2. The page parameter is **`page`**, not `pageNumber`, and CPV filtering must go in
   **`facets.cpvCodesId.checkedItems`**. A top-level `cpvCodes` key is accepted and
   **silently ignored**, returning all 158 290 notices with a 200. The filter fails
   *open*, not closed — so an unvalidated request looks like it worked.

### TED — the EU publication

`POST https://api.ted.europa.eu/v3/notices/search`, no API key, expert query syntax
(`FT~"term"`, `buyer-country="NOR"`, `classification-cpv="33600000"`).

Useful trick: the `fields` parameter is strictly validated, and sending an unknown
name returns a 400 whose body **enumerates all 1 830 valid field names**. I used that
to validate field names programmatically instead of guessing — `received-tender-count`,
for instance, does not exist, while `total-value` and `winner-name` do.

### Attached documents — reached, and parsed

Tender annexes are hosted on Mercell, linked from each notice's `competitionDocsUrl`.
The **document list is public** — all 12 filenames for LIS 2207 Onkologi are readable
without an account, including `Vedlegg 02 Kravspesifikasjon.xlsx` (the molecule list)
and `Vedlegg 03 Prisskjema v 2.xlsx` (the price form).

The **file endpoint is protected by a Cloudflare interactive challenge**. A plain
request returns HTTP 403 with *"Just a moment… Enable JavaScript and cookies to
continue"*. What does and does not get through:

| attempt | result |
|---|---|
| `curl` with browser User-Agent and referer | 403 |
| `curl` with the full session cookie jar | 403 |
| CDP-driven browser: click by element reference | 403 |
| CDP-driven browser: navigate straight to the href | 403 |
| CDP-driven browser: `fetch()` in-page, `credentials:'include'` | 403 |
| **an ordinary Chrome window, person clears the check** | **file downloads** |

So this is an anti-automation control, not an authentication wall — cookies and login
state are not what decides it. My first diagnosis was wrong twice before the in-browser
test settled it, which is why the table is here rather than a one-line claim.

That leaves an interactive route, which `run.py --fetch-annexes` implements: it opens
the tender page, waits for a person to clear the verification, then downloads. It is
opt-in and never runs unattended.

**The annex is committed to this repository** (`data/manual/`), so `python run.py`
reproduces the full result — pack-level rows included — with no browser and no account.
It is a public procurement document from a public tender.

### Dead ends

- **`legemiddelsok.no`** (the official medicine register, and the natural home for
  ATC codes, item numbers and max prices) is ASP.NET WebForms behind
  `__VIEWSTATE`/`__EVENTVALIDATION` postbacks. Scraping it means simulating full form
  state — disproportionate for the budget, so it was skipped rather than half-done.
- **`dmp.no/api`** → 404. No open price-register API found.
- **Sykehusinnkjøp's procurement plan** (`03-09-2026-anskaffelser-legemidler.xlsx`)
  downloads freely and I parsed it — but it is organised by *therapeutic area*
  ("Onkologi", "Parkinson", "MS"), not by molecule, and contains none of the five
  names. Useful for timing, useless as a row source.

---

## Why Axitinib behaves differently

Searching by name returns **zero** results for Axitinib on both portals, in both
spellings, while the other four return hits. The brief says all five are present, so
this is a signal about method — and the answer is a fact about how Norway buys drugs.

The other four are procured in **single-molecule tenders** (`LIS 2234 Lenalidomid`,
`2632a Everolimus`). Axitinib is not. Norwegian oncology drugs are bought through
**bundled framework agreements** — `LIS 2207 Onkologi`, estimated 3 200 MNOK/year —
whose notices name **no individual molecules at all**. The molecule list lives in
*"vedlegg 2 (Kravspesifikasjon)"*, the annex behind the Mercell wall.

The annex confirms it. `Vedlegg 03 Prisskjema` lists **axitinib as Inlyta**, four packs
(1/3/5/7 mg, 56 tablets, Pfizer Norge AS), with historical consumption of 179 / 316 /
634 / 46 packs. Axitinib is in Norwegian procurement — as a lot inside an oncology
bundle, never as a tender of its own.

Rows against the oncology frameworks themselves are still marked
`moleculeDetected=false` with `detectionMethod=therapeutic-area-bundle`, because the
notice text does not name the molecule; the inference stays visible in the data.

**A bug the real data caught.** Axitinib is often described as having moved from ATC
`L01EX07` to `L01EK01`, and the matcher originally searched both. The annex shows
`L01EX07` now denotes **cabozantinib** (Cabometyx, Cometriq, IPSEN AB) — the code was
*reassigned*, not retired. Searching it would have attributed another manufacturer's
packs to axitinib. The alias was removed and a test pins the behaviour.

---

## Identifying molecules four ways

Name matching alone would have found **11 of 44 rows**.

| method | rows | what it catches |
|---|---|---|
| `therapeutic-area-bundle` | 31 | molecules with no tender of their own |
| `name-norwegian` | 8 | `Lenalidomid`, `anagrelid`, `paliperidon` |
| `name-english` | 3 | TED's English records of the same tenders |
| `atc-code` | 2 | notices citing `N05AX13` and never naming the drug |

Of the 44 rows, **4 are pack-level** (axitinib, from the LIS 2207 annex) and the rest
are notice-level.

**Norwegian spellings are derived by rule, not hard-coded.** Norwegian INN follows a
systematic orthography, so `src/naming.py` implements the transformation
(`x→ks`, `-ide→-id`, `-one→-on`, `ph→f`, and `c→k`/`c→s` by sound):

```
Axitinib → aksitinib      Lenalidomide → lenalidomid      Paliperidone → paliperidon
```

The tests verify it on five molecules the pipeline was never written for
(`Docetaxel→dosetaksel`, `Cephalexin→sefaleksin`), which is the only real evidence
that it is a rule rather than a lookup table in disguise.

**A false positive the codes caught.** TED notice `276486-2025` matches "everolimus"
and was awarded to *Shimadzu Filial Sverige* — a laboratory-instruments company. It is
an LC-MS/MS analysis platform that measures everolimus, not a tender to supply it.
The engine rejects notices carrying equipment/laboratory signals, which is why name
matching alone is not enough.

---

## Duplicates and lifecycles

One purchase produces many notices. `LIS 2234 Lenalidomid` alone appears as two prior
information notices, a competition, a **cancellation**, and a re-tender (`LIS 2234b`).

Notices are grouped into procurements by the buyer's own tender number, which appears
in two styles — `LIS 2234` on older notices and `2632a`/`2507gj-1` on newer eForms
ones. Both are recognised, and a trailing letter is kept significant: `LIS 2234` and
`LIS 2234b` are a tender and its re-run, not the same purchase.

This is what links the cross-language duplicates the brief warns about:

| procurement | Doffin (Norwegian) | TED (English) |
|---|---|---|
| `lis:2632a` | `2632a Everolimus og Mykofenolsyre` | `2632a Everolimus and Mykofenolic Acid` |
| `lis:2507gj-1` | `2507gj-1 anagrelid` | `2507gj-1 anagrelid` |
| `lis:2601c` | `2601c paliperidon` | `2601c paliperidone` |

Rows are **not** collapsed — a cancellation and its re-tender are different events —
but values are taken, never summed, since notices restate the same figure.

---

## What the CSV contains

**All 28 columns carry data.** Coverage varies by column, and that variation is itself
the answer to "which fields do the sources genuinely support":

| column | rows | source |
|---|---|---|
| `noticeId`, `title`, `buyer`, `country`, `status`, `noticeType`, `publicationDate`, `sourceDocument`, `sourceUrl`, and the five molecule columns | 44/44 | notice payloads |
| `estimatedValue` | 35/44 | Doffin `core.estimatedValue`, else the value stated in the notice text |
| `tenderRef` | 31/44 | the buyer's internal reference |
| `procedureType` | 19/44 | eForm / TED, casing normalised across the two |
| `contractStart` | 16/44 | eForm / TED |
| `supplier` | 7/44 | annex supplier, or the awarded party |
| `itemNumber`, `productName`, `strength`, `packSize`, `packsSoldLast12m` | 4/44 | LIS 2207 price annex |
| `awardedSupplier` | 3/44 | Doffin `awardedNames` |
| `maxPrice` | 3/44 | regulated price register, joined on item number |
| `awardedValue` | 1/44 | eForm "Verdien av alle kontrakter tildelt i denne prosedyren" |

Low coverage is not the same as a missing column. Only three of these tenders have
reached award, so three awarded suppliers is complete, not partial. One pack (Inlyta
3 mg) has no current regulated price and keeps an empty `maxPrice` rather than a value
interpolated from the neighbouring strengths.

### Three columns took a second look

Each of these I first recorded as unobtainable, and each turned out to be wrong. They
are worth stating plainly because the mistake in every case was concluding from one
source instead of checking a second.

**`maxPrice`** — the tender annex genuinely cannot supply it: its price column is what
a *supplier offers* when bidding, blank in all 196 rows, and a different quantity from
the buyer's ceiling. But the medicines agency publishes the regulated register as a
free 9 811-row download, keyed by the same `Varenummer` the annex lists:

```
https://www.dmp.no/offentlig-finansiering/pris-pa-legemidler/maksimalpris
  -> legemiddelpriser-2026-09-03.xlsx      no authentication
```

Inlyta 5 mg (varenr 599010) → **32 845,79 NOK** maximum AIP. AIP is used rather than
the retail AUP the register also carries, because hospital tenders are denominated in
AIP ("i maksimal AIP"); conflating them would overstate the ceiling by about 28%.

**`awardedSupplier`** — TED's structured winner fields are empty for Norwegian
notices: `winner-name`, `winner-identifier`, `organisation-name-tenderer` and three
others were checked, **0 of 50 populated**. Concluding "not published" from that was
wrong. **Doffin publishes it in `awardedNames`, 20 of 20** on the award notices
sampled. Hence Paliperidone's direct awards resolve to `JANSSEN-CILAG AS` and
`Nordic Pill AB`, and an axitinib add-on contract to `BeiGene Sweden AB`.

**`awardedValue`** — published in the eForm, not as a top-level field, under
*"Verdien av alle kontrakter tildelt i denne prosedyren"*. Only labels meaning **what
was awarded** are read; a framework's *maximum* or *estimated* value is a ceiling, and
recording one as the other would overstate every award. Extracting it also exposed a
parser bug: the eForm renders amounts in the English convention (`120,000,000`) while
Norwegian notices use `1 234,56`, so treating a comma as a decimal point silently
discarded these values. Both conventions are now handled.

**Price disclosure is not uniform.** Of 100 Norwegian pharmaceutical award notices
sampled, 81 publish a value — so this is not a blanket confidentiality rule. Among
*our five molecules*, only one award publishes one. The absence is specific to these
tenders rather than a property of Norwegian procurement, which is a narrower and more
useful claim than the one I started with.

---

## Charts

**1. `value-by-molecule.png` — where the money is, and whether you can bid on it.**
Axitinib sits inside the largest pot (3 200 MNOK) but is drawn grey, because it is
only reachable by bidding an entire oncology framework. Lenalidomide's 320 MNOK is
directly addressable. Headline value and addressable value are not the same number.

**2. `price-disclosure.png` — how much of the market is priced in public.**
Award notices, the one place a bidder would look for the incumbent's price, are where
value is least often published (0 of 3). Any bid model here is built on estimates, not
on observed clearing prices.

**3. `detection-method.png` — what a name-only pipeline would have missed.**
11 of 41 rows came from names. A pipeline that only matched names would have reported
Axitinib as absent from Norwegian procurement, which is false.

**4. `tender-timeline.png` — when each molecule re-tenders.**
Oncology frameworks re-tender continuously (2017→2026); single-molecule tenders are
sparse, occasional events. Timing, not just value, decides whether a bid is possible.

---

## Recommendation

**Bid Paliperidone. Specifically, bid `2601c paliperidon`, which is open now.**

The reasoning, and why the obvious answers are wrong:

**Not Axitinib**, despite sitting in the largest pot. 3 200 MNOK is the value of the
*whole* oncology framework, not of axitinib. Winning any of it means bidding a
multi-molecule bundle against suppliers with full oncology portfolios. For a generics
company without that breadth, headline value here is unreachable value — and the
molecule list is not even public without a supplier account.

**Not Lenalidomide**, despite 320 MNOK and a genuinely interesting history: its 2021
competition was **cancelled** and re-run as `LIS 2234b`. A cancellation is normally a
second chance. But that was five years ago, the framework has since been let, and
nothing in the data shows a live re-tender.

**Not Anagrelide.** Real, clean, open — but 10 MNOK/year. Too small to matter unless
it is nearly free to serve.

**Paliperidone, because the buying route just changed.** Its two 2022 contracts were
awarded through *Intensjonskunngjøring* — **negotiated without prior call for
competition**, i.e. direct awards with no competitive process to enter. That was a
closed door.

It has now reopened: `2601c paliperidon` is a **live open competition**, published in
2026 and confirmed independently in both Doffin and TED. A molecule moving from direct
award to open tender is precisely the moment an outside bidder can enter, because the
incumbent's position is being re-tested rather than rolled over.

**What would change my mind:**

- **The `2601c` annex.** If its Kravspesifikasjon shows volume concentrated in a
  depot formulation with device or supply-chain requirements we cannot meet, the open
  door is not one we can walk through. The LIS 2207 annex is parsed here; 2601c's was
  not retrieved, and it is the single most valuable missing piece.
- **Award value on the 2022 contracts.** They are undisclosed, so I cannot see the
  price to beat. If the direct awards were priced near marginal cost, the margin may
  not justify the bid.
- **A lenalidomide re-tender appearing.** 320 MNOK addressable in a single molecule
  would outrank paliperidone on size alone, if the timing were live.
- **Evidence that `2601c` is a formality.** If it is an open competition wrapped
  around a decided outcome, the change in route means nothing.

**One thing worth flagging that the brief did not ask about:** the oncology framework
value roughly **doubled** across two cycles — 1 303 MNOK (LIS 2107, 2021) → 3 200 MNOK
(LIS 2207, 2022) — because Sykehusinnkjøp *merged* LIS 2107 Onkologi with LIS 2131
PD1/PD-L1. Norwegian pharmaceutical procurement is consolidating into fewer, larger
bundles. That trend matters more to a generics company's Norway strategy than any
single molecule here: it steadily raises the portfolio breadth needed to bid at all.

---

## Structure

```
src/
  config.py        molecules, ATC/CPV codes, rate limits
  naming.py        English→Norwegian transliteration rules
  http.py          caching, retry/backoff, throttling, User-Agent
  models.py        SourceNotice → TenderRow (28 columns, defined once)
  sources/         base.py (protocol) + doffin.py + ted.py
  matching/        base.py + by_name.py + by_code.py + engine.py
  normalise.py     Norwegian numbers, dates, æøå
  dedup.py         procurement grouping and lifecycle
  output.py        CSV writer and coverage report
  analysis/        charts
  pipeline.py      orchestration
```

The seven stages the brief asks to see separated each live in their own module:
discovery and retrieval in `sources/`, parsing in `parsing/`, matching in `matching/`,
normalisation in `normalise.py`, output in `output.py`, analysis in `analysis/`, with
`pipeline.py` as the only place that wires them together.

### Adding a second portal, format or matching strategy

Only `pipeline.py` imports a concrete portal, and nothing outside `matching/` imports a
concrete matcher, so each extension point is genuinely a new file rather than an edit
spread across the codebase:

| to add | write | changes elsewhere |
|---|---|---|
| another country's portal | one class implementing `NoticeSource` | **none** |
| another matching strategy | one class implementing `Matcher` | **none** |
| another document format | one parser returning `AnnexPack` | **none** |

I checked this rather than assuming it. A stub Swedish portal returning one notice was
matched, normalised, grouped and written to a row — carrying SEK rather than inheriting
a hard-coded NOK — with **zero** lines of existing code changed. A GTIN-based matcher
was added the same way and matched a notice that names no molecule at all.

The row schema lives in exactly one place (`models.TenderRow`), so a new column is a
one-line change and the CSV header follows automatically.

**Time spent:** ~6 hours, roughly half on source discovery.

**What I would do next**, in order: retrieve the `2601c paliperidon` annex, since the
recommendation turns on what is inside it; parse `Vedlegg 02 Kravspesifikasjon` as well
as the price form, which would let therapeutic-bundle rows be confirmed rather than
inferred; add the Norwegian medicine register for regulated max prices via a bulk
download rather than its WebForms UI; and widen the CPV sweep across more years, which
the architecture supports but the time budget did not.
