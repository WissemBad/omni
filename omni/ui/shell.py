"""What the API needs from the window that hosts it. The desktop shell (omni/app.py) fills the callbacks in; in the
browser, with the CLI or under test they stay empty and the endpoints answer that there is nothing to do."""
from __future__ import annotations


class Shell:
    def __init__(self):
        self.focus = None           # () -> None: bring the window to the front
        self.job_finished = None    # (job) -> None: tell the user a job ended while the window was not in front

    def request_focus(self) -> bool:
        if self.focus is None:
            return False
        self.focus()
        return True

    def notify_job(self, job: dict) -> None:
        if self.job_finished is not None:
            self.job_finished(job)
