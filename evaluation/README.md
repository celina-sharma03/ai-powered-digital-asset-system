# Search evaluation

**Goal:** check that search returns the *right* assets, not only that the API responds, and use the
measurements to improve ranking.

- Queries: [queries.json](queries.json) (14 test searches: what the user wants and which assets are expected)
- Raw results: [results.md](results.md) (top-10 per query with relevance marks) and `results.json`
- Script: `backend/scripts/evaluate.py` (runs every query through the real `/api/search` endpoint)

## Method

**Ground truth.** Every file in the dataset was downloaded through a known source query (for example Pexels
"woman with cat", Internet Archive "annual report"). That query is recorded in `data/manifest.csv`.
**The indexer never sees the manifest.** Filenames are opaque (`pexels-photo-123.jpg`) and folders are random,
so search can only work from content. For each test search I listed which source queries count as relevant
(`expected.source_queries`).

These are **silver-standard** labels: fast to produce but noisy. A "cat" photo can also contain a woman, and a
home-plans book downloaded as "architecture plan" is also residential. So I **checked the top results by hand**,
looking at the source titles and at keyframes for videos. Clear-cut label errors are corrected in
[judgments_override.json](judgments_override.json), with the reason written next to each.

**Metrics.** Each query runs in five modes, to see what each signal contributes:

| Metric | Meaning |
|---|---|
| P@5, P@10 | Share of the first 5 / 10 results that are relevant: what the user actually sees |
| MRR | 1 / rank of the first relevant result (1.0 = the top result is relevant) |
| R@20 | Share of *all* relevant assets that appear in the top 20. Capped by design: with 120 cat photos, 20 results can only reach 0.17 |

## Results

Mean over the 14 queries:

| Mode | P@5 | P@10 | MRR | R@20 |
|---|---|---|---|---|
| **hybrid (final: gated fusion, best + ½ support, 30% cut-off)** | **0.93** | **0.90** | **1.00** | **0.37** |
| hybrid_plain (first version: plain RRF) | 0.41 | 0.55 | 0.53 | 0.27 |
| visual only (CLIP) | 0.90 | 0.86 | 0.94 | 0.33 |
| text only (BGE over extracted text) | 0.19 | 0.13 | 0.18 | 0.09 |
| keyword only (BM25) | 0.13 | 0.12 | 0.21 | 0.10 |

Per query (hybrid vs visual only):

| ID | Query | Hybrid P@10 | Hybrid MRR | Visual P@10 | Visual MRR | Notes |
|---|---|---|---|---|---|---|
| q01 | A woman standing with a cat | 1.0 | 1.00 | 1.0 | 1.00 | |
| q02 | Customer testimonial videos | 1.0 | 1.00 | 1.0 | 1.00 | Visual match on "person talking to camera"; intent "videos" boosts videos |
| q03 | Brochures related to residential projects | 0.9 | 1.00 | 0.8 | 1.00 | Text signal adds house-plan books and apartment brochures |
| q04 | Images showing a modern living room | 1.0 | 1.00 | 1.0 | 1.00 | |
| q05 | Videos containing construction activity | 1.0 | 1.00 | 1.0 | 1.00 | |
| q06 | A dog playing outside | 1.0 | 1.00 | 1.0 | 1.00 | |
| q07 | Aerial drone view of houses | 1.0 | 1.00 | 1.0 | 1.00 | |
| q08 | Business people shaking hands | 0.9 | 1.00 | 0.9 | 1.00 | One office-interview video without a handshake |
| q09 | Waves crashing on the beach | 1.0 | 1.00 | 1.0 | 1.00 | Mixes beach photos and ocean videos |
| q10 | Someone cooking food in a kitchen | 0.9 | 1.00 | 0.9 | 1.00 | |
| q11 | City skyline lit up at night | 1.0 | 1.00 | 1.0 | 1.00 | |
| q12 | Company annual report with financial results | 0.6 | 1.00 | 0.4 | 1.00 | Text and keyword signals are essential for documents |
| q13 | Travel and tourism brochure | 0.4 | 1.00 | 0.2 | 0.17 | Hardest query, see below |
| q14 | Team meeting in an office | 0.9 | 1.00 | 0.9 | 1.00 | |

## What the evaluation changed

The first version fused the three signals with **plain Reciprocal Rank Fusion**, and it scored *worse* than CLIP
alone (P@5 0.41 vs 0.90). The per-signal score dumps showed why:

- Images carry no text (OCR and captions are off by default), so for "a dog playing outside" the text and keyword
  lists contained **only unrelated PDFs**: the best text hit had cosine 0.64, while real matches score 0.68–0.73.
  A book that mentions "playing" scored high on BM25.
- RRF only looks at rank, so that weak #1 text hit got **the same credit** as the #1 dog photo (CLIP 0.31).

**Fix (`backend/app/search/hybrid.py`):**
1. Each signal's RRF term is multiplied by a **confidence** from 0 to 1, taken from its raw score. It's a linear
   ramp between a "noise" level and a "clearly relevant" level, read off the score distributions
   (CLIP 0.20→0.27, BGE 0.60→0.72).
