#!/usr/bin/env python3
# Copyright 2022 pietro
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, WaitingStatus, BlockedStatus
from charms.compound_status.v0.compound_status import CompoundStatus, Status

logger = logging.getLogger(__name__)


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


if __name__ == "__main__":
    main(TesterCharm)
