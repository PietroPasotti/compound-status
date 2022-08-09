import asyncio
import shutil
from pathlib import Path

import pytest
import pytest_asyncio
from pytest_operator.plugin import OpsTest


@pytest_asyncio.fixture
async def tester_charm(ops_test: OpsTest):
    lib_source = Path() / "compound_status.py"
    charm_root = Path(__file__).parent / "tester"
    libs_folder = charm_root / "lib" / "charms" / "compound_status" / "v0"
    libs_folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(lib_source, libs_folder)
    charm = await ops_test.build_charm(charm_root)
    return charm


@pytest.mark.abort_on_fail
async def test_deploy(tester_charm, ops_test: OpsTest):
    await ops_test.model.deploy(tester_charm)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(['tester'],
                                           raise_on_error=False,
                                           raise_on_blocked=False)


def assert_status(ops_test, name, message=None, agent_status='idle'):
    unit = ops_test.model.units["tester/0"]
    assert unit.agent_status == agent_status
    assert unit.workload_status == name
    if message:
        assert unit.workload_status_message == message


async def test_initial_status(ops_test: OpsTest):
    assert_status(ops_test, "active")


async def test_status_persistence(ops_test: OpsTest):
    # cause update-status to fire a couple of times
    async with ops_test.fast_forward():
        await asyncio.sleep(11)

    # verify that status is unchanged
    assert_status(ops_test, "active")


async def test_status_change(tester_charm, ops_test: OpsTest):
    # this is how we force a change in relation2's status:
    await ops_test.model.applications.get("tester").set_config(
        {"status": "waiting", "message": "for godot"}
    )

    # cause update-status to fire a couple of times
    async with ops_test.fast_forward():
        await asyncio.sleep(10)

    # verify that status is updated correctly
    assert_status(ops_test, "waiting", "(relation_2) for godot")
