import pytest
import yaml
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

from compound_status import StatusPool, Status, MasterStatus, WorstOnly, \
    Summary, Condensed
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
    harness._storage.drop_snapshot(
        "MyCharm/CharmStatus[compound_status]")

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
    assert charm.unit.status.message == "(relation_1) foo"


def test_statuses_setting_magic(charm):
    assert charm.unit.status.name == "unknown"
    assert charm.unit.status.message == ""

    charm.status.relation_1 = ActiveStatus("foo")
    charm.status.commit()

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == "(relation_1) foo"


@pytest.mark.parametrize("statuses, expected_message", (
        ((Status('foo', 1)._set('active', 'argh'),
          Status('bar', 2)._set('active'),
          Status('baz', 3)._set('active')),
         '(foo) argh'),
        ((Status('foo', 1)._set('active'),
          Status('bar', 2)._set('blocked', 'wof'),
          Status('baz', 3)._set('active')),
         '(bar) wof'),
        ((Status('foo', 1)._set('active'),
          Status('bar', 2)._set('waiting'),
          Status('baz', 3)._set('blocked', 'meow')),
         '(baz) meow'),
))
def test_worst_only_clobber(statuses, expected_message):
    clb = WorstOnly().clobber(statuses)
    assert clb == expected_message


@pytest.mark.parametrize("statuses, expected_message", (
        ((Status('foo', 1)._set('active', 'argh'),
          Status('bar', 2)._set('active'),
          Status('baz', 3)._set('active')),
         ''),
        ((Status('foo', 1)._set('active'),
          Status('bar', 2)._set('blocked', 'wof'),
          Status('baz', 3)._set('active')),
         '1 blocked; 2 active'),
        ((Status('foo', 1)._set('active'),
          Status('bar', 2)._set('waiting'),
          Status('baz', 3)._set('blocked', 'meow')),
         '1 blocked; 1 waiting; 1 active'),
))
def test_condensed_clobber(statuses, expected_message):
    clb = Condensed().clobber(statuses)
    assert clb == expected_message


@pytest.mark.parametrize("statuses, expected_message", (
        ((Status('foo', 1)._set('active', 'argh'),
          Status('bar', 2)._set('active'),
          Status('baz', 3)._set('active')),
         '(foo:active) argh; (bar:active) ; (baz:active) '),
        ((Status('foo', 1)._set('active'),
          Status('bar', 2)._set('blocked', 'wof'),
          Status('baz', 3)._set('active')),
         '(bar:blocked) wof; (foo:active) ; (baz:active) '),
        ((Status('foo', 1)._set('active'),
          Status('bar', 2)._set('waiting'),
          Status('baz', 3)._set('blocked', 'meow')),
         '(baz:blocked) meow; (bar:waiting) ; (foo:active) '),
))
def test_summary_clobber(statuses, expected_message):
    clb = Summary().clobber(statuses)
    assert clb == expected_message


@pytest.mark.parametrize("statuses, expected_order", (
        ((Status('foo', 1)._set('active'),
          Status('bar', 2)._set('active'),
          Status('baz', 3)._set('active')),
         ('foo', 'bar', 'baz')),
        ((Status('foo', 1)._set('active'),
          Status('bar', 2)._set('blocked'),
          Status('baz', 3)._set('active')),
         ('bar', 'foo', 'baz')),
        ((Status('foo', 1)._set('active'),
          Status('bar', 2)._set('waiting'),
          Status('baz', 3)._set('blocked')),
         ('baz', 'bar', 'foo')),
))
def test_status_sorting(statuses, expected_order):
    ordered = Status.sort(statuses)
    assert tuple(status.tag for status in ordered) == expected_order


def test_status_priority_auto(charm):
    assert charm.status.workload.priority == 1
    assert charm.status.relation_1.priority == 2
    assert charm.status.relation_2.priority == 3
    assert charm.status.master.priority is 0


def test_status_priority_manual(charm):
    class CharmStatus(StatusPool):
        SKIP_UNKNOWN = True

        workload = Status(priority=12)
        relation_1 = Status(priority=2)
        relation_2 = Status(tag="rel2", priority=7)

    class MyCharm(CharmBase):
        _STATUS_CLS = CharmStatus

        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = CharmStatus(self)

    harness = Harness(MyCharm)
    harness._storage.drop_snapshot("MyCharm/CharmStatus[compound_status]")
    harness.begin_with_initial_hooks()
    charm = harness.charm

    assert charm.status.workload.priority == 12
    assert Status.sort(charm.status.master.children) == [
        charm.status.relation_1,
        charm.status.relation_2,
        charm.status.workload,
    ]


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
    assert restored_charm.status.master.message == "(relation_1) foo"

    # we have reinited the charm, so harness has set it to ''
    # assert restored_charm.unit.status.message == "foo"


