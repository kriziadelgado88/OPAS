"""Pedagogy catalogue endpoint.

Public — no auth dependency. Returns the list of supported instructional
approaches that teacher wizard and student self-serve flows can both render.

TODO: migrate to DB-backed catalogue once we have >3 pedagogies post-demo.
      Each pedagogy would become a row in a `pedagogies` table seeded from
      the YAML files in agent/pedagogies/ (folder not yet created).
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

_CATALOGUE: list[dict] = [
    {
        "id": "discovery-learning",
        "name": "Discovery Learning",
        "description": (
            "Bruner-style constructivist approach. The agent does not deliver "
            "explanations upfront — instead it poses problems or phenomena and "
            "guides the student to construct understanding through exploration. "
            "Heavy use of probes; direct instruction is a last resort."
        ),
        "techniques": [
            "open-ended problem posing",
            "minimal-hint scaffolding",
            "probe-before-explain",
            "celebrate partial understanding",
            "defer full explanation until after student attempt",
        ],
    },
    {
        "id": "socratic",
        "name": "Socratic Method",
        "description": (
            "Question-first dialogue where the agent rarely asserts facts directly. "
            "Every tutor turn ends with an open question. The agent exposes "
            "contradictions in the student's reasoning and waits for the student "
            "to resolve them. Suitable for conceptual and ethical reasoning."
        ),
        "techniques": [
            "never assert without asking first",
            "elenctic questioning (expose contradictions)",
            "every turn ends with a question",
            "acknowledge and probe partial answers",
            "use student's own words back to them",
        ],
    },
    {
        "id": "spiral",
        "name": "Spiral Curriculum",
        "description": (
            "Revisits the same core concept at increasing depth across phases. "
            "Phase 1 builds intuition, phase 2 formalises, phase 3 applies to "
            "novel contexts. Each revisit assumes the prior layer. Good for "
            "quantitative and technical material."
        ),
        "techniques": [
            "concrete-before-abstract sequencing",
            "explicit 'we've seen this before' callbacks",
            "increasing formalism per phase",
            "transfer tasks in final phase",
            "brief recap probe at each phase entry",
        ],
    },
    {
        "id": "direct-instruction",
        "name": "Direct Instruction with Checks",
        "description": (
            "Agent delivers a tight, structured explanation first, then "
            "immediately checks comprehension with a probe before moving on. "
            "Good for procedural knowledge and prerequisite concepts where "
            "discovery would take too long given time constraints."
        ),
        "techniques": [
            "teach-then-check sequence",
            "explicit learning objectives stated upfront",
            "frequent low-stakes comprehension probes",
            "immediate corrective feedback",
            "worked example before independent practice",
        ],
    },
]


@router.get("/pedagogies")
def list_pedagogies() -> list[dict]:
    """Return all supported pedagogy definitions. Public — no auth required."""
    return _CATALOGUE
