"""Deterministic state machine for the Kitchen Rush micro-game."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Dish = Literal["burger", "soup", "salad"]
Step = Literal["prep", "chop", "cook", "plate"]

RECIPES: dict[Dish, list[Step]] = {
    "burger": ["prep", "cook", "plate"],
    "soup": ["prep", "cook", "plate"],
    "salad": ["prep", "chop", "plate"],
}

STEP_SECONDS: dict[Step, int] = {
    "prep": 8,
    "chop": 4,
    "cook": 15,
    "plate": 5,
}

CHECK_SECONDS = 2
SERVE_SECONDS = 3
MISTAKE_SECONDS = 5
MAX_ACTIVE_COOKS = 2


@dataclass(frozen=True)
class KitchenTicket:
    id: str
    dishes: tuple[Dish, ...]
    arrival_sec: int
    deadline_sec: int
    priority: Dish | None = None


@dataclass
class KitchenEvent:
    elapsed_sec: int
    type: str
    message: str
    ok: bool = True
    detail: dict[str, Any] = field(default_factory=dict)


DEFAULT_TICKETS: tuple[KitchenTicket, ...] = (
    KitchenTicket(
        id="T1",
        dishes=("soup", "salad"),
        arrival_sec=0,
        deadline_sec=58,
        priority="soup",
    ),
    KitchenTicket(
        id="T2",
        dishes=("burger", "soup"),
        arrival_sec=12,
        deadline_sec=100,
        priority="burger",
    ),
    KitchenTicket(
        id="T3",
        dishes=("salad",),
        arrival_sec=30,
        deadline_sec=104,
        priority=None,
    ),
)


@dataclass
class KitchenRushGame:
    tickets: tuple[KitchenTicket, ...] = DEFAULT_TICKETS
    max_seconds: int = 105
    elapsed_sec: int = 0
    steps: dict[str, dict[Step, bool]] = field(default_factory=dict)
    served: dict[str, bool] = field(default_factory=dict)
    active_cooks: dict[str, int] = field(default_factory=dict)
    announced_tickets: set[str] = field(default_factory=set)
    mistakes: list[str] = field(default_factory=list)
    unnecessary_tool_calls: int = 0
    missed_deadlines: list[str] = field(default_factory=list)
    voice_updates: int = 0
    answered_manager_questions: int = 0
    manager_questions: int = 0
    events: list[KitchenEvent] = field(default_factory=list)
    ended: bool = False
    loss_reason: str | None = None

    def __post_init__(self) -> None:
        self.steps = {
            self.item_id(ticket.id, dish): {
                "prep": False,
                "chop": False,
                "cook": False,
                "plate": False,
            }
            for ticket in self.tickets
            for dish in ticket.dishes
        }
        self.served = {
            self.item_id(ticket.id, dish): False for ticket in self.tickets for dish in ticket.dishes
        }
        self.active_cooks = {}
        self._record("game_start", "Kitchen Rush started.")
        self._update_arrivals()

    @staticmethod
    def item_id(ticket_id: str, dish: str) -> str:
        return f"{ticket_id.upper()}:{dish.lower()}"

    def check_kitchen(self) -> dict[str, Any]:
        if self.ended:
            return self._result(False, "Game is already over.")
        self._advance(CHECK_SECONDS)
        self._record("check_kitchen", "Checked kitchen state.")
        return self._result(True, self.summary())

    def start_step(self, ticket: str, dish: str, step: str) -> dict[str, Any]:
        if self.ended:
            return self._result(False, "Game is already over.")

        normalized_ticket = ticket.upper()
        normalized_dish = dish.lower()
        normalized_step = step.lower()
        item = self.item_id(normalized_ticket, normalized_dish)
        validation_error = self._validate_item(normalized_ticket, normalized_dish)
        if validation_error:
            return self._mistake(validation_error, end_game="not on any ticket" in validation_error)
        if normalized_step not in ("prep", "chop", "cook", "plate"):
            return self._mistake(f"Unknown step: {step}")

        recipe = RECIPES[normalized_dish]  # type: ignore[index]
        if normalized_step not in recipe:
            return self._mistake(f"{normalized_ticket} {normalized_dish} does not require {step}.")
        if self.served[item]:
            return self._unnecessary(f"{normalized_ticket} {normalized_dish} is already served.")
        if self.steps[item][normalized_step]:  # type: ignore[index]
            return self._unnecessary(
                f"{normalized_ticket} {normalized_dish} {normalized_step} is already complete."
            )
        if normalized_step == "cook" and item in self.active_cooks:
            return self._unnecessary(f"{normalized_ticket} {normalized_dish} is already cooking.")
        if normalized_step == "cook" and len(self.active_cooks) >= MAX_ACTIVE_COOKS:
            return self._mistake(
                f"No burner available for {normalized_ticket} {normalized_dish}; "
                f"{len(self.active_cooks)}/{MAX_ACTIVE_COOKS} burners are busy."
            )

        required_before = recipe[: recipe.index(normalized_step)]  # type: ignore[arg-type]
        missing = [prior for prior in required_before if not self.steps[item][prior]]
        if missing:
            return self._mistake(
                f"Cannot {normalized_step} {normalized_ticket} {normalized_dish} before "
                f"{', '.join(missing)}."
            )
        if normalized_step == "plate" and item in self.active_cooks:
            return self._mistake(
                f"Cannot plate {normalized_ticket} {normalized_dish} while it is still cooking."
            )

        if normalized_step == "cook":
            self._advance(2)
            ready_at = self.elapsed_sec + STEP_SECONDS["cook"]
            self.active_cooks[item] = ready_at
            self._record(
                "start_step",
                f"{normalized_ticket} {normalized_dish} cooking, ready at {ready_at}s.",
                detail={"ticket": normalized_ticket, "dish": normalized_dish, "step": normalized_step},
            )
            return self._result(
                True,
                f"{normalized_ticket} {normalized_dish} cooking, ready at {ready_at}s. "
                f"{self.summary()}",
            )

        self._advance(STEP_SECONDS[normalized_step])  # type: ignore[index]
        self.steps[item][normalized_step] = True  # type: ignore[index]
        self._record(
            "start_step",
            f"{normalized_ticket} {normalized_dish} {normalized_step} complete.",
            detail={"ticket": normalized_ticket, "dish": normalized_dish, "step": normalized_step},
        )
        return self._result(
            True,
            f"{normalized_ticket} {normalized_dish} {normalized_step} complete. {self.summary()}",
        )

    def serve_dish(self, ticket: str, dish: str) -> dict[str, Any]:
        if self.ended:
            return self._result(False, "Game is already over.")

        normalized_ticket = ticket.upper()
        normalized_dish = dish.lower()
        item = self.item_id(normalized_ticket, normalized_dish)
        validation_error = self._validate_item(normalized_ticket, normalized_dish)
        if validation_error:
            return self._mistake(validation_error, end_game="not on any ticket" in validation_error)
        if self.served[item]:
            return self._unnecessary(f"{normalized_ticket} {normalized_dish} is already served.")
        if not self.steps[item]["plate"]:
            return self._mistake(f"Cannot serve {normalized_ticket} {normalized_dish} before it is plated.")

        self._advance(SERVE_SECONDS)
        self.served[item] = True
        self._record(
            "serve_dish",
            f"{normalized_ticket} {normalized_dish} served.",
            detail={"ticket": normalized_ticket, "dish": normalized_dish},
        )
        self._maybe_finish()
        return self._result(True, f"{normalized_ticket} {normalized_dish} served. {self.summary()}")

    def record_voice(self, speech: str, manager_question_pending: bool) -> None:
        if speech.strip():
            self.voice_updates += 1
            if manager_question_pending:
                self.answered_manager_questions += 1

    def note_manager_question(self) -> None:
        self.manager_questions += 1

    def summary(self) -> str:
        ticket_summaries = []
        for ticket in self.tickets:
            if ticket.arrival_sec > self.elapsed_sec:
                ticket_summaries.append(
                    f"{ticket.id} arrives at {ticket.arrival_sec}s, due {ticket.deadline_sec}s"
                )
                continue
            dish_summaries = []
            for dish in ticket.dishes:
                item = self.item_id(ticket.id, dish)
                if item in self.active_cooks:
                    status = f"cooking until {self.active_cooks[item]}s"
                elif self.served[item]:
                    status = "served"
                else:
                    next_step = next((step for step in RECIPES[dish] if not self.steps[item][step]), None)
                    if next_step is None:
                        status = "ready to serve"
                    elif next_step == "prep":
                        status = "untouched"
                    elif next_step == "chop":
                        status = "prepped"
                    elif next_step == "cook":
                        status = "prepped"
                    elif next_step == "plate":
                        status = "ready to plate"
                    else:
                        status = f"ready for {next_step}"
                dish_summaries.append(f"{dish}: {status}")
            priority = f", priority {ticket.priority}" if ticket.priority else ""
            ticket_summaries.append(
                f"{ticket.id} due {ticket.deadline_sec}s{priority} [{'; '.join(dish_summaries)}]"
            )
        return (
            f"{self.elapsed_sec}s elapsed. "
            f"{' | '.join(ticket_summaries)}. "
            f"Burners: {len(self.active_cooks)}/{MAX_ACTIVE_COOKS} busy. "
            f"Mistakes: {len(self.mistakes)}. "
            f"Ready actions: {self.ready_actions_summary()}."
        )

    def ready_actions_summary(self) -> str:
        ready_actions = self.ready_actions()
        return ", ".join(ready_actions[:6]) if ready_actions else "none"

    def ready_actions(self) -> list[str]:
        ready_actions = []
        for ticket in sorted(self.tickets, key=lambda item: (item.deadline_sec, item.arrival_sec)):
            if ticket.arrival_sec > self.elapsed_sec or self._ticket_complete(ticket):
                continue
            for dish in ticket.dishes:
                item = self.item_id(ticket.id, dish)
                if self.served[item] or item in self.active_cooks:
                    continue
                next_step = next((step for step in RECIPES[dish] if not self.steps[item][step]), None)
                if next_step == "cook" and len(self.active_cooks) >= MAX_ACTIVE_COOKS:
                    continue
                if next_step:
                    ready_actions.append(f"start_step({ticket.id},{dish},{next_step})")
                else:
                    ready_actions.append(f"serve_dish({ticket.id},{dish})")
        return ready_actions

    def new_ticket_messages(self, from_event_index: int) -> tuple[list[str], int]:
        messages = [
            event.message
            for event in self.events[from_event_index:]
            if event.type == "ticket_arrived"
        ]
        return messages, len(self.events)

    def final_report(self) -> dict[str, Any]:
        self._maybe_timeout()
        self._maybe_deadline_miss()
        all_served = all(self.served[item] for item in self.served)
        won = (
            all_served
            and self.elapsed_sec <= self.max_seconds
            and not self.missed_deadlines
            and len(self.mistakes) <= 1
        )
        score = (
            140
            - self.elapsed_sec
            - 25 * len(self.missed_deadlines)
            - 15 * len(self.mistakes)
            - 10 * self.unnecessary_tool_calls
            + (15 if all_served else 0)
            + min(5, self.voice_updates)
        )
        return {
            "won": won,
            "score": max(0, score),
            "elapsed_sec": self.elapsed_sec,
            "max_seconds": self.max_seconds,
            "tickets": [ticket.__dict__ for ticket in self.tickets],
            "served": self.served,
            "active_cooks": self.active_cooks,
            "missed_deadline_count": len(self.missed_deadlines),
            "missed_deadlines": self.missed_deadlines,
            "mistake_count": len(self.mistakes),
            "mistakes": self.mistakes,
            "unnecessary_tool_calls": self.unnecessary_tool_calls,
            "voice_updates": self.voice_updates,
            "manager_questions": self.manager_questions,
            "answered_manager_questions": self.answered_manager_questions,
            "manager_question_answered_rate": (
                self.answered_manager_questions / self.manager_questions
                if self.manager_questions
                else 1.0
            ),
            "loss_reason": None if won else self.loss_reason or "objective_not_completed",
            "events": [event.__dict__ for event in self.events],
        }

    def _validate_item(self, ticket_id: str, dish: str) -> str | None:
        ticket = self._ticket(ticket_id)
        if not ticket:
            return f"{ticket_id} is not on any ticket."
        if ticket.arrival_sec > self.elapsed_sec:
            return f"{ticket_id} has not arrived yet."
        if dish not in RECIPES:
            return f"Unknown dish: {dish}"
        if dish not in ticket.dishes:
            return f"{ticket_id} {dish} is not on this ticket."
        return None

    def _ticket(self, ticket_id: str) -> KitchenTicket | None:
        for ticket in self.tickets:
            if ticket.id == ticket_id:
                return ticket
        return None

    def _ticket_complete(self, ticket: KitchenTicket) -> bool:
        return all(self.served[self.item_id(ticket.id, dish)] for dish in ticket.dishes)

    def _advance(self, seconds: int) -> None:
        self.elapsed_sec += seconds
        self._update_arrivals()
        self._update_cooks()
        self._maybe_deadline_miss()
        self._maybe_timeout()

    def _update_arrivals(self) -> None:
        for ticket in self.tickets:
            if ticket.arrival_sec <= self.elapsed_sec and ticket.id not in self.announced_tickets:
                self.announced_tickets.add(ticket.id)
                priority = f" Priority: {ticket.priority}." if ticket.priority else ""
                self._record(
                    "ticket_arrived",
                    f"New ticket {ticket.id}: {', '.join(ticket.dishes)}. "
                    f"Due at {ticket.deadline_sec}s.{priority}",
                    detail=ticket.__dict__,
                )

    def _update_cooks(self) -> None:
        ready = [item for item, ready_at in self.active_cooks.items() if self.elapsed_sec >= ready_at]
        for item in ready:
            del self.active_cooks[item]
            self.steps[item]["cook"] = True
            ticket_id, dish = item.split(":", 1)
            self._record(
                "cook_complete",
                f"{ticket_id} {dish} finished cooking.",
                detail={"ticket": ticket_id, "dish": dish},
            )

    def _maybe_deadline_miss(self) -> None:
        for ticket in self.tickets:
            if (
                ticket.arrival_sec <= self.elapsed_sec
                and self.elapsed_sec > ticket.deadline_sec
                and not self._ticket_complete(ticket)
                and ticket.id not in self.missed_deadlines
            ):
                self.missed_deadlines.append(ticket.id)
                self.ended = True
                self.loss_reason = f"{ticket.id} missed deadline"
                self._record("deadline_missed", self.loss_reason, ok=False)

    def _maybe_timeout(self) -> None:
        if self.elapsed_sec > self.max_seconds and not self.ended:
            self.ended = True
            self.loss_reason = "time_limit_exceeded"
            self._record("time_limit", "Time limit exceeded.", ok=False)

    def _maybe_finish(self) -> None:
        if all(self.served[item] for item in self.served):
            self.ended = True
            self._record("game_complete", "All tickets served.")

    def _mistake(self, message: str, *, end_game: bool = False) -> dict[str, Any]:
        self._advance(MISTAKE_SECONDS)
        self.mistakes.append(message)
        if end_game:
            self.ended = True
            self.loss_reason = message
        self._record("mistake", message, ok=False)
        return self._result(False, f"{message} {self.summary()}")

    def _unnecessary(self, message: str) -> dict[str, Any]:
        self._advance(MISTAKE_SECONDS)
        self.unnecessary_tool_calls += 1
        self._record("unnecessary_tool_call", message, ok=False)
        return self._result(False, f"{message} {self.summary()}")

    def _record(
        self,
        event_type: str,
        message: str,
        *,
        ok: bool = True,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            KitchenEvent(
                elapsed_sec=self.elapsed_sec,
                type=event_type,
                message=message,
                ok=ok,
                detail=detail or {},
            )
        )

    def _result(self, ok: bool, message: str) -> dict[str, Any]:
        return {
            "ok": ok,
            "elapsed_sec": self.elapsed_sec,
            "ended": self.ended,
            "message": message,
            "state_summary": self.summary(),
        }
