"""Skills: expert playbooks injected into the model's context on demand.

Local models are capable executors but arrive with no taste — they don't
know what a consultant-grade spreadsheet or a designed-looking landing page
looks like. Skills fix that: curated markdown playbooks (shipped in the
repo's skills/ folder, extendable in ~/.gt/skills/) that get matched against
each request by trigger keywords and injected into the system prompt.

Deliberately small and targeted: an 8K context window means two sharp
playbooks at the right moment beat gigabytes of retrieved documentation.

Skill file format (markdown with a tiny front matter):

    ---
    name: excel
    triggers: excel, xlsx, spreadsheet
    priority: 5
    ---
    # The playbook body …
"""

import hashlib
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import ROOT, USER_DIR

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)

# Skill library dirs (bulk Agent-Skills, see skill_import.py):
#   BUNDLED_LIBRARY_DIR — an OPTIONAL in-repo slot for FIRST-PARTY skills we
#     author ourselves. It ships empty/absent: GT bundles no third-party
#     content (kept clean for corporate use). Scanned if present.
#   LIBRARY_DIR         — the user's OWN imports, in ~/.gt (that machine only).
BUNDLED_LIBRARY_DIR = ROOT / "skills" / "library"
LIBRARY_DIR = USER_DIR / "skills" / "library"


@dataclass
class Skill:
    name: str
    triggers: list
    priority: int
    body: str
    source: str = ""
    description: str = ""
    category: str = ""
    words: int = field(default=0)

    def __post_init__(self):
        self.words = len(self.body.split())

    def embed_text(self) -> str:
        """What to embed for semantic matching — the gist, not the whole body.

        Name + description carry the 'use when' intent; a slice of the body
        grounds it. Kept short so 775 skills embed fast and cheap."""
        gist = self.description or ", ".join(self.triggers)
        return f"{self.name}. {gist}\n{self.body[:500]}"


def _parse(path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    match = _FRONT.match(text)
    if not match:
        return None
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()
    triggers = [t.strip().lower() for t in meta.get("triggers", "").split(",")
                if t.strip()]
    if not triggers:
        return None
    return Skill(
        name=meta.get("name", path.stem),
        triggers=triggers,
        priority=int(meta.get("priority", 1) or 1),
        body=text[match.end():].strip(),
        source=str(path),
        description=meta.get("description", ""),
        category=meta.get("category", ""),
    )


def load_skills(extra_dirs=None, include_library=True) -> list:
    """Load skills from the repo's skills/ dir, the user's ~/.gt/skills/, and
    the imported library (~/.gt/skills/library/). Later dirs override earlier
    by name; library names are category-prefixed so they never clash with the
    bundled ones."""
    dirs = [ROOT / "skills", USER_DIR / "skills"]
    if include_library:
        dirs += [BUNDLED_LIBRARY_DIR, LIBRARY_DIR]  # repo-shipped, then user's
    dirs += list(extra_dirs or [])
    found = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            skill = _parse(f)
            if skill:
                found[skill.name] = skill  # later dirs override earlier
    return list(found.values())


def _keyword_hits(skill, low):
    return sum(1 for t in skill.triggers
               if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", low))


def _select_keyword(skills, text, limit):
    """Whole-word trigger match, tie-broken by priority — the offline path."""
    low = (text or "").lower()
    scored = []
    for skill in skills:
        hits = _keyword_hits(skill, low)
        if hits:
            scored.append((hits, skill.priority, skill))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [s[2] for s in scored[:max(0, int(limit))]]


def select(skills, text, limit=2, index=None, min_score=0.40):
    """Pick the most relevant skills for a request.

    With an embedding `index` ready, rank the WHOLE library by semantic
    similarity (so 775 imported skills are usable), nudged by exact keyword
    hits and priority. Without one — offline, or before the index finishes
    building — fall back to the pure keyword matcher (unchanged behaviour)."""
    if index is None or not getattr(index, "ready", False):
        return _select_keyword(skills, text, limit)

    low = (text or "").lower()
    try:
        sims = index.similarities(text, skills)
    except Exception:
        return _select_keyword(skills, text, limit)
    scored = []
    for s in skills:
        cos = sims.get(s.name, 0.0)
        hits = _keyword_hits(s, low)
        if cos < min_score and hits == 0:
            continue
        # cosine leads; an exact trigger hit and higher priority break ties.
        score = cos + 0.18 * min(hits, 3) + 0.02 * s.priority
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:max(0, int(limit))]]


