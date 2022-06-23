import pytest
from ops.charm import CharmBase
from ops.model import UnknownStatus, ActiveStatus, WaitingStatus, BlockedStatus
from ops.testing import Harness

from compound_status import CompoundStatus, Status


@pytest.fixture
def harness():
    class CharmStatus(CompoundStatus):
        SKIP_UNKNOWN = True

        workload = Status()
        relation_1 = Status()
        relation_2 = Status(tag='rel2')

    class MyCharm(CharmBase):
        def __init__(self, framework, key=None):
            super().__init__(framework, key)
            self.status = CharmStatus(self)

    harness = Harness(MyCharm)
    harness.begin_with_initial_hooks()
    return harness


@pytest.fixture
def charm(harness):
    return harness.charm


def test_statuses_collection(charm):
    assert len(charm.status.master.children) == 3


def test_statuses_setting(charm):
    assert charm.unit.status.name == 'unknown'
    assert charm.unit.status.message == ''

    charm.status.relation_1.set('active', 'foo')

    assert charm.unit.status.name == 'active'
    assert charm.unit.status.message == '[relation_1] (active) foo'


def test_statuses_setting_magic(charm):
    assert charm.unit.status.name == 'unknown'
    assert charm.unit.status.message == ''

    charm.status.relation_1 = ActiveStatus('foo')

    assert charm.unit.status.name == 'active'
    assert charm.unit.status.message == '[relation_1] (active) foo'


def test_statuses_priority(charm):
    charm.status.relation_1 = ActiveStatus('foo')
    charm.status.relation_2 = WaitingStatus('bar')

    assert charm.status.master.status == 'waiting'
    assert charm.status.master.message == "[relation_1] (active) foo; [rel2] (waiting) bar"

    charm.status.workload = BlockedStatus('qux')
    assert charm.status.master.status == 'blocked'
    assert charm.status.master.message == "[workload] (blocked) qux; [relation_1] (active) foo; [rel2] (waiting) bar"


def test_statuses_master_override(charm):
    charm.status.relation_1 = ActiveStatus('foo')
    charm.status.relation_2 = WaitingStatus('bar')
    charm.status.master = ActiveStatus('overruled!')

    assert charm.status.master.status == charm.unit.status.name == 'active'
    assert charm.status.master.message == charm.unit.status.message == "overruled!"


def test_hold(charm):
    charm.status.set('active', '1')
    with charm.status.hold():
        charm.status.relation_1 = ActiveStatus('foo')
        assert charm.status.master.status == charm.unit.status.name == 'active'
        assert charm.status.master.message == charm.unit.status.message == "1"
        charm.status.relation_2 = WaitingStatus('bar')
        assert charm.status.master.status == charm.unit.status.name == 'active'
        assert charm.status.master.message == charm.unit.status.message == "1"
        charm.status.master = ActiveStatus('2')
        assert charm.status.master.status == charm.unit.status.name == 'active'
        assert charm.unit.status.message == "1"
        assert charm.status.master.message == "2"
    assert charm.status.master.status == charm.unit.status.name == 'active'
    assert charm.status.master.message == charm.unit.status.message == "2"


def test_hold_no_sync(charm):
    charm.status.set('active', '1')
    with charm.status.hold(sync=False):
        charm.status.relation_1 = ActiveStatus('foo')
        assert charm.status.master.status == charm.unit.status.name == 'active'
        assert charm.status.master.message == charm.unit.status.message == "1"
        charm.status.relation_2 = WaitingStatus('bar')
        assert charm.status.master.status == charm.unit.status.name == 'active'
        assert charm.status.master.message == charm.unit.status.message == "1"
        charm.status.master = ActiveStatus('2')
        # now the master status does change!
        assert charm.status.master.status == 'active'
        assert charm.status.master.message == "2"
        # but not the unit status.
        assert charm.unit.status.name == 'active'
        assert charm.unit.status.message == "1"

    # we didn't sync, so everything is as before, still
    assert charm.status.master.status == charm.unit.status.name == 'active'
    assert charm.status.master.message == "2"
    assert charm.unit.status.message == "1"

    charm.status.update()

    # now all is nice and sync.
    assert charm.status.master.status == charm.unit.status.name == 'active'
    assert charm.status.master.message == charm.unit.status.message == "2"



