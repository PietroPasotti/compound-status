# What this is

```python
class StatusPool(CompoundStatus):
    SKIP_UNKNOWN = True

    workload = Status()
    relation_1 = Status()
    relation_2 = Status(tag='rel2')


class TesterCharm(CharmBase):
    def __init__(self, framework, key=None):
        super().__init__(framework, key)
        status_pool = StatusPool(self)

        # pro tip: keep the messages short
        status_pool.relation_1 = ActiveStatus('✅')
        status_pool.commit()  # sync with juju
        
        status_pool.relation_1.unset()  # send status_1 back to unknown, until you set it again. 
        
        status_pool.relation_2 = WaitingStatus('𝌗: foo')
        status_pool.workload.warning('some debug message about why the workload is blocked')
        status_pool.workload.info('some info about the workload')
        status_pool.workload.error('whoopsiedaisies')
        status_pool.workload = BlockedStatus('blocked', 'see debug-log for the reason')
        status_pool.commit()   
``` 

You get:
`tst/0*  blocked   idle   <IP>   [workload] (blocked) 💔; [relation_1] (active) ✅; [rel2] (waiting) 𝌗: foo`


# How to contribute
if you want to publish a new revision, you can run `scripts/update.sh`.
This will 
 - Bump the revision
 - Inline the lib
 - Publish the lib

When you bump to a new (major) version, you'll have to manually change the 
value of `$LIB_V` in `scripts/publish.sh`.
