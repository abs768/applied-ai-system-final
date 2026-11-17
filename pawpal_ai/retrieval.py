"""Retrieval layer (RAG) for the PawPal+ AI Care Assistant.

Two sources are searched and merged:

1. ``KnowledgeBase`` - curated Markdown care guides in ``knowledge/``. Each ``## section`` becomes its own chunk,
   so retrieval returns a focused passage (with a citable id like ``dog_care#feeding``) instead of a whole file.
   Users can drop extra ``.md`` files into the folder (or pass ``extra_dirs``) to add custom documents.
2. ``PetProfileSource`` - the owner's live data (pets, ages, breeds, existing tasks), turned into chunks so the
   agent knows what is already scheduled and does not propose duplicates.

Scoring is a small, dependency-free TF-IDF + cosine similarity with a species-aware boost: a chunk written for the
pet's species (or for ``all`` species) is boosted, a chunk for a different species is down-weighted.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from pawpal_system import Owner

DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "be", "my", "me", "i",
    "it", "its", "at", "as", "by", "do", "does", "how", "what", "when", "should", "can", "this", "that", "from",
    "about", "into", "so", "if", "they", "their", "she", "he", "her", "his", "we", "you", "your", "have", "has",
    "need", "needs", "please", "day", "every", "each", "help", "much", "many", "make", "plan", "add", "today",
}

# Small synonym map so everyday words reach the right guide sections.
SYNONYMS = {
    "puppy": ["puppies", "dog"], "pup": ["puppies", "dog"], "puppies": ["dog"],
    "kitten": ["cat"], "kitty": ["cat"], "senior": ["older", "old"], "old": ["senior"], "older": ["senior"],
    "pill": ["medication"], "pills": ["medication"], "meds": ["medication"], "medicine": ["medication"],
    "diabetic": ["insulin", "diabetes"], "diabetes": ["insulin", "diabetic"],
    "walk": ["exercise"], "walks": ["exercise"], "exercise": ["walk"], "food": ["feeding", "meal"],
    "feed": ["feeding", "meal"], "eat": ["feeding"], "meal": ["feeding"], "meals": ["feeding"],
    "poop": ["litter", "toilet"], "litter": ["box"], "bunny": ["rabbit"], "brush": ["grooming"],
    "groom": ["grooming"], "nails": ["grooming"], "play": ["enrichment"], "toy": ["enrichment", "play"],
    "bored": ["enrichment"], "poison": ["toxic", "poisoning"], "ate": ["toxic"], "flea": ["parasite"],
    "tick": ["parasite"], "heartworm": ["parasite"],
}


def _normalize(token: str) -> str:
    """Lower-case and strip a simple plural 's' so 'walks' and 'walk' match."""
    token = token.lower()
    if len(token) > 6 and token.endswith("ing"):
        token = token[:-3]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def tokenize(text: str, expand: bool = False) -> list[str]:
    """Split text into normalized content words, optionally adding synonyms (used for queries)."""
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]+", text.lower())
    tokens: list[str] = []
    for word in words:
        if word in STOPWORDS:
            continue
        tokens.append(_normalize(word))
        if expand:
            tokens.extend(_normalize(syn) for syn in SYNONYMS.get(word, []))
    return tokens


@dataclass
class TaskHint:
    """A structured task suggestion embedded in a guide (``TASK: title | key=value ...``)."""

    title: str
    category: str = "general"
    minutes: int = 15
    priority: str = "medium"
    time: str | None = None
    frequency: str = "daily"

    @classmethod
    def parse(cls, line: str) -> "TaskHint | None":
        """Parse a ``TASK:`` line; return None if it is malformed."""
        body = line.split("TASK:", 1)[1]
        parts = [part.strip() for part in body.split("|")]
        if not parts or not parts[0]:
            return None
        values = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
        try:
            minutes = int(values.get("minutes", 15))
        except ValueError:
            return None
        return cls(
            title=parts[0],
            category=values.get("category", "general"),
            minutes=minutes,
            priority=values.get("priority", "medium"),
            time=values.get("time"),
            frequency=values.get("frequency", "daily"),
        )


@dataclass
class Chunk:
    """One retrievable passage."""

    chunk_id: str
    title: str
    text: str
    species: str = "all"
    source: str = "knowledge"
    task_hints: list[TaskHint] = field(default_factory=list)


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float


def _parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    _, header, body = raw.split("---", 2)
    meta = {}
    for line in header.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, body


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_markdown_chunks(path: Path) -> list[Chunk]:
    """Split one Markdown guide into section chunks."""
    meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
    species = meta.get("species", "all").lower()
    doc_title = meta.get("title", path.stem)
    chunks: list[Chunk] = []
    for section in re.split(r"^## ", body, flags=re.MULTILINE)[1:]:
        heading, _, content = section.partition("\n")
        hints = [
            hint
            for line in content.splitlines()
            if line.strip().startswith("TASK:")
            for hint in [TaskHint.parse(line.strip())]
            if hint is not None
        ]
        prose = "\n".join(
            line for line in content.splitlines() if not line.strip().startswith("TASK:")
        ).strip()
        chunks.append(
            Chunk(
                chunk_id=f"{path.stem}#{_slug(heading)}",
                title=f"{doc_title} - {heading.strip()}",
                text=prose,
                species=species,
                source="knowledge",
                task_hints=hints,
            )
        )
    return chunks


class TfidfIndex:
    """Minimal TF-IDF index with cosine similarity."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        docs = [tokenize(f"{chunk.title} {chunk.title} {chunk.text}") for chunk in chunks]
        doc_freq: Counter[str] = Counter()
        for tokens in docs:
            doc_freq.update(set(tokens))
        total = len(docs) or 1
        self.idf = {term: math.log((1 + total) / (1 + freq)) + 1 for term, freq in doc_freq.items()}
        self.vectors = [self._vector(Counter(tokens)) for tokens in docs]

    def _vector(self, counts: Counter[str]) -> dict[str, float]:
        vector = {term: (1 + math.log(count)) * self.idf.get(term, 0.0) for term, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        return {term: value / norm for term, value in vector.items()}

    def score(self, query: str) -> list[float]:
        query_vec = self._vector(Counter(tokenize(query, expand=True)))
        return [
            sum(weight * doc_vec.get(term, 0.0) for term, weight in query_vec.items())
            for doc_vec in self.vectors
        ]


def species_weight(chunk_species: str, pet_species: str | None) -> float:
    """Boost chunks for the pet's species, keep 'all' neutral-high, and down-weight other species."""
    if pet_species is None or chunk_species == "all":
        return 1.0 if chunk_species == "all" else 0.9
    if chunk_species == pet_species:
        return 1.5
    return 0.3


class KnowledgeBase:
    """Curated + custom Markdown documents, indexed by section."""

    def __init__(self, knowledge_dir: Path | str = DEFAULT_KNOWLEDGE_DIR, extra_dirs: Iterable[Path | str] = ()) -> None:
        dirs = [Path(knowledge_dir), *(Path(item) for item in extra_dirs)]
        self.chunks: list[Chunk] = []
        for directory in dirs:
            if not directory.is_dir():
                raise FileNotFoundError(f"Knowledge folder not found: {directory}")
            for path in sorted(directory.glob("*.md")):
                self.chunks.extend(load_markdown_chunks(path))
        if not self.chunks:
            raise ValueError("Knowledge base is empty; add Markdown guides with '## ' sections.")
        self.index = TfidfIndex(self.chunks)

    def get(self, chunk_id: str) -> Chunk | None:
        return next((chunk for chunk in self.chunks if chunk.chunk_id == chunk_id), None)

    def search(self, query: str, species: str | None = None, k: int = 4, min_score: float = 0.05) -> list[RetrievedChunk]:
        raw_scores = self.index.score(query)
        results = [
            RetrievedChunk(chunk, round(score * species_weight(chunk.species, species), 4))
            for chunk, score in zip(self.chunks, raw_scores)
        ]
        results = [item for item in results if item.score >= min_score]
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:k]


