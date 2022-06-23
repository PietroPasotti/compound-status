import shutil
from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest


@pytest.fixture
async def tester_charm(ops_test: OpsTest):
    lib_source = Path() / "lib" / "charms" / "compound_status" / "v1" / "compound_status.py"
    charm_root = Path(__file__).parent / "tester"
    libs_folder = charm_root / "lib" / "charms" / "compound_status" / "v1"
    libs_folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(lib_source, libs_folder)
    yield await ops_test.build_charm(charm_root)
    shutil.rmtree(charm_root / "lib")


async def test_deploy(tester_charm, ops_test: OpsTest):
    await ops_test.model.deploy(tester_charm)
    await ops_test.model.wait_for_idle('tester')
    assert await ops_test.model.units['tester/0'].status