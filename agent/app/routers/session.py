"""Session lifecycle endpoints — Phase C implementation."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException

from ..auth import LearnerContext, require_learner_token
from ..config import get_settings
from ..constitutions.enforcer import scan_message, struggle_injection
from ..constitutions.loader import ConstitutionNotFound, load_constitution
from ..db import get_supabase
from ..event_emitter import emit
from ..model_adapter import call_model
from ..probe_scorer import extract_probe_tag, score_single_probe
from ..prompt_assembler import build_system_prompt, off_corpus_augmentation, prior_session_context
from ..rag import retrieve_chunks
from ..schemas import (
    SessionEndResponse,
    SessionProgressResponse,
    SessionStartRequest,
    SessionStartResponse,
    SessionStateResponse,
    SessionTurnRequest,
    SessionTurnResponse,
    SkillMetaResponse,
)
from ..session_store import SessionState, get_state, set_state
from ..skill_loader import load_skill

router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_memory(
    *, learner_id: str, skill_id: str, session_id: str,
    category: str, memory_text: str, supabase,
    group_id: str | None = None,
) -> None:
    row: dict = {
        "learner_id": learner_id,
        "skill_id": skill_id,
        "session_id": session_id,
        "category": category,
        "memory_text": memory_text,
    }
    if group_id:
        row["group_id"] = group_id
    supabase.table("learner_memories").insert(row).execute()


def _query_memories(learner_id: str, skill_id: str, supabase) -> list[dict]:
    # Own memories (no group_id filter needed — these are always this learner's)
    own = (
        supabase.table("learner_memories")
        .select("category, memory_text, created_at")
        .eq("learner_id", learner_id)
        .eq("skill_id", skill_id)
        .execute()
        .data or []
    )

    # Group memories: any row tagged with a group the learner belongs to
    group_rows = (
        supabase.table("group_members")
        .select("group_id")
        .eq("learner_id", learner_id)
        .execute()
        .data or []
    )
    group_mems: list[dict] = []
    if group_rows:
        group_ids = [r["group_id"] for r in group_rows]
        group_mems = (
            supabase.table("learner_memories")
            .select("category, memory_text, created_at")
            .eq("skill_id", skill_id)
            .in_("group_id", group_ids)
            .execute()
            .data or []
        )

    # Union: de-duplicate on (category, memory_text), sort recency desc, cap at 5
    seen: set[tuple] = set()
    combined: list[dict] = []
    for m in own + group_mems:
        key = (m["category"], m["memory_text"])
        if key not in seen:
            seen.add(key)
            combined.append(m)

    combined.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    return combined[:5]


def _extract_memories_from_session(state, supabase, settings) -> int:
    """Generate 1-3 durable learner memories from the just-ended session.

    Calls the LLM with a tight summarization prompt, parses the JSON response,
    and writes each bullet to learner_memories via _save_memory. Returns the
    count of memories written. Failures are non-fatal — session_end completes
    regardless.

    The categories returned to the table:
      interest        — domain/topic the learner connected with
      mastery         — concept the learner now grasps confidently
      struggle        — concept the learner found difficult
      pace            — how the learner prefers to be paced
      case_resonance  — case/example that landed for them
      style           — how they prefer the agent to engage them
    """
    import json as _json
    skill = state.skill
    skill_name = skill.get("name") or state.skill_id

    # Use the last ~16 turns. Each role takes one entry; cap to keep prompt small.
    recent = state.messages[-32:] if len(state.messages) > 32 else state.messages
    convo_lines = []
    for m in recent:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        # Skip system-injected messages (constitution interventions etc.)
        if content.startswith("[SYSTEM:"):
            continue
        speaker = "Learner" if role == "user" else "Agent"
        convo_lines.append(f"{speaker}: {content[:400]}")
    if len(convo_lines) < 2:
        return 0   # nothing to summarize

    convo_text = "\n".join(convo_lines)

    system_prompt = (
        "You are summarizing a tutoring session for the learner's long-term "
        "memory profile. Your output will be loaded into the agent's prompt "
        "the next time this learner returns, so it must be CRISP and TRUE — "
        "do not invent details that aren't in the conversation.\n\n"
        f"Skill: {skill_name}\n"
        f"Turns: {sum(1 for m in state.messages if m.get('role') == 'user')}\n\n"
        "Generate up to 3 short memory bullets in JSON. Each bullet has:\n"
        '  "category": one of [interest, mastery, struggle, pace, case_resonance, style]\n'
        '  "memory_text": ONE sentence (max 18 words) the agent should know next time\n\n'
        "Categories:\n"
        "  interest        — domain/topic the learner connected with\n"
        "  mastery         — a specific concept the learner now grasps confidently\n"
        "  struggle        — a specific concept the learner found difficult\n"
        "  pace            — how the learner prefers to be paced\n"
        "  case_resonance  — a Levy case or example that landed for them\n"
        "  style           — how they prefer the agent to engage them\n\n"
        "Return ONLY a JSON array, no surrounding prose. Examples:\n"
        '  [{"category":"mastery","memory_text":"Confident with expected value as a weighted average of outcomes."},'
        ' {"category":"interest","memory_text":"Domain-anchored examples in sports landed best."}]\n\n'
        "If you can't determine 3 distinct memories, return fewer (or [] if "
        "nothing is notable enough to remember). Bullets should be specific "
        "to THIS learner, not generic."
    )

    extraction_message = [{
        "role": "user",
        "content": f"SESSION TRANSCRIPT (last {len(convo_lines)} entries):\n\n{convo_text}",
    }]

    try:
        raw = call_model(
            system=system_prompt,
            messages=extraction_message,
            skill=skill,
            settings=settings,
        )
    except Exception as exc:
        print(f"[memory] extraction LLM call failed: {exc}")
        return 0

    # The model sometimes wraps JSON in ```json fences; strip them defensively.
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        bullets = _json.loads(raw)
    except Exception as exc:
        print(f"[memory] could not parse extraction JSON: {exc} | raw={raw[:200]}")
        return 0
    if not isinstance(bullets, list):
        return 0

    valid_categories = {
        "interest", "mastery", "struggle", "pace", "case_resonance", "style",
    }
    written = 0
    for b in bullets[:3]:
        if not isinstance(b, dict):
            continue
        cat = str(b.get("category", "")).strip().lower()
        text = str(b.get("memory_text", "")).strip()
        if cat not in valid_categories or not text:
            continue
        # Cap at ~20 words / 200 chars defensively.
        if len(text) > 200:
            text = text[:200].rstrip() + "…"
        try:
            _save_memory(
                learner_id=state.learner_id,
                skill_id=state.skill_id,
                session_id=state.session_db_id,
                category=cat,
                memory_text=text,
                supabase=supabase,
                group_id=state.skill_group_id,
            )
            written += 1
        except Exception as exc:
            print(f"[memory] _save_memory failed for cat={cat}: {exc}")
    return written


def _phase_id(phase: dict, index: int) -> str:
    return phase.get("id") or f"phase-{index + 1}"


def _find_probe(phase: dict, probe_id: str) -> dict | None:
    for p in phase.get("probe_set", []):
        if p.get("id") == probe_id:
            return p
    return None


def _emit(*, verb: str, actor_id: str, session_id: str, skill_id: str,
          object_type: str, object_id: str, context: dict, result: dict, supabase) -> None:
    emit(verb=verb, actor_id=actor_id, session_id=session_id, skill_id=skill_id,
         object_type=object_type, object_id=object_id,
         context=context, result=result, supabase=supabase)


@router.post("/start", response_model=SessionStartResponse)
def session_start(
    req: SessionStartRequest,
    learner: LearnerContext = Depends(require_learner_token),
) -> SessionStartResponse:
    settings = get_settings()
    supabase = get_supabase()

    row = load_skill(str(req.skill_id), supabase)
    skill = row["yaml"]
    version = row["version"]

    # Pedagogy override — when the demo start screen passes a pedagogy_id,
    # swap the skill's instructional_model with the catalogue entry so the
    # next system prompt teaches in that style. Other pedagogy fields
    # (theoretical_basis, etc.) are preserved.
    if req.pedagogy_override:
        from .pedagogies import _CATALOGUE  # local import to avoid cycles
        ped = next((p for p in _CATALOGUE if p["id"] == req.pedagogy_override), None)
        if ped:
            skill = dict(skill)
            ped_block = dict(skill.get("pedagogy", {}))
            im = dict(ped_block.get("instructional_model", {}))
            im["primary"] = ped["id"]
            im["name"] = ped["name"]
            im["description"] = ped["description"]
            im["techniques"] = ped.get("techniques", [])
            # opener_guidance — when set, the prompt assembler uses it
            # instead of the skill's authored opening_prompt. This makes
            # demos visibly different per pedagogy: Discovery poses a
            # puzzle, Direct states the goal, Inquiry asks the learner's
            # question, etc.
            if ped.get("opener_guidance"):
                im["opener_guidance"] = ped["opener_guidance"]
            ped_block["instructional_model"] = im
            skill["pedagogy"] = ped_block

    # Constitution load — fail fast with 409 if declared but missing.
    # Override from the session-start request (demo lets the learner pick
    # a Soul variant per session) takes precedence over the skill YAML.
    constitution = None
    constitution_id = req.constitution_override
    if not constitution_id:
        # Skill YAML may declare a constitution as either 'constitution' (single
        # string) or 'constitutions' (list). Handle both — Lucas's earlier wiring
        # used the singular form here while the YAML schema uses the plural list.
        constitution_id = skill.get("constitution")
        if not constitution_id:
            consts = skill.get("constitutions") or []
            if isinstance(consts, list) and consts:
                constitution_id = consts[0]
    if constitution_id:
        try:
            constitution = load_constitution(constitution_id)
        except ConstitutionNotFound:
            raise HTTPException(
                status_code=409,
                detail=f"Skill requires constitution '{constitution_id}' which is not available on this runtime.",
            )

    phase = skill["phases"][0]
    pid = _phase_id(phase, 0)

    init_result = supabase.rpc(
        "init_session",
        {
            "p_learner_id": learner.learner_id,
            "p_skill_id": str(req.skill_id),
            "p_skill_version": version,
            "p_phase_id": pid,
        },
    ).execute()
    session_id = str(init_result.data)

    # Visibility check for student-generated skills.
    # Teacher-authored skills (owner_learner_id IS NULL) are always accessible.
    ownership = (
        supabase.table("skills")
        .select("owner_learner_id, group_id")
        .eq("id", str(req.skill_id))
        .single()
        .execute()
        .data or {}
    )
    skill_owner = ownership.get("owner_learner_id")
    skill_group = ownership.get("group_id")
    if skill_owner and skill_owner != learner.learner_id:
        access = False
        if skill_group:
            member = (
                supabase.table("group_members")
                .select("learner_id")
                .eq("group_id", skill_group)
                .eq("learner_id", learner.learner_id)
                .execute()
                .data
            )
            access = bool(member)
        if not access:
            raise HTTPException(status_code=404, detail="Skill not found.")

    grounding_policy = skill.get("corpus", {}).get("grounding_policy", {})
    seed_query = phase.get("objectives", ["introduction"])[0]
    chunks = retrieve_chunks(seed_query, str(req.skill_id), grounding_policy, supabase, settings)

    # G1: learner profile prefs
    acct_row = (
        supabase.table("learner_accounts")
        .select("profile_prefs")
        .eq("id", learner.learner_id)
        .single()
        .execute()
        .data or {}
    )
    profile_prefs = acct_row.get("profile_prefs") or {}

    prior_memories = _query_memories(learner.learner_id, str(req.skill_id), supabase)
    system, yaml_refs = build_system_prompt(
        skill, phase, chunks,
        is_session_start=True, constitution=constitution, memories=prior_memories,
        profile_prefs=profile_prefs,
        time_budget_minutes=req.time_budget_minutes,
        mode=req.mode,
    )
    raw_opener = call_model(
        system=system,
        messages=[{"role": "user", "content": "ready"}],
        skill=skill,
        settings=settings,
    )

    opener, probed_id = extract_probe_tag(raw_opener)
    initial_pending_probe = _find_probe(phase, probed_id) if probed_id else None

    # G2: persist time_budget_minutes + mode in sessions.meta
    if req.time_budget_minutes or req.mode != "auto":
        supabase.table("sessions").update({
            "meta": {"time_budget_minutes": req.time_budget_minutes, "mode": req.mode}
        }).eq("id", session_id).execute()

    set_state(
        session_id,
        SessionState(
            skill_id=str(req.skill_id),
            learner_id=learner.learner_id,
            skill=skill,
            current_phase_index=0,
            phase_turn_index=0,
            messages=[
                {"role": "user", "content": "ready"},
                {"role": "assistant", "content": opener},
            ],
            session_db_id=session_id,
            pending_probe=initial_pending_probe,
            constitution=constitution,
            constitution_id=constitution_id,
            memory_context=prior_memories,
            time_budget_minutes=req.time_budget_minutes,
            mode=req.mode,
            skill_group_id=skill_group or None,
        ),
    )

    _emit(verb="initialized", actor_id=learner.learner_id,
          session_id=session_id, skill_id=str(req.skill_id),
          object_type="session", object_id=session_id,
          context={"phase_id": pid}, result={}, supabase=supabase)

    return SessionStartResponse(
        session_id=session_id,
        skill_id=str(req.skill_id),
        phase_id=pid,
        opening_turn=opener,
        yaml_refs=yaml_refs,
        memory_context=prior_memories,
        mode=req.mode,
    )


@router.post("/turn", response_model=SessionTurnResponse)
def session_turn(req: SessionTurnRequest) -> SessionTurnResponse:
    settings = get_settings()
    supabase = get_supabase()

    state = get_state(req.session_id)
    skill = state.skill
    current_phase = skill["phases"][state.current_phase_index]
    current_phase_id = _phase_id(current_phase, state.current_phase_index)
    grounding_policy = skill.get("corpus", {}).get("grounding_policy", {})

    # Track which YAML paths the demo's right-pane should pulse for THIS turn,
    # in addition to the structural refs build_system_prompt returns.
    dynamic_refs: list[str] = []
    # Motivational gamification flags reported back to the demo UI.
    stretch_zone_triggered: bool = False
    comeback_triggered: bool = False

    # Pre-turn constitution scan — before retrieval, per spec.
    if state.constitution:
        scan_result, new_cooldown = scan_message(
            req.learner_msg,
            state.constitution,
            distress_cooldown_until=state.distress_cooldown_until,
        )
        if scan_result.has_triggers():
            for injection in scan_result.injections:
                state.messages.append({"role": "user", "content": injection})
            state.distress_cooldown_until = new_cooldown
            for ev in scan_result.events:
                _emit(
                    verb="protected",
                    actor_id=state.learner_id,
                    session_id=req.session_id,
                    skill_id=state.skill_id,
                    object_type="constitution",
                    object_id=state.constitution_id,
                    context={"rule_id": ev["rule_id"], "pattern_matched": ev["pattern_matched"]},
                    result={},
                    supabase=supabase,
                )
            # Highlight the skill YAML's constitution reference so the demo
            # right-pane visibly pulses when the Soul kicks in. Both 'constitution'
            # (singular string) and 'constitutions' (plural list) are supported
            # by the skill schema; we ref both so the highlighter matches whichever.
            dynamic_refs.append("constitution")
            dynamic_refs.append("constitutions")

    state.messages.append({"role": "user", "content": req.learner_msg})

    chunks = retrieve_chunks(req.learner_msg, state.skill_id, grounding_policy, supabase, settings)

    # Refusal path — check BEFORE scoring probe so off-corpus answer isn't counted
    if not chunks and grounding_policy.get("refuse_if_ungrounded"):
        refusal_refs = ["corpus.grounding_policy.refuse_if_ungrounded"]
        _emit(verb="responded", actor_id=state.learner_id,
              session_id=req.session_id, skill_id=state.skill_id,
              object_type="session", object_id=req.session_id,
              context={"phase_id": current_phase_id, "turn_index": state.phase_turn_index, "refused": True},
              result={}, supabase=supabase)
        refusal_system, _ = build_system_prompt(skill, current_phase, [], constitution=state.constitution)
        refusal_system += off_corpus_augmentation()
        agent_reply = call_model(
            system=refusal_system, messages=state.messages, skill=skill, settings=settings,
        )
        state.messages.append({"role": "assistant", "content": agent_reply})
        _emit(verb="asked", actor_id=state.learner_id,
              session_id=req.session_id, skill_id=state.skill_id,
              object_type="session", object_id=req.session_id,
              context={"phase_id": current_phase_id, "turn_index": state.phase_turn_index, "off_corpus": True},
              result={}, supabase=supabase)
        set_state(req.session_id, state)
        # Merge dynamic_refs (e.g. ["constitution", "constitutions"] from the
        # pre-turn distress scan) so the demo's YAML highlight pulses the Soul
        # rules when a learner says "I'm frustrated/stupid" on an off-corpus
        # turn — not just the refuse_if_ungrounded line. Also preserve streak
        # so the badge doesn't reset on every off-corpus aside.
        return SessionTurnResponse(
            agent_reply=agent_reply, phase_id=current_phase_id,
            phase_turn_index=state.phase_turn_index, mastery_met=False,
            yaml_refs=dynamic_refs + refusal_refs, citations=[],
            resolved_mode=state.resolved_mode,
            streak=state.streak,
        )

    _emit(verb="responded", actor_id=state.learner_id,
          session_id=req.session_id, skill_id=state.skill_id,
          object_type="session", object_id=req.session_id,
          context={"phase_id": current_phase_id, "turn_index": state.phase_turn_index},
          result={}, supabase=supabase)

    # Score pending probe against this on-corpus learner message
    if state.pending_probe:
        probe_result = score_single_probe(state.pending_probe, req.learner_msg)
        supabase.table("probe_attempts").insert({
            "session_id": req.session_id,
            "phase_id": current_phase_id,
            "probe_id": probe_result["probe_id"],
            "response": {"text": req.learner_msg},
            "score": probe_result["score"],
            "scorer": probe_result["scorer"],
        }).execute()
        _emit(verb="probed", actor_id=state.learner_id,
              session_id=req.session_id, skill_id=state.skill_id,
              object_type="probe", object_id=probe_result["probe_id"],
              context={"probe_id": probe_result["probe_id"]},
              result={"score": probe_result["score"], "scorer": probe_result["scorer"]},
              supabase=supabase)
        state.pending_probe = None

        # G4: auto-mode resolution — first probe score determines teach vs review.
        if state.mode == "auto" and state.resolved_mode is None and probe_result["score"] is not None:
            state.resolved_mode = "review" if float(probe_result["score"]) >= 0.5 else "teach"

        # Track streak (consecutive correct) + comeback (correct after stretch zone)
        # + consecutive_failures (drives stretch_zone trigger).
        probe_passed = (
            probe_result["score"] is not None
            and float(probe_result["score"]) >= 0.5
        )
        if probe_passed:
            state.consecutive_failures = 0
            state.streak += 1
            # If we WERE in stretch zone (the previous turn(s) flagged struggle),
            # this correct answer is a comeback. Celebrate it.
            if state.in_stretch_zone:
                comeback_triggered = True
                state.in_stretch_zone = False
            _write_memory(
                learner_id=state.learner_id, skill_id=state.skill_id,
                session_id=req.session_id, category="success",
                memory_text=(
                    f"Demonstrated {probe_result['probe_id']} in phase {current_phase_id}"
                    f" — score {float(probe_result['score']):.2f}"
                ),
                supabase=supabase,
                group_id=state.skill_group_id,
            )
        else:
            state.consecutive_failures += 1
            state.streak = 0  # broken streak

        # Stretch-zone trigger (re-uses the constitution's struggle_tracker
        # threshold, but reframed positively for the learner). The agent
        # still gets the helpful injection (smaller piece, fresh angle);
        # the UI just calls it 'stretch zone' instead of 'struggle'.
        if state.constitution:
            inj = struggle_injection(state.constitution, state.consecutive_failures)
            if inj:
                stretch_zone_triggered = True
                state.in_stretch_zone = True
                _write_memory(
                    learner_id=state.learner_id, skill_id=state.skill_id,
                    session_id=req.session_id, category="struggle",
                    memory_text=(
                        f"Stretch zone in {current_phase_id}.{probe_result['probe_id']}"
                        f" — {state.consecutive_failures} consecutive below-threshold attempts"
                    ),
                    supabase=supabase,
                    group_id=state.skill_group_id,
                )
                state.messages.append({"role": "user", "content": inj})
                _emit(
                    verb="protected",
                    actor_id=state.learner_id,
                    session_id=req.session_id,
                    skill_id=state.skill_id,
                    object_type="constitution",
                    object_id=state.constitution_id,
                    context={"rule_id": "struggle_tracker", "consecutive_failures": state.consecutive_failures},
                    result={},
                    supabase=supabase,
                )
                state.consecutive_failures = 0

    # Effective mode for this turn: use resolved_mode once auto resolves.
    effective_mode = state.resolved_mode or (state.mode if state.mode != "auto" else None)
    system, yaml_refs = build_system_prompt(
        skill, current_phase, chunks,
        constitution=state.constitution,
        time_budget_minutes=state.time_budget_minutes,
        mode=effective_mode,
    )
    raw_reply = call_model(
        system=system, messages=state.messages, skill=skill, settings=settings,
    )

    agent_reply, probed_id = extract_probe_tag(raw_reply)

    probe_resolution = None
    if probed_id:
        probe_map = {p["id"]: p for p in current_phase.get("probe_set", [])}
        if probed_id in probe_map:
            state.pending_probe = probe_map[probed_id]
            probe_resolution = "bound"
        elif not probe_map:
            probe_resolution = "no_probes_in_phase"
        else:
            probe_resolution = "unknown_id"

    state.messages.append({"role": "assistant", "content": agent_reply})
    state.phase_turn_index += 1

    event_context: dict = {"phase_id": current_phase_id, "turn_index": state.phase_turn_index}
    if probe_resolution:
        event_context["probed_id"] = probed_id
        event_context["probed_id_resolution"] = probe_resolution

    _emit(verb="asked", actor_id=state.learner_id,
          session_id=req.session_id, skill_id=state.skill_id,
          object_type="session", object_id=req.session_id,
          context=event_context, result={}, supabase=supabase)

    # Mastery check
    mastery = current_phase.get("mastery", {})
    probe_set = current_phase.get("probe_set", [])
    mastery_met = False
    pass_rate = 0.0

    if not probe_set:
        mastery_met = state.phase_turn_index >= mastery.get("min_turns", 99)
    else:
        scored = (
            supabase.table("probe_attempts")
            .select("score")
            .eq("session_id", req.session_id)
            .eq("phase_id", current_phase_id)
            .execute()
        )
        scored_data = scored.data or []
        pass_count = sum(
            1 for p in scored_data if p.get("score") is not None and float(p["score"]) >= 0.5
        )
        pass_rate = pass_count / len(probe_set)
        mastery_met = pass_rate >= float(mastery.get("advance_threshold", 0.7))

    if mastery_met and state.current_phase_index < len(skill["phases"]) - 1:
        supabase.table("phase_states").update({"advanced_at": _now_iso()}).eq(
            "session_id", req.session_id
        ).eq("phase_id", current_phase_id).is_("advanced_at", "null").execute()

        state.current_phase_index += 1
        state.phase_turn_index = 0
        next_phase = skill["phases"][state.current_phase_index]
        next_phase_id = _phase_id(next_phase, state.current_phase_index)

        supabase.table("phase_states").insert(
            {"session_id": req.session_id, "phase_id": next_phase_id, "entered_at": _now_iso()}
        ).execute()

        _write_memory(
            learner_id=state.learner_id, skill_id=state.skill_id,
            session_id=req.session_id, category="breakthrough",
            memory_text=(
                f"Advanced from {current_phase_id} to {next_phase_id}"
                f" with pass rate {pass_rate:.0%}"
            ),
            supabase=supabase,
            group_id=state.skill_group_id,
        )

        _emit(verb="updated", actor_id=state.learner_id,
              session_id=req.session_id, skill_id=state.skill_id,
              object_type="phase", object_id=next_phase_id,
              context={"from_phase": current_phase_id, "to_phase": next_phase_id},
              result={"mastery_met": True}, supabase=supabase)

    set_state(req.session_id, state)

    citations = [
        {
            "source_id": c.get("source_id"),
            "chunk_text": c.get("chunk_text"),
            "similarity": c.get("similarity"),
            "metadata": c.get("metadata"),
        }
        for c in chunks
    ]

    return SessionTurnResponse(
        agent_reply=agent_reply, phase_id=current_phase_id,
        phase_turn_index=state.phase_turn_index, mastery_met=mastery_met,
        yaml_refs=yaml_refs + dynamic_refs, citations=citations,
        streak=state.streak,
        stretch_zone_triggered=stretch_zone_triggered,
        comeback_triggered=comeback_triggered,
        resolved_mode=state.resolved_mode,
    )


@router.post("/end", response_model=SessionEndResponse)
def session_end(session_id: str = Body(..., embed=True)) -> SessionEndResponse:
    supabase = get_supabase()
    state = get_state(session_id)

    ended_at = _now_iso()
    supabase.table("sessions").update(
        {"status": "completed", "completed_at": ended_at}
    ).eq("id", state.session_db_id).execute()

    _emit(verb="completed", actor_id=state.learner_id,
          session_id=session_id, skill_id=state.skill_id,
          object_type="session", object_id=session_id,
          context={}, result={}, supabase=supabase)

    turn_count = sum(1 for m in state.messages if m["role"] == "user")

    phase_states_data = (
        supabase.table("phase_states")
        .select("phase_id")
        .eq("session_id", state.session_db_id)
        .execute()
        .data or []
    )
    phase_ids_visited = [p["phase_id"] for p in phase_states_data]

    _write_memory(
        learner_id=state.learner_id, skill_id=state.skill_id,
        session_id=session_id, category="completion",
        memory_text=(
            f"Completed {turn_count} turns across phases: {', '.join(phase_ids_visited) or 'none'}"
        ),
        supabase=supabase,
        group_id=state.skill_group_id,
    )

    # Phase 1 of the durable-memory build: extract specific learner-shaped
    # memories from the conversation transcript and persist them. These get
    # auto-loaded on the next session via _query_memories, and the agent
    # references them in its opening turn — that's the "Poppy remembers me"
    # moment. Wrapped in try/except because session-end MUST always succeed
    # for the user even if extraction has a hiccup.
    try:
        settings = get_settings()
        n_memories = _extract_memories_from_session(state, supabase, settings)
        if n_memories:
            print(f"[memory] wrote {n_memories} memories for learner={state.learner_id} skill={state.skill_id}")
    except Exception as exc:
        print(f"[memory] extraction wrapper failed (non-fatal): {exc}")

    return SessionEndResponse(session_id=session_id, ended_at=ended_at, turn_count=turn_count)


@router.get("/skill/{skill_id}", response_model=SkillMetaResponse)
def get_skill_meta(skill_id: str) -> SkillMetaResponse:
    supabase = get_supabase()
    row = load_skill(skill_id, supabase)
    skill = row["yaml"]
    name = skill.get("skill", {}).get("name", skill_id)
    return SkillMetaResponse(skill_id=skill_id, name=name, yaml=skill)


@router.get("/{session_id}/state", response_model=SessionStateResponse)
def session_state(session_id: str) -> SessionStateResponse:
    supabase = get_supabase()

    recent_events = (
        supabase.table("events")
        .select("*")
        .eq("session_id", session_id)
        .order("occurred_at", desc=True)
        .limit(20)
        .execute()
        .data or []
    )

    phase_states = (
        supabase.table("phase_states")
        .select("*")
        .eq("session_id", session_id)
        .execute()
        .data or []
    )

    # Try in-memory cache first; fall back to DB for completed/evicted sessions.
    resolved_mode = None
    try:
        state = get_state(session_id)
        skill_id = state.skill_id
        learner_id = state.learner_id
        current_phase = state.skill["phases"][state.current_phase_index]
        current_phase_id = _phase_id(current_phase, state.current_phase_index)
        resolved_mode = state.resolved_mode
    except Exception:
        # Cache miss — read from DB.
        session_row = (
            supabase.table("sessions")
            .select("skill_id, learner_id")
            .eq("id", session_id)
            .single()
            .execute()
            .data or {}
        )
        skill_id = session_row.get("skill_id", "")
        learner_id = str(session_row.get("learner_id", ""))
        # Most recent phase_state is current phase.
        current_phase_id = phase_states[-1]["phase_id"] if phase_states else None

    session_row = (
        supabase.table("sessions")
        .select("status")
        .eq("id", session_id)
        .single()
        .execute()
        .data or {}
    )
    db_status = session_row.get("status", "active")

    return SessionStateResponse(
        session_id=session_id, skill_id=skill_id, learner_id=learner_id,
        status=db_status, current_phase_id=current_phase_id,
        phase_states=phase_states, recent_events=recent_events,
        resolved_mode=resolved_mode,
    )


@router.get("/{session_id}/progress", response_model=SessionProgressResponse)
def session_progress(
    session_id: str,
    learner: LearnerContext = Depends(require_learner_token),
) -> SessionProgressResponse:
    """G3 — Real-time session progress for the student UI progress bar."""
    supabase = get_supabase()

    # Load from in-memory state (must be a live session)
    state = get_state(session_id)

    # minutes_elapsed from sessions.started_at
    sess_row = (
        supabase.table("sessions")
        .select("started_at")
        .eq("id", session_id)
        .single()
        .execute()
        .data or {}
    )
    started_at_str = sess_row.get("started_at", "")
    try:
        started_at = datetime.fromisoformat(started_at_str)
        minutes_elapsed = round((datetime.now(timezone.utc) - started_at).total_seconds() / 60, 1)
    except Exception:
        minutes_elapsed = 0.0

    skill = state.skill
    phases = skill.get("phases", [])
    total_phases = len(phases)
    current_idx = state.current_phase_index
    current_phase = phases[current_idx] if current_idx < total_phases else {}
    current_phase_id = _phase_id(current_phase, current_idx)

    # Per-phase time estimate — use YAML hint if present, else 5 min fallback
    def _phase_minutes(ph: dict) -> float:
        return float(ph.get("estimated_minutes", ph.get("time_estimate_minutes", 5)))

    remaining_phases = phases[current_idx:]
    estimated_remaining = round(sum(_phase_minutes(p) for p in remaining_phases), 1)

    # Probe counts across all phases
    probes_total = sum(len(ph.get("probe_set", [])) for ph in phases)
    all_attempts = (
        supabase.table("probe_attempts")
        .select("score")
        .eq("session_id", session_id)
        .execute()
        .data or []
    )
    probes_passed = sum(
        1 for a in all_attempts
        if a.get("score") is not None and float(a["score"]) >= 0.5
    )

    return SessionProgressResponse(
        current_phase=current_phase_id,
        current_phase_index=current_idx,
        total_phases=total_phases,
        minutes_elapsed=minutes_elapsed,
        estimated_remaining_minutes=estimated_remaining,
        probes_passed=probes_passed,
        probes_total=probes_total,
        resolved_mode=state.resolved_mode,
    )
