"""
What a credit officer does with an answer after reading it.

Five verbs, all of them V4's own: save an analysis, open an investigation and
put saved work in it, comment on either, share either with a colleague inside
the bank, and notify someone. Nothing here reaches back into the previous
Cockpit's investigation API or its endpoints; the storage, the routes and the
authorization are this module's.

Delivery is the part worth reading carefully
--------------------------------------------
A notification is RECORDED, and it is only ever SENT by a transport that has
been configured and named. With no transport configured -- which is the
default, and the state of every build that has not been given one -- a
notification lands in the outbox in state RECORDED with
`delivered=false` and a reason that says so. No code path in this module can
produce `delivered=true` without a transport object having actually accepted
the message and returned a receipt.

Recipients are not merely filtered, they are gated. `RecipientPolicy` holds
an explicit allow-list, and an address outside it is REFUSED -- the row is
written in state REFUSED so the attempt is auditable, and nothing is handed
to a transport. An empty allow-list therefore refuses everything, which is
the correct behaviour for a build nobody has authorized to mail anyone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

#: What a comment or a share can be about. Both kinds are V4 objects.
SAVED_ANALYSIS = "saved_analysis"
INVESTIGATION = "investigation"
SUBJECT_KINDS = (SAVED_ANALYSIS, INVESTIGATION)

#: An investigation's lifecycle. Deliberately short: this is a workspace for
#: a credit officer, not a ticketing system.
OPEN, IN_REVIEW, CLOSED = "open", "in_review", "closed"
STATUSES = (OPEN, IN_REVIEW, CLOSED)

#: Outbox states. RECORDED is the resting state of a build with no transport.
RECORDED, REFUSED, SENT, FAILED = "RECORDED", "REFUSED", "SENT", "FAILED"

_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_BODY = 4000

#: An IN-PRODUCT recipient: a person or a team inside CreditProbe, written
#: `user:kamal.hassan` or `team:corporate-credit`.
#:
#: Deliberately not an email address. An in-app message never leaves the
#: bank's own deployment, so the whole apparatus that governs mail -- the
#: allow-list, the domain gate, the "who has authorized this build to write
#: to the outside world" question -- is answering a question that was not
#: asked. Keeping the two recipient FORMS apart is what keeps the two
#: policies apart.
IN_APP = re.compile(r"^(user|team):[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_in_app(recipient: str) -> bool:
    return bool(IN_APP.match(recipient.strip()))


class CollaborationError(Exception):
    """A refusal the caller should show, with a code the UI can switch on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Transport(Protocol):
    """A real, authorized delivery channel.

    `name` is recorded on every message it accepts, so an audit can say which
    transport delivered what. `send` returns a receipt id or raises.
    """

    name: str

    def send(self, *, recipient: str, subject: str, body: str) -> str: ...


@dataclass(frozen=True)
class RecipientPolicy:
    """Who this deployment is allowed to notify.

    `allowed` is exact addresses. `allowed_domains` admits a whole domain and
    exists for an internal directory, not for the open internet. Both default
    to empty, so a build that has not been told whom it may write to writes
    to nobody.
    """

    allowed: frozenset[str] = frozenset()
    allowed_domains: frozenset[str] = frozenset()
    label: str = "no recipients authorized"
    #: Whether people and teams inside this deployment may be written to.
    #:
    #: On by default, and that is not a loosening: an in-app message is
    #: delivered INSIDE the product, to someone who already has an account
    #: on this tenant and can already read the analysis being sent. The
    #: allow-list exists to stop a build mailing the open internet, and it
    #: still does.
    in_app: bool = True

    def permits(self, recipient: str) -> bool:
        if is_in_app(recipient):
            return self.in_app
        address = recipient.strip().lower()
        if not _ADDRESS.match(address):
            return False
        if address in {a.lower() for a in self.allowed}:
            return True
        domain = address.rsplit("@", 1)[-1]
        return domain in {d.lower().lstrip("@") for d in self.allowed_domains}


@dataclass
class InAppTransport:
    """Delivery inside CreditProbe itself.

    THE DEFAULT BUILD COULD NOT DELIVER ANYTHING. With no transport
    configured -- which is every build that has not been handed a mail
    relay -- `notify` wrote the row in state RECORDED and stopped, so
    "share this analysis with a colleague" recorded an intention and the
    colleague was never told. The wording was honest about it, which is
    better than lying, and it was still a feature that did not work.

    An in-app message needs no relay. The message IS the row: it is
    addressed to a person on this tenant, and it is delivered the moment
    it is readable by them -- which is what `store.inbox` now makes true.
    So this transport's `send` is a check that the address is one this
    product can reach, and a receipt saying so.

    It does not touch mail. A build with no mail transport still cannot
    send mail, and says so in the same words as before.
    """

    name: str = "creditprobe"

    def send(self, *, recipient: str, subject: str, body: str) -> str:
        if not is_in_app(recipient):
            # Not this transport's business. Raising rather than silently
            # succeeding: a message to an email address that this returned
            # a receipt for would be a message reported as delivered and
            # sitting in nobody's inbox.
            raise ValueError(
                f"{recipient} is not a CreditProbe user or team. In-product "
                f"messages are addressed `user:<id>` or `team:<id>`.")
        return f"in-app:{recipient.strip()}"


