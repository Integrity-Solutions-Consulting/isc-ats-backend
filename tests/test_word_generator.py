"""Regression tests for the Word profile generator's experience section."""

from docx import Document

from app.modules.ai.application.word_generator_service import (
    TEMPLATE_PATH,
    _fill_experience_section,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _is_experience_table(elem: object) -> bool:
    """True if the body element is an experience table (company label first cell)."""
    if getattr(elem, "tag", None) != f"{{{W}}}tbl":
        return False
    tr = elem.find(f"{{{W}}}tr")
    if tr is None:
        return False
    tc = tr.find(f"{{{W}}}tc")
    if tc is None:
        return False
    text = "".join(t.text or "" for t in tc.iter(f"{{{W}}}t"))
    return "Nombre de la empresa" in text


def test_each_experience_renders_as_its_own_separated_table() -> None:
    """Multiple experiences must be separate tables, not one merged table.

    Word fuses two adjacent <w:tbl> elements into a single visual table unless a
    paragraph separates them. Regression: every experience collapsed into one
    table with the fields repeated.
    """
    doc = Document(TEMPLATE_PATH)
    experiences = [
        {
            "company": "Integrity Solutions", "role": "Dev",
            "start_date": "Oct 2025", "end_date": "Actualidad",
            "functions": ["Optimización SQL"], "tools": ["SQL Server"],
        },
        {
            "company": "Next Technology", "role": "Practicante",
            "start_date": "Abr 2025", "end_date": "Jun 2025",
            "functions": ["Pruebas"], "tools": ["SQL Server"],
        },
    ]

    _fill_experience_section(doc, experiences)

    children = list(doc.element.body)
    exp_positions = [i for i, c in enumerate(children) if _is_experience_table(c)]

    assert len(exp_positions) == 2, f"expected 2 experience tables, got {len(exp_positions)}"

    # There must be a paragraph between the two experience tables, otherwise Word
    # merges them into a single table.
    between = children[exp_positions[0] + 1 : exp_positions[1]]
    assert any(c.tag == f"{{{W}}}p" for c in between), (
        "experience tables are adjacent with no separating paragraph — "
        "Word will merge them into one table"
    )
