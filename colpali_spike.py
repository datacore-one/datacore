#!/usr/bin/env python3
"""
ColSmol-500M CPU Feasibility Spike
===================================
Measures: indexing throughput (pages/sec), peak RAM, retrieval quality.
Decision gate for Phase 2 ColPali adoption.

Model: vidore/colSmol-500M (LoRA adapter over ColSmolVLM-Instruct-500M-base)
Architecture: ColIdefics3 (colpali-engine)
Hardware: CPU-only, batch_size=1 (memory constraint)
"""

import os
import sys
import time
import resource
import traceback
import gc
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

PDF_ROOT = Path.home() / "Data/0-personal/3-knowledge"
MODEL_NAME = "vidore/colSmol-500M"
BATCH_SIZE = 1
MAX_PAGES_TOTAL = 50     # cap for timing test (use all pages up to this limit)
RENDER_DPI = 150          # standard for ColPali (150-200 DPI)
MAX_PAGES_PER_PDF = 5     # spread coverage across more PDFs

# Queries chosen to match actual PDFs in the corpus
QUERIES = [
    "data sovereignty personal data rights ownership",
    "decentralized storage network incentive mechanisms Swarm",
    "AI agent security deployment challenges vulnerabilities",
    "tragedy of the commons Ostrom commons governance",
    "neurotechnology brain data rights neural privacy",
]

# ── Helpers ──────────────────────────────────────────────────────────────────

def rss_mb():
    """Current process RSS in MB (Linux: ru_maxrss is in KB)"""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def fmt_time(s):
    if s >= 60:
        return f"{s/60:.1f}min"
    return f"{s:.1f}s"


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.perf_counter()
    print("=" * 60)
    print("ColSmol-500M CPU Feasibility Spike")
    print("=" * 60)
    print(f"Model:     {MODEL_NAME}")
    print(f"PDF root:  {PDF_ROOT}")
    print(f"Batch sz:  {BATCH_SIZE}")
    print(f"Max pages: {MAX_PAGES_TOTAL}")
    print(f"RAM at start: {rss_mb():.0f} MB RSS")
    print()

    # ── 1. Discover PDFs ─────────────────────────────────────────────────────
    pdfs = sorted(PDF_ROOT.rglob("*.pdf"))
    print(f"Found {len(pdfs)} PDFs")
    for p in pdfs:
        print(f"  {p.relative_to(PDF_ROOT)}")
    print()

    # ── 2. PDF → Images ──────────────────────────────────────────────────────
    print(f"Converting PDFs to images at {RENDER_DPI} DPI (max {MAX_PAGES_PER_PDF} pages/PDF)...")
    import fitz  # PyMuPDF
    from PIL import Image

    pages = []   # list of (label: str, img: PIL.Image)
    skipped_pdfs = []
    conv_start = time.perf_counter()

    for pdf_path in pdfs:
        if len(pages) >= MAX_PAGES_TOTAL:
            break
        try:
            doc = fitz.open(str(pdf_path))
            n_pages = min(len(doc), MAX_PAGES_PER_PDF, MAX_PAGES_TOTAL - len(pages))
            scale = RENDER_DPI / 72
            mat = fitz.Matrix(scale, scale)
            for i in range(n_pages):
                pg = doc.load_page(i)
                pix = pg.get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                label = f"{pdf_path.name} [p{i+1}]"
                pages.append((label, img))
            doc.close()
        except Exception as e:
            skipped_pdfs.append((pdf_path.name, str(e)))

    conv_time = time.perf_counter() - conv_start
    print(f"  {len(pages)} pages from {len(pdfs) - len(skipped_pdfs)} PDFs"
          f" in {fmt_time(conv_time)} ({len(pages)/conv_time:.1f} pages/sec render)")
    if skipped_pdfs:
        for name, err in skipped_pdfs:
            print(f"  SKIP {name}: {err}")
    print(f"  RAM after render: {rss_mb():.0f} MB RSS")
    print()

    # ── 3. Load Model ─────────────────────────────────────────────────────────
    print(f"Loading {MODEL_NAME} onto CPU...")
    load_start = time.perf_counter()

    import torch
    from colpali_engine.models import ColIdefics3, ColIdefics3Processor

    # bfloat16 on CPU uses ~1 GB vs 2 GB for float32 — try it first
    try:
        dtype = torch.bfloat16
        model = ColIdefics3.from_pretrained(
            MODEL_NAME,
            torch_dtype=dtype,
            device_map="cpu",
        )
        print(f"  Loaded with bfloat16")
    except Exception as e:
        print(f"  bfloat16 failed ({e}), falling back to float32")
        dtype = torch.float32
        model = ColIdefics3.from_pretrained(
            MODEL_NAME,
            torch_dtype=dtype,
            device_map="cpu",
        )

    model.eval()
    processor = ColIdefics3Processor.from_pretrained(MODEL_NAME)

    load_time = time.perf_counter() - load_start
    n_params = sum(p.numel() for p in model.parameters())
    ram_after_load = rss_mb()

    print(f"  Load time:   {fmt_time(load_time)}")
    print(f"  Params:      {n_params/1e6:.0f}M")
    print(f"  RAM at load: {ram_after_load:.0f} MB RSS")
    print()

    # ── 4. Index Pages ────────────────────────────────────────────────────────
    print(f"Indexing {len(pages)} pages (batch_size={BATCH_SIZE})...")
    embeddings = []     # list of Tensor [num_patches, dim]
    labels = []
    errors = 0

    index_start = time.perf_counter()
    ram_peak = rss_mb()

    for i, (label, img) in enumerate(pages):
        try:
            batch = processor.process_images([img]).to("cpu")
            with torch.no_grad():
                emb = model(**batch)          # [1, num_patches, dim]
            embeddings.append(emb[0].detach().cpu().to(torch.float32))
            labels.append(label)
            del batch, emb
            gc.collect()
        except torch.cuda.OutOfMemoryError:
            errors += 1
            print(f"  OOM at page {i}")
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  ERROR page {i} ({label}): {e}")

        # Progress + RAM every 5 pages
        if (i + 1) % 5 == 0 or (i + 1) == len(pages):
            elapsed = time.perf_counter() - index_start
            r = rss_mb()
            ram_peak = max(ram_peak, r)
            tps = (i + 1) / elapsed
            eta = (len(pages) - i - 1) / tps if tps > 0 else 0
            print(f"  [{i+1:3d}/{len(pages)}] {tps:.3f} p/s | "
                  f"RAM {r:.0f} MB | ETA {fmt_time(eta)}")

    index_time = time.perf_counter() - index_start
    throughput = len(embeddings) / index_time if index_time > 0 else 0
    ram_after_index = rss_mb()
    ram_peak = max(ram_peak, ram_after_index)

    print()
    print("Indexing results:")
    print(f"  Pages indexed:  {len(embeddings)} ({errors} errors)")
    print(f"  Index time:     {fmt_time(index_time)}")
    print(f"  Throughput:     {throughput:.3f} pages/sec  ({throughput*60:.1f} pages/min)")
    print(f"  RAM peak:       {ram_peak:.0f} MB RSS")
    print(f"  Emb shape:      {embeddings[0].shape if embeddings else 'n/a'}")
    print()

    if not embeddings:
        print("FATAL: No embeddings produced — aborting retrieval test")
        return summary(t0, pdfs, pages, embeddings, errors, load_time,
                       index_time, throughput, ram_after_load, ram_peak, [])

    # ── 5. Retrieval Quality ──────────────────────────────────────────────────
    print(f"Retrieval quality ({len(QUERIES)} queries)...")
    results = []

    for query_text in QUERIES:
        try:
            q_batch = processor.process_queries([query_text]).to("cpu")
            with torch.no_grad():
                q_emb = model(**q_batch)[0].cpu().to(torch.float32)  # [Q_tokens, dim]

            # MaxSim: for each doc, for each query token → max similarity over patches
            scores = []
            for doc_emb in embeddings:
                # doc_emb: [P, D], q_emb: [Q, D]
                sim = torch.einsum("qd,pd->qp", q_emb, doc_emb)  # [Q, P]
                score = sim.max(dim=1).values.mean().item()
                scores.append(score)

            top3 = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:3]
            hit = {"query": query_text, "top3": [(labels[i], scores[i]) for i in top3]}
            results.append(hit)

            print(f"\n  Query: \"{query_text}\"")
            for rank, idx in enumerate(top3, 1):
                print(f"    {rank}. {labels[idx]}  (score {scores[idx]:.4f})")
        except Exception as e:
            print(f"  Query error: {e}")
            traceback.print_exc()

    print()
    return summary(t0, pdfs, pages, embeddings, errors, load_time,
                   index_time, throughput, ram_after_load, ram_peak, results)


