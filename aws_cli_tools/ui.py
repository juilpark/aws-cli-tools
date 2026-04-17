import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Input, Static

from .aws_common import get_enabled_regions
from .cache import cache_region_failure, cache_ssm_targets, get_cached_ssm_targets, get_region_failure_entry
from .constants import DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_MAX_ATTEMPTS, DEFAULT_READ_TIMEOUT_SECONDS
from .errors import AwsOperationError, is_skippable_region_error
from .models import InstanceMatch
from .ssm_targets import list_ssm_candidates_in_region


class SsmSelectionApp(App[Optional[InstanceMatch]]):
    """Interactive Textual app for browsing and selecting SSM targets."""

    COLUMN_SPECS = (
        ("Region", "region"),
        ("Name", "name"),
        ("Instance ID", "instance_id"),
        ("Private IP", "private_ip"),
        ("Public IP", "public_ip"),
        ("State", "state"),
    )

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

    class RegionCached(Message):
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
        use_cached_results: bool = True,
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
        self.use_cached_results = use_cached_results
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self.total_regions = 0
        self.completed_regions = 0
        self.total_matches = 0
        self.search_query = ""
        self.all_row_keys: List[str] = []
        self.row_order: List[str] = []
        self.region_row_keys: Dict[str, List[str]] = {}
        self.matches_by_key: Dict[str, InstanceMatch] = {}
        self.cached_regions_pending_refresh: set[str] = set()
        self.loading_error: Optional[Exception] = None

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
        table.add_columns(*self.COLUMN_SPECS)
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
        self.update_status()
        self.query_one("#instances", DataTable).focus()

    def load_candidates(self) -> None:
        try:
            regions = get_enabled_regions()
        except Exception as error:
            self.post_message(self.LoadingFailed(error))
            return

        self.post_message(self.LoadingStarted(len(regions)))

        if self.use_cached_results:
            for region in regions:
                matches = get_cached_ssm_targets(region)
                if matches is not None:
                    self.post_message(self.RegionCached(region, matches))

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

                cache_ssm_targets(region, matches)
                self.post_message(self.RegionLoaded(region, matches))

        self.post_message(self.LoadingFinished())

    @staticmethod
    def get_row_key(match: InstanceMatch) -> str:
        return f"{match['region']}::{match['instance_id']}"

    @staticmethod
    def sort_matches(matches: List[InstanceMatch]) -> List[InstanceMatch]:
        return sorted(
            matches,
            key=lambda item: (
                (item.get("name") or "").lower(),
                item["instance_id"],
            ),
        )

    def add_match_row(self, match: InstanceMatch) -> None:
        row_key = self.get_row_key(match)
        if row_key in self.matches_by_key:
            return

        self.matches_by_key[row_key] = match
        self.region_row_keys.setdefault(match["region"], []).append(row_key)
        self.all_row_keys.append(row_key)
        self.total_matches = len(self.matches_by_key)
        if not self.row_matches_filter(match):
            return

        table = self.query_one("#instances", DataTable)
        had_visible_rows = bool(self.row_order)
        self.append_row(table, row_key)
        if not had_visible_rows:
            self.restore_table_cursor(preferred_row_key=row_key, cursor_column=0)

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

    def append_row(self, table: DataTable, row_key: str) -> None:
        match = self.matches_by_key[row_key]
        self.row_order.append(row_key)
        table.add_row(
            *self.get_row_cells(match),
            key=row_key,
        )

    def get_row_cells(self, match: InstanceMatch) -> tuple[Text, Text, Text, Text, Text, Text]:
        return (
            self.highlight_text(match["region"]),
            self.highlight_text(match.get("name") or "-"),
            self.highlight_text(match["instance_id"]),
            self.highlight_text(match.get("private_ip") or "-"),
            self.highlight_text(match.get("public_ip") or "-"),
            self.highlight_text(match.get("state") or "unknown"),
        )

    def update_row_in_table(self, row_key: str) -> None:
        table = self.query_one("#instances", DataTable)
        match = self.matches_by_key[row_key]
        for (_, column_key), value in zip(self.COLUMN_SPECS, self.get_row_cells(match)):
            table.update_cell(row_key, column_key, value, update_width=False)
        table.refresh_row(table.get_row_index(row_key))

    def ensure_cursor_after_live_update(self, selected_row_key: Optional[str], cursor_column: int) -> None:
        table = self.query_one("#instances", DataTable)
        if selected_row_key and selected_row_key in self.row_order:
            return

        if not self.row_order:
            return

        current_row = table.cursor_row
        if 0 <= current_row < len(self.row_order):
            return

        fallback_row = min(max(current_row, 0), len(self.row_order) - 1)
        table.move_cursor(row=fallback_row, column=max(0, cursor_column), scroll=False)

    def get_selected_row_key(self) -> Optional[str]:
        table = self.query_one("#instances", DataTable)
        cursor_row = table.cursor_row
        if 0 <= cursor_row < len(self.row_order):
            return self.row_order[cursor_row]
        return None

    def restore_table_cursor(self, preferred_row_key: Optional[str], cursor_column: int) -> None:
        if not self.row_order:
            return

        table = self.query_one("#instances", DataTable)
        row_index = 0
        if preferred_row_key and preferred_row_key in self.row_order:
            row_index = table.get_row_index(preferred_row_key)
        table.move_cursor(row=row_index, column=max(0, cursor_column), scroll=False)

    def restore_focus(self, focused_widget: Optional[Widget]) -> None:
        if focused_widget is not None and focused_widget.is_mounted and focused_widget.can_focus:
            focused_widget.focus()

    def refresh_table(
        self,
        *,
        selected_row_key: Optional[str] = None,
        cursor_column: Optional[int] = None,
        focused_widget: Optional[Widget] = None,
    ) -> None:
        table = self.query_one("#instances", DataTable)
        if selected_row_key is None:
            selected_row_key = self.get_selected_row_key()
        if cursor_column is None:
            cursor_column = table.cursor_column
        if focused_widget is None:
            focused_widget = self.focused
        table.clear(columns=False)
        self.row_order = []

        for row_key in self.all_row_keys:
            match = self.matches_by_key[row_key]
            if not self.row_matches_filter(match):
                continue

            self.append_row(table, row_key)

        self.restore_table_cursor(preferred_row_key=selected_row_key, cursor_column=cursor_column)
        self.restore_focus(focused_widget)

    def set_region_matches(self, region: str, matches: List[InstanceMatch], *, rebuild_table: bool) -> None:
        old_keys = self.region_row_keys.get(region, [])
        old_key_set = set(old_keys)
        insertion_index = next((index for index, key in enumerate(self.all_row_keys) if key in old_key_set), len(self.all_row_keys))

        if old_key_set:
            self.all_row_keys = [key for key in self.all_row_keys if key not in old_key_set]
            for row_key in old_key_set:
                self.matches_by_key.pop(row_key, None)

        new_region_keys: List[str] = []
        seen_row_keys = set()
        for match in self.sort_matches(matches):
            row_key = self.get_row_key(match)
            if row_key in seen_row_keys:
                continue

            seen_row_keys.add(row_key)
            self.matches_by_key[row_key] = match
            new_region_keys.append(row_key)

        if new_region_keys:
            self.all_row_keys[insertion_index:insertion_index] = new_region_keys
            self.region_row_keys[region] = new_region_keys
        else:
            self.region_row_keys.pop(region, None)

        self.total_matches = len(self.matches_by_key)
        if rebuild_table:
            self.refresh_table()
            return

        self.apply_region_matches_to_table(region, old_keys, new_region_keys)

    def apply_region_matches_to_table(self, region: str, old_keys: List[str], new_keys: List[str]) -> None:
        table = self.query_one("#instances", DataTable)
        selected_row_key = self.get_selected_row_key()
        cursor_column = table.cursor_column

        old_visible_keys = [row_key for row_key in old_keys if row_key in self.row_order]
        old_visible_key_set = set(old_visible_keys)
        new_visible_keys = [row_key for row_key in new_keys if self.row_matches_filter(self.matches_by_key[row_key])]
        new_visible_key_set = set(new_visible_keys)

        removed_keys = [row_key for row_key in old_visible_keys if row_key not in new_visible_key_set]
        updated_keys = [row_key for row_key in new_visible_keys if row_key in old_visible_key_set]
        added_keys = [row_key for row_key in new_visible_keys if row_key not in old_visible_key_set]

        if removed_keys:
            self.row_order = [row_key for row_key in self.row_order if row_key not in set(removed_keys)]
            for row_key in removed_keys:
                table.remove_row(row_key)

        for row_key in updated_keys:
            self.update_row_in_table(row_key)

        for row_key in added_keys:
            self.row_order.append(row_key)
            table.add_row(*self.get_row_cells(self.matches_by_key[row_key]), key=row_key)

        self.ensure_cursor_after_live_update(selected_row_key, cursor_column)

    def update_status(self, extra: Optional[str] = None) -> None:
        status = self.query_one("#status", Static)
        visible_matches = len(self.row_order)
        prefix = ""
        if self.live_load and self.cached_regions_pending_refresh:
            prefix = "Showing cached results while refreshing in background. "
        base_text = (
            f"{prefix}Loaded {self.total_matches} instance(s)"
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
        self.total_matches = len(self.matches_by_key)
        self.cached_regions_pending_refresh = set()
        self.update_status("loading")

    @on(RegionCached)
    def handle_region_cached(self, message: RegionCached) -> None:
        self.set_region_matches(message.region, message.matches, rebuild_table=False)
        if message.matches:
            self.cached_regions_pending_refresh.add(message.region)
        self.update_status("cache ready")

    @on(RegionLoaded)
    def handle_region_loaded(self, message: RegionLoaded) -> None:
        self.completed_regions += 1
        self.cached_regions_pending_refresh.discard(message.region)
        self.set_region_matches(message.region, message.matches, rebuild_table=False)
        self.update_status(f"{message.region} refreshed")

    @on(RegionSkipped)
    def handle_region_skipped(self, message: RegionSkipped) -> None:
        self.completed_regions += 1
        self.cached_regions_pending_refresh.discard(message.region)
        self.update_status(f"{message.region} skipped")

    @on(LoadingFinished)
    def handle_loading_finished(self, message: LoadingFinished) -> None:
        self.cached_regions_pending_refresh = set()
        if self.total_matches == 0:
            self.query_one("#status", Static).update(
                "No online SSM-manageable EC2 instances were found. Press Q to quit."
            )
            return
        self.update_status("complete")

    @on(LoadingFailed)
    def handle_loading_failed(self, message: LoadingFailed) -> None:
        self.loading_error = message.error
        self.exit(None, return_code=1, message=f"Failed to load SSM targets: {message.error}")
