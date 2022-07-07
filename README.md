from ops.framework import Framework# What this is

```python
from compound_status import *

class MyPool(StatusPool):
    SKIP_UNKNOWN = True

    workload = Status()
    relation_1 = Status()
    relation_2 = Status(tag='rel2')


class TesterCharm(CharmBase):
    def __init__(self, framework, key=None):
        super().__init__(framework, key)
        status_pool = MyPool(self)

        # pro tip: keep the messages short
        status_pool.relation_1 = ActiveStatus('✅')
        status_pool.commit()  # sync with juju
        
        status_pool.relation_1.unset()  # send status_1 back to unknown, until you set it again. 
        
        status_pool.relation_2 = WaitingStatus('𝌗: foo')
        status_pool.workload.warning('some debug message about why the workload is blocked')
        status_pool.workload.info('some info about the workload')
        status_pool.workload.error('whoopsiedaisies')
        status_pool.workload = BlockedStatus('see debug-log for the reason')
        status_pool.commit()   
``` 

You get:
`tst/0*  blocked   idle   <IP>   (workload) 💔`
Only the 'worst' status is displayed.


## Priority

To disambiguate when you have multiple equivalent statuses, the concept of `priority` comes into play.
By default, the order of definition of the Statuses in the pool determines their priority:
from top to bottom = from most important to least important.
Example:

```python
from compound_status import *
class MyPool(StatusPool):
    SKIP_UNKNOWN = True

    relation_1 = Status()       # priority 1
    relation_2 = Status()       # priority 2
    relation_3 = Status()       # priority 3
    workload = Status()         # priority 4
    relation_4 = Status()       # priority 5
```

In this case, if all are active except `relation_3` and `workload`, which are both `blocked`, only the status for `relation_3` will be shown, because it has been defined first and has therefore implicitly priority 3.

To allow more flexibility (subclassing, whatnot), you can also manually pass priorities to the Statuses, like so:

```python
from compound_status import *
class MyPool(StatusPool):
    SKIP_UNKNOWN = True

    relation_1 = Status(priority=12)
    relation_2 = Status(priority=10)
    relation_3 = Status(priority=62)
    workload = Status(priority=40)
    relation_4 = Status(priority=1)
```

In this case, if `relation_3` has lower priority than `workload`, so if both are blocked `workload` will take precedence.

Caveats:
 - you can't mix 'manual' and 'auto' priority modes: either you pass `priority:int` to each and every status, or to none at all.
 - You have to ensure yourself that no two Statuses have the same priority. In that case, the precedence will be (presumably) random.


## Dynamically defining Statuses

Having statically defined Statuses is nice because you get code completion, type hints, and so on, but sometimes it's not enough. Sometimes you want to use statuses to track intrinsically dynamic things, such as many relations attached to an endpoint. Every time a unit joins, you want to track the status of the relation with that remote in a separate status. For that purpose, we offer `StatusPool.add_status` and `StatusPool.remove_status`.

Example usage (pseudocody):

```python
from compound_status import *
from ops.charm import RelationDepartedEvent, RelationJoinedEvent

class MyPool(StatusPool):
    SKIP_UNKNOWN = True
    workload = Status(priority=40)


class MyCharm(CharmBase):
    def __init__(self, framework, key=None):
        super().__init__(framework, key)
        self.status_pool = MyPool(self)
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
        identifier = remote_unit_name.replace('/', '_')
        status = Status(tag=remote_unit_name)
        self.status_pool.add_status(status, identifier)
        
        # from now on you can:
        stat = getattr(self.status_pool, identifier)
        assert stat is status
        
    def _foo_relation_changed(self, event):
        for remote_unit in event.relation.units:
            identifier = remote_unit.name.replace('/', '_')
            
            # you can access the 'previous' status:
            # same as: getattr(self.status_pool, identifier)
            previous_status = self.status_pool.get_status(identifier)
            print(previous_status)
            
            # for example
            new_status = WaitingStatus('this relation is waiting')
            previous_status.warning('waiting because...')
            
            # and then you can
            self.status_pool.set_status(identifier, new_status)
            # same as: setattr(self.status_pool, identifier, new_status)
            
    def _foo_relation_departed(self, event: RelationDepartedEvent):
        remote_unit_name = event.departing_unit.name
        identifier = remote_unit_name.replace('/', '_')
        current_status = self.status_pool.get_status(identifier)
        if current_status.status == 'blocked':
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
