#!/usr/bin/env python3
"""ColSmol-500M retrieval quality test. Run on nightshift after indexing."""
import torch
import io
import json
import pymupdf as fitz
from PIL import Image
from pathlib import Path
from colpali_engine.models import ColIdefics3, ColIdefics3Processor

MODEL = "vidore/ColSmolVLM-Instruct-500M-base"
ROOT = Path("/home/gregor/Data/0-personal/3-knowledge")

PDFS = [
    ("local-first.pdf", ROOT / "literature/local-first.pdf"),
    ("situationalawareness.pdf", ROOT / "literature/situationalawareness.pdf"),
    ("Agents-are-not-enough.pdf", ROOT / "literature/Agents are not enough.pdf"),
    ("Tragedy-of-Commons.pdf", ROOT / "literature/The Tragedy of the Commons.pdf"),
    ("data-freedom-act.pdf", ROOT / "literature/data-freedom-act.pdf"),
    ("Decentralized-Society.pdf", ROOT / "literature/Decentralized Society SSRN-id4105763.pdf"),
    ("Security-AI-Deployment.pdf", ROOT / "literature/Security Challenges in AI Agent Deployment.pdf"),
]

QUERIES = [
    "data sovereignty and personal data ownership rights",
    "artificial intelligence agent systems and deployment challenges",
    "commons governance and collective resource management",
    "local-first software and conflict-free replicated data types",
    "AI situational awareness and transformative risk",
]


def make_img(path, dpi=100, max_dim=448):
    doc = fitz.open(str(path))
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    doc.close()
    w, h = img.size
    s = min(max_dim / w, max_dim / h, 1.0)
    if s < 1.0:
        img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
    return img


def maxsim_score(q_emb, d_emb):
    sim = torch.matmul(q_emb, d_emb.T)
    return float(sim.max(dim=1).values.sum())


def main():
    print("Loading model...")
    proc = ColIdefics3Processor.from_pretrained(MODEL)
    model = ColIdefics3.from_pretrained(MODEL, torch_dtype=torch.float32, device_map="cpu")
    model.eval()
    print("Model loaded.")

    names = []
    embs = []
    for nm, path in PDFS:
        if not path.exists():
            print(f"  SKIP {nm}")
            continue
        img = make_img(path)
        with torch.no_grad():
            ib = proc.process_images([img])
            out = model(**{k: v.cpu() for k, v in ib.items()})
        names.append(nm)
        embs.append(out[0].cpu())
        print(f"  indexed {nm}")

    print(f"Done indexing {len(names)} docs.")

    retrieval_results = []
    for qi, query_str in enumerate(QUERIES):
        with torch.no_grad():
            qb = proc.process_queries([query_str])
            q_out = model(**{k: v.cpu() for k, v in qb.items()})
        q_vec = q_out[0].cpu()

        ranked = []
        for di in range(len(names)):
            sc = maxsim_score(q_vec, embs[di])
            ranked.append({"doc": names[di], "score": round(sc, 4)})

        ranked.sort(key=lambda x: x["score"], reverse=True)

        print(f"\nQ{qi + 1}: {query_str[:65]}")
        for r in ranked[:3]:
            print(f"  [{r['score']:.3f}] {r['doc']}")

        retrieval_results.append({"query": query_str, "top3": ranked[:3], "all_ranked": ranked})

    output = {"model": MODEL, "pdf_count": len(names), "retrieval": retrieval_results}
    out_path = Path("/home/gregor/Data/colpali_retrieval_results.json")
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    main()
