#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ollama_edit_pipeline.py
Single-file pipeline:
  1) Extract text from PDF (auto-detect scanned / optionally OCR)
  2) Chunk text by characters (smart cut on newline/sentence)
  3) For each chunk call Ollama /api/chat with a system prompt that returns JSON:
       keys: revised, edits, notes
  4) Save per-chunk JSON results, extract 'revised' and merge to combined.md
  5) Optionally run pandoc to convert combined.md -> final.docx
  6) Optional: final pass (global style/consistency) by the model
  7) Optional: generate embeddings through Ollama embedding model "nomic-embed-text:latest"
Usage:
  pip install pypdf requests
  optional: pip install tqdm
  optional system tools: ocrmypdf, pdftotext (poppler), pandoc
Example:
  python ollama_edit_pipeline.py -i input.pdf -o outdir --model gpt-oss:20b --chunk_chars 16000 --pandoc
Notes:
  - Ensure ollama server is running (default http://127.0.0.1:11434) and required models are loaded.
  - If PDF is scanned, use --ocr to run ocrmypdf (must be installed).
"""
import os
import sys
import json
import time
import argparse
import subprocess
import shutil
from typing import List, Optional, Any, Dict

try:
    from pypdf import PdfReader
except Exception as e:
    print("Missing pypdf. Install with: pip install pypdf")
    raise

try:
    import requests
except Exception:
    print("Missing requests. Install with: pip install requests")
    raise

# Optional progress bar
try:
    from tqdm import tqdm
except Exception:
    tqdm = lambda x, **k: x  # fallback iterable


############################
# Configurable defaults
############################
DEFAULT_API = "http://127.0.0.1:11434/api/chat"
DEFAULT_EMBED_API = "http://127.0.0.1:11434/api/embed"  # best-effort; may not exist on every Ollama deployment
DEFAULT_MODEL = "gpt-oss:20b"
DEFAULT_EMBED_MODEL = "nomic-embed-text:latest"
DEFAULT_CHUNK_CHARS = 16000
SYSTEM_PROMPT = (
    "You are a meticulous native-English editor. For the given text chunk:\n"
    "1) Produce a corrected, well-flowing version (preserve meaning).\n"
    "2) Provide a short edits list: each edit = {loc: \"paragraph x\", change: \"old -> new\", why: \"...\"}.\n"
    "3) Mention any structural issues (missing headings, repeated sections, bad order).\n"
    "4) Output as JSON with keys: revised (markdown), edits (array), notes (string).\n"
    "Do NOT add new factual claims, do NOT change citations/references.\n"
)

############################
# Utilities
############################
def run_cmd_check(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return CompletedProcess."""
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)


def is_executable_available(name: str) -> bool:
    return shutil.which(name) is not None


############################
# PDF extraction
############################
def extract_text_pypdf(pdf_path: str) -> List[str]:
    """Extract text from each page using pypdf. Returns list of page texts."""
    reader = PdfReader(pdf_path)
    pages = []
    for p in reader.pages:
        try:
            text = p.extract_text() or ""
        except Exception:
            text = ""
        pages.append(text)
    return pages


def detect_scanned(pages_texts: List[str], threshold_chars_per_page: int = 50) -> bool:
    """Return True if extracted text suggests scanned PDF (very few chars per page)."""
    if not pages_texts:
        return True
    avg = sum(len(t.strip()) for t in pages_texts) / max(1, len(pages_texts))
    return avg < threshold_chars_per_page


def run_ocrmypdf(input_pdf: str, output_pdf: str) -> None:
    """Run ocrmypdf to create an OCRed PDF. Requires ocrmypdf installed."""
    if not is_executable_available("ocrmypdf"):
        raise RuntimeError("ocrmypdf not found. Install it or skip --ocr.")
    cmd = ["ocrmypdf", "--rotate-pages", "--deskew", input_pdf, output_pdf]
    print("Running OCRmyPDF (this may take a while)...")
    run_cmd_check(cmd)


