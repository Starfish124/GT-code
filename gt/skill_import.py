"""Import an Anthropic Agent-Skills library into GT's skill format.

GT ships NO third-party skill content of its own — only its first-party core
playbooks. This is opt-in tooling so a user can bring THEIR OWN Agent-Skills
library (one they authored, or a source they control/trust) into GT.

Agent Skills ship as `<skill>/SKILL.md` with YAML front matter — a `name` and
a natural-language `description` ("Use when the user asks to …") — followed by
a markdown playbook. GT's own skills are single `.md` files keyed by trigger
words. This converts the former into the latter: it derives trigger keywords
from the name + description, tags each skill with its source category, and
writes them into the user's library (`~/.gt/skills/library/`, that machine
only) where the loader picks them up. Embedding-based selection (skills.py)
then ranks the whole library semantically per request, so keyword coverage
only needs to be a fallback. Whatever license the imported source carries is
the user's responsibility to honour.

Run from GT: `/skills import <path-or-git-url>`.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)

# Words too generic to be useful triggers (matched as whole words at runtime).
_STOP = set((
    "a an the of to for and or in on at by with without your you our this that "
    "these those is are be as it its into from when where what which who whom "
    "how why use uses used using user users skill skills asks ask asked need "
    "needs needed want wants help helps do does done make makes making create "
    "creates get gets should would could will can may might any all some more "
    "most other others than then them they their there here about across via "
    "over under out up off not no yes if else while each per also just only "
    "such but so we i me my he she his her they’re you’re it’s"
).split())

# 'Use when the user asks to …' boilerplate that opens most descriptions.
_LEAD = re.compile(
    r"^\s*use\s+(this\s+skill\s+)?when(\s+the\s+user)?(\s+(is|wants?|needs?|asks?|"
    r"you|they))?(\s+to)?\b", re.I)


def _clean(v: str) -> str:
    return v.strip().strip("\"'").strip()


def _parse_front(text: str):
    """Return (meta dict, body) for an Agent-Skills SKILL.md."""
    m = _FRONT.match(text)
    if not m:
        return {}, text.strip()
    meta, key = {}, None
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        if line[:1] in " \t" and key:            # folded continuation line
            meta[key] = (meta.get(key, "") + " " + line.strip()).strip()
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            key = k.strip().lower()
            meta[key] = _clean(v)
    return meta, text[m.end():].strip()


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "skill"


def _triggers(name: str, description: str, category: str, limit: int = 14):
    """Derive keyword triggers from the name + description (fallback matcher)."""
    desc = _LEAD.sub(" ", description or "")
    words = re.findall(r"[a-z][a-z0-9+.#-]{2,}", f"{name} {desc}".lower())
    seen, out = set(), []
    # the skill's own name tokens and its category go first — always relevant
    for w in re.findall(r"[a-z0-9]{3,}", f"{name} {category}".lower()):
        if w not in seen and w not in _STOP:
            seen.add(w)
            out.append(w)
    for w in words:
        if w in _STOP or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= limit:
            break
    return out[:limit]


def _resolve_source(src: str) -> tuple[Path, Path | None]:
    """A local path stays put; a git URL is cloned to a temp dir (cleaned up)."""
    if re.match(r"^(https?://|git@).*", src) or src.endswith(".git"):
        tmp = Path(tempfile.mkdtemp(prefix="gt-skills-"))
        subprocess.run(["git", "clone", "--depth", "1", src, str(tmp)],
                       check=True, capture_output=True, text=True)
        return tmp, tmp
    return Path(src).expanduser().resolve(), None


def import_library(src: str, out_dir: Path, priority: int = 1):
    """Convert every SKILL.md under `src` into a GT skill file in `out_dir`.

    Returns (count, categories, source_label). Clears out_dir first so a
    re-import never leaves stale skills behind. Does not touch GT's bundled
    skills/ or the user's hand-written ~/.gt/skills/*.md — only the library.
    """
    root, tmp = _resolve_source(src)
    try:
        if not root.is_dir():
            raise FileNotFoundError(f"not a directory: {root}")
        # Skip hidden dirs — some Agent-Skills repos mirror the whole tree into
        # per-tool plugin dirs (.gemini/.codex/.claude/…); those are duplicates,
        # plus .git/.github. Importing them would double every skill.
        files = sorted(f for f in root.rglob("SKILL.md")
                       if not any(part.startswith(".")
                                  for part in f.relative_to(root).parts))
        if not files:
            raise FileNotFoundError(f"no SKILL.md files under {root}")

        out_dir.mkdir(parents=True, exist_ok=True)
        # Only clear previously-generated skills (named "<cat>__<slug>.md") — a
        # hand-added README.md / LICENSE / attribution in the dir is preserved.
        for old in out_dir.glob("*__*.md"):
            old.unlink()

        count, cats, used = 0, {}, set()
        for f in files:
            try:
                meta, body = _parse_front(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            rel = f.relative_to(root).parts
            category = rel[0] if len(rel) > 1 else "general"
            raw_name = meta.get("name") or f.parent.name
            name = f"{category}/{_slug(raw_name)}"
            description = meta.get("description", "").strip()
            triggers = _triggers(raw_name, description, category)
            if not triggers or not body:
                continue

            fname = f"{_slug(category)}__{_slug(raw_name)}.md"
            i = 2
            while fname in used:                 # de-dup collisions across trees
                fname = f"{_slug(category)}__{_slug(raw_name)}-{i}.md"
                i += 1
            used.add(fname)

            front = (f"---\nname: {name}\n"
                     f"triggers: {', '.join(triggers)}\n"
                     f"priority: {priority}\n"
                     f"category: {category}\n"
                     + (f"description: {description}\n" if description else "")
                     + "---\n")
            (out_dir / fname).write_text(front + body + "\n", encoding="utf-8")
            count += 1
            cats[category] = cats.get(category, 0) + 1
        label = src if tmp is None else src
        return count, cats, label
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