def skills_block(selected, max_chars=1500) -> str:
    """Render selected skills for the system prompt, each capped so a couple
    of long imported playbooks can't blow the context budget."""
    if not selected:
        return ""
    parts = []
    for s in selected:
        body = s.body
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n… [playbook trimmed]"
        parts.append(f"## Playbook: {s.name}\n{body}")
    return ("# Expert playbooks — follow these standards for this task\n"
            + "\n\n".join(parts))


# --------------------------------------------------------------------------- #
#  Embedding index — semantic skill selection over a large library
# --------------------------------------------------------------------------- #

class SkillIndex:
    """Embeds skills once (cached by content hash) so selection can rank the
    whole library by meaning, not just trigger keywords.

    The cache is a tiny sqlite of content-hash → vector, so re-launches and
    re-imports only embed what actually changed. Building runs off the main
    thread; until `ready`, selection transparently uses the keyword matcher.
    """

    def __init__(self, llm, cache_path, embed_role="embed"):
        self.llm = llm
        self.embed_role = embed_role
        self.cache_path = str(cache_path)
        self.vecs = {}          # skill.name -> unit vector (np.float32)
        self.ready = False
        self._lock = threading.Lock()

    @staticmethod
    def _hash(skill):
        return hashlib.sha1(skill.embed_text().encode("utf-8")).hexdigest()

    def _open(self):
        Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.cache_path)
        conn.execute("CREATE TABLE IF NOT EXISTS emb("
                     "h TEXT PRIMARY KEY, vec BLOB, dim INTEGER)")
        return conn

    @staticmethod
    def _unit(v):
        v = np.asarray(v, dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-8)

    def build(self, skills, batch=64, log=None):
        """Embed any uncached skills, load all vectors, mark ready. Returns the
        number newly embedded. Safe to call in a background thread."""
        conn = self._open()
        cached = {h: np.frombuffer(vec, dtype=np.float32)
                  for h, vec, _ in conn.execute("SELECT h,vec,dim FROM emb")}
        vecs, todo = {}, []
        by_hash = {}
        for s in skills:
            h = self._hash(s)
            by_hash[s.name] = h
            if h in cached:
                vecs[s.name] = self._unit(cached[h])
            else:
                todo.append(s)
        embedded = 0
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            embs = self.llm.embed(self.embed_role, [s.embed_text() for s in chunk])
            rows = []
            for s, e in zip(chunk, embs):
                arr = np.asarray(e, dtype=np.float32)
                vecs[s.name] = self._unit(arr)
                rows.append((by_hash[s.name], arr.tobytes(), int(arr.shape[0])))
            conn.executemany("INSERT OR REPLACE INTO emb(h,vec,dim) VALUES(?,?,?)",
                             rows)
            conn.commit()
            embedded += len(chunk)
            if log:
                log(min(i + batch, len(todo)), len(todo))
        conn.close()
        with self._lock:
            self.vecs = vecs
            self.ready = bool(vecs)
        return embedded

    def similarities(self, query, skills):
        """cosine(query, skill) for every skill with a cached vector."""
        if not self.ready:
            return {}
        q = self._unit(self.llm.embed(self.embed_role, [query])[0])
        out = {}
        for s in skills:
            v = self.vecs.get(s.name)
            if v is not None and v.shape == q.shape:
                out[s.name] = float(np.dot(q, v))
        return out