def pdftotext_to_file(pdf_path: str, out_txt_path: str) -> None:
    """Use pdftotext (poppler) if available for better layout."""
    if not is_executable_available("pdftotext"):
        # fallback: use pypdf concatenation
        print("pdftotext not available; falling back to pypdf extraction.")
        pages = extract_text_pypdf(pdf_path)
        with open(out_txt_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(pages))
        return
    cmd = ["pdftotext", "-layout", pdf_path, out_txt_path]
    run_cmd_check(cmd)


def extract_text_to_file(pdf_path: str, out_txt_path: str, use_ocr: bool = False) -> None:
    """
    Extract text from PDF and write to out_txt_path.
    If use_ocr True, try ocrmypdf first when detection suggests scanning.
    """
    pages = extract_text_pypdf(pdf_path)
    scanned = detect_scanned(pages)
    if scanned and use_ocr:
        tmp_ocr_pdf = out_txt_path + ".ocr.pdf"
        run_ocrmypdf(pdf_path, tmp_ocr_pdf)
        # try pdftotext from the OCRed pdf
        pdftotext_to_file(tmp_ocr_pdf, out_txt_path)
        try:
            os.remove(tmp_ocr_pdf)
        except Exception:
            pass
        return
    # else just write pypdf-extracted
    with open(out_txt_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(pages))


############################
# Chunking
############################
def chunk_text(text: str, chunk_chars: int = DEFAULT_CHUNK_CHARS) -> List[str]:
    """
    Chunk text by character length, trying to end on newline or sentence boundary.
    This is the function you provided, improved slightly to also consider sentence punctuation.
    """
    chunks = []
    i = 0
    L = len(text)
    while i < L:
        take_to = min(i + chunk_chars, L)
        chunk = text[i:take_to]
        if take_to < L:
            # attempt to cut at last newline
            cut_candidates = []
            ln = chunk.rfind("\n")
            if ln > 0:
                cut_candidates.append(ln)
            # sentence end boundaries
            for sep in (". ", "? ", "! ", ".\n", "?\n", "!\n"):
                pos = chunk.rfind(sep)
                if pos > 0:
                    # include the punctuation/separator in the chunk (pos + len(sep))
                    cut_candidates.append(pos + len(sep))
            if cut_candidates:
                cut = max(cut_candidates)
                chunk = chunk[:cut]
                i += cut
            else:
                # fallback: hard cut
                i += chunk_chars
        else:
            i += chunk_chars
        chunks.append(chunk)
    return chunks


############################
# Ollama API interaction
############################
def find_text_in_obj(obj: Any) -> Optional[str]:
    """Recursively find the first string-like text in a nested JSON object."""
    if obj is None:
        return None
    if isinstance(obj, str):
        s = obj.strip()
        if s:
            return s
        return None
    if isinstance(obj, dict):
        # prefer common keys
        for key in ("content", "text", "output_text", "message", "response", "result"):
            if key in obj:
                found = find_text_in_obj(obj[key])
                if found:
                    return found
        # else iterate values
        for v in obj.values():
            found = find_text_in_obj(v)
            if found:
                return found
    if isinstance(obj, (list, tuple)):
        for item in obj:
            found = find_text_in_obj(item)
            if found:
                return found
    return None


def extract_assistant_text_from_response(resp: requests.Response) -> str:
    """Try multiple strategies to get assistant output text from response."""
    text = None
    try:
        j = resp.json()
    except Exception:
        # fallback: raw text
        return resp.text.strip()
    # try find typical places
    assistant_text = find_text_in_obj(j)
    if assistant_text:
        return assistant_text.strip()
    # fallback: pretty-print json
    return json.dumps(j, ensure_ascii=False, indent=2)


