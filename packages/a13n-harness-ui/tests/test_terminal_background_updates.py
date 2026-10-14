import asyncio
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive import onboarding, shell, startup, updates
from a13n_harness_ui.updater import UpdateCommand


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_available_update_waits_for_chat_then_notifies_once(self):
        ready = asyncio.Event()
        notices = []
        with (
            patch.object(updates, "check_update", AsyncMock(return_value=updates.AvailableUpdate("0.11.1", "0.12.0"))),
            patch.object(updates, "update_command", return_value=UpdateCommand("uv", Path("tools"))),
        ):
            task = asyncio.create_task(updates.notify_update(Path("unused"), ready, notices.append))
            await asyncio.sleep(0)
            self.assertEqual(notices, [])
            self.assertFalse(task.done())
            ready.set()
            await task
        self.assertEqual(len(notices), 1)
        self.assertIn("0.11.1 → 0.12.0", notices[0])
        self.assertIn("a13n-harness-ui update", notices[0])

    async def test_no_update_is_silent_and_does_not_wait_for_ui(self):
        notices = []
        with patch.object(updates, "check_update", AsyncMock(return_value=None)):
            await asyncio.wait_for(updates.notify_update(Path("unused"), asyncio.Event(), notices.append), 1)
        self.assertEqual(notices, [])

    async def test_network_failure_is_nonfatal(self):
        notices = []
        with (
            patch.object(updates, "check_update", AsyncMock(side_effect=RuntimeError("failure"))),
            self.assertLogs(updates.__name__, level="WARNING"),
        ):
            await updates.notify_update(Path("unused"), asyncio.Event(), notices.append)
        self.assertEqual(notices, [])

    async def test_unknown_installer_does_not_suggest_automatic_update(self):
        notices = []
        ready = asyncio.Event()
        ready.set()
        with (
            patch.object(updates, "check_update", AsyncMock(return_value=updates.AvailableUpdate("1", "2"))),
            patch.object(updates, "update_command", return_value=None),
        ):
            await updates.notify_update(Path("unused"), ready, notices.append)
        self.assertIn("original package manager", notices[0])


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_startup(self, *, no_update_check=False, configured_check=True, cancel=False):
        chat_entered = asyncio.Event()
        chat_release = asyncio.Event()
        check_release = asyncio.Event()
        check_started = asyncio.Event()
        check_cancelled = asyncio.Event()
        notices = []

        async def check(root):
            check_started.set()
            try:
                await check_release.wait()
                return updates.AvailableUpdate("0.11.1", "0.12.0")
            except asyncio.CancelledError:
                check_cancelled.set()
                raise

        class Landing:
            cancel_requested = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def emit(self, text):
                pass

            async def run_chat(self, ui, backend):
                chat_entered.set()
                await chat_release.wait()

        class Shell:
            def __init__(self, *args, **kwargs):
                pass

            def emit(self, text):
                notices.append(text)

        config = SimpleNamespace(
            document=SimpleNamespace(process=SimpleNamespace(terminal_update_check=configured_check))
        )
        backend = SimpleNamespace(
            app=SimpleNamespace(current_configuration=AsyncMock(return_value=config)),
            initialize=AsyncMock(return_value=True),
        )

        @asynccontextmanager
        async def factory(*args):
            yield backend

        with (
            patch.object(onboarding, "LandingScreen", Landing),
            patch.object(shell, "CliShell", Shell),
            patch.object(updates, "check_update", check),
            patch.object(updates, "update_command", return_value=UpdateCommand("uv", Path("tools"))),
        ):
            task = asyncio.create_task(
                startup.run_terminal(CliRequest(no_update_check=no_update_check), runtime_loader=lambda: factory)
            )
            try:
                # Network cannot finish yet, but the UI must already be usable.
                await asyncio.wait_for(chat_entered.wait(), 2)
                self.assertFalse(check_release.is_set())
                if no_update_check or not configured_check:
                    self.assertFalse(check_started.is_set())
                else:
                    await asyncio.wait_for(check_started.wait(), 1)
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                elif no_update_check or not configured_check:
                    chat_release.set()
                    await task
                else:
                    check_release.set()
                    for _ in range(10):
                        await asyncio.sleep(0)
                        if notices:
                            break
                    self.assertEqual(len(notices), 1)
                    chat_release.set()
                    await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(any(t.get_name() == "harness-ui-update-check" for t in asyncio.all_tasks()))
        if cancel and not no_update_check and configured_check:
            self.assertTrue(check_cancelled.is_set())

    async def test_ui_enters_before_network_finishes(self):
        await self.exercise_startup()

    async def test_cli_skip_flag_is_respected(self):
        await self.exercise_startup(no_update_check=True)

    async def test_configuration_disables_network_check(self):
        await self.exercise_startup(configured_check=False)

    async def test_exit_cancels_pending_network_check(self):
        await self.exercise_startup(cancel=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
