import json

import pytest
import yaml
from ops.charm import CharmBase
from ops.framework import Handle, StoredStateData
from ops.model import (
    UnknownStatus,
    ActiveStatus,
    WaitingStatus,
    BlockedStatus,
    MaintenanceStatus,
)
from ops.testing import Harness

from compound_status import StatusPool, Status, WorstOnly, Summary, Condensed
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
    harness._storage.drop_snapshot("MyCharm/CharmStatus[compound_status]")

    harness.begin_with_initial_hooks()
    return harness


@pytest.fixture(scope="function")
def charm(harness):
    return harness.charm


def test_statuses_collection(charm):
    assert len(charm.status._statuses) == 3


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


def test_statuses_setting_magic_keep_equality(charm):
    assert charm.unit.status.name == "unknown"
    assert charm.unit.status.message == ""

    charm.status.relation_1 = ActiveStatus("foo")
    charm.status.commit()

    breakpoint()
    assert charm.status.relation_1 == ActiveStatus("foo")
    assert ActiveStatus("foo") == charm.status.relation_1


@pytest.mark.parametrize(
    "statuses, expected_message",
    (
        (
            (
                Status("foo", 1)._set("active", "argh"),
                Status("bar", 2)._set("active"),
                Status("baz", 3)._set("active"),
            ),
            "(foo) argh",
        ),
        (
            (
                Status("foo", 1)._set("active"),
                Status("bar", 2)._set("blocked", "wof"),
                Status("baz", 3)._set("active"),
            ),
            "(bar) wof",
        ),
        (
            (
                Status("foo", 1)._set("active"),
                Status("bar", 2)._set("waiting"),
                Status("baz", 3)._set("blocked", "meow"),
            ),
            "(baz) meow",
        ),
    ),
)
def test_worst_only_clobber(statuses, expected_message):
    clb = WorstOnly().message(statuses)
    assert clb == expected_message


@pytest.mark.parametrize(
    "statuses, expected_message",
    (
        (
            (
                Status("foo", 1)._set("active", "argh"),
                Status("bar", 2)._set("active"),
                Status("baz", 3)._set("active"),
            ),
            "",
        ),
        (
            (
                Status("foo", 1)._set("active"),
                Status("bar", 2)._set("blocked", "wof"),
                Status("baz", 3)._set("active"),
            ),
            "1 blocked; 2 active",
        ),
        (
            (
                Status("foo", 1)._set("active"),
                Status("bar", 2)._set("waiting"),
                Status("baz", 3)._set("blocked", "meow"),
            ),
            "1 blocked; 1 waiting; 1 active",
        ),
    ),
)
def test_condensed_clobber(statuses, expected_message):
    clb = Condensed().message(statuses)
    assert clb == expected_message


@pytest.mark.parametrize(
    "statuses, expected_message",
    (
        (
            (
                Status("foo", 1)._set("active", "argh"),
                Status("bar", 2)._set("active"),
                Status("baz", 3)._set("active"),
            ),
            "(foo:active) argh; (bar:active) ; (baz:active) ",
        ),
        (
            (
                Status("foo", 1)._set("active"),
                Status("bar", 2)._set("blocked", "wof"),
                Status("baz", 3)._set("active"),
            ),
            "(bar:blocked) wof; (foo:active) ; (baz:active) ",
        ),
        (
            (
                Status("foo", 1)._set("active"),
                Status("bar", 2)._set("waiting"),
                Status("baz", 3)._set("blocked", "meow"),
            ),
            "(baz:blocked) meow; (bar:waiting) ; (foo:active) ",
        ),
    ),
)
def test_summary_clobber(statuses, expected_message):
    clb = Summary().message(statuses)
    assert clb == expected_message


@pytest.mark.parametrize(
    "statuses, expected_order",
    (
        (
            (
                Status("foo", 1)._set("active"),
                Status("bar", 2)._set("active"),
                Status("baz", 3)._set("active"),
            ),
            ("foo", "bar", "baz"),
        ),
        (
            (
                Status("foo", 1)._set("active"),
                Status("bar", 2)._set("blocked"),
                Status("baz", 3)._set("active"),
            ),
            ("bar", "foo", "baz"),
        ),
        (
            (
                Status("foo", 1)._set("active"),
                Status("bar", 2)._set("waiting"),
                Status("baz", 3)._set("blocked"),
            ),
            ("baz", "bar", "foo"),
        ),
    ),
)
def test_status_sorting(statuses, expected_order):
    ordered = Status.sort(statuses)
    assert tuple(status.tag for status in ordered) == expected_order


def test_status_priority_auto(charm):
    assert charm.status.workload.priority == 0
    assert charm.status.relation_1.priority == 0
    assert charm.status.relation_2.priority == 0


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
    assert Status.sort(charm.status._statuses.values()) == [
        charm.status.relation_1,
        charm.status.relation_2,
        charm.status.workload,
    ]


def test_hold(charm):
    assert charm.unit.status.name == "unknown"
    assert charm.unit.status.message == ""

    charm.status.relation_1 = ActiveStatus("foo")
    status = charm.status.commit()

    assert status.name == charm.unit.status.name == "active"
    assert status.message == charm.unit.status.message == "(relation_1) foo"

    charm.status.relation_2 = WaitingStatus("bar")

    assert status.name == charm.unit.status.name == "active"
    assert status.message == charm.unit.status.message == "(relation_1) foo"

    status = charm.status.coalesce()
    assert status.name == "waiting"
    assert status.message == "(rel2) bar"

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == "(relation_1) foo"

    charm.status.commit()

    assert status.name == charm.unit.status.name == "waiting"
    assert status.message == charm.unit.status.message == "(rel2) bar"