class PetProfileSource:
    """Turns the owner's live pets and tasks into retrievable context chunks."""

    def __init__(self, owner: Owner) -> None:
        self.owner = owner

    def chunks_for(self, pet_name: str | None) -> list[Chunk]:
        chunks = []
        for pet in self.owner.pets:
            if pet_name and pet.name.lower() != pet_name.lower():
                continue
            pending = pet.get_pending_tasks()
            task_text = (
                "; ".join(
                    f"{task.title} ({task.duration_minutes} min, {task.priority}"
                    f"{', at ' + task.preferred_time if task.preferred_time else ''})"
                    for task in pending
                )
                or "no tasks yet"
            )
            age_text = f"{pet.age}-year-old " if pet.age else ""
            breed_text = f"{pet.breed} " if pet.breed else ""
            chunks.append(
                Chunk(
                    chunk_id=f"profile#{_slug(pet.name)}",
                    title=f"Pet profile - {pet.name}",
                    text=(
                        f"{pet.name} is a {age_text}{breed_text}{pet.species}. "
                        f"Existing pending tasks: {task_text}. "
                        f"Owner {self.owner.name} has {self.owner.available_minutes} minutes available, "
                        f"day starts at {self.owner.day_start}."
                    ),
                    species=pet.species,
                    source="pet_profile",
                )
            )
        return chunks


class MultiSourceRetriever:
    """Merges knowledge-base results with the pet-profile context."""

    def __init__(self, knowledge_base: KnowledgeBase, owner: Owner) -> None:
        self.knowledge_base = knowledge_base
        self.profiles = PetProfileSource(owner)

    def retrieve(self, query: str, pet_name: str | None, species: str | None, k: int = 4) -> list[RetrievedChunk]:
        knowledge = self.knowledge_base.search(query, species=species, k=k)
        profile = (
            [RetrievedChunk(chunk, 1.0) for chunk in self.profiles.chunks_for(pet_name)] if pet_name else []
        )
        return profile + knowledge