2. **BM25 became supporting evidence.** It can raise an asset another signal found, but can't introduce one.

The result: P@5 went from 0.41 to 0.89. Hybrid now matches CLIP on visual queries and clearly beats it on
documents (q12 P@10 0.4 → 0.7; q13 first relevant hit moves from rank 6 to rank 1).

> Caveat: the confidence ranges were chosen by looking at score distributions for a few of these same queries,
> so the numbers above are somewhat optimistic. A held-out query set would give an unbiased estimate.

### Second change: relative result cut-off

Found by manual testing: "flowers" with the Images filter returned **57** results, but only 2 photos in the
library contain flowers (scores 0.27 / 0.26); the rest were weak look-alikes (mountains, a dog, plants) at about 0.22.
I tried hiding results below 10%–40% of the top result's score on all 14 queries:

| Cut-off | Hybrid P@10 | Hybrid MRR | Relevant results lost (top 20, all queries) |
|---|---|---|---|
| none | 0.893 | 0.964 | 0 |
| 30% (chosen) | 0.893 | 0.964 | 0 |
| 40% | 0.893 | 0.964 | 2 |

30% was the strictest level with no loss. "flowers" now returns 5 results: the 2 flower photos first, then
3 borderline ones (a building behind a lilac bush, and indoor plants). Queries with many true matches, such as
"a woman with a cat", are unaffected. The text-only diagnostic mode's R@20 dips slightly (0.090 → 0.074).

### Third change: best signal + partial support (instead of a plain sum)

Also found by manual testing: "flowers" **without** a type filter ranked three PDFs above the two flower photos.
The scores explained it. The fused score *added up* all signals, and a PDF can match through three (visual, text,
keyword) while a photo can only match visually. A book that mentions trees and plants collected three
medium-sized contributions (0.0035 + 0.0108 + 0.0105 = 0.0249), and beat a photo with one excellent visual match
(0.0162).

New rule: **score = best signal + s × (other signals)**. Tried on all 14 queries plus "flowers":

| s | P@5 | P@10 | MRR | Photo queries q04 / q07 / q11 (P@10) | Document queries q12 / q13 (P@10) | "flowers" (no filter): photo ranks |
|---|---|---|---|---|---|---|
| 1.0 (plain sum, before) | 0.886 | 0.893 | 0.964 | 0.9 / 0.9 / 0.9 | 0.7 / 0.5 | 4, 5 |
| 0.75 | 0.929 | 0.879 | 1.000 | 0.9 / 0.9 / 0.9 | 0.6 / 0.4 | 3, 5 |
| **0.5 (chosen)** | **0.929** | **0.900** | **1.000** | **1.0 / 1.0 / 1.0** | 0.6 / 0.4 | 3, 4 |
| 0.3 | 0.943 | 0.886 | 1.000 | 1.0 / 1.0 / 1.0 | 0.5 / 0.4 | 1, 4 |
| 0 (best signal only) | 0.914 | 0.864 | 0.964 | 1.0 / 1.0 / 1.0 | 0.4 / 0.2 | 1, 2 |

**It's a trade-off.** Reducing the other signals' weight fixes text leakage into photo queries, but document
queries need text and keywords to add up. 0.5 gave the best P@10 and a relevant #1 result for all 14 queries, at
the cost of one relevant PDF in the top 10 for q12 and q13. "flowers" improves (photos from ranks 4–5 to 3–4) but
isn't fully fixed. The real fix is giving photos text as well (captions, see below).

## Where search performs poorly

1. **Document genre vs topic (q13, q12).** "Brochure" and "report" match the *kind* of document, so car brochures
   ("Peugeot 406 Estate Brochure") and an Interpol brochure appear for "travel and tourism brochure". The keyword
   signal rewards the shared word "brochure". *Next step:* treat genre words as a type or intent signal only (as
   the intent detector already does for type) and search the topic words.
2. **Photos compete with fewer signals than PDFs.** Photos have no text (captions and OCR are off by default),
   so a PDF that merely *mentions* a subject can outrank a photo that *shows* it ("flowers" without a filter puts
   2 PDFs above the flower photos). The third change above reduced this, and fixed q04, q07 and q11, but didn't
   remove it. *Next step:* enable captions (`CAPTION_PROVIDER=groq`) so every photo and keyframe also gets a text
   description and competes on all three signals.
3. **Speech is barely exercised.** Stock videos rarely contain speech, so "testimonial" works from visuals (a person
   facing the camera). An actual customer testimonial with a plain background would only be found through its transcript.
4. **Fine-grained visual details.** CLIP ViT-B/32 blurs specifics: "shaking hands" also returns a business
   interview without a handshake (q08), and "at night" occasionally returns dusk shots. A larger model (SigLIP,
   ViT-L) or a VLM re-ranker on the top 20 would help.
5. **Recall is limited by result count, not by retrieval.** R@20 looks low for photo queries because there are
   100+ relevant photos per query. Precision in the first screens is what matters for a search UI.

## How to reproduce

With the server running and the dataset indexed:

```bash
backend/.venv/Scripts/python backend/scripts/evaluate.py
```
