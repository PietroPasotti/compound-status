import pytest
from ops.charm import CharmBase
from ops.framework import Handle
from ops.model import (
    UnknownStatus,
    ActiveStatus,
    WaitingStatus,
    BlockedStatus,
    MaintenanceStatus,
)
from ops.testing import Harness

from compound_status import StatusPool, Status, MasterStatus
from harnessctx import HarnessCtx


@pytest.fixture(scope="function")
def charm_type():
    class CharmStatus(StatusPool):
        SKIP_UNKNOWN = True

        workload = Status()
        relation_1 = Status()
        relation_2 = Status(tag="rel2")

    class MyCharm(CharmBase):
        _STATUS_CLS = CharmStatus

        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = CharmStatus(self)

    return MyCharm


@pytest.fixture(scope="function")
def harness(charm_type):
    harness = Harness(charm_type)

    # reset the stored status state, else we might pollute the tests with reloads
    # status_handle = Handle('MyCharm', 'CharmStatus', 'compound_status')
    harness.framework._storage.drop_snapshot("MyCharm/CharmStatus[compound_status]")

    harness.begin_with_initial_hooks()
    return harness


@pytest.fixture(scope="function")
def charm(harness):
    return harness.charm


def test_statuses_collection(charm):
    assert len(charm.status.master.children) == 3


def test_statuses_setting(charm):
    assert charm.unit.status.name == "unknown"
    assert charm.unit.status.message == ""

    charm.status.relation_1._set("active", "foo")
    charm.status.commit()

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == "[relation_1] (active) foo"


def test_statuses_setting_magic(charm):
    assert charm.unit.status.name == "unknown"
    assert charm.unit.status.message == ""

    charm.status.relation_1 = ActiveStatus("foo")
    charm.status.commit()

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == "[relation_1] (active) foo"


def test_statuses_priority(charm):
    charm.status.relation_1 = ActiveStatus("foo")
    charm.status.relation_2 = WaitingStatus("bar")

    charm.status.commit()
    assert charm.status.master.status == "waiting"
    assert (
        charm.status.master.message == "[rel2] (waiting) bar; [relation_1] (active) foo"
    )

    charm.status.workload = BlockedStatus("qux")
    charm.status.commit()

    assert charm.status.master.status == "blocked"
    assert (
        charm.status.master.message
        == "[workload] (blocked) qux; [rel2] (waiting) bar; [relation_1] (active) foo"
    )


def test_statuses_master_override(charm):
    charm.status.relation_1 = ActiveStatus("foo")
    charm.status.relation_2 = WaitingStatus("bar")
    charm.status.master = ActiveStatus("overruled!")
    charm.status.commit()

    assert charm.status.master.status == charm.unit.status.name == "active"
    assert charm.status.master.message == charm.unit.status.message == "overruled!"


def test_hold(charm):
    charm.status.master = ActiveStatus("1")
    charm.status.commit()

    charm.status.relation_1 = ActiveStatus("foo")
    assert charm.status.master.status == charm.unit.status.name == "active"
    assert charm.status.master.message == charm.unit.status.message == "1"
    charm.status.relation_2 = WaitingStatus("bar")
    assert charm.status.master.status == charm.unit.status.name == "active"
    assert charm.status.master.message == charm.unit.status.message == "1"
    charm.status.master = ActiveStatus("2")
    assert charm.status.master.status == charm.unit.status.name == "active"
    assert charm.unit.status.message == "1"
    assert charm.status.master.message == "2"

    charm.status.commit()

    assert charm.status.master.status == charm.unit.status.name == "active"
    assert charm.status.master.message == charm.unit.status.message == "2"


def test_hold_no_sync(charm):
    charm.status.master = ActiveStatus("1")
    charm.status.relation_1 = ActiveStatus("foo")
    charm.status.commit()

    assert charm.status.master.status == charm.unit.status.name == "active"
    assert charm.status.master.message == charm.unit.status.message == "1"
    charm.status.relation_2 = WaitingStatus("bar")
    assert charm.status.master.status == charm.unit.status.name == "active"
    assert charm.status.master.message == charm.unit.status.message == "1"
    charm.status.master = ActiveStatus("2")
    # now the master status does change!
    assert charm.status.master.status == "active"
    assert charm.status.master.message == "2"
    # but not the unit status.
    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == "1"

    # we didn't sync, so everything is as before, still
    assert charm.status.master.status == charm.unit.status.name == "active"
    assert charm.status.master.message == "2"
    assert charm.unit.status.message == "1"

    charm.status.commit()

    # now all is nice and sync.
    assert charm.status.master.status == charm.unit.status.name == "active"
    assert charm.status.master.message == charm.unit.status.message == "2"


def test_stored_blank(charm):
    charm.status.commit()

    other_harness = Harness(type(charm))
    other_harness.begin()
    restored_charm = other_harness.charm
    assert restored_charm.status.master.status == "unknown"
    assert restored_charm.unit.status.name == "maintenance"
    assert restored_charm.unit.status.message == ""


def test_stored(charm):
    charm.status.relation_1 = BlockedStatus("foo")
    charm.status.commit()

    other_harness = Harness(type(charm))
    other_harness.begin()
    restored_charm = other_harness.charm
    assert restored_charm.status.master.status == "blocked"

    # we have reinited the charm, so harness has set it to 'maintenance'
    # a real live charm would remain blocked.
    # assert restored_charm.unit.status.name == 'blocked'
    assert restored_charm.status.master.message == "[relation_1] (blocked) foo"

    # we have reinited the charm, so harness has set it to ''
    # assert restored_charm.unit.status.message == "foo"


def test_auto_commit(charm_type):
    charm_type._STATUS_CLS.AUTO_COMMIT = True
    with HarnessCtx(charm_type, "update-status") as h:
        charm = h.harness.charm
        charm.status.relation_2 = ActiveStatus("noop")
        charm.status.relation_1 = ActiveStatus("boop")

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == MasterStatus._clobber_statuses(
        (charm.status.relation_1, charm.status.relation_2)
    )


def test_auto_commit_off(charm_type):
    charm_type._STATUS_CLS.AUTO_COMMIT = False
    with HarnessCtx(charm_type, "update-status") as h:
        charm = h.harness.charm
        charm.unit.status = MaintenanceStatus("")

        charm.status.relation_2 = ActiveStatus("noop")
        charm.status.relation_1 = ActiveStatus("boop")

    assert charm.unit.status.name == "maintenance"
    assert charm.unit.status.message == ""

    charm.status.commit()

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == MasterStatus._clobber_statuses(
        (charm.status.relation_1, charm.status.relation_2)
    )


def test_unset(charm):
    charm.status.relation_1 = ActiveStatus("foo")
    charm.status.relation_1.unset()
    assert charm.status.relation_1.name == "unknown"

    charm.status.commit()

    assert charm.unit.status.name == "unknown"
    assert charm.unit.status.message == ""


def test_unset_master(charm):
    charm.status.relation_1 = ActiveStatus("foo")
    charm.status.relation_2 = BlockedStatus("bar")
    charm.status.commit()

    charm.status.unset()

    charm.status.workload = ActiveStatus("woot")
    charm.status.commit()

    # as if nothing happened
    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == "[workload] (active) woot"