def test_auto_commit(charm_type):
    charm_type._STATUS_CLS.AUTO_COMMIT = True
    with HarnessCtx(charm_type, "update-status") as h:
        charm = h.harness.charm
        charm.status.relation_2 = ActiveStatus("noop")
        charm.status.relation_1 = ActiveStatus("boop")

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == charm.status.master._clobber_statuses(
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
    assert charm.unit.status.message == charm.status.master._clobber_statuses(
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
    assert charm.unit.status.message == "(workload) woot"


def test_dynamic_pool():
    class CharmStatus(StatusPool):
        SKIP_UNKNOWN = True

    class MyCharm(CharmBase):
        _STATUS_CLS = CharmStatus

        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = CharmStatus(self)

    h: Harness[MyCharm] = Harness(MyCharm)
    h.begin()

    pool = h.charm.status
    pool.add_status(Status(tag='foo')._set('active', 'foo'))
    pool.add_status(Status(tag='bar')._set('active', 'bar'))
    assert pool.foo.status == 'active'
    assert pool.foo.message == 'foo'
    assert pool.bar.status == 'active'
    assert pool.bar.message == 'bar'

    master = pool.master
    assert len(master.children) == 2
    pool.add_status(Status(tag='woo')._set('blocked', 'meow'))
    assert len(master.children) == 3

    # this will work
    woo = Status(tag='woo')
    pool.add_status(woo, attr='wooz')
    assert len(master.children) == 4
    pool.remove_status(woo)
    assert len(master.children) == 3
    assert woo._master is None
    assert woo._logger is None
    assert woo not in master.children


def test_dynamic_pool_persistence():
    class CharmStatus(StatusPool):
        SKIP_UNKNOWN = True

    class MyCharm(CharmBase):
        _STATUS_CLS = CharmStatus

        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = CharmStatus(self)

    h: Harness[MyCharm] = Harness(MyCharm)
    h.begin()

    pool = h.charm.status
    foo = Status(tag='foo')._set('active', 'foo')
    pool.add_status(foo)
    pool.commit()

    h2 = Harness(MyCharm)
    # copy over the storage
    h2._storage = h._storage
    h2.framework._storage = h._storage

    h2.begin()
    assert h2.charm.status.foo == foo

    # and now without copying over the storage
    h3 = Harness(MyCharm)
    h3.begin()
    assert not hasattr(h3.charm.status, 'foo')


def test_recursive_pool():
    """Test for a specific use case"""

    class CharmStatus(StatusPool):
        SKIP_UNKNOWN = True
        master = MasterStatus(clobberer=Summary())
        relation_1 = Status()

    class MyCharm(CharmBase):
        _STATUS_CLS = CharmStatus

        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = CharmStatus(self)

        def update_relation_1_status(self, statuses: dict):
            class RelationStatus(StatusPool):
                KEY = "relation_1"
                master = MasterStatus(tag='relation_1', clobberer=Summary())

            relation_status = RelationStatus(self)

            for relation in self.model.relations['relation_1']:
                tag = relation.app.name.replace('-', '_')
                relation_status.add_status(Status(tag))

            for key, value in statuses.items():
                setattr(relation_status, key, value)

            self.status.relation_1 = relation_status.master.coalesce()

    h = Harness(MyCharm, meta=yaml.safe_dump(
        {"requires": {"relation_1": {"interface": "foo"}}}))
    h.begin()
    charm = h.charm

    r1_id = h.add_relation("relation_1", "remote_app_1")
    h.add_relation_unit(r1_id, 'remote_app_1/0')
    r2_id = h.add_relation("relation_1", "remote_app_2")
    h.add_relation_unit(r2_id, 'remote_app_2/0')
    r3_id = h.add_relation("relation_1", "remote_app_3")
    h.add_relation_unit(r3_id, 'remote_app_3/0')

    charm.update_relation_1_status(
        {
            "remote_app_1": ActiveStatus("this relation is OK"),
            "remote_app_2": WaitingStatus("this relation is waiting"),
            "remote_app_3": BlockedStatus("this relation is BORK")
        }
    )

    charm.status.commit()
    assert charm.unit.status.name == 'blocked'
    assert charm.unit.status.message == '(relation_1:blocked) (remote_app_3:blocked) this relation is BORK; (remote_app_2:waiting) this relation is waiting; (remote_app_1:active) this relation is OK'
