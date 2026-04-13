import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Footer, Header, Input, Static

from .aws_common import get_enabled_regions
from .cache import cache_region_failure, get_region_failure_entry
from .constants import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, DEFAULT_READ_TIMEOUT_SECONDS
from .errors import AwsOperationError, is_skippable_region_error
from .models import InstanceMatch
from .ssm_targets import list_ssm_candidates_in_region


class SsmSelectionApp(App[Optional[InstanceMatch]]):
    """Interactive Textual app for browsing and selecting SSM targets."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #status {
        height: 2;
        padding: 0 1;
        content-align: left middle;
    }

    #search {
        margin: 0 1;
    }

    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("enter", "connect", "Connect"),
        ("/", "focus_search", "Search"),
        ("ctrl+l", "clear_search", "Clear Search"),
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
    ]

    class RegionLoaded(Message):
        def __init__(self, region: str, matches: List[InstanceMatch]) -> None:
            self.region = region
            self.matches = matches
            super().__init__()

    class RegionSkipped(Message):
        def __init__(self, region: str, detail: str) -> None:
            self.region = region
            self.detail = detail
            super().__init__()

    class LoadingStarted(Message):
        def __init__(self, total_regions: int) -> None:
            self.total_regions = total_regions
            super().__init__()

    class LoadingFinished(Message):
        def __init__(self) -> None:
            super().__init__()

    class LoadingFailed(Message):
        def __init__(self, error: Exception) -> None:
            self.error = error
            super().__init__()

    def __init__(
        self,
        *,
        initial_matches: Optional[List[InstanceMatch]] = None,
        title_text: str = "AWS SSM Targets",
        status_text: str = "Use arrow keys to move, Enter to connect, Q to quit.",
        live_load: bool = False,
        connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout: int = DEFAULT_READ_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__()
        self.title = title_text
        self.sub_title = "Arrow keys to move, Enter to connect"
        self.initial_matches = initial_matches or []
        self.initial_status_text = status_text
        self.live_load = live_load
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self.total_regions = 0
        self.completed_regions = 0
        self.total_matches = 0
        self.search_query = ""
        self.all_row_keys: List[str] = []
        self.row_order: List[str] = []
        self.matches_by_key: Dict[str, InstanceMatch] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Static(self.initial_status_text, id="status")
            yield Input(placeholder="Press / to search...", id="search")
            yield DataTable(id="instances")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#instances", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("Region", "Name", "Instance ID", "Private IP", "Public IP", "State")
        table.focus()

        for match in self.initial_matches:
            self.add_match_row(match)

        if self.live_load:
            self.run_worker(self.load_candidates, thread=True, exclusive=True)
        elif self.initial_matches:
            self.update_status()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        search_input = self.query_one("#search", Input)
        search_input.value = ""
        self.search_query = ""
        self.refresh_table()
        self.query_one("#instances", DataTable).focus()

    def load_candidates(self) -> None:
        try:
            regions = get_enabled_regions()
        except Exception as error:
            self.post_message(self.LoadingFailed(error))
            return

        self.post_message(self.LoadingStarted(len(regions)))

        active_regions: List[str] = []
        for region in regions:
            failure_entry = get_region_failure_entry(region)
            if failure_entry is not None:
                expires_at = int(failure_entry["expires_at"])
                remaining_seconds = max(0, expires_at - int(time.time()))
                self.post_message(
                    self.RegionSkipped(
                        region,
                        f"cached failure for {remaining_seconds}s more: {failure_entry.get('error', 'unknown error')}",
                    )
                )
                continue
            active_regions.append(region)

        max_workers = min(12, len(active_regions)) or 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_region = {
                executor.submit(
                    list_ssm_candidates_in_region,
                    region,
                    self.connect_timeout,
                    self.read_timeout,
                    self.max_attempts,
                ): region
                for region in active_regions
            }

            for future in as_completed(future_to_region):
                region = future_to_region[future]
                try:
                    matches = future.result()
                except AwsOperationError as error:
                    if is_skippable_region_error(error):
                        cache_region_failure(region, error.error)
                        self.post_message(self.RegionSkipped(region, f"network timeout/error: {error.error}"))
                        continue
                    self.post_message(self.LoadingFailed(error))
                    return
                except Exception as error:
                    self.post_message(self.LoadingFailed(error))
                    return

                self.post_message(self.RegionLoaded(region, matches))

        self.post_message(self.LoadingFinished())

    def add_match_row(self, match: InstanceMatch) -> None:
        row_key = f"{match['region']}::{match['instance_id']}"
        if row_key in self.matches_by_key:
            return

        self.matches_by_key[row_key] = match
        self.all_row_keys.append(row_key)
        self.total_matches += 1
        self.refresh_table()

    def get_match_search_text(self, match: InstanceMatch) -> str:
        return " ".join(
            [
                match["region"],
                match.get("name") or "-",
                match["instance_id"],
                match.get("private_ip") or "-",
                match.get("public_ip") or "-",
                match.get("state") or "unknown",
            ]
        ).lower()

    def highlight_text(self, value: str) -> Text:
        text = Text(value)
        if not self.search_query:
            return text

        pattern = re.escape(self.search_query)
        for found in re.finditer(pattern, value, re.IGNORECASE):
            text.stylize("bold", found.start(), found.end())
        return text

    def row_matches_filter(self, match: InstanceMatch) -> bool:
        if not self.search_query:
            return True
        return self.search_query.lower() in self.get_match_search_text(match)

    def refresh_table(self) -> None:
        table = self.query_one("#instances", DataTable)
        table.clear(columns=False)
        self.row_order = []

        for row_key in self.all_row_keys:
            match = self.matches_by_key[row_key]
            if not self.row_matches_filter(match):
                continue

            self.row_order.append(row_key)
            table.add_row(
                self.highlight_text(match["region"]),
                self.highlight_text(match.get("name") or "-"),
                self.highlight_text(match["instance_id"]),
                self.highlight_text(match.get("private_ip") or "-"),
                self.highlight_text(match.get("public_ip") or "-"),
                self.highlight_text(match.get("state") or "unknown"),
                key=row_key,
            )

        if self.row_order:
            table.move_cursor(row=0, column=0)

    def update_status(self, extra: Optional[str] = None) -> None:
        status = self.query_one("#status", Static)
        visible_matches = len(self.row_order)
        base_text = (
            f"Loaded {self.total_matches} instance(s)"
            f" from {self.completed_regions}/{self.total_regions} region(s). "
            f"Showing {visible_matches}. Use arrow keys to move, / to search, Enter to connect, Q to quit."
        )
        if not self.live_load:
            base_text = (
                f"{self.total_matches} instance(s) ready. Showing {visible_matches}. "
                "Use arrow keys to move, / to search, Enter to connect, Q to quit."
            )
        if self.search_query:
            base_text = f"{base_text} [search: {self.search_query}]"
        if extra:
            base_text = f"{base_text} [{extra}]"
        status.update(base_text)

    def action_connect(self) -> None:
        if not self.row_order:
            self.bell()
            return

        table = self.query_one("#instances", DataTable)
        cursor_row = table.cursor_row
        if cursor_row < 0 or cursor_row >= len(self.row_order):
            self.bell()
            return

        row_key = self.row_order[cursor_row]
        self.exit(self.matches_by_key[row_key])

    @on(DataTable.RowSelected)
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = event.control
        cursor_row = table.cursor_row
        if 0 <= cursor_row < len(self.row_order):
            row_key = self.row_order[cursor_row]
            self.exit(self.matches_by_key[row_key])

    @on(Input.Changed, "#search")
    def handle_search_changed(self, event: Input.Changed) -> None:
        self.search_query = event.value.strip()
        self.refresh_table()
        self.update_status()

    @on(Input.Submitted, "#search")
    def handle_search_submitted(self, event: Input.Submitted) -> None:
        if self.row_order:
            self.query_one("#instances", DataTable).focus()
        else:
            event.input.focus()

    @on(LoadingStarted)
    def handle_loading_started(self, message: LoadingStarted) -> None:
        self.total_regions = message.total_regions
        self.completed_regions = 0
        self.total_matches = 0
        self.update_status("loading")

    @on(RegionLoaded)
    def handle_region_loaded(self, message: RegionLoaded) -> None:
        self.completed_regions += 1
        for match in sorted(
            message.matches,
            key=lambda item: ((item.get("name") or "").lower(), item["instance_id"]),
        ):
            self.add_match_row(match)
        self.update_status(f"{message.region} done")

    @on(RegionSkipped)
    def handle_region_skipped(self, message: RegionSkipped) -> None:
        self.completed_regions += 1
        self.update_status(f"{message.region} skipped")

    @on(LoadingFinished)
    def handle_loading_finished(self, message: LoadingFinished) -> None:
        if self.total_matches == 0:
            self.query_one("#status", Static).update(
                "No online SSM-manageable EC2 instances were found. Press Q to quit."
            )
            return
        self.update_status("complete")

    @on(LoadingFailed)
    def handle_loading_failed(self, message: LoadingFailed) -> None:
        self.exit(None, return_code=1, message=f"Failed to load SSM targets: {message.error}")

