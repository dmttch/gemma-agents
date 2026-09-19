"""Synchronize user steering, pauses, cancellation, and run completion."""

import threading


class RunCancelled(KeyboardInterrupt):
    """Signal cooperative cancellation through the KeyboardInterrupt cleanup path."""

    pass


class RunControl:
    """Cooperative control: pause between actions, cancel during streamed output."""

    def __init__(self):
        """Initialize the shared condition, cancellation event, and steering queue."""
        self.condition = threading.Condition()
        self.cancelled = threading.Event()
        self.paused = False
        self.messages = []
        self.closed = False

    def steer(self, message: str) -> None:
        """Queue a bounded user message, rejecting closed runs or a full queue."""
        if not message.strip() or len(message) > 16000:
            raise ValueError("Précision vide ou trop longue.")
        with self.condition:
            if self.closed:
                raise ValueError("Tour terminé ; envoie un nouveau message.")
            if len(self.messages) >= 10:
                raise ValueError("Dix précisions sont déjà en attente.")
            if sum(len(m) + 2 for m in self.messages) + len(message) > 16000:
                raise ValueError("Les précisions en attente dépassent "
                                 "16000 caractères.")
            self.messages.append(message)
            self.condition.notify_all()

    def pause(self, paused: bool = True) -> None:
        """Set or clear the pause flag and wake threads waiting at an action boundary.
        """
        with self.condition:
            self.paused = paused
            self.condition.notify_all()

    def cancel(self) -> None:
        """Request cancellation and wake paused workers so they can exit."""
        with self.condition:
            self.cancelled.set()
            self.condition.notify_all()

    def check_cancelled(self) -> None:
        """Raise RunCancelled when the user has requested cancellation."""
        if self.cancelled.is_set():
            raise RunCancelled("Exécution annulée par l'utilisateur.")

    def boundary(self) -> None:
        """Wait while paused, then raise if cancelled before the next action."""
        with self.condition:
            while self.paused and not self.cancelled.is_set():
                self.condition.wait(0.2)
            self.check_cancelled()

    def pending(self) -> bool:
        """Report whether steering messages are waiting under the shared lock."""
        with self.condition:
            return bool(self.messages)

    def drain(self) -> list[str]:
        """Atomically detach and return all queued steering messages."""
        with self.condition:
            messages, self.messages = self.messages, []
            return messages

    def finish_if_empty(self) -> bool:
        """Close the run only if no steering is queued, atomically with that check."""
        with self.condition:
            # Share the steering lock so a message cannot arrive between the
            # empty-queue check and closing the run to further input.
            if self.messages:
                return False
            self.closed = True
            return True
