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

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT, USER_DIR

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class Skill:
    name: str
    triggers: list
    priority: int
    body: str
    source: str = ""
    words: int = field(default=0)

    def __post_init__(self):
        self.words = len(self.body.split())


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
    )


def load_skills(extra_dirs=None) -> list:
    """Load skills from the repo's skills/ dir plus the user's ~/.gt/skills/
    (user skills with the same name override shipped ones)."""
    dirs = [ROOT / "skills", USER_DIR / "skills"] + list(extra_dirs or [])
    found = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            skill = _parse(f)
            if skill:
                found[skill.name] = skill  # later dirs override earlier
    return list(found.values())


def select(skills, text, limit=2):
    """Pick the most relevant skills for a request: score by how many
    trigger phrases appear (whole-word), tie-break by priority."""
    low = (text or "").lower()
    scored = []
    for skill in skills:
        hits = sum(1 for t in skill.triggers
                   if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", low))
        if hits:
            scored.append((hits, skill.priority, skill))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [s[2] for s in scored[:max(0, int(limit))]]


def skills_block(selected) -> str:
    """Render selected skills for the system prompt."""
    if not selected:
        return ""
    parts = [f"## Playbook: {s.name}\n{s.body}" for s in selected]
    return ("# Expert playbooks — follow these standards for this task\n"
            + "\n\n".join(parts))
