In complex charms that have multiple parallel duties, we often see that it is not enough to know if the charm as a whole is `active/blocked/waiting/...`; charmers search for ways to track multiple statuses independently. We want to tell the user: 'the charm as a whole is blocked, because this part is blocked, that other part is waiting, and that other part over there is doing maintenance'.
That is, we want a taxonomy of statuses.

The first step to get there is to allow charmers to define the status as a pool of independently-trackable statuses that can be associated with tasks, components, integrations, or (why not) individual relations.

# What this is

This charm lib exposes utilities to create 'status pools'.
```python
from compound_status import StatusPool, Status

class MyCharm(CharmBase):
    def __init__(self, framework, key=None):
        super().__init__(framework, key)
        status_pool = StatusPool(self)
        status_pool.add(Status("workload"))  # this tracks workload status
        status_pool.add(Status("relation_1"))  # this tracks my integration #1
        status_pool.add(Status("relation_2"))  # this tracks my integration #2

        status_pool.set_status("relation_1", ActiveStatus('✅'))
        status_pool.relation_2 = ActiveStatus('✅')

        workload_status = status_pool.workload
        workload_status.status = ActiveStatus('✅')
        ...

        status_pool.commit()  # sync with juju

        status_pool.relation_1.unset()  # send status_1 back to unknown, until you set it again.

        status_pool.relation_2 = WaitingStatus('𝌗: foo')

        # write some logs with automatic prefixes based on the status name
        status_pool.workload.warning('found something weird')
        status_pool.workload.info('attempting to work around...')
        status_pool.workload.error('whoopsiedaisies!')

        status_pool.workload = BlockedStatus('💔')
        status_pool.commit()
```

Juju status will display:

`tst/0*  blocked   idle   <IP>   (workload) 💔`

Only the 'worst' status is displayed.
The logging message, tagged with `['workload']`, will be visible in `juju debug-log`.

## The statuses

Sorted best-to-worst, all possible statuses are:
 - `unknown`
 - `active`
 - `maintenance`
 - `waiting`
 - `blocked`

Their intended usage mirrors that of [statuses in charms](https://discourse.charmhub.io/t/status-values/1168).

In `ops` you can't set a Unit status to `unknown`. Unknown is reserved for units that are not initialized yet (i.e. the charm hasn't had a chance to run).

Here `unknown` is also special.

  - When you first create the pool, all statuses start off as `unknown`.
  - `unknown` means: not relevant/not interesting, such as when a non-necessary relation for a charm is not present. As such, you typically don't want to surface unknown statuses to the user. Therefore, you can also choose to init the pool with attribute `skip_unknown=True` to automatically hide `unknown` statuses from the summarized pool message.
  - As soon as you set a status, the status can be brought back to `unknown` by calling `Status.unset()` (a handy shortcut).  You can also set the status to `UnknownStatus()` directly.

## Priority

To unambiguously be able to point out the 'worst' status in a pool, the concept of `priority` comes into play.
By default, the order of addition of the Statuses in the pool determines their priority:
from first to list == from most important to least important.
Example:

```python
from compound_status import *
status_pool = StatusPool(self)
status_pool.add(Status("workload"))  # priority 1
status_pool.add(Status("relation_1"))  # priority 2
status_pool.add(Status("relation_2"))  # priority 3
```

In this case, if all are active except `workload` and `relation_2`, which are both `blocked`, only the status for `workload` will be shown, because it has been added first and has therefore implicitly priority 3.

To allow more flexibility, you can also manually pass priorities to the Statuses, like so:

```python
from compound_status import *
status_pool = StatusPool(self)
status_pool.add(Status("workload", priority=99))  # priority 3
status_pool.add(Status("relation_1", priority=1))  # priority 1
status_pool.add(Status("relation_2", priority=3))  # priority 2
```

In this case, if `workload` has lower priority than `relation_1`, so if both are blocked `relation_1` will take precedence.

Notes:
- priority defaults to `0` if not explicitly set, thus ties are broken by insertion order (assuming stable sorting is used in summarizing functions).

TODO: document how to use and create summarizer functions.

TODO: document what auto_commit does and what commits are in this context.

## Example

Here's an example that uses more advanced features:

```python
from compound_status import StatusPool, Status
from ops.charm import RelationDepartedEvent, RelationJoinedEvent

class MyPool(StatusPool):
    workload = Status()

class MyCharm(CharmBase):
    def __init__(self, framework, key=None):
        super().__init__(framework, key)
        self.status_pool = StatusPool(self)
        self.status_pool.add(Status('workload'))
        self.framework.observe(self.on.workload_pebble_ready,
                               self._workload_ready)
        self.framework.observe(self.on.foo_relation_joined,
                               self._foo_relation_joined)
        self.framework.observe(self.on.foo_relation_changed,
                               self._foo_relation_changed)
        self.framework.observe(self.on.foo_relation_departed,
                               self._foo_relation_departed)

    def _workload_ready(self, evt):
        # do your thing
        self.status_pool.workload = ActiveStatus()

    def _foo_relation_joined(self, event:RelationJoinedEvent):
        remote_unit_name = event.unit  # the unit that just joined
        status = Status(remote_unit_name)
        self.status_pool.add(status)

        # now it's in the pool
        stat = self.status_pool.get(remote_unit_name)
        assert stat is status

    def _foo_relation_changed(self, event):
        for remote_unit in event.relation.units:
            # you can access the 'previous' status:
            # same as: getattr(self.status_pool, identifier)
            previous_status = self.status_pool.get(remote_unit.name)
            print(previous_status)

            # for example
            new_status = WaitingStatus('this relation is waiting')
            previous_status.warning('waiting because...')

            # and then you can
            self.status_pool.set_status(remote_unit.name, new_status)
            # same as this, because remember that `previous_status` is a `Status` object in the pool:
            # previous_status.status = new_status

    def _foo_relation_departed(self, event: RelationDepartedEvent):
        remote_unit_name = event.departing_unit.name
        current_status = self.status_pool.get(remote_unit_name)
        if current_status.get_status_name() == 'blocked':
            current_status.error(
                'This unit departed while the relation status was blocked;'
                'this means very bad things.')
        # forget about this status:
        self.status_pool.remove_status(current_status)
```


# How to get it

`charmcraft fetch-lib charms.compound_status.v0.compound_status`

# How to contribute
if you want to publish a new revision, you can run `scripts/update.sh`.
This will
 - Bump the revision
 - Inline the lib
 - Publish the lib

When you bump to a new (major) version, you'll have to manually change the
value of `$LIB_V` in `scripts/publish.sh`.

# Demo charm

There is a demo charm implementing StatusPool with both a static and a dynamic use case, where you can try out most of the features in a live setup.
https://github.com/PietroPasotti/status-pool-example
