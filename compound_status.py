import inspect
import json
import logging
import typing
from collections import Counter
from itertools import chain
from logging import getLogger
from operator import itemgetter
from typing import (
    TYPE_CHECKING,
    Dict,
    Literal,
    Optional,
    Sequence,
    Tuple,
    Iterable,
    Type,
    TypedDict,
    Union, Set,
)

from ops.charm import CharmBase
from ops.framework import Handle, Object, StoredStateData
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    StatusBase,
    WaitingStatus,
)
from ops.storage import NoSnapshotError

log = getLogger("compound-status")

StatusName = Literal["blocked", "waiting", "maintenance", "unknown", "active"]
# are sorted best-to-worst
STATUSES = ("unknown", "active", "maintenance", "waiting", "blocked")
STATUS_PRIORITIES: Dict[str, int] = {val: i for i, val in enumerate(STATUSES)}

PositiveNumber = Union[float, int]


class _StatusDict(TypedDict, total=False):
    type: Literal["subordinate", "master"]  # noqa
    status: StatusName
    message: str
    priority: PositiveNumber
    tag: str
    attr: str
    user_set: bool


class Status:
    """Represents a status."""

    _ID = 0

    def __repr__(self):
        return "<Status {} ({}): {}>".format(self._status, self.tag,
                                             self._message)

    def __init__(
            self, tag: Optional[str] = None,
            priority: Optional[PositiveNumber] = None
    ):
        # to keep track of instantiation order
        self._id = Status._ID
        Status._ID += 1

        # if tag is None, we'll guess it from the attr name
        # and late-bind it
        self.tag = tag  # type: Optional[str]
        self._status = "unknown"  # type: StatusName
        self._message = ""

        # externally managed (and henceforth immutable) state
        self._master = None  # type: Optional[MasterStatus]
        self._logger = None  # type: Optional[logging.Logger]
        self._attr = None  # type: Optional[str]

        if priority is not None:
            if not isinstance(priority, (float, int)):
                raise TypeError(
                    f"priority needs to be float|int, not {type(priority)}")
            if priority <= 0:
                raise TypeError(f"priority needs to be > 0, not {priority}")

        self._priority = priority  # type: Optional[PositiveNumber]  # externally managed

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
        Master clobber.
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
        # tag should not change, and is reloaded on each init.
        attr = self._attr
        assert attr, attr  # type guard
        tag = self.tag
        assert tag, tag  # type guard

        dct: _StatusDict = {
            "type": "subordinate",
            "status": self._status,
            "message": self._message,
            "tag": tag,
            "attr": attr,
            "priority": self.priority
        }
        return dct

    def _restore(self, dct: _StatusDict):
        """Restore Status from stored state."""
        type_ = dct.get("type")
        assert type_, type_
        assert type_ == "subordinate", type_

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


class Clobberer:
    """Clobberer. Repeat it many times fast."""

    def clobber(self, statuses: Iterable[Status],
                skip_unknown: bool = False) -> str:
        """Produce a clobbered representation of the statuses."""
        raise NotImplementedError


class WorstOnly(Clobberer):
    """This clobberer provides a worst-only view of the current statuses in the pool.

    e.g. if the status pool has three statuses:
        relation_1 = ActiveStatus('✅')
        relation_2 = WaitingStatus('𝌗: foo')
        workload = BlockedStatus('💔')

    The Summary clobbered status will have as message::
        (workload) 💔
    """

    def __init__(self, fmt: str = "({0}) {1}", sep: str = "; "):
        self._fmt = fmt

    def clobber(self, statuses: Iterable[Status],
                skip_unknown: bool = False) -> str:
        """Produce a clobbered representation of the statuses."""
        worst = Status.sort(statuses)[0]
        return self._fmt.format(worst.tag, worst.message)


class Summary(Clobberer):
    """This clobberer provides a worst-first, summarized view of all statuses.

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

    def clobber(self, statuses: Iterable[Status], skip_unknown: bool = False):
        """Produce a clobbered representation of the statuses."""
        msgs = []
        for status in Status.sort(statuses):
            if skip_unknown and status.status == "unknown":
                continue
            msgs.append(
                self._fmt.format(status.tag, status.status, status.message))
        return self._sep.join(msgs)


class Condensed(Clobberer):
    """This clobberer provides a very compact, summarized view of all statuses.

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

    def clobber(self, statuses: Iterable[Status], skip_unknown: bool = False):
        """Produce a clobbered representation of the statuses."""
        ctr = Counter(s.status for s in statuses)

        if set(ctr) == {
            "active",
        }:  # only active statuses
            return ""

        msgs = []
        for status, count in sorted(
                ctr.items(), key=lambda v: Status.priority_key(v[0]),
                reverse=True
        ):
            if skip_unknown and status == "unknown":
                continue
            msgs.append(self._fmt.format(count, status))
        return self._sep.join(msgs)


