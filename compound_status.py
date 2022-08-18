import inspect
import json
import logging
import typing
from collections import Counter
from logging import getLogger
from typing import TYPE_CHECKING, Dict, Iterable, Literal, Optional, TypedDict, Union

from ops.charm import CharmBase
from ops.framework import Object, StoredState
from ops.model import StatusBase

log = getLogger("compound-status")

StatusName = Literal["blocked", "waiting", "maintenance", "unknown", "active"]
# are sorted best-to-worst
STATUSES = ("unknown", "active", "maintenance", "waiting", "blocked")
STATUS_PRIORITIES: Dict[str, int] = {val: i for i, val in enumerate(STATUSES)}


class _StatusDict(TypedDict, total=False):
    status: StatusName
    message: str
    priority: float
    tag: str
    attr: str
    user_set: bool


class Status:
    """Represents a status."""

    _ID = 0

    def __repr__(self):
        return "<Status {} ({}): {}>".format(self._status, self.tag, self._message)

    def __init__(self, tag: Optional[str] = None, priority: float = 0):
        # to keep track of instantiation order
        self._id = Status._ID
        Status._ID += 1

        # if tag is None, we'll guess it from the attr name
        # and late-bind it
        self.tag = tag  # type: Optional[str]
        self._status = "unknown"  # type: StatusName
        self._message = ""

        # externally managed (and henceforth immutable) state
        self._logger = None  # type: Optional[logging.Logger]
        self._attr = None  # type: Optional[str]

        if not isinstance(priority, (float, int)):
            raise TypeError(f"priority needs to be float|int, not {type(priority)}")

        self._priority = priority  # type: float  # externally managed

    @property
    def priority(self):
        """Return the priority of this status."""
        return self._priority

    @staticmethod
    def priority_key(status: Union["Status", StatusName]):
        """Return the priority key."""
        if isinstance(status, str):
            return STATUS_PRIORITIES[status]
        return STATUS_PRIORITIES[status.status], -(status.priority or 0)

    @staticmethod
    def sort(statuses: Iterable["Status"]):
        """Return the statuses, sorted worst-to-best."""
        return sorted(statuses, key=Status.priority_key, reverse=True)

    def log(self, level: int, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `log`."""
        if not self._logger:
            raise RuntimeError(f"_logger not set on {self}.")
        self._logger.log(level, msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `critical`."""
        self.log(logging.CRITICAL, msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `error`."""
        self.log(logging.ERROR, msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `warning`."""
        self.log(logging.WARNING, msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `info`."""
        self.log(logging.INFO, msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `debug`."""
        self.log(logging.DEBUG, msg, *args, **kwargs)

    def _set(self, status: StatusName, msg: str = ""):
        assert isinstance(msg, str), type(msg)

        self._status = status
        self._message = msg

        return self

    def unset(self):
        """Unsets status and message.

        This status will go back to its initial state and be removed from the
        toplevel clobber.
        """
        self.debug("unset")
        self._status = "unknown"
        self._message = ""

    def __get__(self, instance, owner):
        return self

    def __set__(self, instance, value: StatusBase):
        assert value.name in STATUSES, f"{value} has an invalid name: {value.name}"
        self._set(value.name, value.message)

    @property
    def status(self) -> StatusName:
        """Return the string representing this status."""
        return self._status

    @property
    def name(self) -> StatusName:
        """Alias for interface-compatibility with ops.model.StatusBase."""
        return self.status

    @property
    def message(self) -> str:
        """Return the message associated with this status."""
        return self._message

    def _snapshot(self) -> _StatusDict:
        """Serialize Status for storage."""
        # fixme: when externally managed state goes, these should too
        # tag should not change, and is reloaded on each init.
        attr = self._attr
        assert attr, attr  # type guard
        tag = self.tag
        assert tag, tag  # type guard

        dct: _StatusDict = {
            "status": self._status,
            "message": self._message,
            "tag": tag,
            "attr": attr,
            "priority": self.priority,
        }
        return dct

    def _restore(self, dct: _StatusDict):
        """Restore Status from stored state."""
        status = dct.get("status")
        message = dct.get("message")
        priority = dct.get("priority")
        tag = dct.get("tag")
        attr = dct.get("attr")

        assert status is not None, status
        assert message is not None, message
        assert priority is not None, priority
        assert tag is not None, tag
        assert attr is not None, attr

        self._status = status
        self._message = message
        self._priority = priority
        self.tag = tag
        self._attr = attr

    def __hash__(self):
        return hash((self.tag, self.status, self.message))

    def __eq__(self, other: "Status") -> bool:
        return hash(self) == hash(other)


class Facade:
    """Toplevel presentation of a group of Status objects."""

    def worst(self, statuses: Iterable[Status]) -> Status:
        """Return worst status."""
        return Status.sort(statuses)[0]

    def status(
        self, statuses: Iterable[Status], skip_unknown: bool = False
    ) -> StatusName:
        """Status Name resulting from this facade."""
        return self.worst(statuses).status

    def message(self, statuses: Iterable[Status], skip_unknown: bool = False) -> str:
        """Clobber the status messages."""
        raise NotImplementedError

    def coalesce(
        self, statuses: Iterable[Status], skip_unknown: bool = False
    ) -> StatusBase:
        """Coalesce a group of Statuses into a single StatusBase instance."""
        return StatusBase.from_name(
            self.status(statuses), self.message(statuses, skip_unknown)
        )


class WorstOnly(Facade):
    """This facade provides a worst-only view of the current statuses in the pool.

    e.g. if the status pool has three statuses:
        relation_1 = ActiveStatus('✅')
        relation_2 = WaitingStatus('𝌗: foo')
        workload = BlockedStatus('💔')

    The Summary clobbered status will be:
        (workload) 💔
    """

    def __init__(self, fmt: str = "({0}) {1}", sep: str = "; "):
        self._fmt = fmt

    def message(self, statuses: Iterable[Status], skip_unknown: bool = False) -> str:
        """Produce a clobbered representation of the statuses."""
        worst = self.worst(statuses)
        return self._fmt.format(worst.tag, worst.message)


class Summary(Facade):
    """This facade provides a worst-first, summarized view of all statuses.

    e.g. if the status pool has three statuses:
        relation_1 = ActiveStatus('✅')
        relation_2 = WaitingStatus('𝌗: foo')
        workload = BlockedStatus('💔')

    The Summary clobbered status will have as message:
        (workload:blocked) 💔; (relation_1:active) ✅; (rel2:waiting) 𝌗: foo
    """

    def __init__(self, fmt: str = "({0}:{1}) {2}", sep: str = "; "):
        self._fmt = fmt
        self._sep = sep

    def message(self, statuses: Iterable[Status], skip_unknown: bool = False):
        """Produce a clobbered representation of the statuses."""
        msgs = []
        for status in Status.sort(statuses):
            if skip_unknown and status.status == "unknown":
                continue
            msgs.append(self._fmt.format(status.tag, status.status, status.message))
        return self._sep.join(msgs)


class Condensed(Facade):
    """This facade provides a very compact, summarized view of all statuses.

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

    def __init__(self, fmt: str = "{0} {1}", sep: str = "; "):
        self._fmt = fmt
        self._sep = sep

    def message(self, statuses: Iterable[Status], skip_unknown: bool = False):
        """Produce a clobbered representation of the statuses."""
        ctr = Counter(s.status for s in statuses)

        if set(ctr) == {
            "active",
        }:  # only active statuses
            return ""

        msgs = []
        for status, count in sorted(
            ctr.items(), key=lambda v: Status.priority_key(v[0]), reverse=True
        ):
            if skip_unknown and status == "unknown":
                continue
            msgs.append(self._fmt.format(count, status))
        return self._sep.join(msgs)


class StatusPool(Object):
    """Represents the pool of statuses available to an Object."""

    # whether unknown statuses should be omitted from the master message
    SKIP_UNKNOWN = False
    # whether the status should be committed automatically when the hook exits
    AUTO_COMMIT = True
    # key used to register handle
    KEY = "status_pool"

    if TYPE_CHECKING:
        _statuses = {}  # type: Dict[str, Status]
        _charm: CharmBase
        _facade: Facade
        _logger: logging.Logger

    _state = StoredState()

    def __init__(
        self,
        charm: CharmBase,
        key: Optional[str] = None,
        facade: Facade = WorstOnly(),
    ):
        _key = key or self.KEY
        super().__init__(charm, _key)

        # skip setattr
        self.__dict__["_statuses"] = {}
        self.__dict__["_facade"] = facade
        self.__dict__["_logger"] = log.getChild(_key)
        self.__dict__["_charm"] = charm

        self._state.set_default(statuses="{}")  # type:ignore
        self._init_statuses(charm)
        self._load_from_stored_state()
        if self.AUTO_COMMIT:
            charm.framework.observe(
                charm.framework.on.commit, self._on_framework_commit  # type: ignore
            )

    def get_status(self, attr: str) -> Status:
        """Retrieve a status by name. Equivalent to getattr(self, attr)."""
        return getattr(self, attr)

    def set_status(self, attr: str, status: StatusBase):
        """Set a status by name. Equivalent to setattr(self, attr, status)."""
        return setattr(self, attr, status)

    def add_status(self, status: Status, attr: Optional[str] = None):
        """Add status to this pool; under attr: `attr`.

        If attr is not provided, status.tag will be used instead if set.

        NB if `attr` is a valid Python identifier, you can
        do `getattr(pool, attr)` to reference the Status object,
        otherwise you can't.
        """
        tag = status.tag
        if not attr and not tag:
            raise ValueError(
                f"either give status {status} a tag, or pass `attr`" f"to add_status."
            )

        # pyright ain't to bright with inline conditionals
        attribute: str = typing.cast(str, attr or tag)
        # will check that attribute is not in use already
        self._add_status(status, attribute)

    def __getattribute__(self, item):
        try:
            return super().__getattribute__(item)
        except AttributeError:
            pass

        if item in self._statuses:
            return self._statuses[item]

        raise AttributeError(item)

    def remove_status(self, status: Status):
        """Remove the status and forget about it."""
        # some safety-first cleanup
        status.unset()
        attr = status._attr  # noqa
        assert attr is not None, status
        del self._statuses[attr]
        status._logger = None

    def _add_status(self, status: Status, attr: str):
        status.tag = status.tag or attr
        status._logger = self._logger.getChild(status.tag)
        status._attr = attr
        self._statuses[attr] = status

    def _init_statuses(self, charm: CharmBase):
        """Extract the statuses from the class namespace."""

        def _is_child_status(obj):
            return isinstance(obj, Status)

        statuses_ = inspect.getmembers(self, predicate=_is_child_status)
        statuses = sorted(statuses_, key=lambda s: s[1]._id)

        # bind, set tag if unset, init logger
        for attr, obj in statuses:
            self._add_status(obj, attr)

    def _load_from_stored_state(self):
        """Retrieve stored state snapshot of current statuses."""
        statuses_raw = typing.cast(str, self._state.statuses)  # type:ignore
        stored_statuses = typing.cast(Dict[str, _StatusDict], json.loads(statuses_raw))
        for attr, status_dct in stored_statuses.items():
            if hasattr(self, attr):  # status was statically defined
                status = getattr(self, attr)
            else:  # status was dynamically added
                status = Status()
                attr = status_dct.get("attr", None)
                assert attr is not None, status_dct  # type guard
                self.add_status(status, attr)

            status._restore(status_dct)  # noqa

    def _store(self):
        """Dump stored state."""
        statuses = {s._attr: s._snapshot() for s in self._statuses.values()}
        self._state.statuses = json.dumps(statuses)  # type:ignore

    def __setattr__(self, key: str, value: StatusBase):
        if isinstance(value, StatusBase):
            name = typing.cast(Optional[StatusName], getattr(value, "name", None))
            if name not in STATUSES:
                raise RuntimeError(
                    f"You cannot set {self} to {value}; its name is {name}, "
                    f"which is an invalid status name. `value` should "
                    f"be an instance of a StatusBase subclass."
                )
            if key in self._statuses:
                self._statuses[key]._set(name, value.message)  # noqa
            else:
                status = Status(key)
                self._add_status(status, key)
                status._set(name, value.message)  # noqa
            return
        return super().__setattr__(key, value)

    def _on_framework_commit(self, _event):
        self._logger.debug("auto-committed")
        self.commit()

    def commit(self):
        """Store the current state and sync with juju."""
        coalesced = self.coalesce()
        if coalesced.name == "unknown":
            self._logger.error('cannot coalesce: status is "unknown"')
        else:
            self._charm.unit.status = coalesced
            self._store()
        return coalesced

    def coalesce(self) -> StatusBase:
        """Cast to an ops.model.StatusBase instance by clobbering statuses and messages."""
        return self._facade.coalesce(self._statuses.values(), self.SKIP_UNKNOWN)

    def unset(self):
        """Unsets all statuses."""
        for child in self._statuses.values():
            child.unset()

    def __repr__(self):
        if not self._statuses:
            return "<StatusPool -- empty>"
        if self.status == "unknown":
            return "unknown"
        return str(self.coalesce())
