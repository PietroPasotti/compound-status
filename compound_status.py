import json
import typing
from collections import Counter
from logging import CRITICAL, DEBUG, ERROR, INFO, WARNING, getLogger
from typing import Callable, Dict, List, Literal, Optional, Tuple, TypedDict

from ops.charm import CharmBase
from ops.framework import Handle, Object, StoredStateData
from ops.model import StatusBase, UnknownStatus, WaitingStatus
from ops.storage import NoSnapshotError

log = getLogger("compound-status")

StatusName = Literal["blocked", "waiting", "maintenance", "unknown", "active"]
# statuses are sorted worst-to-best
STATUSES = ("blocked", "waiting", "maintenance", "active", "unknown")
STATUS_PRIORITIES: Dict[str, int] = {val: i for i, val in enumerate(STATUSES)}


class _StatusDict(TypedDict):
    status: StatusName
    message: str
    label: str
    priority: str


def _priority_key(status: "Status") -> Tuple[int, int]:
    """Return the priority key, used to sort statuses."""
    return STATUS_PRIORITIES[status.get_name()], status.get_priority()


class Status:
    """Represents a status."""

    def __repr__(self):
        return "<Status {} ({}): {}>".format(
            self._status.name, self._label, self.get_message()
        )

    def __init__(self, label: str, priority: int = 0):
        self._logger = log.getChild(label)
        self._status = UnknownStatus()
        # this label mustn't be changed after adding to a pool,
        # because it ideally should remain in sync with
        # the pool's identifier for the status.
        self._label = label
        self._priority = priority

    def critical(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `critical`."""
        self._logger.log(CRITICAL, msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `error`."""
        self._logger.log(ERROR, msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `warning`."""
        self._logger.log(WARNING, msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `info`."""
        self._logger.log(INFO, msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `debug`."""
        self._logger.log(DEBUG, msg, *args, **kwargs)

    def get_label(self) -> str:
        """Get the label of this status."""
        return self._label

    def get_priority(self) -> int:
        """Get the priority of this status."""
        return self._priority

    def get_status(self) -> StatusBase:
        """Get the actual status."""
        return self._status

    def get_message(self) -> str:
        """
        Get the status message consistently.

        Useful because UnknownStatus has no message attribute.
        """
        if self._status.name == "unknown":
            return ""
        return self._status.message

    def get_name(self) -> StatusName:
        """Get the StatusName of the status."""
        return typing.cast(StatusName, self._status.name)

    def set(self, status: StatusBase):  # noqa: A003
        """Set the status and return it."""
        self._status = status
        return self

    def unset(self):
        """
        Reset the status back to the initial status.

        (UnknownStatus)
        """
        self._status = UnknownStatus()

    def _to_dict(self) -> _StatusDict:
        """Serialize Status for storage."""
        dct: _StatusDict = {
            "status": typing.cast(StatusName, self._status.name),
            "message": self.get_message(),
            "label": self._label,
            "priority": str(self._priority),
        }
        return dct

    def __hash__(self):
        return hash((self._label, self._status.name, self.get_message()))

    def __eq__(self, other: "Status") -> bool:
        return hash(self) == hash(other)


def summarize_worst_only(statuses: List[Status], _) -> str:
    """
    Provide a worst-only view of the current statuses in the pool.

    e.g. if the status pool has three statuses:
        relation_1 = ActiveStatus('✅')
        relation_2 = WaitingStatus('𝌗: foo')
        workload = BlockedStatus('💔')

    The Summary clobbered status will have as message::
        (workload) 💔
    """
    if not statuses:
        return ""
    worst = sorted(statuses, key=_priority_key)[0]
    return f"({worst.get_label()}) {worst.get_message()}"


def summarize_worst_first(statuses: List[Status], skip_unknown: bool) -> str:
    """
    Provide a worst-first, summarized view of all statuses.

    e.g. if the status pool has three statuses:
        relation_1 = ActiveStatus('✅')
        relation_2 = WaitingStatus('𝌗: foo')
        workload = BlockedStatus('💔')

    The Summary clobbered status will have as message:
        (workload:blocked) 💔; (relation_1:active) ✅; (rel2:waiting) 𝌗: foo
    """
    msgs = []
    for status in sorted(statuses, key=_priority_key):
        if skip_unknown and status.get_name() == "unknown":
            continue
        msgs.append(
            "({0}:{1}) {2}".format(
                status.get_label(),
                status.get_name(),
                status.get_message(),
            )
        )
    return "; ".join(msgs)


def summarize_condensed(statuses: List[Status], skip_unknown: bool) -> str:
    """
    Provide a very compact, summarized view of all statuses.

    e.g. if the status pool has three statuses:
        relation_1 = ActiveStatus('✅')
        relation_2 = WaitingStatus('✅')
        relation_3 = BlockedStatus('✅')
        relation_... = ???
        relation_N = ActiveStatus('✅')
        relation_2 = WaitingStatus('𝌗: foo')
        workload = BlockedStatus('💔')

    The Condensed clobbered status will have as message:
        15 blocked; 43 waiting; 12 active

    If all are active the message will be empty.
    Priority will be ignored.
    """
    ctr = Counter(s.get_name() for s in statuses)

    if set(ctr) == {
        "active",
    }:  # only active statuses
        return ""

    msgs = []
    for status, count in sorted(
        ctr.items(), key=lambda x: STATUS_PRIORITIES[x[0]]
    ):
        if skip_unknown and status == "unknown":
            continue
        msgs.append("{0} {1}".format(count, status))
    return "; ".join(msgs)


class StatusPool(Object):
    """Represents the pool of statuses available to an Object."""

    def __init__(
        self,
        charm: CharmBase,
        key: str = "status_pool",
        summarizer: Callable[[List[Status], bool], str] = summarize_worst_only,
        # whether the status should be committed automatically when the hook exits
        auto_commit: bool = True,
        # whether unknown statuses should be omitted from the master message
        skip_unknown: bool = False,
    ):
        super().__init__(charm, key)
        self._pool = {}  # type: Dict[str, Status]
        self._summarizer_func = summarizer
        self._skip_unknown = skip_unknown
        self._charm = charm

        stored_handle = Handle(
            self, StoredStateData.handle_kind, "_status_pool_state"
        )
        self.stored_handle = stored_handle
        charm.framework.register_type(
            StoredStateData, self, StoredStateData.handle_kind
        )
        try:
            self._state = charm.framework.load_snapshot(stored_handle)
        except NoSnapshotError:
            self._state = StoredStateData(self, "_status_pool_state")
            self._state["statuses"] = "{}"

        self._load_from_stored_state()
        if auto_commit:
            charm.framework.observe(
                charm.framework.on.commit, self._on_autocommit  # type: ignore
            )

    def get(self, name: str) -> Optional[Status]:
        """Retrieve a status by name."""
        return self._pool.get(name)

    def set_status(self, name: str, status: StatusBase):
        """Set a status by name."""
        self._pool[name].set(status)

    def __getattr__(self, name: str) -> Status:
        """
        Light magic for syntax sugar to retrieve a status.

        Allows things like this:

        ```
        assert pool.workload.get_name() == "active"
        pool.workload.debug("logging a debug message")
        ```
        """
        if name in self._pool:
            return self._pool[name]
        raise AttributeError(f"This pool has no status labelled {repr(name)}")

    def __setattr__(self, name: str, value):
        """
        Light magic for syntax sugar to set a status.

        Allows things like this:

        ```
        pool.workload == ActiveStatus(":)")
        pool.relation_1 == WaitingStatus("relation_1 is mandatory")

        # equivalent to
        pool.set_status("relation_1", WaitingStatus("relation_1 is mandatory"))
        ```
        """
        # heuristic to decide whether to access a status from the pool,
        # or to set a standard attribute on the class.
        # There may be a neater method; we want to use the principle of least surprise here.
        if isinstance(value, StatusBase):
            self.set_status(name, value)
        else:
            super().__setattr__(name, value)

    def define_status(self, name: str, priority: int = 0) -> Status:
        """
        Define a status in the pool with a name and optional priority.

        Return the status object that was defined and added to the pool.

        This is the only official way to build and get a Status object,
        ensuring that adding/defining statuses plays well with reconstituted
        statuses from stored state.

        Note that if no priorities are set on the status,
        the priority defaults to 0.
        Ties are broken by insertion order into the pool,
        so if no statuses are set, the priority order will
        effectively be the order they were defined with this function.
        """
        status = self._pool.get(name)
        # basically, update the priority if it already exists,
        # but don't reset the status to unknown.
        # Note that this logic also means that calling define_status(x)
        # always will return the same Status object.
        if status is not None:
            status._priority = priority
        else:
            status = Status(name, priority=priority)
            self._pool[name] = status
        return status

    def remove_status(self, label: str):
        """Remove the status and forget about it."""
        self._pool.pop(label)

    def _load_from_stored_state(self):
        """Retrieve stored state snapshot of current statuses."""
        stored_statuses = typing.cast(
            Dict[str, _StatusDict],
            json.loads(typing.cast(str, self._state["statuses"])),
        )
        for name, status_dict in stored_statuses.items():
            status = self.define_status(
                status_dict["label"], int(status_dict["priority"])
            )
            status.set(
                StatusBase.from_name(
                    status_dict["status"],
                    status_dict["message"],
                )
            )

    def _on_autocommit(self, _event):
        log.debug("master status auto-committed")
        self.commit()

    def commit(self):
        """Store the current state and sync with juju."""
        self._charm.unit.status = self.summarize()
        self._state["statuses"] = json.dumps(
            {name: status._to_dict() for name, status in self._pool.items()}
        )
        self._charm.framework.save_snapshot(self._state)  # type: ignore
        self._charm.framework._storage.commit()

    def unset(self):
        """Unsets status for all statuses in the pool."""
        for status in self._pool.values():
            status.unset()

    def summarize(self) -> StatusBase:
        """Cast to an ops.model.StatusBase instance by summarizing statuses and messages."""
        if not self._pool:
            return UnknownStatus()

        worst_status = sorted(self._pool.values(), key=_priority_key)[0]
        if worst_status.get_name() == "unknown":
            return WaitingStatus("no status set yet")

        return StatusBase.from_name(
            worst_status.get_name(),
            self._summarizer_func(
                list(self._pool.values()), self._skip_unknown
            ),
        )

    def __repr__(self):
        if not self._pool:
            return "<StatusPool: empty>"
        return str(self.summarize())
