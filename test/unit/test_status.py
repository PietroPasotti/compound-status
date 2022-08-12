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

from compound_status import StatusPool, Status, summarize_condensed, summarize_worst_first, summarize_worst_only, _priority_key
from harnessctx import HarnessCtx


@pytest.fixture(scope="function")
def charm_type():

    class MyCharm(CharmBase):
        AUTO_COMMIT = False
        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = StatusPool(self, skip_unknown=True, auto_commit=self.AUTO_COMMIT)
            self.status.add(Status("workload"))
            self.status.add(Status("relation_1"))
            self.status.add(Status("relation_2"))

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
    assert len(charm.status._pool) == 3


def test_statuses_setting(charm):
    assert charm.unit.status.name == "unknown"
    assert charm.unit.status.message == ""

    charm.status.set_status("relation_1", ActiveStatus("foo"))
    charm.status.commit()

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == "(relation_1) foo"


def test_statuses_setting_alternate(charm):
    assert charm.unit.status.name == "unknown"
    assert charm.unit.status.message == ""

    charm.status.relation_1 = ActiveStatus("foo")
    charm.status.commit()

    assert charm.status.relation_1 is charm.status._pool["relation_1"]

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
    clb = summarize_worst_only(statuses, False)
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
    clb = summarize_condensed(statuses, False)
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
    clb = summarize_worst_first(statuses, True)
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
    ordered = sorted(statuses, key=_priority_key)
    assert tuple(status.name for status in ordered) == expected_order


def test_status_priority_auto(charm):
    assert charm.status.get("workload").priority == 0
    assert charm.status.get("relation_1").priority == 0
    assert charm.status.get("relation_2").priority == 0
    assert sorted(charm.status._pool.values(), key=_priority_key) == [
        charm.status.get("workload"),
        charm.status.get("relation_1"),
        charm.status.get("relation_2"),
    ]


def test_status_priority_manual(charm):
    class MyCharm(CharmBase):
        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = StatusPool(self, skip_unknown=True)
            self.status.add(Status("workload", priority=12))
            self.status.add(Status("relation_1", priority=2))
            self.status.add(Status("relation_2", priority=7))

    harness = Harness(MyCharm)
    harness._storage.drop_snapshot("MyCharm/CharmStatus[compound_status]")
    harness.begin_with_initial_hooks()
    charm = harness.charm

    assert charm.status.get("workload").priority == 12
    assert sorted(charm.status._pool.values(), key=_priority_key) == [
        charm.status.get("relation_1"),
        charm.status.get("relation_2"),
        charm.status.get("workload"),
    ]


# XXX: test harness doesn't save/load snapshots apparently
#      need to test this in an integration test with a real charm
# def test_stored_blank(charm):
#     charm.status.commit()

#     other_harness = Harness(type(charm))
#     other_harness.begin()
#     restored_charm = other_harness.charm
#     assert restored_charm.status.master.status == "unknown"
#     assert restored_charm.unit.status.name == "maintenance"
#     assert restored_charm.unit.status.message == ""


# XXX: test harness doesn't save/load snapshots apparently
#      need to test this in an integration test with a real charm
# def test_stored(charm):
#     charm.status.set_status("relation_1", BlockedStatus("foo"))
#     charm.status.commit()

#     other_harness = Harness(type(charm))
#     other_harness.begin()
#     restored_charm = other_harness.charm
#     assert restored_charm.status.summarize().name == "blocked"

#     restored_charm.status.commit()
#     assert restored_charm.unit.status.name == 'blocked'
#     assert restored_charm.unit.status.message == "(relation_1) foo"


def test_auto_commit(charm_type):
    charm_type.AUTO_COMMIT = True
    with HarnessCtx(charm_type, "update-status") as h:
        charm = h.harness.charm
        charm.status.set_status("relation_2", ActiveStatus("noop"))
        charm.status.set_status("relation_1", ActiveStatus("boop"))

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == summarize_worst_only(
        [charm.status.get("relation_1"), charm.status.get("relation_2")], False
    )


def test_auto_commit_off(charm_type):
    charm_type.AUTO_COMMIT = False
    with HarnessCtx(charm_type, "update-status") as h:
        charm = h.harness.charm
        charm.unit.status = MaintenanceStatus("")

        charm.status.set_status("relation_2", ActiveStatus("noop"))
        charm.status.set_status("relation_1", ActiveStatus("boop"))

    assert charm.unit.status.name == "maintenance"
    assert charm.unit.status.message == ""

    charm.status.commit()

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == summarize_worst_only(
        [charm.status.get("relation_1"), charm.status.get("relation_2")], False
    )


def test_auto_commit_with_setattr_magic(charm_type):
    charm_type.AUTO_COMMIT = True
    with HarnessCtx(charm_type, "update-status") as h:
        charm = h.harness.charm
        charm.status.relation_2 = ActiveStatus("noop")
        charm.status.relation_1 = ActiveStatus("boop")

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == summarize_worst_only(
        [charm.status.get("relation_1"), charm.status.get("relation_2")], False
    )


def test_unset(charm):
    charm.status.set_status("relation_1", ActiveStatus("foo"))
    charm.status.get("relation_1").unset()
    assert charm.status.get("relation_1").get_status_name() == "unknown"

    charm.status.commit()

    assert charm.unit.status.name == "unknown"
    assert charm.unit.status.message == ""


def test_unset_master(charm):
    charm.status.set_status("relation_1", ActiveStatus("foo"))
    charm.status.set_status("relation_2", BlockedStatus("bar"))
    charm.status.commit()

    charm.status.unset()

    charm.status.set_status("workload", ActiveStatus("woot"))
    charm.status.commit()

    # as if nothing happened
    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == "(workload) woot"


def test_dynamic_pool():
    class MyCharm(CharmBase):
        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = StatusPool(self, skip_unknown=True)


    h: Harness[MyCharm] = Harness(MyCharm)
    h.begin()

    pool = h.charm.status
    pool.add(Status('foo')._set('active', 'foo'))
    pool.add(Status('bar')._set('active', 'bar'))
    assert pool.get("foo").get() == ActiveStatus("foo")
    assert pool.get("bar").get() == ActiveStatus("bar")

    assert len(pool._pool) == 2
    pool.add(Status('woo')._set('blocked', 'meow'))
    assert len(pool._pool) == 3

    # already added a status with the same tag
    pool.add(Status('woo'))

    # this will work
    woo = Status('wooz')
    pool.add(woo)
    assert len(pool._pool) == 4
    pool.remove_status(woo)
    assert len(pool._pool) == 3
    assert woo not in pool._pool


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
    foo = Status('foo')._set('active', 'foo')
    pool.add(foo)
    pool.commit()

    h2 = Harness(MyCharm)
    # copy over the storage
    h2._storage = h._storage
    h2.framework._storage = h._storage

    h2.begin()
    assert h2.charm.status.get("foo") == foo

    # and now without copying over the storage
    h3 = Harness(MyCharm)
    h3.begin()
    assert h3.charm.status.get("foo") is None


def test_recursive_pool():
    """Test for a specific use case"""

    class MyCharm(CharmBase):
        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = StatusPool(self, skip_unknown=True, summarizer=summarize_worst_first)
            self.status.add(Status("relation_1"))

        def update_relation_1_status(self, statuses: dict):
            relation_status = StatusPool(self, key="relation_1", summarizer=summarize_worst_first)

            for relation in self.model.relations['relation_1']:
                relation_status.add(Status(relation.app.name))

            for key, value in statuses.items():
                relation_status.set_status(key, value)

            self.status.set_status("relation_1", relation_status.summarize())

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
