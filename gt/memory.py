"""Vector memory: nomic-embed embeddings stored in sqlite, cosine search w/ numpy.

Deliberately dependency-light — no Chroma / FAISS / native builds. For personal
scale (thousands of items) a brute-force numpy cosine over sqlite rows is fast
and bulletproof on Windows.

Three kinds of memory share one table:
  - "note"   : facts you told GT to remember (/remember)
  - "lesson" : reusable lessons the self-improve loop extracted
  - "doc"    : chunks of files you indexed for RAG (/index)
"""

import sqlite3
import threading
import time
from pathlib import Path

import numpy as np


class Memory:
    def __init__(self, llm, db_path, embed_role="embed"):
        self.llm = llm
        self.embed_role = embed_role
        self.db_path = str(db_path)
        # The self-improve loop writes lessons from a background thread, so
        # the connection must be shareable; the lock serializes writes.
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS memory(
                 id        INTEGER PRIMARY KEY AUTOINCREMENT,
                 kind      TEXT,
                 text      TEXT,
                 source    TEXT,
                 embedding BLOB,
                 dim       INTEGER,
                 created   REAL
               )"""
        )
        self.conn.commit()

    # ---- writes -------------------------------------------------------------

    def add(self, text, kind="note", source=None):
        self.add_many([(text, kind, source)])

    def add_many(self, items):
        """items: list of (text, kind, source)."""
        items = [it for it in items if it[0] and it[0].strip()]
        if not items:
            return 0
        embs = self.llm.embed(self.embed_role, [t for t, _, _ in items])
        now = time.time()
        rows = []
        for (text, kind, source), emb in zip(items, embs):
            arr = np.asarray(emb, dtype=np.float32)
            rows.append((kind, text, source, arr.tobytes(), int(arr.shape[0]), now))
        with self._lock:
            self.conn.executemany(
                "INSERT INTO memory(kind,text,source,embedding,dim,created) "
                "VALUES(?,?,?,?,?,?)",
                rows,
            )
            self.conn.commit()
        return len(rows)

    # ---- reads --------------------------------------------------------------

    def search(self, query, k=5, kinds=None, min_score=0.0):
        """Return list of (score, kind, text, source) sorted by similarity."""
        rows = self.conn.execute(
            "SELECT kind,text,source,embedding FROM memory"
        ).fetchall()
        if not rows:
            return []
        if kinds:
            rows = [r for r in rows if r[0] in kinds]
            if not rows:
                return []

        q = np.asarray(self.llm.embed(self.embed_role, [query])[0], dtype=np.float32)
        q = q / (np.linalg.norm(q) + 1e-8)

        scored = []
        for kind, text, source, blob in rows:
            v = np.frombuffer(blob, dtype=np.float32)
            if v.shape[0] != q.shape[0]:
                continue  # skip vectors from a different embed model
            v = v / (np.linalg.norm(v) + 1e-8)
            score = float(np.dot(q, v))
            if score >= min_score:
                scored.append((score, kind, text, source))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]

    def count(self, kind=None):
        if kind:
            return self.conn.execute(
                "SELECT COUNT(*) FROM memory WHERE kind=?", (kind,)
            ).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]

    def recent(self, kind=None, limit=20):
        if kind:
            cur = self.conn.execute(
                "SELECT text,source FROM memory WHERE kind=? ORDER BY created DESC LIMIT ?",
                (kind, limit),
            )
        else:
            cur = self.conn.execute(
                "SELECT text,source FROM memory ORDER BY created DESC LIMIT ?",
                (limit,),
            )
        return cur.fetchall()

    def clear(self, kind=None):
        with self._lock:
            if kind:
                self.conn.execute("DELETE FROM memory WHERE kind=?", (kind,))
            else:
                self.conn.execute("DELETE FROM memory")
            self.conn.commit()


# ---- chunking helper (used by /index) --------------------------------------

def chunk_text(text, size=1000, overlap=150):
    """Split text into overlapping character windows for embedding."""
    text = text.strip()
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks
