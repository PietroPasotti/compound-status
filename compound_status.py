import inspect
import json
import typing
from contextlib import contextmanager
from itertools import chain
from logging import getLogger
from operator import itemgetter
from typing import Tuple, Optional, Sequence, Literal, Dict

from ops.charm import CharmBase
from ops.framework import StoredState, Object
from ops.model import (
    BlockedStatus,
    WaitingStatus,
    MaintenanceStatus,
    ActiveStatus,
    StatusBase,
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
    _ID = 0

    def __repr__(self):
        return "<Status {} ({}): {}>".format(self._status, self.tag, self._message)

    def __init__(self, tag: Optional[str] = None):
        # to keep track of instantiation order
        self._id = Status._ID
        Status._ID += 1

        # if tag is None, we'll guess it from the attr name
        self.tag = tag
        self._status = "unknown"
        self._message = ""
        self._master = None  # type: Optional[MasterStatus]  # externally managed

    @property
    def _logger(self):
        return log.getChild(self.tag)

    def log(self, level: int, msg: str, *args, **kwargs):
        self._logger.log(level, msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        self._logger.critical(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self._logger.error(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self._logger.warning(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        self._logger.info(msg, *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs):
        self._logger.debug(msg, *args, **kwargs)

    def set(self, status: StatusName, msg: str = ""):
        assert status in STATUS_NAME_TO_CLASS, "invalid status: {}".format(status)
        assert isinstance(msg, str), type(msg)

        self._status = status
        self._message = msg
        self._master.update()

    def __get__(self, instance, owner):
        return self

    def __set__(self, instance, value: StatusBase):
        self.set(value.name, value.message)

    @property
    def status(self) -> str:
        return self._status

    @property
    def message(self) -> str:
        return self._message

    def snapshot(self) -> dict:
        # tag should not change, and is reloaded on each init.
        dct = {"type": "subordinate", "status": self._status, "message": self._message}
        return dct

    def restore(self, dct) -> Self:
        assert dct["type"] == "subordinate", dct["type"]
        self._status = dct["status"]
        self._message = dct["message"]


class MasterStatus(Status):
    SKIP_UNKNOWN = False

    def __init__(self, tag: Optional[str] = "master"):
        super().__init__(tag)
        self.children = ()  # type: Tuple[Status, ...]  # gets populated by CompoundStatus
        self._owner = None  # type: CharmBase  # externally managed
        self._user_set = False

        self._on_hold = False
        self._missed_update = False

        self._master = self  # lucky you

    @contextmanager
    def hold(self, sync=True):
        """Do not sync status within this context.

        >>> with self.status.hold():
        >>>     self.status.foo.set('waiting', '...')
        >>>     self.status.bar.set('waiting', '...')
        >>>     # lots more statuses...
        >>> # now we update it all at once.
        """
        if self._on_hold:
            raise ValueError("already holding...?")

        self._on_hold = True

        yield

        self._on_hold = False

        if self._missed_update:
            self._missed_update = False
            if sync:
                self.update()

    @property
    def message(self) -> str:
        if self._user_set:
            return self._message
        return self._clobber_statuses(self.children, self.SKIP_UNKNOWN)

    @staticmethod
    def _clobber_statuses(statuses: Sequence[Status], skip_unknown=False) -> str:
        """Produce a message summarizing the child statuses."""
        msgs = []
        for status in statuses:
            if skip_unknown and status.status == "unknown":
                continue
            msgs.append(
                "[{}] ({}) {}".format(status.tag, status.status, status.message)
            )
        return "; ".join(msgs)

    @staticmethod
    def _get_worst_case(statuses: Sequence[str]):
        worst_so_far = statuses[0]
        for status in statuses[1:]:
            if STATUS_PRIORITIES.index(status) < STATUS_PRIORITIES.index(worst_so_far):
                worst_so_far = status
        return worst_so_far

    @property
    def status(self) -> str:
        if self._user_set:
            return self._status
        statuses = [c.status for c in self.children]
        return self._get_worst_case(statuses)

    def coalesce(self) -> StatusBase:
        if self.status == "unknown":
            raise ValueError("cannot coalesce unknown status")
        status_type = STATUS_NAME_TO_CLASS[self.status]
        status_msg = self.message
        return status_type(status_msg)

    def set(self, status: StatusName, msg: str = ""):
        self._user_set = True
        super().set(status, msg)

    def update(self):
        # cannot coalesce in unknown status
        if self._on_hold:
            self._missed_update = True
            return

        if self.status != "unknown":
            self._owner.unit.status = self.coalesce()

    def snapshot(self) -> dict:
        dct = super().snapshot()
        dct["type"] = "master"
        dct["user-set"] = self._user_set
        return dct

    def restore(self, dct) -> Self:
        assert dct["type"] == "master", dct["type"]
        self._status = dct["status"]
        self._message = dct["message"]
        self._user_set = dct["user-set"]


class CompoundStatus(Object):
    SKIP_UNKNOWN = (
        False  # whether unknown statuses should be omitted from the master message
    )
    master = MasterStatus()
    _state = StoredState()

    if typing.TYPE_CHECKING:
        _statuses = {}  # type: Dict[str, Status]

    def __init__(self, charm: CharmBase, key: str = "compound_status"):
        super().__init__(charm, key)
        self._init_statuses(charm)
        self._load_from_stored_state()

    def _init_statuses(self, charm: CharmBase):
        """Extract the statuses from the class namespace.

        And associate them with the master status.
        """
        is_status = lambda obj: isinstance(obj, Status) and not isinstance(
            obj, MasterStatus
        )
        statuses_ = inspect.getmembers(self, is_status)
        statuses = sorted(statuses_, key=lambda s: s[1]._id)

        master = self.master
        # bind children to master, set tag if unset.
        for attr, obj in statuses:
            obj.tag = obj.tag or attr
            obj._master = master

        master.SKIP_UNKNOWN = self.SKIP_UNKNOWN
        master.children = tuple(a[1] for a in statuses)
        master._owner = charm
        self.__dict__["_statuses"] = dict(statuses)

        all_statuses = chain(map(itemgetter(1), statuses), (master,))
        self._state.set_default(
            **{status.tag: json.dumps(status.snapshot()) for status in all_statuses}
        )

    def _load_from_stored_state(self):
        """Retrieve stored state snapshot of current statuses."""
        for status in self._statuses.values():
            stored = getattr(self._state, status.tag)

            try:
                dct = json.loads(stored)
            except json.JSONDecodeError as e:
                raise ValueError("not a valid status: {}".format(stored)) from e

            status.restore(dct)

    # CompoundStatus is a proxy of the master status to some extent
    def set(self, status: StatusName, message: str):
        return self.master.set(status, message)

    def __setattr__(self, key: str, value: StatusBase):
        if isinstance(value, StatusBase):
            if key == "master":
                return self.master.set(value.name, value.message)
            elif key in self._statuses:
                return self._statuses[key].set(value.name, value.message)
            else:
                raise AttributeError(key)
        return super().__setattr__(key, value)

    @property
    def hold(self):
        return self.master.hold

    def update(self):
        self.master.update()
