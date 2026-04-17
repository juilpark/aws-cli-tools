import asyncio
from unittest.mock import Mock

from textual.widgets import DataTable, Input, Static

from aws_cli_tools.ui import SsmSelectionApp


SAMPLE_MATCHES = [
    {
        "region": "ap-northeast-2",
        "instance_id": "i-0123456789abcdef0",
        "private_ip": "10.0.0.12",
        "public_ip": None,
        "state": "running",
        "name": "web-a",
    },
    {
        "region": "us-west-2",
        "instance_id": "i-0123456789abcdef1",
        "private_ip": "10.0.1.12",
        "public_ip": "3.39.10.20",
        "state": "stopped",
        "name": "worker-b",
    },
]


def test_ssm_selection_app_filters_and_clears_search():
    async def run():
        app = SsmSelectionApp(initial_matches=SAMPLE_MATCHES, live_load=False)
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#instances", DataTable)
            search = app.query_one("#search", Input)

            assert len(app.row_order) == 2
            table.move_cursor(row=1, column=0)
            assert app.row_order[table.cursor_row] == "us-west-2::i-0123456789abcdef1"

            search.value = "worker"
            app.handle_search_changed(Input.Changed(search, search.value))
            await pilot.pause()

            assert app.search_query == "worker"
            assert app.row_order == ["us-west-2::i-0123456789abcdef1"]
            assert table.cursor_row == 0
            assert app.row_order[table.cursor_row] == "us-west-2::i-0123456789abcdef1"

            app.action_clear_search()
            await pilot.pause()

            assert app.search_query == ""
            assert search.value == ""
            assert len(app.row_order) == 2
            assert table.cursor_row == 1
            assert app.row_order[table.cursor_row] == "us-west-2::i-0123456789abcdef1"
            assert app.query_one("#instances", DataTable).has_focus

    asyncio.run(run())


def test_ssm_selection_app_live_load_preserves_selected_row():
    async def run():
        app = SsmSelectionApp(initial_matches=SAMPLE_MATCHES, live_load=False)
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#instances", DataTable)
            table.move_cursor(row=1, column=0)
            selected_row_key = app.row_order[table.cursor_row]

            app.handle_region_loaded(
                app.RegionLoaded(
                    "eu-west-1",
                    [
                        {
                            "region": "eu-west-1",
                            "instance_id": "i-0123456789abcdef2",
                            "private_ip": "10.0.2.12",
                            "public_ip": None,
                            "state": "running",
                            "name": "api-c",
                        }
                    ],
                )
            )
            await pilot.pause()

            assert len(app.row_order) == 3
            assert table.cursor_row == 1
            assert app.row_order[table.cursor_row] == selected_row_key

    asyncio.run(run())


def test_ssm_selection_app_live_update_bypasses_full_refresh():
    async def run():
        app = SsmSelectionApp(initial_matches=SAMPLE_MATCHES[:1], live_load=False)
        async with app.run_test() as pilot:
            await pilot.pause()

            app.refresh_table = Mock(side_effect=AssertionError("live region update should not rebuild the whole table"))
            app.handle_region_loaded(
                app.RegionLoaded(
                    "ap-northeast-2",
                    [
                        {
                            "region": "ap-northeast-2",
                            "instance_id": "i-0123456789abcdef0",
                            "private_ip": "10.0.0.12",
                            "public_ip": None,
                            "state": "running",
                            "name": "web-a",
                        }
                    ],
                )
            )
            await pilot.pause()

            app.refresh_table.assert_not_called()

    asyncio.run(run())


def test_ssm_selection_app_cached_rows_show_before_live_refresh_finishes():
    async def run():
        app = SsmSelectionApp(initial_matches=[], live_load=True)
        async with app.run_test() as pilot:
            await pilot.pause()

            status = app.query_one("#status", Static)

            app.handle_loading_started(app.LoadingStarted(2))
            app.handle_region_cached(app.RegionCached("ap-northeast-2", SAMPLE_MATCHES[:1]))
            await pilot.pause()

            assert app.row_order == ["ap-northeast-2::i-0123456789abcdef0"]
            assert "Showing cached results while refreshing in background." in str(status.render())
            assert app.cached_regions_pending_refresh == {"ap-northeast-2"}

    asyncio.run(run())


