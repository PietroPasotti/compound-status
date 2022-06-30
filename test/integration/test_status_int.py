import asyncio
import shutil
from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest


@pytest.fixture
async def tester_charm(ops_test: OpsTest):
    lib_source = Path() / "compound_status.py"
    charm_root = Path(__file__).parent / "tester"
    libs_folder = charm_root / "lib" / "charms" / "compound_status" / "v1"
    libs_folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(lib_source, libs_folder)
    yield await ops_test.build_charm(charm_root)
    shutil.rmtree(charm_root / "lib")


def assert_status(ops_test, name, message):
    status = ops_test.model.units["tester/0"].status
    assert status.agent_status == name
    assert status.agent_status_message == message


async def test_deploy(tester_charm, ops_test: OpsTest):
    await ops_test.model.deploy(tester_charm)
    await ops_test.model.wait_for_idle("tester")


async def test_initial_status(ops_test:OpsTest):
    assert_status(ops_test, "active", "")


async def test_status_persistence(tester_charm, ops_test: OpsTest):
    # cause update-status to fire a couple of times
    async with ops_test.fast_forward():
        await asyncio.sleep(11)

    # verify that status is unchanged
    assert_status(ops_test, "active", "")


async def test_status_change(tester_charm, ops_test: OpsTest):
    await ops_test.model.applications.get("tester").set_config(
        {"status": "waiting", "message": "for godot"}
    )

    # cause update-status to fire a couple of times
    async with ops_test.fast_forward():
        await asyncio.sleep(11)

    # verify that status is updated correctly
    assert await ops_test.model.units["tester/0"].agent_status == "waiting"
    assert await ops_test.model.units["tester/0"].agent_status_message == "for godot"