def test_hold_no_sync(charm):
    charm.status.relation_1 = ActiveStatus("foo")
    status = charm.status.commit()

    assert status.name == charm.unit.status.name == "active"
    assert status.message == charm.unit.status.message == "(relation_1) foo"

    # now we start touching stuff
    charm.status.relation_2 = WaitingStatus("bar")
    status = charm.status.coalesce()

    # desync
    assert status.name == "waiting"
    assert status.message == "(rel2) bar"
    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == "(relation_1) foo"

    charm.status.commit()

    # now all is nice and sync.
    assert status.name == charm.unit.status.name == "waiting"
    assert status.message == charm.unit.status.message == "(rel2) bar"


def test_stored_blank(charm):
    charm.status.commit()

    other_harness = Harness(type(charm))
    other_harness.begin()
    restored_charm = other_harness.charm
    assert restored_charm.status.coalesce().name == "unknown"
    assert restored_charm.unit.status.name == "maintenance"
    assert restored_charm.unit.status.message == ""


def carry_over_stored(old_framework, new_framework, obj, kind, key):
    """Utility to copy over stored data between framework instances."""
    h = Handle(obj, kind, key)
    data = old_framework._objects.pop(h.path)
    new_framework._storage.save_snapshot(h.path, data._cache)


def test_stored(charm):
    charm.status.relation_1 = BlockedStatus("foo")
    charm.status.commit()
    charm.framework.commit()

    other_harness = Harness(type(charm))
    carry_over_stored(
        charm.framework, other_harness.framework, charm.status, "StoredStateData", "_state"
    )

    other_harness.begin()

    other_charm = other_harness.charm
    statuses = json.loads(other_charm.status._state.statuses)

    assert statuses
    assert (
        statuses["relation_1"]
        == charm.status.relation_1._snapshot()
        == other_charm.status.relation_1._snapshot()
    )

    status = other_charm.status.coalesce()
    assert status.name == "blocked"
    assert status.message == "(relation_1) foo"


def test_auto_commit(charm_type):
    charm_type._STATUS_CLS.AUTO_COMMIT = True
    with HarnessCtx(charm_type, "update-status") as h:
        charm = h.harness.charm
        charm.status.relation_2 = ActiveStatus("noop")
        charm.status.relation_1 = ActiveStatus("boop")

    assert charm.unit.status.name == "active"
    assert charm.unit.status.message == charm.status._facade.message(
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
    assert charm.unit.status.message == charm.status._facade.message(
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
    pool.add_status(Status(tag="foo")._set("active", "foo"))
    pool.add_status(Status(tag="bar")._set("active", "bar"))
    assert pool.foo.status == "active"
    assert pool.foo.message == "foo"
    assert pool.bar.status == "active"
    assert pool.bar.message == "bar"

    statuses = pool._statuses
    assert len(statuses) == 2
    pool.add_status(Status(tag="woo")._set("blocked", "meow"))
    assert len(statuses) == 3

    # this will work
    woo = Status(tag="woo")
    pool.add_status(woo, attr="wooz")
    assert len(statuses) == 4
    pool.remove_status(woo)
    assert len(statuses) == 3
    assert woo._logger is None
    assert woo not in statuses


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
    foo = Status(tag="foo")._set("active", "foo")
    pool.add_status(foo)
    pool.commit()

    h2 = Harness(MyCharm)
    carry_over_stored(h.framework, h2.framework, pool, "StoredStateData", "_state")
    h2.begin()
    assert h2.charm.status.foo == foo

    # and now without copying over the storage
    h3 = Harness(MyCharm)
    h3.begin()
    assert not hasattr(h3.charm.status, "foo")


def test_recursive_pool():
    """Test for a specific use case"""

    class CharmStatus(StatusPool):
        SKIP_UNKNOWN = True
        relation_1 = Status()

    class MyCharm(CharmBase):
        _STATUS_CLS = CharmStatus

        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = CharmStatus(self, facade=Summary())

        def update_relation_1_status(self, statuses: dict):
            class RelationStatus(StatusPool):
                KEY = "relation_1"

            relation_status = RelationStatus(self, facade=Summary())

            for relation in self.model.relations["relation_1"]:
                tag = relation.app.name.replace("-", "_")
                relation_status.add_status(Status(tag))

            for key, value in statuses.items():
                setattr(relation_status, key, value)

            self.status.relation_1 = relation_status.coalesce()

    h = Harness(MyCharm, meta=yaml.safe_dump({"requires": {"relation_1": {"interface": "foo"}}}))
    h.begin()
    charm = h.charm

    r1_id = h.add_relation("relation_1", "remote_app_1")
    h.add_relation_unit(r1_id, "remote_app_1/0")
    r2_id = h.add_relation("relation_1", "remote_app_2")
    h.add_relation_unit(r2_id, "remote_app_2/0")
    r3_id = h.add_relation("relation_1", "remote_app_3")
    h.add_relation_unit(r3_id, "remote_app_3/0")

    charm.update_relation_1_status(
        {
            "remote_app_1": ActiveStatus("this relation is OK"),
            "remote_app_2": WaitingStatus("this relation is waiting"),
            "remote_app_3": BlockedStatus("this relation is BORK"),
        }
    )

    charm.status.commit()
    assert charm.unit.status.name == "blocked"
    assert (
        charm.unit.status.message == "(relation_1:blocked) "
        "(remote_app_3:blocked) this relation is BORK; "
        "(remote_app_2:waiting) this relation is waiting; "
        "(remote_app_1:active) this relation is OK"
    )
