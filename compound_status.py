import json
import typing
from collections import Counter
from logging import CRITICAL, DEBUG, ERROR, INFO, WARNING, getLogger
from typing import (
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    TypedDict,
    Union,
)

from ops.charm import CharmBase
from ops.framework import Handle, Object, StoredStateData
from ops.model import StatusBase, UnknownStatus
from ops.storage import NoSnapshotError

log = getLogger("compound-status")

StatusName = Literal["blocked", "waiting", "maintenance", "unknown", "active"]
# statuses are sorted worst-to-best
STATUSES = ("blocked", "waiting", "maintenance", "active", "unknown")
STATUS_PRIORITIES: Dict[str, int] = {val: i for i, val in enumerate(STATUSES)}


class _StatusDict(TypedDict):
    status: StatusName
    message: str
    name: str


Number = Union[float, int]


def _priority_key(status: "Status") -> Tuple[int, Number]:
    """Return the priority key, used to sort statuses."""
    return STATUS_PRIORITIES[status.status.name], status.priority


class Status:
    """Represents a status."""

    def __repr__(self):
        return "<Status {} ({}): {}>".format(
            self.status.name, self.name, self.get_message()
        )

    def __init__(self, name: str, priority: Number = 0):
        self._logger = log.getChild(name)
        self.status = UnknownStatus()
        # this name shouldn't be changed after adding to a pool,
        # because it ideally should remain in sync with
        # the pool's identifier for the status.
        self.name = name
        self.priority = priority

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

    def get_message(self) -> str:
        """
        Get the status message consistently.

        Useful because UnknownStatus has no message attribute.
        """
        if self.status.name == "unknown":
            return ""
        return self.status.message

    def get_status_name(self) -> StatusName:
        """Get the StatusName of the status."""
        return typing.cast(StatusName, self.status.name)

    def _serialize(self) -> _StatusDict:
        """Serialize Status for storage."""
        dct: _StatusDict = {
            "status": typing.cast(StatusName, self.status.name),
            "message": self.get_message(),
            "name": self.name,
        }
        return dct

    def _deserialize(self, dct: _StatusDict):
        """Restore Status from stored state."""
        self.status = StatusBase.from_name(
            dct.get("status", "unknown"), dct.get("message", "")
        )
        self.name = dct.get("name")

    def _set(self, status: StatusName, msg: str = ""):
        """For testing purposes."""
        self.status = StatusBase.from_name(status, msg)
        return self

    def unset(self):
        """
        Reset the status back to the initial status.

        (UnknownStatus)
        """
        self.status = UnknownStatus()

    def __hash__(self):
        return hash((self.name, self.status.name, self.get_message()))

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
    return f"({worst.name}) {worst.get_message()}"


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
        if skip_unknown and status.status.name == "unknown":
            continue
        msgs.append(
            "({0}:{1}) {2}".format(
                status.name,
                status.status.name,
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
    ctr = Counter(s.get_status_name() for s in statuses)

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
        self._manual_priorities = False
        self._priority_counter = 0
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
        self._pool[name].status = status

    def __getattr__(self, name: str) -> Status:
        """
        Light magic for syntax sugar to retrieve a status.

        Allows things like this:

        ```
        assert pool.workload.get_status_name() == "active"
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
            self._pool[name].status = value
        else:
            super().__setattr__(name, value)

    def add(self, status: Status):
        """
        Idempotently add a Status to the pool.

        Note that if no priorities are set on the status,
        the priority defaults to 0.
        Ties are broken by insertion order into the pool,
        so if no statuses are set, the priority order will
        effectively be the order they were added.
        """
        self._pool[status.name] = status

    def remove_status(self, status: Status):
        """Remove the status and forget about it."""
        self._pool.pop(status.name)

    def _load_from_stored_state(self):
        """Retrieve stored state snapshot of current statuses."""
        stored_statuses = typing.cast(
            Dict[str, _StatusDict],
            json.loads(typing.cast(str, self._state["statuses"])),
        )
        for name, status_dict in stored_statuses.items():
            if name in self._pool:
                self._pool[name]._deserialize(status_dict)
            else:
                status = Status(name=status_dict["name"])
                status._deserialize(status_dict)
                self.add(status)

    def _on_autocommit(self, _event):
        log.debug("master status auto-committed")
        self.commit()

    def commit(self):
        """Store the current state and sync with juju."""
        self._charm.unit.status = self.summarize()
        self._state["statuses"] = json.dumps(
            {name: status._serialize() for name, status in self._pool.items()}
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
        return StatusBase.from_name(
            worst_status.status.name,
            self._summarizer_func(
                list(self._pool.values()), self._skip_unknown
            ),
        )

    def __repr__(self):
        if not self._pool:
            return "<StatusPool: empty>"
        return str(self.summarize())