def summary(t0, pdfs, pages, embeddings, errors, load_time,
            index_time, throughput, ram_after_load, ram_peak, results):
    total_time = time.perf_counter() - t0
    print("=" * 60)
    print("SPIKE SUMMARY")
    print("=" * 60)
    print(f"PDFs found:            {len(pdfs)}")
    print(f"Pages rendered:        {len(pages)}")
    print(f"Pages indexed:         {len(embeddings)}  ({errors} errors)")
    print(f"Model load time:       {fmt_time(load_time)}")
    print(f"Indexing throughput:   {throughput:.3f} pages/sec"
          f"  ({throughput*60:.1f} pages/min)")
    print(f"RAM at model load:     {ram_after_load:.0f} MB RSS")
    print(f"Peak RAM (indexing):   {ram_peak:.0f} MB RSS")
    print(f"Total spike time:      {fmt_time(total_time)}")
    print()
    print("DECISION GATE INPUTS:")
    print(f"  CPU feasible?        {'YES' if throughput > 0 else 'NO'}")
    print(f"  Throughput class:    ", end="")
    if throughput >= 0.5:
        print("GOOD (≥0.5 p/s)")
    elif throughput >= 0.1:
        print("MARGINAL (0.1–0.5 p/s)")
    else:
        print("SLOW (<0.1 p/s)")
    print(f"  RAM headroom:        ", end="")
    if ram_peak < 5500:
        print(f"OK ({ram_peak:.0f} MB < 5.5 GB)")
    else:
        print(f"TIGHT ({ram_peak:.0f} MB — watch OOM risk)")
    print()
    if results:
        print("QUALITATIVE RETRIEVAL:")
        for r in results:
            print(f"  Q: {r['query'][:60]}")
            print(f"     → {r['top3'][0][0]}  ({r['top3'][0][1]:.4f})")
    print("=" * 60)


if __name__ == "__main__":
    main()