def call_ollama_chat_chunk(api_url: str, model: str, system_prompt: str, user_text: str, timeout: int = 600, max_retries: int = 3) -> Dict[str, Any]:
    """
    Call Ollama /api/chat with given model and messages.
    Returns a dict with keys: status_code, assistant_text, raw_json (if available).
    Retries on transient errors.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ],
        "stream": False
    }
    headers = {"Content-Type": "application/json"}
    attempt = 0
    last_exc = None
    while attempt < max_retries:
        try:
            r = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 200:
                assistant_text = extract_assistant_text_from_response(r)
                try:
                    raw_json = r.json()
                except Exception:
                    raw_json = None
                return {"status_code": r.status_code, "assistant_text": assistant_text, "raw_json": raw_json, "resp_text": r.text}
            else:
                # server returned non-200. read content for diagnostics, maybe ratelimit issues
                attempt += 1
                time.sleep(2 ** attempt)
                last_exc = RuntimeError(f"Non-200 from Ollama: {r.status_code}: {r.text[:400]}")
        except requests.exceptions.RequestException as e:
            last_exc = e
            attempt += 1
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to call Ollama after {max_retries} attempts. Last error: {last_exc}")


############################
# Parse assistant JSON content
############################
def extract_json_like(s: str) -> Optional[Dict]:
    """
    Try to extract JSON object from assistant string.
    Finds first outermost {...} and attempts json.loads.
    Returns dict or None.
    """
    if not s:
        return None
    s = s.strip()
    # quick full-parse
    try:
        return json.loads(s)
    except Exception:
        pass
    # find first { and last } and try to parse
    first = s.find("{")
    last = s.rfind("}")
    if first >= 0 and last > first:
        candidate = s[first:last+1]
        try:
            return json.loads(candidate)
        except Exception:
            pass
    return None


############################
# Embeddings (optional)
############################
def generate_embeddings_for_chunks(embed_api_url: str, embed_model: str, chunks: List[str], out_path: str) -> None:
    """
    Try to call Ollama's embedding endpoint. Save embeddings to out_path as JSON lines:
      {"idx": i, "embedding": [...], "text_preview": chunk[:200]}
    Note: endpoint existence is environment-dependent.
    """
    if not is_executable_available("curl") and not shutil.which("curl"):
        # curl not required, we use requests
        pass
    results = []
    headers = {"Content-Type": "application/json"}
    for i, chunk in enumerate(tqdm(chunks, desc="Embedding chunks")):
        payload = {
            "model": embed_model,
            "input": chunk  # best-effort; some deployments expect "input" or "inputs"
        }
        try:
            r = requests.post(embed_api_url, headers=headers, json=payload, timeout=120)
            if r.status_code == 200:
                j = r.json()
                # try to find embedding structure
                emb = None
                if isinstance(j, dict):
                    # common shapes: j["data"][0]["embedding"] or j["embedding"]
                    if "data" in j and isinstance(j["data"], list) and j["data"] and "embedding" in j["data"][0]:
                        emb = j["data"][0]["embedding"]
                    elif "embedding" in j:
                        emb = j["embedding"]
                    elif "embeddings" in j and isinstance(j["embeddings"], list):
                        emb = j["embeddings"][0]
                    else:
                        # try to find any list of numbers recursively
                        def find_nums(o):
                            if isinstance(o, list) and o and all(isinstance(x, (int, float)) for x in o):
                                return o
                            if isinstance(o, dict):
                                for v in o.values():
                                    f = find_nums(v)
                                    if f:
                                        return f
                            if isinstance(o, list):
                                for item in o:
                                    f = find_nums(item)
                                    if f:
                                        return f
                            return None
                        emb = find_nums(j)
                if emb is None:
                    print(f"Warning: could not parse embedding response for chunk {i}. Saving raw response.")
                    results.append({"idx": i, "embedding": None, "raw": j, "text_preview": chunk[:200]})
                else:
                    results.append({"idx": i, "embedding": emb, "text_preview": chunk[:200]})
            else:
                print(f"Embedding call failed for chunk {i}: {r.status_code} {r.text[:400]}")
                results.append({"idx": i, "embedding": None, "error": r.text[:400], "text_preview": chunk[:200]})
        except Exception as e:
            print(f"Exception during embedding chunk {i}: {e}")
            results.append({"idx": i, "embedding": None, "error": str(e), "text_preview": chunk[:200]})
    # write results
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Saved embeddings results to {out_path}")


############################
# Merge revised chunks
############################
def merge_revised_chunks(results_dir: str, out_md: str) -> None:
    """
    Scan results_dir for files named edited_chunk_{idx}.json and merge
    their 'revised' field (or assistant_text fallback) in order.
    """
    files = []
    for name in os.listdir(results_dir):
        if name.startswith("edited_chunk_") and name.endswith(".json"):
            try:
                idx = int(name[len("edited_chunk_"):-len(".json")])
                files.append((idx, name))
            except Exception:
                continue
    files.sort()
    merged_parts = []
    for idx, name in files:
        path = os.path.join(results_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            j = json.load(f)
        # try raw_json -> assistant_text extraction, or use j.get("assistant_text")
        assistant_text = None
        if isinstance(j, dict):
            # first priority: if raw_json exists and contains assistant text we extract
            if "raw_json" in j and j["raw_json"]:
                assistant_text = find_text_in_obj(j["raw_json"])
            if not assistant_text and "assistant_text" in j:
                assistant_text = j["assistant_text"]
            if not assistant_text and "resp_text" in j:
                assistant_text = j["resp_text"]
            # if assistant_text looks like JSON, parse it to get 'revised'
            parsed = extract_json_like(assistant_text or "")
            if parsed and isinstance(parsed, dict) and "revised" in parsed:
                revised = parsed["revised"]
            else:
                # fallback: if j itself contains fields 'assistant_text' or 'revised' already
                if "revised" in j and isinstance(j["revised"], str):
                    revised = j["revised"]
                elif assistant_text:
                    revised = assistant_text
                else:
                    revised = ""
        else:
            revised = ""
        # ensure separation
        merged_parts.append(revised.strip())
    combined = "\n\n---\n\n".join(part for part in merged_parts if part)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(combined)
    print(f"Merged {len(merged_parts)} parts into {out_md}")


############################
# Final pass (global)
############################
def final_pass_global(api_url: str, model: str, system_prompt: str, combined_md_path: str, out_json: str, max_chars: int = 20000) -> None:
    """
    Run a final book-level pass. If combined is too large, split into big chunks.
    Save the model's response JSON to out_json.
    """
    with open(combined_md_path, "r", encoding="utf-8") as f:
        content = f.read()
    if len(content) <= max_chars:
        # single pass
        resp = call_ollama_chat_chunk(api_url, model, system_prompt + "\n\nThis is a final book-level pass. Provide a single JSON output with keys: revised, edits, notes.", content)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(resp, f, ensure_ascii=False, indent=2)
        print(f"Saved final pass result to {out_json}")
        return
    # else split into a few large chunks
    chunks = chunk_text(content, chunk_chars=max_chars)
    all_responses = []
    for i, ch in enumerate(chunks):
        print(f"Final-pass chunk {i+1}/{len(chunks)} size={len(ch)}")
        prompt = system_prompt + f"\n\nThis is final book pass chunk {i+1}/{len(chunks)}. Provide JSON with keys: revised, edits, notes.\nAlso summarize any cross-chunk consistency issues."
        resp = call_ollama_chat_chunk(api_url, model, prompt, ch)
        all_responses.append({"idx": i, "resp": resp})
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(all_responses, f, ensure_ascii=False, indent=2)
    print(f"Saved final pass results to {out_json}")


############################
# Main orchestration
############################
def main():
    parser = argparse.ArgumentParser(description="Ollama edit pipeline for large PDF -> chunk -> Ollama edits -> merge")
    parser.add_argument("-i", "--input", required=True, help="Input PDF path")
    parser.add_argument("-o", "--outdir", required=True, help="Output directory to store chunks and results")
    parser.add_argument("--api_url", default=DEFAULT_API, help="Ollama chat API URL (default: %(default)s)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name to use on Ollama (default: %(default)s)")
    parser.add_argument("--chunk_chars", type=int, default=DEFAULT_CHUNK_CHARS, help="Chunk size in characters (default 16000)")
    parser.add_argument("--ocr", action="store_true", help="Try OCR if PDF looks scanned (requires ocrmypdf)")
    parser.add_argument("--pandoc", action="store_true", help="If set, run pandoc to convert combined.md -> final.docx (requires pandoc installed)")
    parser.add_argument("--do_embed", action="store_true", help="Generate embeddings via Ollama embed API (best-effort).")
    parser.add_argument("--embed_model", default=DEFAULT_EMBED_MODEL, help="Embedding model name (default: %(default)s)")
    parser.add_argument("--embed_api", default=DEFAULT_EMBED_API, help="Embedding endpoint URL (default: %(default)s)")
    parser.add_argument("--final_pass", action="store_true", help="Run final book-level pass after merging (may be large)")
    parser.add_argument("--timeout", type=int, default=600, help="API request timeout in seconds")
    args = parser.parse_args()

    inp = args.input
    outdir = args.outdir
    api_url = args.api_url
    model = args.model
    chunk_chars = args.chunk_chars
    timeout = args.timeout

    os.makedirs(outdir, exist_ok=True)
    txt_path = os.path.join(outdir, "input_extracted.txt")

    print(f"Extracting text from {inp} -> {txt_path} (use OCR={args.ocr})")
    try:
        extract_text_to_file(inp, txt_path, use_ocr=args.ocr)
    except Exception as e:
        print(f"Error extracting text: {e}")
        sys.exit(1)

    with open(txt_path, "r", encoding="utf-8") as f:
        full_text = f.read()

    if not full_text.strip():
        print("No text extracted. If the PDF is scanned, retry with --ocr and ensure ocrmypdf is installed.")
        sys.exit(1)

    print("Chunking text...")
    chunks = chunk_text(full_text, chunk_chars=chunk_chars)
    print(f"Created {len(chunks)} chunks (chunk_chars={chunk_chars}).")

    # chunk output dir
    results_dir = os.path.join(outdir, "chunk_results")
    os.makedirs(results_dir, exist_ok=True)

    # process chunks
    for idx, chunk in enumerate(tqdm(chunks, desc="Processing chunks")):
        out_json_path = os.path.join(results_dir, f"edited_chunk_{idx}.json")
        if os.path.exists(out_json_path):
            print(f"Skipping idx {idx}, result exists: {out_json_path}")
            continue
        try:
            resp = call_ollama_chat_chunk(api_url, model, SYSTEM_PROMPT, chunk, timeout=timeout)
            # save resp dict with some metadata
            dump = {
                "index": idx,
                "chunk_chars": len(chunk),
                "timestamp": int(time.time()),
                "model": model,
                "api_url": api_url,
                "assistant_text": resp.get("assistant_text"),
                "raw_json": resp.get("raw_json"),
                "resp_text": resp.get("resp_text"),
            }
            with open(out_json_path, "w", encoding="utf-8") as f:
                json.dump(dump, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error processing chunk {idx}: {e}")
            # write a minimal error file so we can resume later
            with open(out_json_path + ".error", "w", encoding="utf-8") as f:
                f.write(str(e))
            # continue processing remaining chunks
            continue

    # merge revised chunks to combined.md
    combined_md = os.path.join(outdir, "combined.md")
    merge_revised_chunks(results_dir, combined_md)

    # optional embeddings
    if args.do_embed:
        emb_out = os.path.join(outdir, "embeddings.json")
        print("Generating embeddings (best-effort)...")
        try:
            generate_embeddings_for_chunks(args.embed_api, args.embed_model, chunks, emb_out)
        except Exception as e:
            print(f"Embeddings generation failed: {e}")

    # optional pandoc convert
    if args.pandoc:
        if not is_executable_available("pandoc"):
            print("pandoc not found. Please install pandoc to enable conversion.")
        else:
            docx_path = os.path.join(outdir, "final.docx")
            cmd = ["pandoc", combined_md, "-o", docx_path]
            print("Running pandoc to produce DOCX...")
            try:
                run_cmd_check(cmd)
                print(f"Generated {docx_path}")
            except Exception as e:
                print(f"Pandoc conversion failed: {e}")

    # optional final pass
    if args.final_pass:
        final_json = os.path.join(outdir, "final_pass.json")
        print("Running final pass (may take long depending on book size) ...")
        try:
            final_pass_global(api_url, model, SYSTEM_PROMPT, combined_md, final_json)
        except Exception as e:
            print(f"Final pass failed: {e}")

    print("Pipeline finished. Check outputs in:", outdir)
    print("Notes:")
    print(" - If you see .error files next to chunk JSON, inspect them and re-run those chunks later.")
    print(" - You can resume by re-running with same args; existing edited_chunk_*.json files are skipped.")
    print(" - Adjust --chunk_chars smaller if you hit memory/timeout issues with your model.")

if __name__ == "__main__":
    main()
