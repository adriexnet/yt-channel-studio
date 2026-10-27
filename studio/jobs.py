"""Background jobs: a production keeps running when the browser tab closes or the tunnel drops, and any
page that opens the Control Center again can follow it. This module is NOT reloaded by studio.reload(),
so a job started before a `git pull` is still found afterwards."""
import threading, time, traceback

_STATE = {"current": None}
_LOCK = threading.Lock()


class Job:
    """Runs a snapshot generator (produce_reel, reassemble_reel) in a thread; keeps the latest snapshot."""

    def __init__(self, kind, label, generator_factory):
        self.kind, self.label = kind, label
        self.started = time.time()
        self.snapshot, self.updates = None, 0
        self.finished = self.cancelled = False
        self._factory = generator_factory
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"studio-{kind}")

    def _run(self):
        generator = self._factory()
        try:
            for snapshot in generator:
                self._publish(snapshot)
                if self.cancelled:
                    generator.close()          # stops at the next step; the step already running finishes alone
                    self._publish(dict(snapshot, failed=True, done=False, stage="Detenido por ti",
                                       log=snapshot.get("log", "") + "\n⏹ Detenido por ti."))
                    break
        except Exception:
            last = dict(self.snapshot or {})
            self._publish(dict(last, failed=True, stage="Error interno",
                               log=last.get("log", "") + "\n❌ Error interno:\n" + traceback.format_exc()[-800:]))
        finally:
            self.finished = True
            self.updates += 1

    def _publish(self, snapshot):
        self.snapshot = snapshot
        self.updates += 1

    @property
    def running(self):
        return not self.finished

    def cancel(self):
        self.cancelled = True


def start(kind, label, generator_factory):
    """Starts a job; refuses while another one runs (one GPU, one production at a time)."""
    with _LOCK:
        current = _STATE["current"]
        if current is not None and current.running:
            raise RuntimeError(f"Ya hay un trabajo en curso ({current.label}). Espera a que termine o detenlo.")
        job = Job(kind, label, generator_factory)
        _STATE["current"] = job
        job.thread.start()
        return job


def current():
    """The running job, or the last one of this session (finished), or None."""
    return _STATE["current"]


def follow(job, interval=0.5):
    """Yields the job's snapshots as they change, until it finishes. Closing this generator (a browser that
    disconnects) does not affect the job."""
    seen = -1
    while True:
        updates, finished = job.updates, job.finished
        if updates != seen and job.snapshot is not None:
            seen = updates
            yield job.snapshot
        if finished and seen == job.updates:
            return
        time.sleep(interval)
