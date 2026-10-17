"""
Why-lines for the forecast ("Rising because heart rate jumped 18 bpm in 10 min ...").

Session H built explain(signals, minute, predictor) in ml/explain/ (branch
claude/ml-explain-071c08). Until that branch is merged, this adapter is switched
off and every "why" is None. The why-line explains; it never decides anything.

explain() takes 0.1 to 0.3 s, too slow to run inside the pipeline every minute
for every worker. So it runs on one background thread, on its OWN copy of the
model (it computes gradients, which must not touch the live model), and only
the newest request per worker is kept: if a worker's minute is still waiting
when the next one comes in, the old one is dropped. Results come back to the
event loop through on_result(worker_id, minute, result).
"""

import logging
import threading

log = logging.getLogger(__name__)


def load_explain():
    """Session H's explain(), or None if ml/explain isn't merged yet."""
    try:
        from ml.explain.explain import explain
        return explain
    except ImportError:
        return None


class WhyExplainer:
    def __init__(self, checkpoint_path, on_result, explain_fn=None, predictor=None):
        """
        checkpoint_path: the forecaster file the live pipeline loaded (a private copy is loaded from it).
        on_result(worker_id, minute, result_dict): called on the thread; the caller hops back to the loop.
        explain_fn / predictor: for tests.
        """
        self.explain = explain_fn or load_explain()
        self.on_result = on_result
        self.predictor = predictor
        if self.explain is not None and self.predictor is None:
            from backend.models.forecaster import ForecasterPredictor
            self.predictor = ForecasterPredictor.load(checkpoint_path)
        self.pending: dict[str, tuple] = {}            # worker -> (signals, minute), newest only
        self.cond = threading.Condition()
        self.thread = None

    @property
    def enabled(self) -> bool:
        return self.explain is not None

    def request(self, worker_id: str, signals: dict, minute: int) -> None:
        """Ask for a why-line for this worker's minute. Never blocks."""
        if not self.enabled:
            return
        with self.cond:
            self.pending[worker_id] = (signals, minute)
            self.cond.notify()
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, daemon=True, name="why-explainer")
            self.thread.start()

    def _work(self) -> None:
        while True:
            with self.cond:
                while not self.pending:
                    self.cond.wait()
                worker_id, (signals, minute) = self.pending.popitem()
            try:
                result = self.explain(signals, minute, self.predictor)
            except Exception as e:                    # an explanation bug must never touch alerts
                log.warning("explain failed for %s minute %d: %s", worker_id, minute, e)
                continue
            self.on_result(worker_id, minute, result)