def test_ssm_selection_app_live_refresh_replaces_cached_rows_without_duplicates():
    async def run():
        cached_match = [
            {
                "region": "ap-northeast-2",
                "instance_id": "i-oldcached",
                "private_ip": "10.0.0.11",
                "public_ip": None,
                "state": "running",
                "name": "cached-only",
            }
        ]
        live_matches = [
            {
                "region": "ap-northeast-2",
                "instance_id": "i-live1",
                "private_ip": "10.0.0.12",
                "public_ip": None,
                "state": "running",
                "name": "web-a",
            },
            {
                "region": "ap-northeast-2",
                "instance_id": "i-live2",
                "private_ip": "10.0.0.13",
                "public_ip": None,
                "state": "running",
                "name": "web-b",
            },
        ]

        app = SsmSelectionApp(initial_matches=[], live_load=True)
        async with app.run_test() as pilot:
            await pilot.pause()

            app.handle_loading_started(app.LoadingStarted(1))
            app.handle_region_cached(app.RegionCached("ap-northeast-2", cached_match))
            app.handle_region_loaded(app.RegionLoaded("ap-northeast-2", live_matches))
            await pilot.pause()

            assert app.row_order == [
                "ap-northeast-2::i-live1",
                "ap-northeast-2::i-live2",
            ]
            assert "ap-northeast-2::i-oldcached" not in app.all_row_keys
            assert len(app.row_order) == 2

    asyncio.run(run())


def test_ssm_selection_app_live_refresh_drops_stale_cached_rows_when_region_is_empty():
    async def run():
        app = SsmSelectionApp(initial_matches=[], live_load=True)
        async with app.run_test() as pilot:
            await pilot.pause()

            app.handle_loading_started(app.LoadingStarted(1))
            app.handle_region_cached(app.RegionCached("ap-northeast-2", SAMPLE_MATCHES[:1]))
            app.handle_region_loaded(app.RegionLoaded("ap-northeast-2", []))
            await pilot.pause()

            assert app.row_order == []
            assert app.all_row_keys == []
            assert app.total_matches == 0

    asyncio.run(run())


def test_ssm_selection_app_live_refresh_preserves_or_falls_back_cursor_on_replace():
    async def run():
        initial_matches = [
            {
                "region": "ap-northeast-2",
                "instance_id": "i-keep",
                "private_ip": "10.0.0.10",
                "public_ip": None,
                "state": "running",
                "name": "alpha",
            },
            {
                "region": "ap-northeast-2",
                "instance_id": "i-drop",
                "private_ip": "10.0.0.11",
                "public_ip": None,
                "state": "running",
                "name": "bravo",
            },
        ]

        app = SsmSelectionApp(initial_matches=initial_matches, live_load=False)
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#instances", DataTable)
            table.move_cursor(row=1, column=0)
            assert app.row_order[table.cursor_row] == "ap-northeast-2::i-drop"

            app.handle_region_loaded(app.RegionLoaded("ap-northeast-2", initial_matches))
            await pilot.pause()
            assert app.row_order[table.cursor_row] == "ap-northeast-2::i-drop"

            app.handle_region_loaded(app.RegionLoaded("ap-northeast-2", initial_matches[:1]))
            await pilot.pause()
            assert app.row_order == ["ap-northeast-2::i-keep"]
            assert table.cursor_row == 0
            assert app.row_order[table.cursor_row] == "ap-northeast-2::i-keep"

    asyncio.run(run())