class MasterStatus(Status):
    """The Master status of the pool.

    Parameters:
        - `tag`: the name to associate the master status with.

        - `fmt`: The format for each child status. Needs to contain three {}
            slots, will receive three arguments in this order:

            - the tag of the child status (a string)
            - the name of the child status (e.g. 'blocked', or 'active')
            - the message associated with the child status (another string)

        - `sep`: The separator used to join together the child statuses.
    """

    SKIP_UNKNOWN = False

    def __init__(
            self,
            tag: str = "master",
            clobberer: Clobberer = WorstOnly(),
            priority: Optional[PositiveNumber] = None,
    ):
        super().__init__(tag, priority=priority)
        self.children = set()  # type: Set[Status, ...]  # gets populated by CompoundStatus
        self._owner = None  # type: Optional[CharmBase]  # externally managed
        self._user_set = False
        self._clobberer = clobberer

        self._logger = log.getChild(tag)
        self._master = self  # lucky you
        self._attr = "*master*"

    def _add_child(self, status: Status):
        """Add a child status."""
        status._master = self
        logger = self._logger
        assert logger  # type guard
        tag = status.tag
        assert tag  # type guard

        status._logger = logger.getChild(tag)
        self.children.add(status)

    def _remove_child(self, status: Status):
        """Remove a child status."""
        if status not in self.children:
            raise ValueError(f"{status} not in {self}")

        status._master = None
        status._logger = None
        self.children.remove(status)

    @property
    def message(self) -> str:
        """Return the message associated with this status."""
        if self._user_set:
            return self._message
        return self._clobber_statuses(self.children, self.SKIP_UNKNOWN)

    def _clobber_statuses(
            self, statuses: Iterable[Status], skip_unknown: bool = False
    ) -> str:
        """Produce a message summarizing the child statuses."""
        return self._clobberer.clobber(statuses, skip_unknown)

    @property
    def status(self) -> StatusName:
        """Return the status."""
        if self._user_set:
            return self._status
        return Status.sort(self.children)[0].status

    def coalesce(self) -> StatusBase:
        """Cast to an ops.model.StatusBase instance by clobbering statuses and messages."""
        if self.status == "unknown":
            raise ValueError("cannot coalesce unknown status")
        ops_status = StatusBase.from_name(self.status, self.message)
        return ops_status

    def _set(self, status: StatusName, msg: str = ""):
        """Force-set this status and message.

        Should not be called by user code.
        """
        self._user_set = True
        super()._set(status, msg)

    def unset(self):
        """Unset all child statuses, as well as any user_set Master status."""
        super().unset()

        self._user_set = False
        for child in self.children:
            child.unset()

    def _snapshot(self) -> _StatusDict:
        """Serialize Status for storage."""
        dct = super()._snapshot()
        dct["type"] = "master"
        dct["user_set"] = self._user_set
        return dct

    def _restore(self, dct: _StatusDict):
        """Restore Status from stored state."""
        type_ = dct.get("type", None)
        assert type_, type_  # type guard
        assert type_ == "master", type_

        status = dct.get("status", None)
        message = dct.get("message", None)
        user_set = dct.get("user_set", None)

        assert status is not None, status
        assert message is not None, message
        assert user_set is not None, user_set

        self._status = status
        self._message = message
        self._user_set = user_set

    def __repr__(self):
        if not self.children:
            return "<MasterStatus -- empty>"
        if self.status == "unknown":
            return "unknown"
        return str(self.coalesce())


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
        master = MasterStatus()  # type: MasterStatus
        _priority_counter = 0  # type: int

    def __init__(self, charm: CharmBase, key: Optional[str] = None):
        super().__init__(charm, key or self.KEY)
        # skip setattr
        self.__dict__["master"] = MasterStatus()
        self.__dict__["_statuses"] = {}
        self.__dict__["_priority_counter"] = 0

        stored_handle = Handle(self, StoredStateData.handle_kind,
                               "_status_pool_state")
        charm.framework.register_type(
            StoredStateData, self, StoredStateData.handle_kind
        )
        try:
            self._state = charm.framework.load_snapshot(stored_handle)
        except NoSnapshotError:
            self._state = StoredStateData(self, "_status_pool_state")
            self._state["statuses"] = "{}"

        self._init_statuses(charm)
        self._load_from_stored_state()
        if self.AUTO_COMMIT:
            charm.framework.observe(
                charm.framework.on.commit, self._on_framework_commit
                # type: ignore
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

        NB `attr` needs to be a valid Python identifier.
        """
        tag = status.tag
        if not attr and not tag:
            raise ValueError(
                f"either give status {status} a tag, or pass `attr`" f"to add_status."
            )

        # pyright ain't to bright with inline conditionals
        attribute: str = typing.cast(str, attr or tag)

        if not attribute.isidentifier():
            raise ValueError(
                f"cannot set {attribute!r}={status} on {self}: "
                f"attribute needs to be a valid Python identifier."
            )

        # will check that attribute is not in use already
        self._add_status(status, attribute)

        setattr(self, attribute, status)

    def remove_status(self, status: Status):
        """Remove the status and forget about it."""
        # some safety-first cleanup
        status.unset()
        self.master._remove_child(status)  # noqa
        attr = status._attr  # noqa
        assert attr is not None, status
        delattr(self, attr)

    def _add_status(self, status: Status, attr: str):
        if not status.priority:
            self._priority_counter += 1
            status._priority = self._priority_counter

        status.tag = status.tag or attr
        self.master._add_child(status)  # noqa

        status._attr = attr
        self._statuses[attr] = status

    def _init_statuses(self, charm: CharmBase):
        """Extract the statuses from the class namespace.

        And associate them with the master status.
        """

        def _is_child_status(obj):
            return isinstance(obj, Status) and not isinstance(obj, MasterStatus)

        statuses_ = inspect.getmembers(self, predicate=_is_child_status)
        statuses = sorted(statuses_, key=lambda s: s[1]._id)

        master = self.master
        # bind children to master, set tag if unset, init logger
        for attr, obj in statuses:
            self._add_status(obj, attr)

        master.SKIP_UNKNOWN = self.SKIP_UNKNOWN
        master.children = set(a[1] for a in statuses)

        # skip setattr
        self.__dict__["_statuses"] = dict(statuses)
        self.__dict__["_charm"] = charm

    def _load_from_stored_state(self):
        """Retrieve stored state snapshot of current statuses."""
        statuses_raw = typing.cast(str, self._state["statuses"])
        stored_statuses = typing.cast(Dict[str, _StatusDict],
                                      json.loads(statuses_raw))
        for attr, status_dct in stored_statuses.items():
            if attr == "*master*":
                status = self.master
            else:
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
        all_statuses = chain(map(itemgetter(1), self._statuses.items()),
                             (self.master,))
        statuses = {s._attr: s._snapshot() for s in all_statuses}
        self._state["statuses"] = json.dumps(statuses)

    def __setattr__(self, key: str, value: StatusBase):
        if isinstance(value, StatusBase):
            name = typing.cast(Optional[StatusName],
                               getattr(value, "name", None))
            if name not in STATUSES:
                raise RuntimeError(
                    f"You cannot set {self} to {value}; its name is {name}, "
                    f"which is an invalid status name. `value` should "
                    f"be an instance of a StatusBase subclass."
                )

            if key == "master":
                return self.master._set(name, value.message)  # noqa
            elif key in self._statuses:
                return self._statuses[key]._set(name, value.message)  # noqa
            else:
                raise AttributeError(key)
        return super().__setattr__(key, value)

    def _on_framework_commit(self, _event):
        log.debug("master status auto-committed")
        self.commit()

    def commit(self):
        """Store the current state and sync with juju."""
        assert isinstance(self.master, MasterStatus), type(self.master)

        # cannot coalesce in unknown status
        if self.master.status != "unknown":
            self._charm.unit.status = self.master.coalesce()
            self._store()

        self._charm.framework.save_snapshot(self._state)  # type: ignore
        self._charm.framework._storage.commit()  # noqa

    def unset(self):
        """Unsets master status (and all children)."""
        self.master.unset()

    def __repr__(self):
        return repr(self.master)
