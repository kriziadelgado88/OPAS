"""Build the Claude system prompt from a skill YAML + current phase + RAG chunks.

Returns (system_prompt_text, yaml_refs) where yaml_refs is a list of dotted
YAML paths that shaped this prompt — used by the front-end side-panel.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Stand-alone block helpers (importable individually for tests)
# ---------------------------------------------------------------------------

def constitution_augmentation(constitution: dict | None) -> str:
    if not constitution:
        return ""
    injection = constitution.get("system_prompt_injection", "").strip()
    return ("\n\n" + injection) if injection else ""


def prior_session_context(memories: list[dict]) -> str:
    if not memories:
        return ""
    lines = [f"- [{m['category']}] {m['memory_text']}" for m in memories]
    return (
        "## Prior sessions with this learner\n"
        + "\n".join(lines)
        + "\nUse this to calibrate difficulty and acknowledge their prior work when relevant. Do not reference dates."
    )


def learner_profile_block(profile_prefs: dict | None) -> str:
    """G1 — [LEARNER PROFILE] injection. Returns '' when prefs are empty."""
    if not profile_prefs:
        return ""
    parts: list[str] = []
    lang = profile_prefs.get("language")
    if lang and lang.lower() not in ("en", "english"):
        parts.append(
            f"Preferred language: {lang} — respond in {lang} "
            f"unless the student switches to English."
        )
    interests = profile_prefs.get("interests") or []
    if interests:
        parts.append(
            f"Interests: {', '.join(interests)}. "
            f"Draw examples from these domains when possible."
        )
    bandwidth = profile_prefs.get("bandwidth")
    if bandwidth == "low":
        parts.append("Bandwidth hint: low — keep responses concise; skip long explanations unless asked.")
    elif bandwidth == "high":
        parts.append("Bandwidth hint: high — you may use richer explanations and more depth.")
    if not parts:
        return ""
    return "[LEARNER PROFILE]\n" + "\n".join(parts)


def time_budget_block(minutes: int) -> str:
    """G2 — [TIME BUDGET] injection."""
    return (
        f"[TIME BUDGET]\n"
        f"The learner has approximately {minutes} minutes this session. "
        f"Cover the highest-impact phase completely; defer optional depth to a future session. "
        f"If you sense the session is running long, pivot to a short recap and one final probe."
    )


def mode_directive(mode: str) -> str:
    """G4 — [MODE] injection. Controls teach-first vs review-first vs auto-probe."""
    if mode == "teach":
        return (
            "[MODE: TEACH]\n"
            "The student has NOT read the materials. Begin by teaching the core concept "
            "in 3-5 sentences grounded in the retrieved corpus. Then ask the first "
            "practice question. Do NOT assume prior knowledge of the readings."
        )
    if mode == "review":
        return (
            "[MODE: REVIEW]\n"
            "Assume the student has read the materials. Skip any introductory explanation "
            "and go straight to a practice question that targets the phase objectives."
        )
    if mode == "auto":
        return (
            "[MODE: AUTO]\n"
            "You do not know whether the student has read the materials. "
            "Open with a single friendly, low-stakes question to gauge prior knowledge "
            "(e.g., 'In your own words, what is...?'). "
            "If probes are listed below, embed <probe id='PROBE_ID'/> in your opening turn "
            "so the system can score the response and calibrate the session. "
            "Do NOT lecture first. Wait for the student's answer before deciding whether "
            "to explain or continue to practice."
        )
    return ""


# ---------------------------------------------------------------------------
# Main system-prompt builder
# ---------------------------------------------------------------------------

def build_system_prompt(
    skill: dict,
    phase: dict,
    corpus_chunks: list[dict],
    *,
    is_session_start: bool = False,
    constitution: dict | None = None,
    memories: list[dict] | None = None,
    profile_prefs: dict | None = None,
    time_budget_minutes: int | None = None,
    mode: str | None = None,
) -> tuple[str, list[str]]:
    sections: list[str] = []
    refs: list[str] = []
    phase_id = phase.get("id", "unknown")

    # 0. Learner profile (language/interests/bandwidth) — must come first so
    #    the language instruction is in scope for every subsequent section.
    profile_blk = learner_profile_block(profile_prefs)
    if profile_blk:
        sections.append(profile_blk)

    # 0.5. Instructional mode directive (teach / review / auto)
    if mode:
        md = mode_directive(mode)
        if md:
            sections.append(md)

    # 1. Instructional model
    im = skill.get("pedagogy", {}).get("instructional_model", {})
    if im.get("description"):
        sections.append(f"## Instructional model\n{im['description']}")
        refs.append("pedagogy.instructional_model.description")

    # 2. Persona voice + register
    persona = skill.get("persona", {})
    voice_parts = []
    if persona.get("voice"):
        voice_parts.append(f"Voice: {persona['voice']}")
    if persona.get("register"):
        voice_parts.append(f"Register: {persona['register']}")
    if voice_parts:
        sections.append("## Persona\n" + "\n".join(voice_parts))
        refs.append("persona.voice")

    # 3. Forbidden moves (hard negatives)
    forbidden = im.get("forbidden_moves", [])
    if forbidden:
        lines = "\n".join(f"- Never: {m}" for m in forbidden)
        sections.append(f"## Forbidden moves (hard constraints)\n{lines}")
        refs.append("pedagogy.instructional_model.forbidden_moves")

    # 4. Disallowed phrases
    disallowed = persona.get("disallowed_phrases", [])
    if disallowed:
        lines = "\n".join(f'- Never output: "{p}"' for p in disallowed)
        sections.append(f"## Disallowed phrases\n{lines}")
        refs.append("persona.disallowed_phrases")

    # 5. Current phase objectives
    objectives = phase.get("objectives", [])
    if objectives:
        obj_text = "\n".join(f"- {o}" for o in objectives)
        sections.append(f"## Current phase objectives (phase: {phase_id})\n{obj_text}")
        refs.append(f"phases[{phase_id}].objectives")

    # 6. Personalization
    personalization = skill.get("personalization", {})
    hard_locked = personalization.get("hard_locked", [])
    if hard_locked:
        sections.append(
            f"## Personalization — hard-locked (never adapt these)\n"
            f"The following may not be adapted regardless of learner request: {', '.join(hard_locked)}"
        )
        refs.append("personalization.hard_locked")

    allowed_surfaces = personalization.get("allowed_surfaces", [])
    if allowed_surfaces:
        surface_lines = []
        for s in allowed_surfaces:
            desc = s.get("description", "")
            bounds = s.get("bounds", {})
            bound_str = "; ".join(f"{k}: {v}" for k, v in bounds.items()) if bounds else ""
            surface_lines.append(
                f"- {s.get('id', '?')}: {desc}" + (f" (bounds: {bound_str})" if bound_str else "")
            )
        sections.append(
            "## Personalization — allowed surfaces (vary these within stated bounds)\n"
            + "\n".join(surface_lines)
        )
        refs.append("personalization.allowed_surfaces")

    # 7. Grounding / citation instruction
    grounding = skill.get("corpus", {}).get("grounding_policy", {})
    if grounding.get("require_citation"):
        style = grounding.get("citation_style", "[Source, §section]")
        sections.append(
            f"## Citation requirement\n"
            f"Cite every factual claim using the format: {style}. "
            f"Do not state facts about the course material without a citation."
        )
        refs.append("corpus.grounding_policy")

    # 8. Session start instruction (mode-aware: skip generic opener when mode is set)
    if is_session_start and not mode:
        sections.append(
            "## Session opening instruction\n"
            "Begin the session with your first Socratic question targeting the current phase objectives. "
            "Do not lecture. Do not give an overview. Ask one question."
        )

    # 8.5. Prior session memories
    if memories:
        sections.append(prior_session_context(memories))

    # 9. Corpus chunks
    if corpus_chunks:
        chunk_lines = []
        for c in corpus_chunks:
            meta = c.get("metadata") or {}
            title = meta.get("source_title", c.get("source_id", "Source"))
            page = meta.get("page_num", "")
            section = meta.get("section_heading", "")
            label_parts = [title]
            if page:
                label_parts.append(f"p.{page}")
            if section:
                label_parts.append(f"§{section}")
            label = ", ".join(label_parts)
            chunk_lines.append(f"--- [{label}] ---\n{c['chunk_text']}")
        sections.append("## Grounded corpus (use for all factual claims)\n" + "\n\n".join(chunk_lines))

    # 10. Probe tag instruction
    probe_set = phase.get("probe_set", [])
    if probe_set:
        probe_list = "\n".join(
            f"- id={p['id']}: {p.get('prompt', p.get('question', ''))}" for p in probe_set
        )
        sections.append(
            "## Probe elicitation\n"
            "When you ask a question that corresponds to one of the evaluation probes listed below, "
            "output `<probe id='PROBE_ID'/>` at the very start of your reply (before any other text). "
            "This tag is stripped before the learner sees it.\n\n"
            "Evaluation probes for this phase:\n" + probe_list
        )
        refs.append(f"phases[{phase_id}].probe_set")

    # 11. Time budget (operational constraint — near end, after content)
    if time_budget_minutes:
        sections.append(time_budget_block(time_budget_minutes))

    # 12. Constitution injection (safety — always last)
    aug = constitution_augmentation(constitution)
    if aug:
        sections.append(aug.strip())
        for i, rule in enumerate(constitution.get("rules", [])):
            refs.append(f"constitution.rules[{i}].id")

    return "\n\n".join(sections), refs


def off_corpus_augmentation() -> str:
    return (
        "\n\n## Off-corpus turn\n"
        "The learner has asked something that falls outside the grounded course materials. "
        "Explain warmly that you can only discuss topics grounded in the course materials, and redirect them."
    )
