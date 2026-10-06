"""
The additive V4 API. New paths; V3's contract is untouched.

`POST /runs` accepts and returns promptly. It does NOT generate anything
before it has committed the run and its outbox row in one transaction: an
HTTP 202 means the work is durable and claimable, not that an answer exists.
If the store cannot commit, the request is rejected and no run is claimed --
because "accepted" that later evaporates is worse than a refusal.

SSE is authenticated like any result endpoint. A run id is not a bearer
token, and a stream that anyone with the id can read leaks a tenant's
analysis.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import (JSONResponse, Response,
                               StreamingResponse)
from pydantic import BaseModel, Field

from backend.cockpit_v4 import DEEP, MODES, STANDARD
from backend.cockpit_v4 import attention
from backend.cockpit_v4 import lake as lake_mod
from backend.cockpit_v4 import collaboration as collab
from backend.cockpit_v4 import config as config_mod
from backend.cockpit_v4 import envelope
from backend.cockpit_v4.intake import normalize_question
from backend.cockpit_v4 import events as ev
from backend.cockpit_v4 import states as st
from backend.cockpit_v4.run_store import (IdempotencyConflict, RunStore,
                                          StorageUnavailable)
from backend.cockpit_v4.service import PreflightFailed, diagnostics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cockpit-v4", tags=["cockpit-v4"])

#: Set by the application factory. Kept explicit rather than global magic so
#: a test can install its own store and runtime.
_STATE: dict[str, Any] = {}


def install(*, store: RunStore, runtime: Any, principal_resolver: Any,
            startup_sha: str = "", cfg: Any = None,
            preflight_error: str = "") -> None:
    """Install the REAL runtime, or None. Never a stand-in.

    `cfg` and `preflight_error` are carried separately precisely because
    `runtime` may be None: a runtime that could not be built still has a
    configuration worth reporting and a reason worth naming.
    """
    _STATE.update({"store": store, "runtime": runtime,
                   "cfg": cfg or getattr(runtime, "cfg", None),
                   "preflight_error": preflight_error,
                   "principal": principal_resolver,
                   "startup_sha": startup_sha})


def _config() -> Any:
    return _STATE.get("cfg") or getattr(
        _STATE.get("runtime"), "cfg", None) or config_mod.load()


def readiness() -> Any:
    """What this runtime can do. One answer, consulted by every route."""
    from backend.cockpit_v4 import readiness as ready_mod

    return ready_mod.assess(
        _STATE.get("runtime"), cfg=_config(),
        preflight_error=str(_STATE.get("preflight_error") or ""))


def _require(capability: str) -> Any:
    """The runtime, once this capability is known to be available."""
    readiness().require(capability)
    return _STATE["runtime"]


def _store() -> RunStore:
    store = _STATE.get("store")
    if store is None:
        raise HTTPException(503, {"error_code": st.STORAGE_UNAVAILABLE,
                                  "message": "The V4 runtime is not started."})
    return store


async def principal(request: Request) -> dict[str, Any]:
    """Server-derived identity. Never read from the request body."""
    resolver = _STATE.get("principal")
    if resolver is None:
        raise HTTPException(503, {"error_code": st.INTERNAL_ERROR,
                                  "message": "No principal resolver is "
                                             "installed."})
    who = resolver(request)
    if not who:
        raise HTTPException(401, {"error_code": "UNAUTHENTICATED",
                                  "message": "Sign in to use the Cockpit."})
    return who


class StartRun(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    thread_id: str = ""
    mode: str = STANDARD
    release_id: str = ""
    #: Which book. Read only when a NEW conversation is being opened -- a
    #: thread that already exists decides for itself, and a request that
    #: disagrees with it is refused rather than obeyed or ignored.
    domain: str = ""
    ui_filters: dict[str, Any] = Field(default_factory=dict)
    provider: str = ""
    model: str = ""


def _digest(body: StartRun, who: dict[str, Any]) -> str:
    payload = json.dumps({"q": body.question, "t": body.thread_id,
                          "m": body.mode, "r": body.release_id,
                          "d": body.domain,
                          "f": body.ui_filters, "provider": body.provider,
                          "model": body.model, "p": who.get("id")},
                         sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _models() -> list[dict[str, Any]]:
    """Models the server may accept; keys and upstream URLs never leave here."""
    cfg = _config()
    entries: list[dict[str, Any]] = []
    if (os.environ.get("ANTHROPIC_API_KEY", "").strip() or
            os.environ.get(config_mod.CREDENTIAL_VAR, "").strip()) and cfg.reasoning_model:
        entries.append({"provider": "anthropic", "model": cfg.reasoning_model,
                        "available": True, "qualified": True, "label": "Claude"})
    try:
        from backend.llm import get_provider
        discovered = get_provider(provider="omlx", model="").list_models()
        qualified = {item.strip() for item in os.environ.get("OMLX_QUALIFIED_MODELS", "").split(",") if item.strip()}
        from backend.cockpit_v4.model_capabilities import traits_for
        for model in discovered:
            profile = traits_for(model)
            approved = model in qualified or profile.source.startswith("omlx-live-spike-")
            entries.append({"provider": "omlx", "model": model, "available": True,
                            "qualified": approved,
                            "label": "oMLX (local)",
                            "reason": "Model has not passed the oMLX qualification spike." if not approved else ""})
    except Exception:
        # Discovery is display-only. A configured Claude option stays usable.
        pass
    return entries


def _selection(body: StartRun, who: dict[str, Any]) -> tuple[str, str]:
    if bool(body.provider) != bool(body.model):
        raise HTTPException(422, {"error_code": "MODEL_SELECTION_INVALID",
                                  "message": "Provider and model must be supplied together."})
    store = _store(); tenant = str(who.get("tenant") or ""); principal_id = str(who.get("id") or "")
    choice = {"provider": body.provider.strip().lower(), "model": body.model.strip()} if body.provider else store.model_preference(tenant_id=tenant, principal_id=principal_id)
    if not choice:
        # The installation default is the runtime's own configured pair. Its
        # capability and price were verified when the runtime was built, so it
        # is accepted as-is rather than re-gated on discovery or key presence.
        cfg = _config()
        return str(cfg.provider).lower(), str(cfg.reasoning_model)
    match = next((entry for entry in _models() if entry["provider"] == choice["provider"] and entry["model"] == choice["model"]), None)
    if not match or not match["available"] or not match["qualified"]:
        raise HTTPException(409, {"error_code": "MODEL_UNAVAILABLE", "message": "The selected model is unavailable or not qualified."})
    return choice["provider"], choice["model"]


class ModelSetting(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


@router.get("/models")
async def list_models(who: dict[str, Any] = Depends(principal)) -> dict[str, Any]:
    return {"models": _models(), "selection": _store().model_preference(tenant_id=str(who.get("tenant") or ""), principal_id=str(who.get("id") or ""))}


@router.get("/settings/model")
async def get_model_setting(who: dict[str, Any] = Depends(principal)) -> dict[str, Any]:
    return {"selection": _store().model_preference(tenant_id=str(who.get("tenant") or ""), principal_id=str(who.get("id") or ""))}


@router.put("/settings/model")
async def put_model_setting(body: ModelSetting, who: dict[str, Any] = Depends(principal)) -> dict[str, Any]:
    probe = StartRun(question="selection", provider=body.provider, model=body.model)
    provider, model = _selection(probe, who)
    _store().set_model_preference(tenant_id=str(who.get("tenant") or ""), principal_id=str(who.get("id") or ""), provider=provider, model=model)
    return {"provider": provider, "model": model}


class CreateThread(BaseModel):
    """Which book the conversation is being opened in."""

    domain: str | None = None


@router.post("/threads", status_code=201)
async def create_thread(body: CreateThread | None = None,
                        who: dict[str, Any] = Depends(principal)
                        ) -> dict[str, Any]:
    """A server-generated conversation id. Never a shared global constant.

    The BOOK is stamped here, not inferred later. It used to be left unset,
    so a thread opened from the Retail home page was a Corporate thread until
    the first run happened to resolve a scope for it -- and the questions it
    opened on were the Corporate book's, offered to a reader looking at
    Retail. A thread is in one book from the moment it exists.
    """
    from backend.cockpit_v4 import domain_resolver as resolver
    from backend.cockpit_v4 import domains as dom_mod

    scope = resolver.scope_for(
        dom_mod.parse((body.domain if body else None)))
    thread_id = _store().create_thread(
        tenant_id=str(who.get("tenant") or ""),
        principal_id=str(who.get("id") or ""),
        domain_id=scope.domain_id, release_id=scope.release_id,
        release_fingerprint=scope.release_fingerprint)
    return {"thread_id": thread_id, "domain_id": scope.domain_id,
            "release_id": scope.release_id}


@router.post("/runs", status_code=202)
async def start_run(body: StartRun, request: Request,
                    idempotency_key: str = Header("", alias="Idempotency-Key"),
                    who: dict[str, Any] = Depends(principal)) -> JSONResponse:
    store = _store()
    provider_name, model_name = _selection(body, who)
    from backend.cockpit_v4 import readiness as ready_mod

    state = readiness()
    # A run this runtime cannot process must not be accepted.
    #
    # The live failure: with the release unpublished the API answered 202,
    # the browser showed "Request accepted", and the worker had never been
    # started -- so the run sat in ACCEPTED until the user gave up. 202 is a
    # promise to do the work. Nothing that cannot keep it may send one.
    #
    # Product Help is refused separately and for a different reason: it needs
    # a model, not a portfolio, so a runtime with no release open can still
    # answer what the product does.
    if not state[ready_mod.PRODUCT_HELP_READY]:
        state.require(ready_mod.PRODUCT_HELP_READY)
    if not state[ready_mod.SQL_ANALYSIS_READY]:
        # The question's own mode is not known until the analyst declares an
        # intent, which is a model call away. What IS known here is that no
        # analysis can run at all, so a question is accepted only if Product
        # Help could answer it -- and the refusal names the capability.
        state.require(ready_mod.SQL_ANALYSIS_READY)
    runtime = _STATE.get("runtime")
    cfg = _config()

    mode = body.mode.lower() if body.mode.lower() in MODES else STANDARD
    from backend.cockpit_v4 import domain_resolver as resolver
    from backend.cockpit_v4 import domains as dom_mod

    tenant = str(who.get("tenant") or "")
    thread_id = body.thread_id
    pinned = ""
    if thread_id:
        owner = store.thread_owner(thread_id)
        if owner is None or owner[0] != tenant:
            # Does not reveal whether the thread exists for someone else.
            raise HTTPException(404, {"error_code": "NOT_FOUND",
                                      "message": "No such conversation."})
        pin = store.thread_domain(thread_id) or {}
        pinned = pin.get("domain_id", "")
        # AND THE RELEASE IT WAS PINNED TO, not only the book.
        #
        # This read the thread's domain and threw its release away, so a
        # follow-up asked in a thread pinned to a superseded release was
        # accepted against the CURRENT one, with no warning and no note in
        # the transcript. The thread then held two turns computed from two
        # different books -- which is precisely what pinning the domain
        # exists to prevent, one level down.
        #
        # A moved release is refused rather than rebased. Reading an old
        # thread is history and is allowed; adding a new turn to it under
        # different numbers is a substitution, and the reader would have no
        # way to see it afterwards.
        pinned_release = str(pin.get("release_id") or "")
        current = dom_mod.DEFAULT_RELEASES.get(pinned or "", "")
        if pinned_release and current and pinned_release != current:
            raise HTTPException(409, {
                "error_code": "RELEASE_SUPERSEDED",
                "message": (
                    f"This conversation was answered against "
                    f"{pinned_release}, and this book now publishes "
                    f"{current}. Its existing turns still open and still "
                    f"render. A new question would be answered from "
                    f"different numbers, so it belongs in a new "
                    f"conversation rather than this one."),
                "release_id": pinned_release,
                "current_release_id": current})

    # The book, decided once. Inside a thread the thread wins; a request that
    # names a different one is REFUSED rather than obeyed or silently
    # overruled, because evidence from two books in one transcript cannot be
    # told apart afterwards.
    try:
        scope = resolver.resolve(thread_domain=pinned, requested=body.domain,
                                 tenant_id=tenant or lake_mod.DEFAULT_TENANT)
    except resolver.DomainPinned as exc:
        raise HTTPException(409, {
            "error_code": "DOMAIN_PINNED",
            "message": str(exc),
            "thread_domain": exc.thread_domain,
            "requested_domain": exc.asked,
            "action": {"label": f"Start a "
                                f"{dom_mod.SHORT_LABELS[exc.asked]} "
                                f"conversation",
                       "domain": exc.asked}}) from exc
    except resolver.DomainUnavailable as exc:
        raise HTTPException(503, {
            "error_code": st.DATA_UNAVAILABLE, "message": str(exc),
            "domain_id": exc.domain_id,
            "provision_command": resolver.provision_command(
                exc.domain_id)}) from exc
    except dom_mod.UnknownDomain as exc:
        raise HTTPException(400, {"error_code": "UNKNOWN_DOMAIN",
                                  "message": str(exc)}) from exc

    # A book that can be browsed but not yet asked is refused HERE, before a
    # model call is paid for and before a thread is left holding a question
    # nothing can answer.
    # A request that names a release is refused, not obeyed. The release
    # belongs to the book and the book belongs to the thread; letting a
    # caller pick one would reintroduce, from the outside, exactly the
    # substitution the domain model exists to prevent.
    if body.release_id and body.release_id != scope.release_id:
        raise HTTPException(409, {
            "error_code": "RELEASE_NOT_SETTABLE",
            "message": (f"This request named release {body.release_id!r}. "
                        f"The {dom_mod.LABELS[scope.domain_id]} book reads "
                        f"{scope.release_id!r}, and a release is not "
                        f"settable from a request. Nothing was "
                        f"substituted."),
            "domain_id": scope.domain_id,
            "release_id": scope.release_id})

    asked_tenant = tenant or lake_mod.DEFAULT_TENANT
    if not resolver.analysis_supported(scope.domain_id,
                                       tenant_id=asked_tenant):
        exc = resolver.AnalysisNotWiredForDomain(scope.domain_id,
                                                 tenant_id=asked_tenant)
        raise HTTPException(503, {
            "error_code": st.DATA_UNAVAILABLE,
            "message": str(exc),
            "domain_id": exc.domain_id,
            "provision_command": resolver.provision_command(exc.domain_id),
            "analysis_domains": list(
                resolver.analysis_domains(tenant_id=asked_tenant))})

    if not thread_id:
        thread_id = store.create_thread(
            tenant_id=tenant, principal_id=str(who.get("id") or ""),
            domain_id=scope.domain_id, release_id=scope.release_id,
            release_fingerprint=scope.release_fingerprint)

    # A conversation is named the moment it has a question in it, not when
    # the answer lands. Naming it on `append_turn` meant a reader watched a
    # thread called "New conversation" for the whole time it was working --
    # and a thread whose run failed kept that name for ever. No model call:
    # the title is the question.
    store.title_thread_from_question(thread_id, body.question)

    # WHICH ALLOWANCE, decided before anything is spent. A thread opened
    # from an attention card, or one whose last turn ran an analysis, is a
    # conversation about the book's numbers: it gets the analysis clock from
    # its first second rather than sixty seconds later, once the analyst has
    # spent a generation saying so. See `envelope.classify`.
    verdict = envelope.for_request(
        store=store, thread_id=thread_id, question=body.question, mode=mode)
    limits = verdict.limits

    # An idempotent retry is the SAME run. It is resolved before the
    # concurrency check, or a dropped 202 would come back as a 409 the
    # caller has no way to clear.
    digest = _digest(body, who)
    existing, stored_digest = store.run_for_key(
        str(who.get("id") or ""), idempotency_key)
    if existing is not None:
        if stored_digest != digest:
            raise HTTPException(409, {
                "error_code": "IDEMPOTENCY_CONFLICT",
                "message": ("This idempotency key was already used for a "
                            "different request.")})
        return _accepted(existing, created=False)

    if store.active_runs_for(thread_id=thread_id) >= 1:
        raise HTTPException(409, {
            "error_code": "RUN_IN_PROGRESS",
            "message": "This conversation already has a request running."})
    if store.active_runs_for(principal_id=str(who.get("id") or "")) >= 2:
        raise HTTPException(429, {
            "error_code": "TOO_MANY_RUNS",
            "message": "You already have two requests running."})

    deadline_at = _deadline(limits.deadline_seconds)
    # Mechanical only — Unicode, whitespace, invisible controls. See
    # `intake.normalize_question`: nothing about the wording, the spelling,
    # the language or any number is touched here.
    normalized = normalize_question(body.question)
    try:
        record, created = store.accept_run(
            thread_id=thread_id, tenant_id=str(who.get("tenant") or ""),
            principal_id=str(who.get("id") or ""), question=normalized.text,
            # THE RELEASE IS THE BOOK'S, and it is not negotiable.
            #
            # This read `body.release_id or cfg.release_id`: the request, or
            # else whatever release the PROCESS was configured for. On a Mac
            # configured for the pre-domain quarterly release, every run --
            # Corporate or Retail, typed or seeded from an attention card --
            # was accepted against `v4-saudi-20q-v1` while being labelled
            # with the right domain. The worker then found a release that
            # belongs to no book, fell back to the configured one, and
            # answered from the quarterly corporate data. That is the whole
            # of "this book is recorded quarterly, not monthly" and the whole
            # of a Retail card opening a thread that reads Corporate.
            #
            # A release is a server decision derived from the book. A request
            # naming a different one is refused above, not obeyed here.
            mode=mode, release_id=scope.release_id,
            ui_filters=dict(body.ui_filters),
            provider=provider_name, model=model_name,
            idempotency_key=idempotency_key, body_digest=digest,
            startup_sha=str(_STATE.get("startup_sha") or ""),
            deadline_at=deadline_at,
            # The book, and the exact bytes, this run was ACCEPTED against.
            # The worker reads these rather than re-resolving, so a run that
            # settles after the reader switched the page still reads the
            # book it was asked in.
            domain_id=scope.domain_id,
            release_fingerprint=scope.release_fingerprint)
    except IdempotencyConflict as exc:
        raise HTTPException(409, {"error_code": "IDEMPOTENCY_CONFLICT",
                                  "message": str(exc)}) from exc
    except StorageUnavailable as exc:
        # No run is claimed when the store cannot commit.
        raise HTTPException(503, {
            "error_code": st.STORAGE_UNAVAILABLE,
            "message": ("The request was not accepted because it could not "
                        "be stored durably. Nothing is running.")}) from exc

    if created:
        emitter = ev.Emitter(store, record.run_id,
                             started_monotonic=time.monotonic())
        emitter.append(
            ev.RUN_ACCEPTED, stage="accepted", operation="intake",
            status=ev.STATUS_OK,
            public_message=f"Request accepted · release {record.release_id}")
        if normalized.changed:
            # Recorded because it happened, and only when it happened. A
            # trace that says text was normalised when it was not is the
            # same defect as a trace that hides it.
            emitter.append(
                ev.RUN_ACCEPTED, stage="accepted", operation="normalize",
                status=ev.STATUS_OK,
                detail_ref=store.put_detail(record.run_id, {
                    "normalization": normalized.report(),
                    "original": normalized.original[:8000]}),
                public_message="Question text normalized (formatting only).")

    return _accepted(record, created=created)


def _accepted(record: Any, *, created: bool) -> JSONResponse:
    return JSONResponse(status_code=202, content={
        "run_id": record.run_id, "thread_id": record.thread_id,
        "state": record.state, "mode": record.mode,
        # §17, §18. Which book this run was ACCEPTED against, echoed back so
        # the caller can see the thread's own domain won rather than the one
        # it may have asked for.
        "domain_id": record.domain_id,
        "release_id": record.release_id, "duplicate": not created,
        "status_url": f"{router.prefix}/runs/{record.run_id}",
        "events_url": f"{router.prefix}/runs/{record.run_id}/events",
        "cancel_url": f"{router.prefix}/runs/{record.run_id}/cancel",
        "note": ("Accepted means durable and claimable. It does not mean "
                 "answered.")})


def _deadline(seconds: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc)
            + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def _authorize(run_id: str, who: dict[str, Any]) -> Any:
    record = _store().get_run(run_id)
    if record is None or record.tenant_id != str(who.get("tenant") or ""):
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such run."})
    return record


@router.get("/runs/{run_id}")
async def get_run(run_id: str, who: dict[str, Any] = Depends(principal)
                  ) -> dict[str, Any]:
    record = _authorize(run_id, who)
    store = _store()
    body = record.to_dict()
    body["last_event_seq"] = store.last_seq(run_id)
    body["terminal"] = st.is_terminal(record.state)
    body["note"] = ("HTTP 200 means this status was read. It does not mean "
                    "the analysis succeeded.")
    return body


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, who: dict[str, Any] = Depends(principal)
                     ) -> dict[str, Any]:
    record = _authorize(run_id, who)
    if st.is_terminal(record.state):
        # Idempotent, and it does not un-settle a finished run.
        return {"run_id": run_id, "state": record.state,
                "cancelled": False,
                "message": "This run had already finished."}
    updated = _store().request_cancel(run_id)
    return {"run_id": run_id, "state": getattr(updated, "state", record.state),
            "cancelled": True}


@router.post("/runs/{run_id}/delivered")
async def acknowledge(run_id: str, who: dict[str, Any] = Depends(principal)
                      ) -> dict[str, Any]:
    """Presentation acknowledgement, kept separate from analytical status."""
    _authorize(run_id, who)
    _store().mark_delivered(run_id)
    return {"run_id": run_id, "acknowledged": True}


@router.get("/runs/{run_id}/artifacts/{artifact_id}")
async def read_artifact(run_id: str, artifact_id: str,
                        offset: int = Query(0, ge=0),
                        limit: int = Query(100, ge=1, le=1000),
                        who: dict[str, Any] = Depends(principal)
                        ) -> dict[str, Any]:
    _authorize(run_id, who)
    record = _store().get_artifact(artifact_id,
                                   tenant_id=str(who.get("tenant") or ""))
    if record is None or record["run_id"] != run_id:
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such artifact."})
    rows = record["rows"][offset:offset + limit]
    return {"artifact_id": artifact_id, "columns": record["columns"],
            "total_rows": record["row_count"], "offset": offset,
            "rows": rows,
            "omitted_rows": max(0, record["row_count"] - (offset + len(rows))),
            "executed_code_digest": record["code_digest"]}


@router.get("/runs/{run_id}/details/{detail_ref}")
async def read_detail(run_id: str, detail_ref: str,
                      who: dict[str, Any] = Depends(principal)
                      ) -> dict[str, Any]:
    """The operator record an event points at.

    Every process event may carry a `detail_ref` -- which model, which
    allowance, which check refused and with what evidence. Twenty-odd places
    write one; until now nothing could read one back, so an operator looking
    at a failed run saw `dt-3f9a1c…` on screen and had nowhere to take it.
    The reference was a promise the API did not keep.

    Scoped exactly like a result artifact: the run is authorized first, and
    then the record's own `run_id` must match. A `detail_ref` is not a
    bearer token, and one from somebody else's run is NOT FOUND rather than
    forbidden -- the same refusal `_authorize` gives, for the same reason.

    These bodies are redacted on the way in, at `RunStore.put_detail`. This
    route reads what was stored; it does not filter afterwards, because a
    filter applied at read time protects only the readers who go through it.
    """
    _authorize(run_id, who)
    record = _store().get_detail(detail_ref)
    if record is None or record.get("run_id") != run_id:
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such detail record."})
    return {"run_id": run_id, "detail_ref": detail_ref,
            "body": record.get("body") or {},
            "note": ("Operator diagnostics. Public operations and their "
                     "outcomes -- never a credential, and never the model's "
                     "private reasoning.")}


# ---- the schema, browsable per book -------------------------------------

@router.get("/schema")
async def schema_browser(domain: str = Query(""), relation: str = Query(""),
                         who: dict[str, Any] = Depends(principal)
                         ) -> dict[str, Any]:
    """What one book contains, as DATA rather than as a document.

    Two depths. Without a relation: every relation, its grain, its keys, its
    column count and its row count. With one: every column, with its type,
    unit, aggregation and definition.

    A relation belonging to the OTHER book is refused by name. Browsing is
    not analysis, but a schema browser that quietly showed the other book's
    columns would be teaching the reader the wrong thing about what their
    thread can reach.
    """
    from backend.cockpit_v4 import catalog as cat_mod
    from backend.cockpit_v4 import domains as dom_mod
    from backend.cockpit_v4 import schema as schema_mod

    try:
        domain_id = dom_mod.parse(domain)
    except dom_mod.UnknownDomain as exc:
        raise HTTPException(400, {"error_code": "UNKNOWN_DOMAIN",
                                  "message": str(exc)}) from exc
    scope, session, tenant = _domain_book(who, domain_id)
    catalog = cat_mod.build(domain_id=domain_id,
                            release_id=scope.release_id, tenant_id=tenant)
    manifest = lake_mod.read_manifest(scope.release_id)
    counts = dict(manifest.get("row_counts") or {})

    common = {
        "domain_id": domain_id,
        "domain_label": dom_mod.LABELS[domain_id],
        "release_id": scope.release_id,
        "release_fingerprint": scope.release_fingerprint,
        "country": manifest.get("geography") or "",
        "country_name": manifest.get("geography_name") or "",
        "reporting_currency": scope.currency,
        "amount_scale": scope.amount_scale,
        "reporting_frequency": scope.reporting_frequency,
        # §13. What a period IS here, so a reader looking at the Corporate
        # book is not left to infer from `2026Q2` that it might be a month.
        "period_noun": scope.period_noun,
        "period_column": scope.period_column,
        "reporting_periods": list(scope.periods),
        "earliest_period": scope.periods[0] if scope.periods else "",
        "latest_period": scope.latest_period,
        "previous_period": scope.previous_period,
        "period_count": len(scope.periods),
        # §13. The size of the book in things a credit officer counts:
        # borrowers and facilities, or customers and accounts.
        "entity_counts": dict(manifest.get("entity_counts") or {}),
        "status": "published",
        "not_client_data": manifest.get("not_client_data"),
        "geography_name": manifest.get("geography_name"),
    }

    if not relation:
        outline = catalog.outline()
        return {
            **common,
            "relations": [{**entry, "rows": int(counts.get(entry["relation"],
                                                           0))}
                          for entry in outline["relations"]],
            # §14. The SUBJECT AREAS this book is organised into -- identity,
            # exposure, IFRS 9, rating, financials, collateral, covenants --
            # with the relations and the column count behind each one. A
            # ninety-column relation listed as ninety columns is a list; the
            # same ninety columns under seven headings is a book someone can
            # find their way around.
            "subject_areas": _subject_areas(catalog),
            "joins": catalog.joins(),
            "total_rows": sum(int(v) for v in counts.values()),
            "note": ("These are the relations this book publishes. A "
                     "question asked in this book reads these and nothing "
                     "else."),
        }

    try:
        name = catalog.require_relation(relation)
    except cat_mod.CrossDomainAccess as exc:
        raise HTTPException(403, {
            "error_code": st.SECURITY_DENIED, "message": str(exc),
            "domain_id": domain_id, "relation": relation}) from exc
    except schema_mod.UnknownRelation as exc:
        raise HTTPException(404, {
            "error_code": "NOT_FOUND", "message": str(exc),
            "domain_id": domain_id, "relation": relation}) from exc

    spec = catalog.spec(name)
    return {
        **common,
        "relation": spec.name,
        "grain": spec.grain,
        "description": spec.description,
        "period_column": spec.period_column,
        "key_columns": list(spec.key_columns),
        "rows": int(counts.get(spec.name, 0)),
        "joins": [j for j in catalog.joins()
                  if j["left"] == spec.name or j["right"] == spec.name],
        "subject_areas": _subject_areas(catalog, relation=name),
        # §15, §16, §41. Each column with the label a person reads, the
        # identifier SQL filters on, and -- where the column is a governed
        # category -- the values it may actually hold. A field inspector that
        # says "string" and stops has not told the reader the one thing they
        # came for, which is what they are allowed to type.
        "fields": [{"name": f.name, "label": f.label, "dtype": f.dtype,
                    "unit": f.unit, "aggregation": f.additive,
                    "group": f.group, "definition": f.description,
                    **_field_values(session, name, f)}
                   for f in spec.fields],
    }


#: How many distinct values a field inspector shows before it stops. A
#: bounded sample is a sample; an unbounded one is a data export, and this
#: page does not export data.
_SAMPLE_LIMIT = 12


def _subject_areas(catalog: Any,
                   relation: str = "") -> list[dict[str, Any]]:
    """This book's columns grouped by what they are ABOUT.

    The grouping is the governed schema's own `group`, so it is the same
    grouping the analyst is shown. A second, page-only taxonomy here would
    be a second answer to "what does this book cover".
    """
    # THROUGH THE CATALOGUE IT WAS HANDED, not around it. This took a
    # `catalog` as its first argument and then asked `schema_mod` for the
    # domain's CURRENT relations, so the subject areas of a superseded
    # release counted columns that release never had.
    specs = ([catalog.spec(relation)] if relation
             else [catalog.spec(name) for name in catalog.relations()])
    areas: dict[str, dict[str, Any]] = {}
    for spec in specs:
        for column in spec.fields:
            name = (column.group or "Other").strip() or "Other"
            area = areas.setdefault(name, {"area": name, "columns": 0,
                                           "relations": []})
            area["columns"] += 1
            if spec.name not in area["relations"]:
                area["relations"].append(spec.name)
    return sorted(areas.values(), key=lambda a: (-a["columns"], a["area"]))


def _field_values(session: Any, relation: str, field: Any) -> dict[str, Any]:
    """The values this column actually holds, bounded.

    Two different things, deliberately named differently. `governed_values`
    is the CLOSED set a category column may hold -- the reader may filter on
    any of them and on nothing else. `sample_values` is a handful of real
    values from a column that is not a closed set, shown so the reader knows
    what the shape of one looks like. Calling a sample a governed list would
    invite somebody to treat twelve borrower names as the whole book.
    """
    from backend.cockpit_v4 import values as val_mod

    if str(getattr(field, "dtype", "")) != "string":
        return {}
    try:
        rows = session.connection.execute(
            f'SELECT DISTINCT "{field.name}" AS v FROM "{relation}" '
            f'WHERE "{field.name}" IS NOT NULL AND "{field.name}" <> \'\' '
            f'ORDER BY 1 LIMIT {val_mod.MAX_CARDINALITY + 1}').fetchall()
    except Exception:  # noqa: BLE001 - an unreadable column is not a failure
        return {}
    seen = [str(row[0]) for row in rows]
    if len(seen) <= val_mod.MAX_CARDINALITY and not _looks_like_a_period(
            field.name):
        return {"governed_values": seen,
                "value_labels": {v: val_mod.pretty(v) for v in seen},
                "distinct_values": len(seen)}
    return {"sample_values": seen[:_SAMPLE_LIMIT],
            "distinct_values_at_least": len(seen)}


def _looks_like_a_period(name: str) -> bool:
    """A calendar is not a category, in any book."""
    from backend.cockpit_v4 import schema as schema_mod

    return any(str(name).endswith(f"_{noun}")
               for noun in schema_mod.PERIOD_NOUNS.values())


# ---- export ------------------------------------------------------------

def _export_context(run_id: str, who: dict[str, Any]):
    """The run, its answer and its artifacts, or a typed refusal.

    An export of a run that has not finished would be an export of nothing,
    and an export of a FAILED run would be an export of a question with no
    answer. Both are refused by name rather than served as an empty file.
    """
    from backend.cockpit_v4 import export as export_mod

    record = _authorize(run_id, who)
    if not st.is_terminal(record.state):
        raise HTTPException(409, {
            "error_code": "RUN_NOT_FINISHED",
            "message": ("This run has not finished, so there is nothing to "
                        "export yet."),
            "state": record.state})
    answer = record.final_response or {}
    if not answer:
        raise HTTPException(409, {
            "error_code": st.DATA_UNAVAILABLE,
            "message": (f"This run ended {record.state} and published no "
                        f"answer, so there is nothing to export. Nothing "
                        f"was substituted for it."),
            "state": record.state,
            "error_code_detail": record.error_code})
    del export_mod
    return record, answer


def _run_artifacts(record: Any, answer: dict[str, Any]
                   ) -> dict[str, dict[str, Any]]:
    store = _store()
    found: dict[str, dict[str, Any]] = {}
    ids = {str(t.get("artifact_id") or "")
           for t in (answer.get("tables") or [])}
    ids |= {str(c.get("artifact_id") or "")
            for c in (answer.get("charts") or [])}
    ids |= {str((claim.get("evidence") or {}).get("artifact_id") or "")
            for claim in (answer.get("numeric_claims") or [])}
    for artifact_id in sorted(i for i in ids if i):
        stored = store.get_artifact(artifact_id,
                                    tenant_id=record.tenant_id)
        if stored is not None and stored["run_id"] == record.run_id:
            found[artifact_id] = stored
    return found


def _attach(body: str, *, media: str, name: str) -> Response:
    return Response(
        content=body, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "X-CreditProbe-Export": "1"})


@router.get("/runs/{run_id}/export")
async def export_analysis(run_id: str,
                          who: dict[str, Any] = Depends(principal)
                          ) -> Response:
    """The whole analysis as one document: answer, figures, tables, lineage."""
    from backend.cockpit_v4 import export as export_mod

    record, answer = _export_context(run_id, who)
    artifacts = _run_artifacts(record, answer)
    rows = sum(len(a.get("rows") or []) for a in artifacts.values())
    lineage = export_mod.lineage_for(record=record, row_count=rows,
                                     answer=answer, artifacts=artifacts,
                                     header=_release_header_for(record))
    lineage = export_mod.Lineage(
        **{**lineage.__dict__,
           "artifact_ids": tuple(sorted(artifacts)),
           "code_digests": tuple(
               sorted({str(a.get("code_digest") or "")
                       for a in artifacts.values()
                       if a.get("code_digest")}))})
    body = export_mod.analysis_markdown(
        record=record, answer=answer, artifacts=artifacts, lineage=lineage)
    return _attach(body, media=export_mod.MEDIA_TYPES[
        export_mod.FORMAT_MARKDOWN],
        name=export_mod.filename(kind="analysis", run_id=run_id,
                                 suffix="md"))


@router.get("/runs/{run_id}/governance")
async def read_governance(run_id: str,
                          who: dict[str, Any] = Depends(principal)
                          ) -> dict[str, Any]:
    """How this question became a number, from what was already stored.

    `/trace` answers "what did it do": a list of stages with ticks against
    them. It does not say what was understood from the question, which
    governed values the reader's own words resolved to, what query was
    written, whether a query was REFUSED, which relations were read, or how
    the figure in the second paragraph reached the page.

    All of that was on file and none of it had a route. This is the route.

    It reads. It opens no model, runs no query and recomputes no figure.

    `_authorize` rather than `_export_context`: a run that failed, or that
    was refused before it ever ran, is exactly the run a reviewer opens
    this for, and the export guard turns both away.
    """
    from backend.cockpit_v4 import governance as gov

    record = _authorize(run_id, who)
    store = _store()
    answer = record.final_response or {}
    events = list(store.events_since(run_id, 0))
    details = store.details_for_run(run_id)
    artifacts = _governance_artifacts(record)
    show_sql = gov.may_read_sql(who)
    return {
        "run_id": run_id,
        "thread_id": record.thread_id,
        "state": record.state,
        "mode": record.mode,
        "error_code": record.error_code,
        "created_at": record.created_at,
        "question": gov.question_block(record, events, details),
        "interpretation": gov.interpretation_block(events, details, answer),
        "submissions": gov.submissions_block(
            store.submissions_for_run(run_id), events, details, artifacts,
            show_sql=show_sql),
        "waterfall": gov.waterfall_block(answer, artifacts,
                                         _run_release_block(record)),
        "validation": {
            "warnings": list((answer.get("validation") or {}).get(
                "warnings") or ()),
            "limitations": list(answer.get("limitations") or ()),
            "claims_checked": len(answer.get("numeric_claims") or ()),
        },
        "cost": {**(record.budget or {}), **store.spend(run_id)},
        # Said plainly rather than inferred from an absent field: a reader
        # without the role must be able to tell "withheld" from "there was
        # no query".
        "sql_visible": show_sql,
        "sql_policy": gov.SQL_WITHHELD if not show_sql else "",
    }


@router.get("/runs/{run_id}/governance/export")
async def export_governance(run_id: str,
                            who: dict[str, Any] = Depends(principal)
                            ) -> Response:
    """The governance record as a pack: the document, the JSON, the rows.

    Three formats because three readers. The Markdown is what a person
    reads and circulates; the JSON is the same record whole, for anyone
    reproducing the account mechanically; the CSVs are the stored results
    themselves, so a reviewer can re-run the arithmetic rather than take
    the answer's word for it.

    The SQL is in the pack exactly as it is in the route -- shown to an
    administrator, withheld otherwise. A download is not a way around a
    permission.
    """
    import io
    import json as json_mod
    import zipfile

    from backend.cockpit_v4 import export as export_mod

    record = _authorize(run_id, who)
    governance = await read_governance(run_id, who)
    artifacts = _governance_artifacts(record)
    rows = sum(len(a.get("rows") or []) for a in artifacts.values())
    lineage = export_mod.lineage_for(
        record=record, row_count=rows, answer=record.final_response or {},
        artifacts=artifacts, header=_release_header_for(record))
    lineage = export_mod.Lineage(
        **{**lineage.__dict__,
           "artifact_ids": tuple(sorted(artifacts)),
           "code_digests": tuple(sorted({str(a.get("code_digest") or "")
                                         for a in artifacts.values()
                                         if a.get("code_digest")}))})

    buffer = io.BytesIO()
    # DEFLATED rather than stored: a governance pack for a twenty-period
    # analysis is mostly repeated column names.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pack:
        pack.writestr(
            f"governance-{run_id[:12]}.md",
            export_mod.governance_markdown(record=record,
                                           governance=governance,
                                           lineage=lineage))
        pack.writestr(f"governance-{run_id[:12]}.json",
                      json_mod.dumps(governance, indent=2, ensure_ascii=False,
                                     default=str))
        for artifact_id, artifact in sorted(artifacts.items()):
            try:
                body = export_mod.table_csv(artifact=artifact,
                                            lineage=lineage)
            except export_mod.ExportUnavailable:
                # One unexportable result must not cost the reader the
                # whole pack; the record says the artifact exists either
                # way.
                continue
            pack.writestr(f"results/{artifact_id}.csv", body)
    return Response(
        content=buffer.getvalue(),
        media_type=export_mod.MEDIA_TYPES[export_mod.FORMAT_ZIP],
        headers={"content-disposition":
                 f'attachment; filename="'
                 f'{export_mod.filename(kind="governance", run_id=run_id, suffix="zip")}"'})


def _governance_artifacts(record: Any) -> dict[str, dict[str, Any]]:
    """EVERY artifact this run stored, not only the published ones.

    `_run_artifacts` starts from the answer, which is right for an export
    -- it carries what the reader was shown. A governance record is the
    other question: an intermediate result that fed a later step, and a
    result that was computed and then not published, both belong in the
    account of what happened.
    """
    store = _store()
    found: dict[str, dict[str, Any]] = {}
    for artifact_id in store.artifact_ids_for_run(record.run_id,
                                                  tenant_id=record.tenant_id):
        stored = store.get_artifact(artifact_id, tenant_id=record.tenant_id)
        if stored is not None:
            found[artifact_id] = stored
    return found


@router.get("/runs/{run_id}/artifacts/{artifact_id}/export")
async def export_table(run_id: str, artifact_id: str,
                       rows: str = Query("all"),
                       who: dict[str, Any] = Depends(principal)) -> Response:
    """One result as CSV. Every row by default, and it says which."""
    from backend.cockpit_v4 import export as export_mod

    record, answer = _export_context(run_id, who)
    if rows not in export_mod.ROW_MODES:
        raise HTTPException(400, {
            "error_code": "INVALID_ROW_SCOPE",
            "message": (f"rows must be one of "
                        f"{', '.join(export_mod.ROW_MODES)}.")})
    stored = _store().get_artifact(artifact_id, tenant_id=record.tenant_id)
    if stored is None or stored["run_id"] != run_id:
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such artifact."})
    table = next((t for t in (answer.get("tables") or [])
                  if str(t.get("artifact_id") or "") == artifact_id), {})
    units = dict(table.get("column_units") or {})
    columns = [str(c) for c in (table.get("columns")
                                or stored.get("columns") or [])]
    body_rows = list(stored.get("rows") or [])
    total = int(stored.get("row_count") or len(body_rows))
    if rows == export_mod.DISPLAYED_ROWS:
        # What the reader actually saw: the rendered table's own rows.
        shown = len(table.get("rows") or []) or len(body_rows)
        body_rows = body_rows[:shown]
    payload = dict(stored, rows=body_rows)
    lineage = export_mod.lineage_for(
        record=record, artifact=stored, row_scope=rows, answer=answer,
        header=_release_header_for(record),
        row_count=len(body_rows), total_rows=total,
        complete=len(body_rows) >= total)
    try:
        body = export_mod.table_csv(artifact=payload, lineage=lineage,
                                    columns=columns, units=units)
    except export_mod.ExportUnavailable as exc:
        raise HTTPException(409, {"error_code": st.DATA_UNAVAILABLE,
                                  "message": str(exc)}) from exc
    return _attach(body, media=export_mod.MEDIA_TYPES[export_mod.FORMAT_CSV],
                   name=export_mod.filename(kind="table", run_id=run_id,
                                            suffix="csv"))


@router.get("/runs/{run_id}/charts/{index}/export")
async def export_chart(run_id: str, index: int,
                       who: dict[str, Any] = Depends(principal)) -> Response:
    """One published chart as SVG, drawn from the points the server supplied."""
    from backend.cockpit_v4 import export as export_mod

    record, answer = _export_context(run_id, who)
    charts = list(answer.get("charts") or [])
    if index < 0 or index >= len(charts):
        raise HTTPException(404, {
            "error_code": "NOT_FOUND",
            "message": (f"This answer published {len(charts)} chart(s); "
                        f"there is no chart {index}.")})
    artifacts = _run_artifacts(record, answer)
    chart = charts[index]
    stored = artifacts.get(str(chart.get("artifact_id") or ""))
    lineage = export_mod.lineage_for(
        record=record, artifact=stored, answer=answer,
        header=_release_header_for(record),
        row_count=len(chart.get("points") or []))
    try:
        body = export_mod.chart_svg(chart=chart, lineage=lineage)
    except export_mod.ExportUnavailable as exc:
        raise HTTPException(409, {"error_code": st.DATA_UNAVAILABLE,
                                  "message": str(exc)}) from exc
    return _attach(body, media=export_mod.MEDIA_TYPES[export_mod.FORMAT_SVG],
                   name=export_mod.filename(kind="chart", run_id=run_id,
                                            suffix="svg"))


# ---- the Cockpit home feed ---------------------------------------------

def _attention_session(who: dict[str, Any]) -> tuple[Any, Any, Any]:
    """The read-only session, catalog and config the feed is computed from.

    Same scope machinery an analysis uses: the release is pinned by the
    server, the tenant comes from the principal, and neither can be widened
    by the request.
    """
    # Typed, before anything is dereferenced. The live failure was an
    # AttributeError from a stand-in runtime reaching this line -- a raw 500
    # with a traceback in it, where the honest answer is "this runtime has no
    # release open, and here is the command that publishes it".
    from backend.cockpit_v4 import readiness as ready_mod

    runtime = _require(ready_mod.ATTENTION_READY)

    from backend.cockpit_agentic import sql as v3_sql

    scope = runtime.scope_for(who)
    try:
        session = v3_sql.open_session(scope=scope, catalog=runtime.catalog)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, {
            "error_code": "ATTENTION_UNAVAILABLE",
            "message": ("The segment attention feed could not read the "
                        f"pinned release: {exc}")}) from exc
    return session, runtime, scope


def _domain_feed(who: dict[str, Any], domain_id: str, *,
                 refresh: bool = False) -> dict[str, Any]:
    """The dashboard for ONE book, computed from that book's own release.

    Switching domains is not a relabel and not a filter over one dataset: it
    opens a different release, a different catalogue and a different session,
    and the cache entry it fills is keyed by domain, release and fingerprint
    so it can never be served to the other book.
    """
    from backend.cockpit_v4 import attention_v2
    from backend.cockpit_v4 import catalog as cat_mod
    from backend.cockpit_v4 import domain_resolver as resolver

    tenant = str(who.get("tenant") or "") or lake_mod.DEFAULT_TENANT
    try:
        scope = resolver.scope_for(domain_id, tenant_id=tenant)
    except resolver.DomainUnavailable as exc:
        # One book being unpublished never makes the other stand in for it.
        raise HTTPException(503, {
            "error_code": st.DATA_UNAVAILABLE,
            "message": str(exc),
            "component": f"{domain_id}_attention_feed",
            "domain_id": domain_id,
            "provision_command": resolver.provision_command(domain_id),
        }) from exc
    try:
        session = cat_mod.open_session(
            catalog=cat_mod.build(domain_id=scope.domain_id,
                                  release_id=scope.release_id,
                                  tenant_id=tenant))
    except PermissionError as exc:
        # A tenant this release holds nothing for. Refused with the reason
        # and a typed code -- not an unhandled 500, and emphatically not an
        # empty feed, which would read as "your portfolio is fine".
        raise HTTPException(403, {
            "error_code": st.SECURITY_DENIED, "message": str(exc),
            "component": f"{domain_id}_attention_feed",
            "domain_id": domain_id}) from exc
    try:
        return attention_v2.cached(session=session, scope=scope,
                                   tenant_id=tenant, refresh=refresh)
    except attention_v2.AttentionUnavailable as exc:
        # A component-level failure with a reference, never a bare 404 and
        # never a claim that the backend is down: Ask keeps working.
        raise HTTPException(503, {
            "error_code": st.DATA_UNAVAILABLE, "message": str(exc),
            "component": f"{domain_id}_attention_feed",
            "domain_id": domain_id,
            "error_reference": "att-" + hashlib.sha256(
                str(exc).encode("utf-8")).hexdigest()[:12]}) from exc


def _domain_book(who: dict[str, Any], domain_id: str):
    """This book's scope and an open session on it, or a typed refusal.

    Shared by every per-domain server-computed panel, so a new panel cannot
    accidentally reach a different release than the dashboard beside it.
    """
    from backend.cockpit_v4 import catalog as cat_mod
    from backend.cockpit_v4 import domain_resolver as resolver

    tenant = str(who.get("tenant") or "") or lake_mod.DEFAULT_TENANT
    try:
        scope = resolver.scope_for(domain_id, tenant_id=tenant)
    except resolver.DomainUnavailable as exc:
        raise HTTPException(503, {
            "error_code": st.DATA_UNAVAILABLE, "message": str(exc),
            "domain_id": domain_id,
            "provision_command": resolver.provision_command(domain_id),
        }) from exc
    try:
        session = cat_mod.open_session(
            catalog=cat_mod.build(domain_id=scope.domain_id,
                                  release_id=scope.release_id,
                                  tenant_id=tenant))
    except PermissionError as exc:
        raise HTTPException(403, {
            "error_code": st.SECURITY_DENIED, "message": str(exc),
            "domain_id": domain_id}) from exc
    return scope, session, tenant


@router.get("/ecl")
async def ecl_panel(domain: str = Query(""), refresh: bool = Query(False),
                    who: dict[str, Any] = Depends(principal)
                    ) -> dict[str, Any]:
    """Where this book's ECL is, and what moved it. No model call.

    Two server-computed statements: the stage profile, and a decomposition of
    the month's ECL movement whose components sum to that movement exactly.
    The residual is published rather than absorbed, so a reader can see that
    they do.
    """
    from backend.cockpit_v4 import domains as dom_mod
    from backend.cockpit_v4 import ecl as ecl_mod

    try:
        domain_id = dom_mod.parse(domain)
    except dom_mod.UnknownDomain as exc:
        raise HTTPException(400, {"error_code": "UNKNOWN_DOMAIN",
                                  "message": str(exc)}) from exc
    scope, session, tenant = _domain_book(who, domain_id)
    try:
        body = ecl_mod.cached(session=session, scope=scope,
                              tenant_id=tenant, refresh=refresh)
    except ecl_mod.EclUnavailable as exc:
        raise HTTPException(503, {
            "error_code": st.DATA_UNAVAILABLE, "message": str(exc),
            "component": f"{domain_id}_ecl_panel",
            "domain_id": domain_id}) from exc
    # §17. Every surface that serves a book NAMES the book, at its top level
    # and not only inside a nested panel. A caller that has to reach into
    # `profile.domain_id` to find out which release it is looking at is a
    # caller that will eventually not bother.
    return {
        "domain_id": scope.domain_id,
        "domain_label": scope.label,
        "release_id": scope.release_id,
        "release_fingerprint": scope.release_fingerprint,
        "reporting_frequency": scope.reporting_frequency,
        "period_noun": scope.period_noun,
        **body,
    }


def _feed(who: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
    session, runtime, _scope = _attention_session(who)
    catalog = runtime.catalog
    currency = " ".join(
        part for part in (getattr(catalog, "reporting_currency", ""),
                          getattr(catalog, "amount_scale", "")) if part)
    calendar = getattr(catalog, "calendar", None)
    quarters = [str(q) for q in (getattr(calendar, "populated", ()) or ())]
    try:
        return attention.cached(
            session=session, release_id=str(runtime.cfg.release_id),
            tenant_id=str(who.get("tenant") or ""), quarters=quarters,
            currency=currency, refresh=refresh)
    except attention.AttentionUnavailable as exc:
        # A component-level failure with a reference, never a bare 404 and
        # never a claim that the backend is down: Ask keeps working.
        raise HTTPException(503, {
            "error_code": exc.code, "message": exc.message,
            "component": "segment_attention_feed",
            "error_reference": "att-" + hashlib.sha256(
                exc.message.encode("utf-8")).hexdigest()[:12]}) from exc


@router.get("/domains")
async def domain_list(who: dict[str, Any] = Depends(principal)
                      ) -> dict[str, Any]:
    """Which books this runtime serves, and whether each can be asked.

    §56, §57: reported separately. One domain being unpublished does not make
    the other unavailable and absolutely does not make it a substitute, so a
    switch can disable the book that is missing and say why.
    """
    from backend.cockpit_v4 import domain_resolver as resolver

    tenant = str(who.get("tenant") or "") or lake_mod.DEFAULT_TENANT
    body = resolver.availability(tenant_id=tenant).to_dict()
    # Browsable is not the same as askable, and the switch needs to know the
    # difference so it can offer the book without promising an answer.
    body["analysis_domains"] = list(
        resolver.analysis_domains(tenant_id=tenant))
    for entry in body["domains"]:
        entry["analysis_ready"] = entry["domain_id"] in \
            body["analysis_domains"]
    return body


@router.get("/attention")
async def attention_feed(refresh: bool = Query(False),
                         domain: str = Query(""),
                         who: dict[str, Any] = Depends(principal)
                         ) -> dict[str, Any]:
    """One book's dashboard: what requires attention, and this month's ECL.

    Deterministic, cached per tenant AND domain AND release fingerprint, and
    free of model calls: rendering this page costs nothing at the provider.
    """
    from backend.cockpit_v4 import domains as dom_mod

    try:
        domain_id = dom_mod.parse(domain)
    except dom_mod.UnknownDomain as exc:
        raise HTTPException(400, {"error_code": "UNKNOWN_DOMAIN",
                                  "message": str(exc)}) from exc
    return _domain_feed(who, domain_id, refresh=refresh)


@router.get("/attention-legacy")
async def attention_feed_legacy(refresh: bool = Query(False),
                                who: dict[str, Any] = Depends(principal)
                                ) -> dict[str, Any]:
    """The quarterly corporate feed, kept while its suite still reads it."""
    feed = _feed(who, refresh=refresh)
    public = {k: v for k, v in feed.items() if k not in ("method", "dropped")}
    public["method_summary"] = {
        "formula": feed["method"]["formula"],
        "indicators": len(feed["method"]["indicators"]),
        "candidates_considered": feed["method"]["candidates_considered"],
        "candidates_dropped": feed["method"]["candidates_dropped"],
    }
    return public


def _find_across_domains(who: dict[str, Any], item_id: str
                         ) -> tuple[dict[str, Any], dict[str, Any]]:
    """The item, and the feed it belongs to, whichever book that is.

    An item id carries its domain in the seed it was hashed from, so looking
    in both books cannot confuse them: at most one can hold this id, and the
    feed returned is the one that does.
    """
    from backend.cockpit_v4 import attention_v2
    from backend.cockpit_v4 import domains as dom_mod

    for domain_id in dom_mod.DOMAIN_IDS:
        try:
            feed = _domain_feed(who, domain_id)
        except HTTPException:
            continue  # a book that is not published holds no items
        item = attention_v2.find_item(feed, item_id)
        if item is not None:
            return item, feed
    raise HTTPException(404, {"error_code": "NOT_FOUND",
                              "message": "No such attention item."})


@router.get("/attention/{item_id}")
async def attention_item(item_id: str,
                         who: dict[str, Any] = Depends(principal)
                         ) -> dict[str, Any]:
    """One item with its full technical evidence, for Trace / operator view."""
    item, feed = _find_across_domains(who, item_id)
    return {"item": item,
            "domain_id": feed["domain_id"],
            "method": feed["method_summary"],
            "method_summary": feed["method_summary"],
            "release_id": feed["release_id"],
            "release_fingerprint": feed["release_fingerprint"],
            "reporting_quarter": feed["reporting_month"],
            "reporting_month": feed["reporting_month"]}


@router.post("/attention/{item_id}/investigate", status_code=201)
async def investigate(item_id: str,
                      who: dict[str, Any] = Depends(principal)
                      ) -> dict[str, Any]:
    """Open a V4 thread seeded with this attention item.

    The seed is stored on the THREAD, so the segment, the quarter, the
    comparison period and the evidence are carried by every turn in it. That
    is what lets the next question be "show me the customers behind this"
    rather than the whole sentence again.
    """
    item, feed = _find_across_domains(who, item_id)
    store = _store()
    tenant = str(who.get("tenant") or "")
    # §53: the thread is opened in the book the finding came FROM. A
    # corporate card cannot seed a retail conversation, because the evidence
    # the seed names lives in one release and nowhere else.
    thread_id = store.create_thread(
        tenant_id=tenant, principal_id=str(who.get("id") or ""),
        domain_id=feed["domain_id"], release_id=feed["release_id"],
        release_fingerprint=feed["release_fingerprint"])
    seed = {
        "item_id": item["item_id"],
        "origin": item["section"],
        "domain_id": feed["domain_id"],
        "release_id": feed["release_id"],
        "release_fingerprint": feed["release_fingerprint"],
        "headline": item["headline"],
        "segment": item["segment"],
        "segment_dimension": item["segment_dimension"],
        # The period, under the name this book actually uses. The quarter
        # keys are kept beside them because older readers index on those,
        # but a monthly finding described only as a "quarter" is a finding
        # the analyst will resolve against the wrong calendar.
        "reporting_period": item.get("reporting_month")
        or item["reporting_quarter"],
        "comparison_period": item.get("comparison_month")
        or item["comparison_quarter"],
        "reporting_month": item.get("reporting_month", ""),
        "comparison_month": item.get("comparison_month", ""),
        "reporting_quarter": item["reporting_quarter"],
        "comparison_quarter": item["comparison_quarter"],
        "comparison_basis": item["comparison_basis"],
        "metric": item["metric"],
        "metric_label": item["metric_label"],
        "issue": item["what_changed"],
        "why_it_appeared": item["why_it_appeared"],
        "movement": item["movement"],
        "key_numbers": item["key_numbers"],
        "evidence": item["evidence"],
        "drilldown": item.get("drilldown", {}),
        "evidence_url": item["evidence_url"],
    }
    store.set_thread_context(thread_id, tenant_id=tenant,
                             kind="attention_item", body=seed)
    # A seeded thread is ABOUT something before anybody types in it. Naming
    # it after the case beats "New conversation" in a list of conversations,
    # and it costs no model call: the headline is already written.
    store.set_thread_title(thread_id, tenant_id=tenant,
                           title=str(item["headline"])[:120])
    return {"thread_id": thread_id, "item_id": item["item_id"], "seed": seed,
            "suggested_questions": (item.get("drilldown", {})
                                    .get("suggested_questions", []))}


@router.get("/session")
async def session_summary(who: dict[str, Any] = Depends(principal)
                          ) -> dict[str, Any]:
    """Who is asking, and what they can reopen.

    The landing page greets by name when a name is available and greets
    plainly when it is not. It never invents one, and the time of day is
    computed in the browser from the reader's own clock rather than the
    server's.
    """
    runtime = _STATE.get("runtime")
    store = _store()
    tenant = str(who.get("tenant") or "")
    display = str(who.get("name") or "").strip()
    return {
        # A person's name, or nothing. A profile label -- "Local UAT",
        # "Service Account", the name of a deployment -- is NOT a person and
        # travels separately, so the greeting cannot accidentally address one.
        "display_name": display,
        "profile_label": str(who.get("profile_label") or "").strip(),
        "tenant": tenant,
        "release_id": str(getattr(getattr(runtime, "cfg", None),
                                  "release_id", "") or ""),
        "recent_threads": store.recent_threads(
            tenant_id=tenant, principal_id=str(who.get("id") or ""), limit=5),
    }


@router.get("/threads/{thread_id}")
async def read_thread(thread_id: str,
                      who: dict[str, Any] = Depends(principal)
                      ) -> dict[str, Any]:
    """Reopen a real persisted conversation. Never a fabricated history."""
    store = _store()
    tenant = str(who.get("tenant") or "")
    owner = store.thread_owner(thread_id)
    if owner is None or owner[0] != tenant:
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such conversation."})
    context = store.thread_context(thread_id, tenant_id=tenant)
    turns = store.thread_turns(thread_id)
    title = store.thread_title(thread_id)
    if not title and turns:
        title = str(turns[0].get("question") or "")
    from backend.cockpit_v4 import domains as dom_mod

    # Which book this conversation was held in. Decided when it was created
    # and carried with the transcript, so a reader reopening it months later
    # is not left inferring the domain from the vocabulary of the answers.
    pinned = store.thread_domain(thread_id) or {}
    domain_id = pinned.get("domain_id") or dom_mod.DEFAULT_DOMAIN
    return {"thread_id": thread_id,
            "title": title,
            "domain_id": domain_id,
            "domain_label": dom_mod.LABELS.get(domain_id, domain_id),
            "domain_short_label": dom_mod.SHORT_LABELS.get(domain_id,
                                                           domain_id),
            "release_id": pinned.get("release_id", ""),
            "release_fingerprint": pinned.get("release_fingerprint", ""),
            # EVERY turn, oldest first. A transcript that renders only its
            # last few exchanges is a window, and a reader scrolling up to
            # find what they asked half an hour ago finds nothing.
            "turns": turns,
            "turn_count": len(turns),
            "created_at": store.thread_created_at(thread_id),
            "context": context or {},
            # §31. What to ask, BEFORE anybody has asked anything.
            #
            # A thread opened from an attention card arrived on screen as a
            # headline and an empty box: the reader had just clicked a
            # finding and was then asked to compose a question about it from
            # scratch. The card already carries five questions this release
            # can answer, computed when the card was, so they travel with the
            # transcript and the thread opens on them.
            "opening_questions": _opening_questions(context, turns,
                                                    domain_id),
            # The SAME book `release_id` two fields up names. These two were
            # computed from different sources and could disagree.
            "release": _release_header_for_pin(
                {**pinned, "domain_id": domain_id})}


#: Deterministic openers for a thread that was not seeded from a card, and
#: for one whose seed carried nothing. No model call: a suggestion that costs
#: a generation is a suggestion that arrives after the reader has given up
#: waiting for it.
#:
#: `{period}` is filled from the book's own latest period and `{window}`
#: from its own calendar -- eight quarters in the Corporate book, twelve
#: months in the Retail one. Both books said "twelve months", which in a
#: book that publishes quarters is four data points under a chip that reads
#: like twelve.
_GENERIC_OPENERS: dict[str, tuple[str, ...]] = {
    "corporate": (
        "What is total exposure at default by sector in {period}?",
        "Which sectors carry the most ECL in {period}?",
        "Which borrowers moved to Stage 2 in {period}?",
        "How has ECL coverage moved over the last {window}?",
        "Which covenants are in breach in {period}?",
    ),
    "retail": (
        "What is total exposure at default by product in {period}?",
        "Which products carry the most ECL in {period}?",
        "How has delinquency moved over the last {window}?",
        "Which regions deteriorated most in {period}?",
        "What is ECL coverage by score band in {period}?",
    ),
}

#: How far back a trend question reaches, IN THIS BOOK'S OWN PERIODS. Two
#: years of a quarterly book and one of a monthly one: both are a span a
#: credit officer reads without converting it.
_TREND_WINDOWS: dict[str, str] = {
    "quarter": "eight quarters",
    "month": "twelve months",
}


def _opening_questions(context: dict[str, Any] | None,
                       turns: list[dict[str, Any]],
                       domain_id: str) -> list[dict[str, Any]]:
    """Three to five questions this thread can open on.

    Empty once the conversation has started: after the first answer the
    follow-ups belong to that answer, and offering the opening set again is
    offering to ask the question that has just been answered.
    """
    if turns:
        return []
    seeded = ((context or {}).get("body") or {}) if context else {}
    offered = list((seeded.get("drilldown") or {})
                   .get("suggested_questions") or [])
    shaped = [{"question": str(q.get("question") or ""),
               "kind": str(q.get("kind") or "opening")}
              for q in offered if isinstance(q, dict) and q.get("question")]
    if shaped:
        return shaped[:5]
    # The seed's own period, whichever calendar it was written on. Reading
    # `reporting_month` alone left every corporate opener asking about "the
    # latest month" of a book that reports quarters.
    period = str(seeded.get("reporting_period")
                 or seeded.get("reporting_quarter")
                 or seeded.get("reporting_month") or "")
    if not period:
        period = _latest_period(domain_id)
    from backend.cockpit_v4 import schema as schema_mod

    noun = schema_mod.period_noun(domain_id)
    window = _TREND_WINDOWS.get(noun, "twelve months")
    return [{"question": text.format(period=period or f"the latest {noun}",
                                     window=window),
             "kind": "opening"}
            for text in _GENERIC_OPENERS.get(domain_id, ())][:5]


def _latest_period(domain_id: str) -> str:
    """The latest populated period of this book, or nothing. Never a guess."""
    try:
        from backend.cockpit_v4 import analytical_runtime as arun_mod
        from backend.cockpit_v4 import semantics as sem_mod

        runtime = arun_mod.for_domain(domain_id)
        populated = sem_mod.populated_periods(runtime.catalog)
        return str(populated[-1]) if populated else ""
    except Exception:  # noqa: BLE001 - an unopenable book offers no period
        return ""


def _release_header_for(record: Any):
    """The header of the book THIS RUN was accepted in.

    Not the runtime's configured release: an export is a record of one
    analysis, and stamping it with whichever release the process happens to
    be pointed at is how a Retail document ends up naming the Corporate book.
    Returns None rather than a wrong header when the book cannot be opened.
    """
    from backend.cockpit_v4 import analytical_runtime as arun_mod
    from backend.cockpit_v4 import release as release_mod

    try:
        runtime = arun_mod.for_run(record, store=_store())
        return release_mod.header(
            release_id=runtime.release_id, catalog=runtime.catalog,
            release_summary=runtime.release_summary(),
            tenant_id=str(getattr(record, "tenant_id", "")))
    except Exception:  # noqa: BLE001 - an unopenable book stamps nothing
        return None


def _run_release_block(record: Any) -> dict[str, Any]:
    """The release a trace states, as a dict.

    Three answers, in order of what can actually be established:

      * the run's book opens -> its full header;
      * it does not open (a legacy release, or one rebuilt underneath the
        run) -> the release id the run was ACCEPTED against, and nothing
        else, because currency, scale and fingerprint would be guesses;
      * the run records no release at all -> an empty block.

    What it never does is fall back to the release this PROCESS is
    configured with. That fallback is what let a trace report
    `v4-saudi-20q-v1` for a run accepted against
    `v4-saudi-corporate-20q-v3` -- on a machine where the former was not
    published at all.
    """
    header = _release_header_for(record)
    if header is not None:
        return header.to_dict()
    accepted = str(getattr(record, "release_id", "") or "")
    return {"release_id": accepted} if accepted else {}


def _release_header_for_pin(pinned: dict[str, Any]) -> dict[str, Any]:
    """The header of the book a THREAD was pinned to, as a dict.

    A transcript already reports `release_id` from its pin. It also carried a
    `release` block built from whichever release this process is configured
    with, so a single response could name two different books -- and on a
    machine whose configured release is not even published, the block named a
    release that does not exist there.

    `for_domain` refuses rather than substituting when the pinned release was
    rebuilt underneath the thread, and an empty block is the honest answer to
    "which book is this?" when the book cannot be opened. Falling back to the
    configured release is what produced the mismatch.
    """
    from backend.cockpit_v4 import analytical_runtime as arun_mod
    from backend.cockpit_v4 import release as release_mod

    try:
        runtime = arun_mod.for_domain(
            str(pinned.get("domain_id") or ""),
            release_id=str(pinned.get("release_id") or ""),
            release_fingerprint=str(pinned.get("release_fingerprint") or ""))
        return release_mod.header(
            release_id=runtime.release_id, catalog=runtime.catalog,
            release_summary=runtime.release_summary()).to_dict()
    except Exception:  # noqa: BLE001 - an unopenable book stamps nothing
        return {}


class RenameThread(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.post("/threads/{thread_id}/title")
async def rename_thread(thread_id: str, body: RenameThread,
                        who: dict[str, Any] = Depends(principal)
                        ) -> dict[str, Any]:
    """Rename a conversation. Tenant-checked: a URL is not an authorization."""
    store = _store()
    tenant = str(who.get("tenant") or "")
    if not store.set_thread_title(thread_id, tenant_id=tenant,
                                  title=body.title):
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such conversation."})
    return {"thread_id": thread_id,
            "title": store.thread_title(thread_id)}


# ---- what a credit officer does with an answer -------------------------
#
# Save it, put it in an investigation, comment on it, share it with a
# colleague, notify someone. All five are V4's own storage and routes; none
# of them calls the previous Cockpit's investigation API.
#
# The notifier is the part with teeth. It is constructed with no transport
# and an empty recipient allow-list, so this build records notifications and
# sends none. `GET /notifications` says so on every row, and the outbox
# listing carries `delivered: false` rather than a hopeful "queued".

# The default build now carries the IN-APP transport, and only that one.
#
# Before it carried none at all, which meant "share this analysis with a
# colleague" recorded an intention and the colleague was never told. The
# wording was honest about it -- `delivered: false`, with the reason -- and
# it was still a feature that did not work.
#
# An in-app message needs no relay: it is addressed to someone who already
# has an account on this tenant and can already read the analysis. Mail is
# unchanged. A build that has not been given a mail transport and a
# recipient allow-list still cannot mail anyone, and still says so in the
# same words.
_NOTIFIER = collab.Notifier(in_app_transport=collab.InAppTransport())


def notifier() -> collab.Notifier:
    installed = _STATE.get("notifier")
    return installed if isinstance(installed, collab.Notifier) else _NOTIFIER


def _refuse(exc: collab.CollaborationError) -> HTTPException:
    return HTTPException(400, {"error_code": exc.code, "message": exc.message})


class SaveAnalysis(BaseModel):
    run_id: str = Field(min_length=1)
    title: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)


class NewInvestigation(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    origin: str = Field(default="", max_length=120)
    thread_id: str = ""
    saved_ids: list[str] = Field(default_factory=list)


class InvestigationItem(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    ref_id: str = Field(min_length=1, max_length=120)
    label: str = Field(default="", max_length=200)


class InvestigationStatus(BaseModel):
    status: str


class NewComment(BaseModel):
    subject_kind: str
    subject_id: str
    body: str = Field(min_length=1, max_length=collab.MAX_BODY)


class NewShare(BaseModel):
    subject_kind: str
    subject_id: str
    audience_id: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=2000)
    notify_email: str = ""
    #: A person or team inside CreditProbe: `user:<id>` or `team:<id>`.
    #: Separate from `notify_email` because they are governed differently
    #: and a reader choosing one should not look like a reader choosing
    #: the other.
    notify_in_app: str = ""


@router.post("/saved-analyses", status_code=201)
async def save_analysis(body: SaveAnalysis,
                        who: dict[str, Any] = Depends(principal)
                        ) -> dict[str, Any]:
    """Keep the PUBLISHED answer, not a re-rendering of it.

    A saved analysis that regenerated its text would drift from what the
    reader actually saw and approved, so the stored copy is the response
    itself, with the artifact ids it cites.
    """
    store = _store()
    tenant = str(who.get("tenant") or "")
    record = store.get_run(body.run_id)
    if record is None or record.tenant_id != tenant:
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such analysis."})
    if record.state not in st.TERMINAL_STATES or not record.final_response:
        raise HTTPException(
            409, {"error_code": "NOT_FINISHED",
                  "message": "An analysis can be saved once it has finished."})
    saved = store.save_analysis(
        tenant_id=tenant, principal_id=str(who.get("id") or ""),
        thread_id=record.thread_id, run_id=record.run_id,
        title=(body.title.strip() or record.question[:120]),
        note=body.note, question=record.question,
        release_id=record.release_id,
        body=collab.summarize_answer(record.final_response))
    return saved


@router.get("/saved-analyses")
async def list_saved(who: dict[str, Any] = Depends(principal)
                     ) -> dict[str, Any]:
    return {"saved": _store().list_saved_analyses(
        tenant_id=str(who.get("tenant") or ""))}


@router.get("/saved-analyses/{saved_id}")
async def read_saved(saved_id: str,
                     who: dict[str, Any] = Depends(principal)
                     ) -> dict[str, Any]:
    saved = _store().get_saved_analysis(
        saved_id, tenant_id=str(who.get("tenant") or ""))
    if saved is None:
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such saved analysis."})
    return saved


@router.post("/investigations", status_code=201)
async def create_investigation(body: NewInvestigation,
                               who: dict[str, Any] = Depends(principal)
                               ) -> dict[str, Any]:
    store = _store()
    tenant = str(who.get("tenant") or "")
    actor = str(who.get("id") or "")
    investigation = store.create_investigation(
        tenant_id=tenant, principal_id=actor, title=body.title.strip(),
        summary=body.summary, origin=body.origin, thread_id=body.thread_id)
    for saved_id in body.saved_ids:
        saved = store.get_saved_analysis(saved_id, tenant_id=tenant)
        if saved is None:
            raise HTTPException(
                404, {"error_code": "NOT_FOUND",
                      "message": f"Saved analysis {saved_id} is not "
                                 f"available to you."})
        store.add_investigation_item(
            investigation_id=investigation["investigation_id"],
            tenant_id=tenant, kind=collab.SAVED_ANALYSIS, ref_id=saved_id,
            label=saved["title"], added_by=actor)
    return store.get_investigation(investigation["investigation_id"],
                                   tenant_id=tenant)


@router.get("/investigations")
async def list_investigations(who: dict[str, Any] = Depends(principal)
                              ) -> dict[str, Any]:
    return {"investigations": _store().list_investigations(
        tenant_id=str(who.get("tenant") or ""))}


@router.get("/investigations/{investigation_id}")
async def read_investigation(investigation_id: str,
                             who: dict[str, Any] = Depends(principal)
                             ) -> dict[str, Any]:
    found = _store().get_investigation(
        investigation_id, tenant_id=str(who.get("tenant") or ""))
    if found is None:
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such investigation."})
    return found


@router.post("/investigations/{investigation_id}/items", status_code=201)
async def add_investigation_item(investigation_id: str,
                                 body: InvestigationItem,
                                 who: dict[str, Any] = Depends(principal)
                                 ) -> dict[str, Any]:
    store = _store()
    tenant = str(who.get("tenant") or "")
    entry = store.add_investigation_item(
        investigation_id=investigation_id, tenant_id=tenant, kind=body.kind,
        ref_id=body.ref_id, label=body.label,
        added_by=str(who.get("id") or ""))
    if entry is None:
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such investigation."})
    return entry


@router.post("/investigations/{investigation_id}/status")
async def set_investigation_status(investigation_id: str,
                                   body: InvestigationStatus,
                                   who: dict[str, Any] = Depends(principal)
                                   ) -> dict[str, Any]:
    if body.status not in collab.STATUSES:
        raise HTTPException(
            400, {"error_code": "UNKNOWN_STATUS",
                  "message": f"Status is one of "
                             f"{', '.join(collab.STATUSES)}."})
    tenant = str(who.get("tenant") or "")
    if not _store().set_investigation_status(investigation_id,
                                             tenant_id=tenant,
                                             status=body.status):
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such investigation."})
    return _store().get_investigation(investigation_id, tenant_id=tenant)


def _subject_exists(store, tenant: str, kind: str, subject_id: str) -> bool:
    if kind == collab.SAVED_ANALYSIS:
        return store.get_saved_analysis(subject_id,
                                        tenant_id=tenant) is not None
    return store.get_investigation(subject_id, tenant_id=tenant) is not None


@router.post("/comments", status_code=201)
async def add_comment(body: NewComment,
                      who: dict[str, Any] = Depends(principal)
                      ) -> dict[str, Any]:
    store = _store()
    tenant = str(who.get("tenant") or "")
    try:
        collab.check_subject(body.subject_kind, body.subject_id)
    except collab.CollaborationError as exc:
        raise _refuse(exc) from exc
    if not _subject_exists(store, tenant, body.subject_kind, body.subject_id):
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such item."})
    return store.add_comment(
        tenant_id=tenant, subject_kind=body.subject_kind,
        subject_id=body.subject_id, author_id=str(who.get("id") or ""),
        body=body.body)


@router.get("/comments")
async def list_comments(subject_kind: str = Query(...),
                        subject_id: str = Query(...),
                        who: dict[str, Any] = Depends(principal)
                        ) -> dict[str, Any]:
    tenant = str(who.get("tenant") or "")
    store = _store()
    if not _subject_exists(store, tenant, subject_kind, subject_id):
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such item."})
    return {"comments": store.list_comments(
        tenant_id=tenant, subject_kind=subject_kind, subject_id=subject_id)}


@router.post("/shares", status_code=201)
async def share(body: NewShare, who: dict[str, Any] = Depends(principal)
                ) -> dict[str, Any]:
    """Share inside the bank. An optional notification is RECORDED, not sent.

    The response always carries the outbox row, including the reason it was
    not delivered, so nothing in the UI can honestly render "email sent".
    """
    store = _store()
    tenant = str(who.get("tenant") or "")
    try:
        collab.check_subject(body.subject_kind, body.subject_id)
    except collab.CollaborationError as exc:
        raise _refuse(exc) from exc
    if not _subject_exists(store, tenant, body.subject_kind, body.subject_id):
        raise HTTPException(404, {"error_code": "NOT_FOUND",
                                  "message": "No such item."})
    record = store.add_share(
        tenant_id=tenant, subject_kind=body.subject_kind,
        subject_id=body.subject_id, shared_by=str(who.get("id") or ""),
        audience_id=body.audience_id.strip(), message=body.message)
    sent: list[dict[str, Any]] = []
    for recipient in (body.notify_in_app.strip(), body.notify_email.strip()):
        if not recipient:
            continue
        try:
            sent.append(notifier().notify(
                store, tenant_id=tenant, actor_id=str(who.get("id") or ""),
                recipient=recipient,
                subject=f"Shared with you: {body.subject_kind}",
                body=body.message or "A colleague shared this with you.",
                subject_kind=body.subject_kind, subject_id=body.subject_id))
        except collab.CollaborationError as exc:
            raise _refuse(exc) from exc
    return {"share": record,
            # Kept singular for the callers that read it, and a list beside
            # it for the reader who addressed both a colleague and a
            # mailbox. Dropping the singular would break a saved thread's
            # share panel for no gain.
            "notification": sent[0] if sent else None,
            "notifications": sent,
            "delivery": notifier().describe()}


@router.get("/shares")
async def list_shares(subject_kind: str = Query(default=""),
                      subject_id: str = Query(default=""),
                      who: dict[str, Any] = Depends(principal)
                      ) -> dict[str, Any]:
    return {"shares": _store().list_shares(
        tenant_id=str(who.get("tenant") or ""), subject_kind=subject_kind,
        subject_id=subject_id)}


@router.get("/notifications")
async def list_notifications(who: dict[str, Any] = Depends(principal)
                             ) -> dict[str, Any]:
    """The outbox. Every row says whether it was delivered, and why not."""
    return {"delivery": notifier().describe(),
            "notifications": _store().list_notifications(
                tenant_id=str(who.get("tenant") or ""))}


@router.get("/inbox")
async def read_inbox(who: dict[str, Any] = Depends(principal)
                     ) -> dict[str, Any]:
    """The messages addressed to THIS reader, with their bodies.

    `/notifications` is the tenant's whole outbox -- every row anyone sent,
    without its body, which is what an operator wants and the opposite of
    what a person wants. A reader who had been sent an analysis had no
    route that would show it to them.

    Addressed by handle rather than by user id alone, because a team is a
    recipient too: `team:corporate-credit` reaches everyone on it.
    """
    store = _store()
    tenant = str(who.get("tenant") or "")
    me = str(who.get("id") or "")
    handles = [f"user:{me}"] if me else []
    handles += [f"team:{str(team).strip()}"
                for team in (who.get("teams") or ()) if str(team).strip()]
    messages: list[dict[str, Any]] = []
    for handle in handles:
        messages.extend(store.inbox(tenant_id=tenant, recipient=handle))
    messages.sort(key=lambda row: str(row.get("created_at") or ""),
                  reverse=True)
    return {"me": me, "handles": handles, "messages": messages,
            "delivery": notifier().describe()}


@router.get("/recipients")
async def list_recipients(who: dict[str, Any] = Depends(principal)
                          ) -> dict[str, Any]:
    """Who this reader may send an analysis to, inside CreditProbe.

    The share panel asked for a free-text `audience_id`, which is a field
    a reader has to already know the answer to. This is the directory it
    should have been offering.

    Derived from who has actually used this tenant, plus the reader's own
    teams. It is not an org chart and does not pretend to be: a person who
    has never opened CreditProbe is not in it, and the panel says so
    rather than implying the list is everyone.
    """
    store = _store()
    tenant = str(who.get("tenant") or "")
    me = str(who.get("id") or "")
    people = sorted({p for p in store.principals_of(tenant_id=tenant)
                     if p and p != me})
    teams = sorted({str(team).strip()
                    for team in (who.get("teams") or ()) if str(team).strip()})
    return {
        "people": [{"handle": f"user:{p}", "label": p} for p in people],
        "teams": [{"handle": f"team:{t}", "label": t} for t in teams],
        "note": ("People who have used CreditProbe on this tenant. Somebody "
                 "who has not signed in yet will not be listed."),
    }


@router.get("/diagnostics")
async def get_diagnostics() -> dict[str, Any]:
    runtime = _STATE.get("runtime")
    cfg = getattr(runtime, "cfg", None)
    return diagnostics(cfg, startup_sha=str(_STATE.get("startup_sha") or ""))


#: The routes the application SHELL polls, served at the paths it already
#: uses. Separate from `router` because these are a compatibility surface, not
#: part of the V4 contract: the shell must not report the whole backend
#: offline merely because this runtime does not serve the dashboard.
compat_router = APIRouter(prefix="/api/v1", tags=["cockpit-v4-compat"])


@compat_router.get("/health")
async def shell_health() -> dict[str, Any]:
    """`HealthResponse`, describing THIS runtime truthfully.

    Unauthenticated on purpose: it is the same contract the main backend's
    `/api/v1/health` serves, it carries no tenant data, and a status
    indicator that needs a session to say "I am up" cannot say it on the
    screen where it matters.
    """
    from backend.cockpit_v4.compat_health import health_payload

    state = readiness()
    return health_payload(
        _config(), startup_sha=str(_STATE.get("startup_sha") or ""),
        capabilities=state.to_dict(),
        preflight_error=str(_STATE.get("preflight_error") or ""))


@router.get("/runs/{run_id}/trace")
async def read_trace(run_id: str,
                     who: dict[str, Any] = Depends(principal)
                     ) -> dict[str, Any]:
    """What this run did, after it finished. §28.

    The live panel shows a run while it happens; a reader who comes back to
    a past turn -- their own, an hour later, or after a reload -- had no way
    to see the same thing. This serves the COMMITTED events, which is the
    same material the stream delivered, plus what the run cost and which
    release answered it.

    It costs no model call and it reads no model reasoning. Public
    operations, their timings and their outcomes; nothing that was not
    already written down while the run was going.
    """
    record = _authorize(run_id, who)
    store = _store()
    events = [e.to_dict() if hasattr(e, "to_dict") else dict(e)
              for e in store.events_since(run_id, 0)]
    return {
        "run_id": run_id,
        "thread_id": record.thread_id,
        "question": record.question,
        "state": record.state,
        "mode": record.mode,
        "error_code": record.error_code,
        "created_at": record.created_at,
        "events": events,
        "budget": record.budget or {},
        # THE BOOK THIS RUN WAS ACCEPTED IN. This used to be the process's
        # configured release, so a trace could name a release the run was
        # never run against -- and, where the configured release is not
        # published on that machine, one that does not exist there. The
        # record is already in hand, twenty lines up.
        "release": _run_release_block(record),
    }


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request,
                        last_event_id: str = Header("", alias="Last-Event-ID"),
                        cursor: int = Query(0, ge=0),
                        who: dict[str, Any] = Depends(principal)
                        ) -> StreamingResponse:
    """Ordered SSE with replay from a cursor and a heartbeat.

    A browser disconnect does not cancel the run and a reconnect never
    resubmits the question: the client sends `Last-Event-ID` and gets the
    committed events it missed, in order.
    """
    record = _authorize(run_id, who)
    store = _store()
    start = cursor
    if last_event_id:
        try:
            start = max(start, int(last_event_id))
        except ValueError:
            start = cursor

    cfg = getattr(_STATE.get("runtime"), "cfg", None) or config_mod.load()
    heartbeat = max(1.0, float(cfg.heartbeat_seconds))

    async def publish():
        seq = start
        last_beat = time.monotonic()
        # A bounded delivery buffer: a slow subscriber is disconnected and
        # replays, rather than growing the worker's memory without limit.
        while True:
            if await request.is_disconnected():
                return
            try:
                batch = store.events_since(run_id, seq, limit=200)
            except Exception as exc:  # noqa: BLE001
                yield (f"event: stream.error\ndata: "
                       f"{json.dumps({'message': str(exc)[:200]})}\n\n")
                return
            for event in batch:
                seq = event.seq
                yield event.to_sse()
                last_beat = time.monotonic()
                if event.event_type in ev.TERMINAL_EVENTS:
                    current = store.get_run(run_id)
                    yield ("event: run.settled\ndata: "
                           + json.dumps(current.to_dict() if current else {},
                                        default=str) + "\n\n")
                    return
            current = store.get_run(run_id)
            if current is not None and st.is_terminal(current.state) \
                    and seq >= store.last_seq(run_id):
                yield ("event: run.settled\ndata: "
                       + json.dumps(current.to_dict(), default=str) + "\n\n")
                return
            if time.monotonic() - last_beat >= heartbeat:
                yield ev.heartbeat_frame()
                last_beat = time.monotonic()
            await asyncio.sleep(0.1)

    return StreamingResponse(
        publish(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform",
                 "Connection": "keep-alive",
                 # Defeats proxy buffering that would hold events until the
                 # response completes -- which is exactly the failure the
                 # live panel exists to make impossible.
                 "X-Accel-Buffering": "no"})


__all__ = ["compat_router", "install", "router"]
