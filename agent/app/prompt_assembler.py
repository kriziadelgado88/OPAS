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


def agent_identity_block(profile_prefs: dict | None) -> str:
    """Injects the learner-chosen agent name (and color, if set) into the
    system prompt so the agent introduces itself by name and self-references
    consistently across turns. Returns '' when no agent_name is set.

    Stored under profile_prefs.agent_name (free-text, capped at ~20 chars by
    the frontend). Optional companion field profile_prefs.agent_color (hex).
    """
    if not profile_prefs:
        return ""
    name = (profile_prefs.get("agent_name") or "").strip()
    if not name:
        return ""
    parts = [
        f"## Agent identity",
        f"Your name is **{name}** — chosen by this learner. Introduce yourself in your opening turn (\"Hi! I'm {name}.\") and self-reference by this name when natural in conversation. Don't overuse it — once at the start is enough; only reuse it when it adds warmth or clarity (e.g., \"I'll be honest with you here — \" not \"As {name}, I'll be honest with you\").",
    ]
    return "\n".join(parts)


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


def signal_tag_instructions() -> str:
    """9.5 — Signal tags. Tells the model to emit machine-parseable cues that the
    student frontend renders as blackboard elements (keyword pills, diagrams,
    celebration cards, mastery progress, beat structure). The tags are stripped
    before the chat bubble renders, so they never appear in plain text.

    Grammar (v2 — adds BEAT structuring, hero flag !, POINT callbacks, LaTeX):

      [BEAT:title]                    structural marker — start of a teaching unit
      [BEAT:title|min=15]             optional minimum dwell in seconds
      [CONCEPT:name]                  label above the blackboard
      [MODE:EXPLAIN|CHECK|DIAGNOSE]   internal mode marker
      [KEYWORD:term|definition]       supporting key term
      [KEYWORD!:term|definition]      HERO term — the one to remember from this beat
      [DRAW:type|label|details]       diagram (type ∈ compare|steps|tree|formula|curve)
      [DRAW!:type|label|details]      HERO diagram — held longer, larger
      [POINT:term]                    point at a previously-introduced keyword
      [WIN:short description]         celebrate correct understanding
      [MASTERY:+N]                    award progress points (10–25 typical)
      [MASTERY:COMPLETE]              mark the unit fully mastered

    EXPLAIN mode REQUIRES one BEAT, at least one KEYWORD, and at least one DRAW.
    """
    return (
        "## Signal tags (REQUIRED in every teaching response)\n"
        "Embed bracketed tags inline in your reply. The student frontend strips "
        "them from the chat text and renders them on the blackboard. They are "
        "silent rendering hints — never describe them to the learner.\n\n"

        "### Available tags\n"
        "  [BEAT:title]\n"
        "      Open a new teaching unit. One main concept per beat. Every reply "
        "      that introduces new material should start with [BEAT:..]. Beats "
        "      become slides in the end-of-session deck — title yours like a "
        "      slide title (a clean phrase, not a full sentence).\n"
        "      Optional: [BEAT:title|min=20] sets a floor in seconds before the "
        "      blackboard moves on (use only when the concept needs unusual dwell).\n\n"

        "  [CONCEPT:name]\n"
        "      Sets the blackboard's current-concept label. Usually emitted with "
        "      [BEAT:..]; can update mid-beat as you zoom in.\n\n"

        "  [MODE:EXPLAIN] | [MODE:CHECK] | [MODE:DIAGNOSE]\n"
        "      EXPLAIN when teaching new material; CHECK when probing comprehension; "
        "      DIAGNOSE when investigating a misconception.\n\n"

        "  [KEYWORD:term|definition]   supporting key term — small pill\n"
        "  [KEYWORD!:term|definition]  HERO key term — large pill, held longer\n"
        "      Mark exactly ONE OR TWO heroes per beat — the concept(s) the "
        "      student should walk away with. Supporting keywords add texture; "
        "      heroes are the point. If everything is hero, nothing is.\n\n"

        "  [DRAW:type|label|details]   diagram\n"
        "  [DRAW!:type|label|details]  HERO diagram — held longer, larger\n"
        "      type is one of:\n"
        "        compare  — details = 'left_text||right_text' (two columns).\n"
        "                   The label should read 'A vs B'.\n"
        "        steps    — details = 'step1 -> step2 -> step3' (ordered flow).\n"
        "        tree     — details = 'root: child1, child2; child1: leaf1, leaf2'.\n"
        "        formula  — details = LaTeX. Use \\frac{}{}, \\dfrac{}{}, "
        "                   \\cdot, \\mid, \\sum, \\int, etc. Examples:\n"
        "                       P(H \\mid E) = \\dfrac{P(E \\mid H)\\,P(H)}{P(E)}\n"
        "                       \\sigma^2 = \\dfrac{1}{n}\\sum_{i=1}^n (x_i - \\bar{x})^2\n"
        "        curve    — details = 'X_AXIS_LABEL,Y_AXIS_LABEL;shape' where you\n"
        "                   REPLACE X_AXIS_LABEL and Y_AXIS_LABEL with the actual\n"
        "                   labels for your concept (e.g. 'stated confidence,actual\n"
        "                   accuracy;falling' for a calibration curve, or 'time,\n"
        "                   forecast error;bell' for a learning curve). NEVER emit\n"
        "                   the literal placeholder text 'x_label' or 'y_label' —\n"
        "                   that renders as nonsense. shape ∈ rising | falling |\n"
        "                   bell | flat | sigmoid.\n\n"

        "  [POINT:term]\n"
        "      Refer back to a previously-introduced keyword. The renderer pulses "
        "      that pill on the blackboard so the learner's eye snaps to it. Use "
        "      this whenever you ask 'remember when we said...?' — the visual "
        "      callback is what makes the connection land. Match the term EXACTLY "
        "      as you wrote it in the original [KEYWORD:..] (case-insensitive, but "
        "      same spelling).\n\n"

        "  [WIN:short description]\n"
        "      Celebrate correct understanding. Briefly takes center stage on the "
        "      blackboard, then docks. Make the description specific to what the "
        "      learner just did ('You stated a prior without prompting' — not "
        "      'Great job!'). Save these for moments that actually merit it.\n\n"

        "  [MASTERY:+N]\n"
        "      Award N progress points (10 = small step, 25 = big step). Fire only "
        "      when the learner demonstrates correct understanding (right answer, "
        "      correct restatement, successful application).\n"
        "  [MASTERY:COMPLETE]\n"
        "      Mark the current unit fully mastered.\n\n"

        "### Pacing & structure rules\n"
        "  • Open every teaching reply with [BEAT:..]. One beat = one teachable "
        "    unit. Don't cram three concepts into one beat — split them.\n"
        "  • Each beat needs: 1 main concept ([KEYWORD!:..]), 0–2 supporting "
        "    [KEYWORD:..]s, and ideally 1 [DRAW:..] anchoring the visual.\n"
        "  • Use [POINT:..] generously when building on prior beats. The visual "
        "    callback is the single highest-leverage move for retention.\n"
        "  • In [MODE:EXPLAIN] you MUST emit a [BEAT:..], at least one [KEYWORD:..], "
        "    and at least one [DRAW:..]. Without these the blackboard stays empty.\n"
        "  • For formula DRAWs, always use proper LaTeX. Plain ASCII works as a "
        "    fallback but looks like code, not math.\n"
        "  • Tags may appear anywhere in the reply (start, middle, end). They are "
        "    regex-stripped before display.\n\n"

        "### Worked example (EXPLAIN mode — what one good agent reply looks like)\n"
        "  [MODE:EXPLAIN][BEAT:Updating belief with new evidence][CONCEPT:Bayes' rule]\n"
        "  Evidence shows up. Your job is to update — to move from prior to "
        "  posterior. That move has a name. [KEYWORD:posterior|your belief AFTER "
        "  incorporating the new evidence — written P(H|E)] [KEYWORD:likelihood|"
        "  how well the hypothesis explains the evidence — P(E|H)] "
        "  [DRAW!:formula|Bayes' rule|P(H \\mid E) = \\dfrac{P(E \\mid H)\\,P(H)}{P(E)}] "
        "  See it? [POINT:prior] Your prior gets multiplied by how well the "
        "  hypothesis explains what you saw, then normalized. That's the whole "
        "  machine. Your turn — given a prior of 0.3 and a likelihood of 0.8, "
        "  what's roughly the shape of the posterior?\n\n"
        "  Notes on this example:\n"
        "    • Opens with [BEAT:..] naming the unit ('Updating belief with new evidence').\n"
        "    • Has ONE hero — the Bayes formula [DRAW!:..]. The two keywords "
        "      (posterior, likelihood) are supporting context.\n"
        "    • Uses [POINT:prior] to call back to a concept from an earlier beat — "
        "      the renderer will pulse that pill on the right side of the board.\n"
        "    • Ends with a question (Socratic — does NOT give the answer away).\n"
        "    • No [WIN] or [MASTERY] yet — those fire on the NEXT turn after the "
        "      learner demonstrates understanding.\n"
        "    • The learner will see only the prose between the tags."
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

    # 00. OUTPUT DISCIPLINE — placed first because it overrides everything else.
    #     Stops the model from narrating its own process ("Let me draft this:",
    #     "Let me think about word count...", "Let me also remember...") and
    #     keeps the reply directed at the learner.
    sections.append(
        "## Output discipline (read this first, follow it always)\n"
        "Your output is the agent's reply to the learner. Nothing else. "
        "Never narrate your reasoning, drafting, planning, or rule-checking. "
        "Never write meta phrases like \"Let me draft this:\", \"Let me think "
        "about\", \"Let me also remember\", \"OK, the rule says...\", \"First "
        "I need to...\", \"Let me make sure...\". Do not enumerate the "
        "instructions you were given. Do not restate constraints (word count, "
        "format) in the reply. Do all reasoning silently. Output only what "
        "the learner should see — a direct, in-character reply."
    )

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

    # 2.5. Learner-chosen agent identity — name + (later) avatar + voice.
    #      The learner names their agent during onboarding ('Lumi', 'Atlas',
    #      'Sage', etc.); the agent must introduce itself by that name in
    #      its opening turn so the persona feels owned, not generic.
    identity_block = agent_identity_block(profile_prefs)
    if identity_block:
        sections.append(identity_block)
        refs.append("learner.profile_prefs.agent_name")

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

    # 5. Current phase context — name, position, key concepts, the teacher's
    #    opening prompt verbatim (on session start), follow-ups, and resolved
    #    SLO statements. This is the single biggest lever on agent quality:
    #    without this, the agent invents content from corpus chunks; with this,
    #    it follows the pedagogy the teacher actually authored.
    phases = skill.get("phases", []) or []
    total_phases = len(phases)
    # Find the index of the current phase by id, defaulting to its position.
    current_idx = next(
        (i for i, p in enumerate(phases) if p.get("id") == phase.get("id")),
        0,
    )
    phase_name = phase.get("name") or ""
    class_number = phase.get("class_number")
    phase_header_parts = [f"Phase {current_idx + 1} of {total_phases}"] if total_phases else []
    if phase_name:
        phase_header_parts.append(f"'{phase_name}'")
    if class_number:
        phase_header_parts.append(f"Class {class_number}")
    phase_header = " — ".join(phase_header_parts) if phase_header_parts else f"phase: {phase_id}"

    objectives = phase.get("objectives", [])
    # Resolve SLO ids to their actual statements when possible.
    slo_lookup: dict[str, str] = {}
    for slo in skill.get("learning_objectives", {}).get("sub_objectives", []) or []:
        if isinstance(slo, dict) and slo.get("id"):
            slo_lookup[slo["id"]] = slo.get("statement", "")
    primary_lo = skill.get("learning_objectives", {}).get("primary", {})
    if isinstance(primary_lo, dict) and primary_lo.get("id"):
        slo_lookup[primary_lo["id"]] = primary_lo.get("statement", "")

    phase_block_lines: list[str] = [f"## Current phase: {phase_header}"]
    if objectives:
        phase_block_lines.append("### Phase objectives (use the actual statements, not the IDs)")
        for o in objectives:
            stmt = slo_lookup.get(o, "")
            phase_block_lines.append(f"- {o}: {stmt}" if stmt else f"- {o}")

    key_concepts = phase.get("key_concepts", []) or []
    if key_concepts:
        phase_block_lines.append("### Concepts to introduce in this phase (in order)")
        for kc in key_concepts:
            if not isinstance(kc, dict):
                continue
            concept = kc.get("concept", "")
            anchor = kc.get("anchor_case", "")
            if concept and anchor:
                phase_block_lines.append(f"- {concept} (anchor: {anchor})")
            elif concept:
                phase_block_lines.append(f"- {concept}")

    socratic = phase.get("socratic_script", {}) or {}
    # If a pedagogy_override was applied, the instructional_model carries an
    # opener_guidance that supersedes the skill's authored opening_prompt —
    # this is how Discovery / Direct / Inquiry / etc. produce visibly
    # different openers from the same phase content.
    pedagogy_opener_guidance = im.get("opener_guidance") if isinstance(im, dict) else None
    if is_session_start and pedagogy_opener_guidance:
        phase_block_lines.append("### Opening guidance (this pedagogy's specific way of opening — supersedes the skill's authored opener)")
        phase_block_lines.append(str(pedagogy_opener_guidance).strip())
        if socratic.get("opening_prompt"):
            phase_block_lines.append("### Skill's authored opener (FOR REFERENCE ONLY — do not use verbatim; the pedagogy's opening guidance above takes precedence)")
            phase_block_lines.append(str(socratic["opening_prompt"]).strip())
    elif is_session_start and socratic.get("opening_prompt"):
        phase_block_lines.append("### Opening question (use this exact wording, or a very close paraphrase)")
        phase_block_lines.append(str(socratic["opening_prompt"]).strip())
    elif socratic.get("opening_prompt"):
        # Mid-phase reminder — context only, don't restate.
        phase_block_lines.append("### Phase-opener reference (the teacher-authored opener for this phase, for context)")
        phase_block_lines.append(str(socratic["opening_prompt"]).strip())

    follow_ups = socratic.get("follow_ups", []) or []
    if follow_ups:
        phase_block_lines.append("### Follow-up questions (paraphrase OK, use as the next Socratic step)")
        for fq in follow_ups:
            phase_block_lines.append(f"- {fq}")

    transition = socratic.get("transition_to_next")
    if transition:
        phase_block_lines.append("### Transition cue (use when phase mastery is reached)")
        phase_block_lines.append(str(transition).strip())

    sections.append("\n".join(phase_block_lines))
    refs.append(f"phases[{phase_id}].objectives")
    if key_concepts:
        refs.append(f"phases[{phase_id}].key_concepts")
    if socratic:
        refs.append(f"phases[{phase_id}].socratic_script")

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
            f"Do not state facts about the course material without a citation.\n\n"
            f"### Cite naturally, not mechanically\n"
            f"When you reference an idea from the corpus, bring the source into the "
            f"conversation by name and chapter — e.g. 'As Tetlock argues in Ch. 5, "
            f"the best forecasters update incrementally...' rather than dropping a "
            f"bare bracketed citation. Use authors' names (Tetlock, Kahneman, Maxim 5, "
            f"the Cuban Missile Crisis case) when their ideas come up — this is how "
            f"a serious tutor talks about readings, and it helps the learner connect "
            f"what you're saying to what they're being asked to read. Bracketed "
            f"citations in the format above are still required as the formal anchor; "
            f"the natural reference is what makes the agent feel like a teacher, not "
            f"a search engine."
        )
        refs.append("corpus.grounding_policy")

    # 8. Session start instruction (mode-aware: skip generic opener when mode is set)
    if is_session_start and not mode:
        sections.append(
            "## Session opening instruction\n"
            "Open with a warm one-sentence greeting (use the learner's name if you have it from the profile), "
            "then ask your first Socratic question targeting the current phase objectives. "
            "Do not lecture. Do not give an overview. Greeting + one question, nothing more."
        )

    # 8.1. Reply brevity rule — tighter than before. Push detail to the
    #      blackboard, keep chat tight. Hard target: 40-80 words. Allow up to
    #      ~120 words ONLY when the learner explicitly asked for explanation
    #      AND the concept genuinely needs it. Default to short.
    sections.append(
        "## Reply length and structure\n"
        "Keep every chat reply brief — target 40-80 words. The blackboard does the visual heavy "
        "lifting via [BEAT], [KEYWORD], [DRAW] signals; chat is for the question, a short "
        "acknowledgement of the learner's answer, and the next Socratic step. Push concept "
        "definitions, diagrams, and worked examples to the blackboard signals — do NOT restate "
        "in chat what is already on the blackboard. You may go up to ~120 words ONLY when the "
        "learner has explicitly asked for an explanation AND the concept needs it; otherwise stay "
        "under 80. Short feels like a real person. Long feels like a chatbot."
    )

    # 8.15. Conversational warmth — the agent must feel like a teacher in
    #       office hours, not an interrogator. Acknowledge first, then move.
    sections.append(
        "## Conversational warmth (this is what separates a tutor from a quiz bot)\n"
        "Every reply must FIRST acknowledge what the learner just said before moving forward. "
        "The pattern is:\n"
        "  1. Brief acknowledgement (one short clause) — name what they offered, even if "
        "incomplete or wrong. Replies that jump straight to the next question without "
        "responding to the last message read as cold and interrogative.\n"
        "  2. Optional brief paraphrase or build — when their thinking is interesting or "
        "almost-right, restate it back so they know you heard it.\n"
        "  3. Then ask the next question, OR offer one sentence of guidance, OR confirm + advance.\n\n"
        "Vary your openers across turns. Do NOT start two consecutive replies with the same word "
        "or phrase. If your last reply began with \"Right,\" do not begin the next reply with "
        "\"Right.\" Pick a different acknowledgement.\n\n"
        "BANNED phrases — these are bot tells; never use them:\n"
        "  - \"Great question\" / \"Excellent question\" / \"Good question\"\n"
        "  - \"Excellent point\" / \"Excellent\" (as a standalone reaction)\n"
        "  - \"Let's dive in\" / \"Let's break this down\" / \"Let's explore\"\n"
        "  - \"Let me ask you...\" (just ASK; don't announce that you're asking)\n"
        "  - \"That's a really good way of thinking about it\"\n"
        "Instead use natural office-hours acknowledgements, varied per turn — for example:\n"
        "  \"Yes, and notice that...\", \"Right, so the key piece is...\", "
        "\"Mm, you're onto something — \", \"OK, and what about...\", \"True. Now,...\", "
        "\"You're close. Here's what's missing:\", \"Almost. The thing you didn't account for is...\"."
    )

    # 8.2. Writing-style rules — sound like a human, not a corporate blog.
    sections.append(
        "## Writing style (apply to every chat reply)\n"
        "- NEVER use em-dashes (—). Replace them with a period and start a new sentence, "
        "or with a comma to continue.\n"
        "- Avoid contrastive reframing and antithesis structures. Do NOT write things like "
        "\"It's not X, it's Y\" or \"This isn't about A, it's about B.\" Explain the idea "
        "directly without comparison.\n"
        "- Do not use semicolons. Do not use colons to introduce lists. Do not stack "
        "complex subordinate clauses.\n"
        "- Use simple sentence structures. Use active voice.\n"
        "- Vary sentence length naturally. Write the way a thoughtful tutor would talk in "
        "office hours — conversational, plain, sometimes short, sometimes longer when an "
        "idea genuinely needs it. Never sound like a corporate blog post or marketing copy."
    )

    # 8.25. When to teach vs. when to keep asking. Default is Socratic; the
    #       allowance below prevents the dreaded "stuck-in-an-interrogation"
    #       feel where the learner says they don't get it and the agent just
    #       asks another question.
    sections.append(
        "## When to teach vs. when to ask\n"
        "Default mode: ask, don't tell. Stay Socratic.\n\n"
        "BUT — when the learner is clearly stuck, switch to a brief micro-teach. The triggers:\n"
        "  (a) The learner explicitly says they don't understand / are confused / are lost.\n"
        "  (b) The learner gave a clearly wrong answer for the second consecutive turn on the "
        "same probe.\n"
        "  (c) The learner directly asks you to explain.\n\n"
        "When ANY trigger fires, format your reply as:\n"
        "  [acknowledgement] + [ONE crisp sentence of guidance] + [reframed simpler question].\n\n"
        "Example: \"Yeah, this one trips a lot of people. Probability isn't about what WILL happen; "
        "it's about how confident we should be that it does. With that in mind: if I told you the "
        "weather forecast says 70% chance of rain, what's a real action you'd take differently?\"\n\n"
        "This is NOT a license to lecture. ONE sentence of teaching, then immediately back to "
        "asking. A learner saying \"I don't understand\" once does not require teaching — try "
        "simplifying the question first. Only after a second sign of stuckness does the "
        "micro-teach kick in. If you find yourself writing more than one sentence of explanation, "
        "you've drifted out of teaching mode and into lecturing mode. Stop, ask instead."
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

    # 9.5. Signal tags — Poppy blackboard rendering hints. Always emitted, even
    #      when there are no corpus chunks; the frontend always benefits from them.
    sections.append(signal_tag_instructions())

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
