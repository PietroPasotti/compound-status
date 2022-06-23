# What this is

```python
class CharmStatus(CompoundStatus):
    SKIP_UNKNOWN = True

    workload = Status()
    relation_1 = Status()
    relation_2 = Status(tag='rel2')


class TesterCharm(CharmBase):
    def __init__(self, framework, key=None):
        super().__init__(framework, key)
        status = CharmStatus(self)

        with status.hold():
            status.relation_1 = ActiveStatus('✅')
            status.relation_2 = WaitingStatus('𝌗: foo')
            status.workload.warning('some debug message about why the workload is blocked')
            status.workload.info('some info about the workload')
            status.workload.set('blocked', '💔')
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