@dataclass
class Notifier:
    """Records every notification; delivers only what a transport accepts."""

    policy: RecipientPolicy = field(default_factory=RecipientPolicy)
    #: The OUTSIDE channel: mail, or whatever a deployment has authorized.
    #: Still None by default, and a build with none still sends no mail.
    transport: Transport | None = None
    #: The INSIDE channel. Present by default, because delivering a message
    #: to someone who already has an account on this tenant needs no relay
    #: and no allow-list -- they can already read the analysis being sent.
    #:
    #: THE TWO ARE SEPARATE FIELDS rather than one, because one boolean
    #: cannot answer "can this build deliver?" once the answer is "inside,
    #: yes; outside, no". A single `can_deliver` that went true the moment
    #: in-app delivery arrived would have quietly reported a build as able
    #: to mail people it cannot mail.
    in_app_transport: Transport | None = None

    @property
    def transport_name(self) -> str:
        return getattr(self.transport, "name", "") if self.transport else ""

    def _route(self, recipient: str) -> Transport | None:
        """Which channel a recipient belongs to. Never a fallback: a mail
        address handed to the in-app transport would be a message reported
        as delivered and sitting in nobody's inbox."""
        if is_in_app(recipient):
            return self.in_app_transport
        return self.transport

    def describe(self) -> dict[str, Any]:
        return {"transport": self.transport_name or None,
                # Kept, and kept meaning what it always meant: whether this
                # build can deliver to the OUTSIDE world.
                "can_deliver": self.transport is not None,
                "can_deliver_in_app": self.in_app_transport is not None,
                "in_app_transport": (
                    getattr(self.in_app_transport, "name", "")
                    if self.in_app_transport else None),
                "recipient_policy": self.policy.label,
                "authorized_recipients": sorted(self.policy.allowed),
                "authorized_domains": sorted(self.policy.allowed_domains)}

    def notify(self, store, *, tenant_id: str, actor_id: str, recipient: str,
               subject: str, body: str, subject_kind: str = "",
               subject_id: str = "") -> dict[str, Any]:
        """Write the outbox row, then attempt delivery only if allowed."""
        if not recipient.strip():
            raise CollaborationError("INVALID_RECIPIENT",
                                     "A recipient is required.")
        if len(body) > MAX_BODY:
            raise CollaborationError(
                "BODY_TOO_LONG",
                f"A notification body is limited to {MAX_BODY} characters.")

        if not self.policy.permits(recipient):
            return store.put_notification(
                tenant_id=tenant_id, actor_id=actor_id, recipient=recipient,
                subject=subject, body=body, subject_kind=subject_kind,
                subject_id=subject_id, state=REFUSED, transport="",
                receipt="",
                reason=(f"{recipient} is not an authorized recipient for "
                        f"this deployment ({self.policy.label}). The message "
                        f"was recorded and NOT sent."))

        transport = self._route(recipient)
        if transport is None:
            return store.put_notification(
                tenant_id=tenant_id, actor_id=actor_id, recipient=recipient,
                subject=subject, body=body, subject_kind=subject_kind,
                subject_id=subject_id, state=RECORDED, transport="",
                receipt="",
                reason=("No delivery transport is configured, so this "
                        "message was recorded and NOT sent."))

        name = getattr(transport, "name", "")
        try:
            receipt = transport.send(recipient=recipient,
                                     subject=subject, body=body)
        except Exception as exc:                              # noqa: BLE001
            return store.put_notification(
                tenant_id=tenant_id, actor_id=actor_id, recipient=recipient,
                subject=subject, body=body, subject_kind=subject_kind,
                subject_id=subject_id, state=FAILED,
                transport=name, receipt="",
                reason=f"The transport refused the message: {exc}")
        return store.put_notification(
            tenant_id=tenant_id, actor_id=actor_id, recipient=recipient,
            subject=subject, body=body, subject_kind=subject_kind,
            subject_id=subject_id, state=SENT,
            transport=name, receipt=str(receipt),
            reason=f"Delivered by {name}.")


def check_subject(kind: str, subject_id: str) -> None:
    if kind not in SUBJECT_KINDS:
        raise CollaborationError(
            "UNKNOWN_SUBJECT_KIND",
            f"A comment or share is about one of {', '.join(SUBJECT_KINDS)}.")
    if not subject_id.strip():
        raise CollaborationError("MISSING_SUBJECT",
                                 "A subject id is required.")


def summarize_answer(response: dict[str, Any] | None) -> dict[str, Any]:
    """What a saved analysis keeps: the answer and where to verify it.

    The stored copy is the PUBLISHED response, not a re-rendering of it, so a
    saved analysis can never drift from what the reader saw.
    """
    body = response or {}
    return {
        "narrative": str(body.get("narrative") or ""),
        "disposition": str(body.get("disposition") or ""),
        "executed": bool(body.get("executed")),
        "numeric_claims": list(body.get("numeric_claims") or []),
        "tables": list(body.get("tables") or []),
        "charts": list(body.get("charts") or []),
        "limitations": list(body.get("limitations") or []),
        "artifact_ids": sorted({
            str(t.get("artifact_id")) for t in
            (list(body.get("tables") or []) + list(body.get("charts") or []))
            if t.get("artifact_id")}),
    }


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = ["CLOSED", "CollaborationError", "FAILED", "INVESTIGATION",
           "IN_APP", "IN_REVIEW", "InAppTransport", "MAX_BODY", "Notifier",
           "OPEN", "RECORDED",
           "REFUSED", "RecipientPolicy", "SAVED_ANALYSIS", "SENT",
           "STATUSES", "SUBJECT_KINDS", "Transport", "as_json",
           "check_subject", "is_in_app", "summarize_answer"]
