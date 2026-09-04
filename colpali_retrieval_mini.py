#!/usr/bin/env python3
"""
ColSmol-500M Retrieval Quality Mini-Test
Tests 5 targeted PDFs × 1 page each for 5 queries.
Uses reduced resolution (100 DPI) to trade some accuracy for speed.
"""
import time
import gc
import resource
from pathlib import Path

PDF_ROOT = Path.home() / "Data/0-personal/3-knowledge"
MODEL_NAME = "vidore/colSmol-500M"
RENDER_DPI = 100   # reduced from 150 to cut inference time

# 5 targeted PDFs mapped to expected queries
TARGETS = [
    ("literature/data-freedom-act.pdf",                           0),  # data sovereignty
    ("swarm/storage_incentives.pdf",                              0),  # decentralized storage
    ("literature/Security Challenges in AI Agent Deployment.pdf", 0),  # AI agent security
    ("literature/Araral(2013)OstromHardinCommons.pdf",            0),  # Ostrom commons
    ("literature/Towards new human rights in the age of neuroscience and neurotechnology.pdf", 0),
]

QUERIES = [
    "data sovereignty personal data rights ownership",
    "decentralized storage network incentive mechanisms Swarm",
    "AI agent security deployment challenges vulnerabilities",
    "tragedy of the commons Ostrom commons governance",
    "neurotechnology brain data rights neural privacy",
]

def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

def main():
    t0 = time.perf_counter()
    print("=== ColSmol-500M Retrieval Quality Mini-Test ===")
    print(f"DPI: {RENDER_DPI}  |  Pages: {len(TARGETS)}  |  Queries: {len(QUERIES)}")
    print()

    # -- Render pages --
    import fitz
    from PIL import Image

    pages = []
    for rel_path, page_num in TARGETS:
        pdf = PDF_ROOT / rel_path
        try:
            doc = fitz.open(str(pdf))
            mat = fitz.Matrix(RENDER_DPI/72, RENDER_DPI/72)
            pix = doc.load_page(page_num).get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            pages.append((pdf.name, img))
            doc.close()
            print(f"  OK  {pdf.name}  {img.size}")
        except Exception as e:
            print(f"  FAIL {rel_path}: {e}")

    print(f"  RAM: {rss_mb():.0f} MB after render")
    print()

    # -- Load model --
    print(f"Loading {MODEL_NAME}...")
    import torch
    from colpali_engine.models import ColIdefics3, ColIdefics3Processor

    load_start = time.perf_counter()
    model = ColIdefics3.from_pretrained(MODEL_NAME, torch_dtype=torch.bfloat16, device_map="cpu")
    model.eval()
    processor = ColIdefics3Processor.from_pretrained(MODEL_NAME)
    print(f"  Loaded in {time.perf_counter()-load_start:.1f}s  |  RAM: {rss_mb():.0f} MB")
    print()

    # -- Index --
    print("Indexing...")
    embeddings = []
    labels = []
    idx_start = time.perf_counter()
    for label, img in pages:
        t = time.perf_counter()
        batch = processor.process_images([img]).to("cpu")
        with torch.no_grad():
            emb = model(**batch)
        embeddings.append(emb[0].detach().cpu().float())
        labels.append(label)
        del batch, emb; gc.collect()
        print(f"  {label}: {time.perf_counter()-t:.1f}s  |  RAM: {rss_mb():.0f} MB")

    idx_time = time.perf_counter() - idx_start
    tps = len(embeddings) / idx_time
    print(f"\n  Indexing: {tps:.4f} p/s ({len(embeddings)} pages in {idx_time:.1f}s)")
    print()

    if not embeddings:
        print("No embeddings — abort")
        return

    # -- Query --
    print("Retrieval results:")
    for q in QUERIES:
        q_batch = processor.process_queries([q]).to("cpu")
        with torch.no_grad():
            q_emb = model(**q_batch)[0].cpu().float()

        scores = []
        for doc_emb in embeddings:
            sim = torch.einsum("qd,pd->qp", q_emb, doc_emb)
            scores.append(sim.max(dim=1).values.mean().item())

        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        print(f"\n  Q: \"{q}\"")
        for rank, idx in enumerate(ranked[:3], 1):
            print(f"    {rank}. {labels[idx]}  score={scores[idx]:.4f}")

    print(f"\nTotal time: {time.perf_counter()-t0:.1f}s")
    print("=== Done ===")

if __name__ == "__main__":
    main()
