"""Track bounded background jobs belonging to a runtime session."""

import shlex
from uuid import uuid4

from gemma_agents.security.sandbox import SandboxRunner


class ProcessTools:
    """Processes outlive a turn, but are owned by this runtime and session."""

    def __init__(self, runner: SandboxRunner):
        """Bind the sandbox runner and initialize the session's background job registry.
        """
        self.runner = runner
        self.jobs = {}

    def start_process(self, command: str) -> str:
        """Start an approved background command, return its process identifier.

        Args:
            command: Program and arguments to run in the sandbox.
        """
        if sum(job.process.poll() is None for job in self.jobs.values()) >= 4:
            raise ValueError("Maximum de quatre processus actifs par session.")
        process_id = uuid4().hex[:12]
        self.jobs[process_id] = self.runner.start(shlex.split(command))
        return process_id

    def process_status(self, process_id: str) -> str:
        """Read process status and the last 40000 output bytes.

        Args:
            process_id: Identifier returned by start_process.
        """
        job = self.jobs[process_id]
        code = job.process.poll()
        if code is not None:
            job.reader.join(timeout=1)
        status = "running" if code is None else f"exited ({code})"
        return f"{status}\n{job.text()}"

    def stop_process(self, process_id: str) -> str:
        """Stop a process group belonging to this session.

        Args:
            process_id: Identifier returned by start_process.
        """
        self.jobs[process_id].stop()
        return "Processus arrêté."

    def close(self) -> None:
        """Stop session jobs and remove them from the shared runner's process registry.
        """
        for job in self.jobs.values():
            job.stop()
            if job in self.runner.processes:
                self.runner.processes.remove(job)
        self.jobs.clear()
