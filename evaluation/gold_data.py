"""Parse and load the gold question/answer sets (per chapter) used to drive evaluation."""
from __future__ import annotations
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from configs.settings import get_settings

_CHAPTER_HEADING = re.compile(r"^# (Chapter \d+) – (.+?) \(\d+ Questions\)$")

QUESTIONS_DIR = Path(__file__).parent / "questions"


HF_DATASET_ID = "Fatema142/BB-FinQA-X"


@dataclass
class GoldQuestion:
    id: str
    number: int
    chapter: str
    chapter_title: str
    question: str
    answer: str
    query_type: str
    presentation_format: str
    difficulty: str
    source_page: str
    reasoning_skill: str = ""
    evidence: str | None = None


def _strip_markdown_bold(text: str) -> str:
    return text.replace("**", "")


def _chapter_slug(chapter: str) -> str:
    """"Chapter 1" -> "chapter_1" — used for both filenames and dict keys."""
    return chapter.lower().replace(" ", "_")


def parse_questions_md(path: Path | None = None) -> list[GoldQuestion]:
    """Every question across all chapters in `docs/questions.md`, in file order. This is the
    source-of-truth parser; `load_chapter_questions` (below) is what the rest of the evaluation
    pipeline actually reads from day to day."""
    md_path = path or (get_settings().paths.project_root / "docs" / "questions.md")
    text = md_path.read_text(encoding="utf-8")

    questions: list[GoldQuestion] = []
    chapter, chapter_title = "", ""
    for line in text.splitlines():
        heading_match = _CHAPTER_HEADING.match(line.strip())
        if heading_match:
            chapter, chapter_title = heading_match.group(1), heading_match.group(2)
            continue

        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("|----") or stripped.startswith("| ID |"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 9 or not cells[0].isdigit():
            continue

        questions.append(
            GoldQuestion(
                id=f"{_chapter_slug(chapter)}-q{cells[0]}",
                number=int(cells[0]),
                chapter=chapter,
                chapter_title=chapter_title,
                question=cells[2],
                answer=_strip_markdown_bold(cells[7]),
                query_type=cells[3],
                reasoning_skill=cells[4],
                presentation_format=cells[5],
                difficulty=cells[6],
                source_page=cells[8],
                evidence=None,
            )
        )
    return questions


def export_chapter_json(questions: list[GoldQuestion] | None = None, out_dir: Path = QUESTIONS_DIR) -> list[Path]:
    """Writes one JSON file per chapter (`chapter_1.json`, `chapter_2.json`, ...), each a list of
    that chapter's gold question records. Re-run this (`python -m evaluation.gold_data`) whenever
    `docs/questions.md` changes — these files, not the markdown, are what the eval scripts read."""
    questions = questions if questions is not None else parse_questions_md()
    out_dir.mkdir(parents=True, exist_ok=True)

    by_chapter: dict[str, list[GoldQuestion]] = {}
    for q in questions:
        by_chapter.setdefault(q.chapter, []).append(q)

    written: list[Path] = []
    for chapter, chapter_questions in by_chapter.items():
        out_path = out_dir / f"{_chapter_slug(chapter)}.json"
        out_path.write_text(json.dumps([asdict(q) for q in chapter_questions], indent=2), encoding="utf-8")
        written.append(out_path)
    return written


def load_chapter_questions(chapter: int | str, questions_dir: Path = QUESTIONS_DIR) -> list[GoldQuestion]:
    """Loads one chapter's gold questions from its materialized JSON file. `chapter` may be an int
    (1, 2, 3) or the slug/filename stem directly ("chapter_1")."""
    slug = f"chapter_{chapter}" if isinstance(chapter, int) or str(chapter).isdigit() else str(chapter)
    path = questions_dir / f"{slug}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} doesn't exist — run `python -m evaluation.gold_data` first to generate it from docs/questions.md"
        )
    return [GoldQuestion(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def _chapter_number(value: str) -> str | None:
    """Pull the chapter number out of an id ("chapter_3-q7" -> "3") or a chapter label
    ("Chapter 3: Price and Inflation" -> "3")."""
    m = re.search(r"chapter[_ ](\d+)", value, flags=re.IGNORECASE)
    return m.group(1) if m else None


def load_hf_questions(chapter: int | str | None = None) -> list[GoldQuestion]:
    """Load the gold questions straight from the Hugging Face Hub (`Fatema142/BB-FinQA-X`,
    `train` split, 500 rows). Pass `chapter` (1-9) to keep only that chapter, or leave it `None`
    for the whole dataset. Requires the `datasets` package (already a project dependency)."""
    from datasets import load_dataset

    rows = load_dataset(HF_DATASET_ID, split="train")
    want = str(chapter) if chapter is not None else None

    questions: list[GoldQuestion] = []
    for row in rows:
        num = _chapter_number(str(row.get("id", ""))) or _chapter_number(str(row.get("chapter", "")))
        if want is not None and num != want:
            continue
        chapter_label = str(row.get("chapter", "") or (f"Chapter {num}" if num else ""))
        chapter_title = str(row.get("chapter_title") or "")
        if not chapter_title and ":" in chapter_label:
            chapter_label, chapter_title = (p.strip() for p in chapter_label.split(":", 1))
        questions.append(
            GoldQuestion(
                id=str(row["id"]),
                number=int(row.get("number") or len(questions) + 1),
                chapter=chapter_label.split(":", 1)[0].strip(),
                chapter_title=chapter_title,
                question=str(row["question"]),
                answer=_strip_markdown_bold(str(row["answer"])),
                query_type=str(row.get("query_type", "")),
                reasoning_skill=str(row.get("reasoning_skill", "")),
                presentation_format=str(row.get("presentation_format", "")),
                difficulty=str(row.get("difficulty", "")),
                source_page=str(row.get("source_page", "")),
                evidence=(str(row["evidence"]) if row.get("evidence") else None),
            )
        )
    return questions


def hf_chapters() -> list[str]:
    """Chapter numbers present in the Hugging Face dataset, sorted."""
    seen = {_chapter_number(q.id) for q in load_hf_questions()}
    return sorted((n for n in seen if n), key=int)


def list_available_chapters(questions_dir: Path = QUESTIONS_DIR) -> list[str]:
    """Chapter numbers (as strings, e.g. "1") with a materialized JSON file, sorted."""
    if not questions_dir.exists():
        return []
    numbers = []
    for path in questions_dir.glob("chapter_*.json"):
        m = re.match(r"chapter_(\d+)$", path.stem)
        if m:
            numbers.append(m.group(1))
    return sorted(numbers, key=int)


def parse_page_range(page_no: str) -> set[int]:
    """`docs/questions.md`'s Page No. column is a single page ("4"), an en-dash range ("4–5"), or
    occasionally an ASCII hyphen range ("4-5") — normalize all forms to the explicit set of page
    numbers, used as the gold-relevance signal for Hit Rate@k / MRR (the general RAG metrics) since no per-chunk
    gold annotation exists, only this page-level one."""
    pages: set[int] = set()
    for part in re.split(r"[,/]", page_no):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[–—-]\s*(\d+)$", part)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            pages.update(range(start, end + 1))
            continue
        if part.isdigit():
            pages.add(int(part))
    return pages


if __name__ == "__main__":
    paths = export_chapter_json()
    for p in paths:
        print(f"Wrote {p}")
