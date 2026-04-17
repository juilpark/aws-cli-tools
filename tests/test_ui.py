import asyncio

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

            search.value = "worker"
            app.handle_search_changed(Input.Changed(search, search.value))
            await pilot.pause()

            assert app.search_query == "worker"
            assert app.row_order == ["us-west-2::i-0123456789abcdef1"]

            app.action_clear_search()
            await pilot.pause()

            assert app.search_query == ""
            assert search.value == ""
            assert len(app.row_order) == 2
            assert app.query_one("#instances", DataTable).has_focus

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
