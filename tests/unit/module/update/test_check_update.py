from unittest.mock import Mock, patch

from app import resource_sync_coordinator
from module.update import check_update


def test_local_build_version_skips_remote_update_check():
    statuses = []
    thread = check_update.UpdateThread(timeout=5, flag=False)
    thread.updateSignal.connect(statuses.append)

    with (
        patch.object(check_update.cfg, "version", check_update.LOCAL_BUILD_VERSION),
        patch.object(thread, "check_update_info_mirrorchyan") as remote_check,
    ):
        thread.run()

    assert statuses == [check_update.UpdateStatus.SUCCESS]
    assert thread.is_current_version_latest is True
    remote_check.assert_not_called()


def test_local_build_version_skips_startup_resource_sync():
    coordinator = Mock()

    with patch.object(
        resource_sync_coordinator.cfg,
        "version",
        check_update.LOCAL_BUILD_VERSION,
    ):
        resource_sync_coordinator.ResourceSyncCoordinator.start_startup_check(coordinator)

    coordinator._continue_startup_sequence_once.assert_called_once_with()
    coordinator._start_resource_sync_gate_check.assert_not_called()
