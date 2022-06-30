import inspect
import json
import logging
import typing
from itertools import chain
from logging import getLogger
from operator import itemgetter
from typing import Dict, Literal, Optional, Sequence, Tuple

from ops.charm import CharmBase
from ops.framework import Object, StoredState
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    StatusBase,
    WaitingStatus,
)
from typing_extensions import Self

log = getLogger("compound-status")

StatusName = Literal["blocked", "waiting", "maintenance", "unknown", "active"]
STATUS_PRIORITIES = ("blocked", "waiting", "maintenance", "active", "unknown")
STATUS_NAME_TO_CLASS = {
    "blocked": BlockedStatus,
    "waiting": WaitingStatus,
    "maintenance": MaintenanceStatus,
    "active": ActiveStatus
    # omit unknown as it should not be used directly.
}


class Status:
    """Represents a status."""

    _ID = 0

    def __repr__(self):
        return "<Status {} ({}): {}>".format(self._status, self.tag, self._message)

    def __init__(self, tag: Optional[str] = None):
        # to keep track of instantiation order
        self._id = Status._ID
        Status._ID += 1

        # if tag is None, we'll guess it from the attr name
        # and late-bind it
        self.tag = tag  # type: str
        self._status = "unknown"  # type: StatusName
        self._message = ""
        self._master = None  # type: Optional[MasterStatus]  # externally managed
        self._logger = None  # type: Optional[logging.Logger]  # externally managed

    def log(self, level: int, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `log`."""
        self._logger.log(level, msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `critical`."""
        self._logger.critical(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `error`."""
        self._logger.error(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `warning`."""
        self._logger.warning(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `info`."""
        self._logger.info(msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        """Associate with this status a log entry with level `debug`."""
        self._logger.debug(msg, *args, **kwargs)

    def _set(self, status: StatusName, msg: str = ""):
        assert status in STATUS_NAME_TO_CLASS, "invalid status: {}".format(status)
        assert isinstance(msg, str), type(msg)

        self._status = status
        self._message = msg

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

    def _snapshot(self) -> dict:
        """Serialize Status for storage."""
        # tag should not change, and is reloaded on each init.
        dct = {"type": "subordinate", "status": self._status, "message": self._message}
        return dct

    def _restore(self, dct) -> Self:
        """Restore Status from stored state."""
        assert dct["type"] == "subordinate", dct["type"]
        self._status = dct["status"]
        self._message = dct["message"]


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
        self, tag: Optional[str] = "master", fmt: str = "({0}:{1}) {2}", sep: str = "; "
    ):
        super().__init__(tag)
        self._fmt = fmt
        self._sep = sep
        self.children = ()  # type: Tuple[Status, ...]  # gets populated by CompoundStatus
        self._owner = None  # type: CharmBase  # externally managed
        self._user_set = False

        self._logger = log.getChild(tag)
        self._master = self  # lucky you

    @property
    def message(self) -> str:
        """Return the message associated with this status."""
        if self._user_set:
            return self._message
        return self._clobber_statuses(self.children, self.SKIP_UNKNOWN)

    def _clobber_statuses(self, statuses: Sequence[Status], skip_unknown=False) -> str:
        """Produce a message summarizing the child statuses."""
        msgs = []
        for status in sorted(statuses, key=lambda s: STATUS_PRIORITIES.index(s.status)):
            if skip_unknown and status.status == "unknown":
                continue
            msgs.append(self._fmt.format(status.tag, status.status, status.message))
        return self._sep.join(msgs)

    @staticmethod
    def _get_worst_case(statuses: Sequence[str]):
        worst_so_far = statuses[0]
        for status in statuses[1:]:
            if STATUS_PRIORITIES.index(status) < STATUS_PRIORITIES.index(worst_so_far):
                worst_so_far = status
        return worst_so_far

    @property
    def status(self) -> str:
        """Return the status."""
        if self._user_set:
            return self._status
        statuses = [c.status for c in self.children]
        return self._get_worst_case(statuses)

    def coalesce(self) -> StatusBase:
        """Cast to an ops.model.StatusBase instance by clobbering statuses and messages."""
        if self.status == "unknown":
            raise ValueError("cannot coalesce unknown status")
        status_type = STATUS_NAME_TO_CLASS[self.status]
        status_msg = self.message
        return status_type(status_msg)

    def _set(self, status: StatusName, msg: str = ""):
        """Force-set this status and message.

        Should not be called by user code.
        """
        self._user_set = True
        super()._set(status, msg)

    def unset(self):
        """Unset all child statuses, as well as any user-set Master status."""
        super().unset()

        self._user_set = False
        for child in self.children:
            child.unset()

    def _snapshot(self) -> dict:
        """Serialize Status for storage."""
        dct = super()._snapshot()
        dct["type"] = "master"
        dct["user-set"] = self._user_set
        return dct

    def _restore(self, dct) -> Self:
        """Restore Status from stored state."""
        assert dct["type"] == "master", dct["type"]
        self._status = dct["status"]
        self._message = dct["message"]
        self._user_set = dct["user-set"]

    def __repr__(self):
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

    _state = StoredState()

    if typing.TYPE_CHECKING:
        _statuses = {}  # type: Dict[str, Status]
        _charm = {}  # type: CharmBase
        master = MasterStatus()  # type: MasterStatus

    def __init__(self, charm: CharmBase, key: str = None):
        super().__init__(charm, key or self.KEY)
        # skip setattr
        self.__dict__["master"] = MasterStatus()

        self._init_statuses(charm)
        self._load_from_stored_state()

        if self.AUTO_COMMIT:
            charm.framework.observe(
                charm.framework.on.commit, self._on_framework_commit
            )

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
            obj.tag = tag = obj.tag or attr
            obj._master = master
            obj._logger = master._logger.getChild(tag)

        master.SKIP_UNKNOWN = self.SKIP_UNKNOWN
        master.children = tuple(a[1] for a in statuses)

        # skip setattr
        self.__dict__["_statuses"] = dict(statuses)
        self.__dict__["_charm"] = charm

    def _load_from_stored_state(self):
        """Retrieve stored state snapshot of current statuses."""
        for status in self._statuses.values():
            stored = getattr(self._state, status.tag, None)

            if stored is None:
                continue

            try:
                dct = json.loads(stored)
            except json.JSONDecodeError as e:
                raise ValueError("not a valid status: {}".format(stored)) from e

            status._restore(dct)  # noqa

    def _store(self):
        """Dump stored state."""
        all_statuses = chain(map(itemgetter(1), self._statuses.items()), (self.master,))
        for status in all_statuses:
            setattr(self._state, status.tag, status._snapshot())  # noqa

    def __setattr__(self, key: str, value: StatusBase):
        if isinstance(value, StatusBase):
            if key == "master":
                return self.master._set(value.name, value.message)  # noqa
            elif key in self._statuses:
                return self._statuses[key]._set(value.name, value.message)  # noqa
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

    def unset(self):
        """Unsets master status (and all children)."""
        self.master.unset()

    def __repr__(self):
        return repr(self.master)