def test_ssm_selection_app_live_refresh_does_not_clear_or_move_cursor_when_selection_survives(monkeypatch):
    async def run():
        app = SsmSelectionApp(initial_matches=SAMPLE_MATCHES, live_load=False)
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#instances", DataTable)
            table.move_cursor(row=1, column=0)

            clear_calls = []
            original_clear = table.clear

            def tracked_clear(*args, **kwargs):
                clear_calls.append((args, kwargs))
                return original_clear(*args, **kwargs)

            move_calls = []
            original_move_cursor = table.move_cursor

            def tracked_move_cursor(*args, **kwargs):
                move_calls.append((args, kwargs))
                return original_move_cursor(*args, **kwargs)

            monkeypatch.setattr(table, "clear", tracked_clear)
            monkeypatch.setattr(table, "move_cursor", tracked_move_cursor)
            move_calls.clear()

            app.handle_region_loaded(
                app.RegionLoaded(
                    "ap-northeast-2",
                    [
                        {
                            "region": "ap-northeast-2",
                            "instance_id": "i-0123456789abcdef0",
                            "private_ip": "10.0.0.12",
                            "public_ip": None,
                            "state": "running",
                            "name": "web-a-updated",
                        }
                    ],
                )
            )
            await pilot.pause()

            assert clear_calls == []
            assert move_calls == []
            assert table.cursor_row == 1
            assert app.row_order[table.cursor_row] == "us-west-2::i-0123456789abcdef1"

    asyncio.run(run())


def test_ssm_selection_app_filter_falls_back_to_first_visible_row():
    async def run():
        app = SsmSelectionApp(initial_matches=SAMPLE_MATCHES, live_load=False)
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#instances", DataTable)
            search = app.query_one("#search", Input)
            table.move_cursor(row=1, column=0)
            assert app.row_order[table.cursor_row] == "us-west-2::i-0123456789abcdef1"

            search.value = "web"
            app.handle_search_changed(Input.Changed(search, search.value))
            await pilot.pause()

            assert app.row_order == ["ap-northeast-2::i-0123456789abcdef0"]
            assert table.cursor_row == 0
            assert app.row_order[table.cursor_row] == "ap-northeast-2::i-0123456789abcdef0"

    asyncio.run(run())


def test_ssm_selection_app_search_refresh_still_uses_full_rebuild(monkeypatch):
    async def run():
        app = SsmSelectionApp(initial_matches=SAMPLE_MATCHES, live_load=False)
        async with app.run_test() as pilot:
            await pilot.pause()

            table = app.query_one("#instances", DataTable)
            clear_calls = []
            original_clear = table.clear

            def tracked_clear(*args, **kwargs):
                clear_calls.append((args, kwargs))
                return original_clear(*args, **kwargs)

            monkeypatch.setattr(table, "clear", tracked_clear)

            search = app.query_one("#search", Input)
            search.value = "worker"
            app.handle_search_changed(Input.Changed(search, search.value))
            await pilot.pause()

            assert clear_calls != []
            assert app.row_order == ["us-west-2::i-0123456789abcdef1"]

    asyncio.run(run())


def test_ssm_selection_app_connect_and_loading_statuses():
    async def run():
        app = SsmSelectionApp(initial_matches=SAMPLE_MATCHES[:1], live_load=False)
        async with app.run_test() as pilot:
            await pilot.pause()

            app.action_connect()
            await pilot.pause()
            assert app.return_value == SAMPLE_MATCHES[0]

        app = SsmSelectionApp(initial_matches=[], live_load=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            status = app.query_one("#status", Static)

            app.handle_loading_started(app.LoadingStarted(3))
            app.handle_region_cached(app.RegionCached("ap-northeast-2", SAMPLE_MATCHES[:1]))
            app.handle_region_loaded(app.RegionLoaded("ap-northeast-2", SAMPLE_MATCHES[:1]))
            app.handle_region_skipped(app.RegionSkipped("us-west-2", "timeout"))
            app.handle_loading_finished(app.LoadingFinished())
            await pilot.pause()

            assert app.total_regions == 3
            assert app.completed_regions == 2
            assert app.total_matches == 1
            assert "Loaded 1 instance(s)" in str(status.render())

        app = SsmSelectionApp(initial_matches=[], live_load=False)
        async with app.run_test() as pilot:
            await pilot.pause()

            app.handle_loading_finished(app.LoadingFinished())
            await pilot.pause()

            status = app.query_one("#status", Static)
            assert "No online SSM-manageable EC2 instances were found" in str(status.render())

    asyncio.run(run())
