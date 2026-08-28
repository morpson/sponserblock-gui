import aiohttp
from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Header, Footer, Static, Input, Button, Label, SelectionList
from textual.widgets.selection_list import Selection

from . import api_helpers, ytlounge
from .helpers import Config

class iSponsorBlockTVTUI(App):
    """A Textual app to control iSponsorBlockTV."""

    BINDINGS = [("d", "toggle_dark", "Toggle dark mode")]

    def __init__(self, config: Config, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.web_session = None
        self.api_helper = None
        self.lounge_controller = None
        self.devices_discovered_dial = []
        self.search_results = []
        self.volume_state = {}

    async def on_mount(self) -> None:
        self.web_session = aiohttp.ClientSession(trust_env=self.config.use_proxy)
        self.api_helper = api_helpers.ApiHelper(self.config, self.web_session)

    async def on_unmount(self) -> None:
        if self.web_session and not self.web_session.closed:
            await self.web_session.close()

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield Footer()
        with Vertical(id="main-container"):
            yield Label("Paired Device: Not Paired", id="paired-device")
            yield Button("Scan for devices", id="scan-devices-button")
            yield SelectionList(id="devices-list")
            with Horizontal(id="manual-pair-container"):
                yield Input(placeholder="Enter screen ID manually", id="manual-screen-id-input")
                yield Button("Pair manually", id="manual-pair-button")
            yield Input(placeholder="Search for a video", id="search-input")
            yield SelectionList(id="search-results-list")
            with Vertical(id="controls-container"):
                yield Label("Playback Controls", id="playback-label")
                with Horizontal(id="playback-controls"):
                    yield Button("Play", id="play-button")
                    yield Button("Pause", id="pause-button")
                    yield Button("Rewind", id="rewind-button")
                    yield Button("Fast Forward", id="ff-button")
                yield Label("Volume Controls", id="volume-label")
                with Horizontal(id="volume-controls"):
                    yield Button("Volume Up", id="volume-up-button")
                    yield Button("Volume Down", id="volume-down-button")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "scan-devices-button":
            if not self.api_helper:
                return
            self.query_one("#scan-devices-button").disabled = True
            devices_found = await self.api_helper.discover_youtube_devices_dial()
            list_widget: SelectionList = self.query_one("#devices-list")
            list_widget.clear_options()
            if devices_found:
                devices_found_parsed = []
                for index, i in enumerate(devices_found):
                    devices_found_parsed.append(Selection(i["name"], index, False))
                list_widget.add_options(devices_found_parsed)
                self.query_one("#devices-list").disabled = False
                self.devices_discovered_dial = devices_found
            else:
                list_widget.add_option(("No devices found", "", False))
            self.query_one("#scan-devices-button").disabled = False

        elif event.button.id == "manual-pair-button":
            screen_id = self.query_one("#manual-screen-id-input", Input).value.strip()
            if not screen_id:
                return
            self.lounge_controller = ytlounge.YtLoungeApi(
                screen_id,
                self.config,
                self.api_helper,
                self.log,
            )
            await self.lounge_controller.change_web_session(self.web_session)
            self.query_one("#paired-device").update(f"Paired Device: {screen_id} (manual)")
            self.query_one("#manual-screen-id-input", Input).value = ""

        if not self.lounge_controller:
            return

        if event.button.id == "play-button":
            await self.lounge_controller._command("play")
        elif event.button.id == "pause-button":
            await self.lounge_controller._command("pause")
        elif event.button.id == "rewind-button":
            state = await self.lounge_controller.get_now_playing()
            await self.lounge_controller._command("seekTo", {"newTime": str(int(state.get('currentTime', 0)) - 10)})
        elif event.button.id == "ff-button":
            state = await self.lounge_controller.get_now_playing()
            await self.lounge_controller._command("seekTo", {"newTime": str(int(state.get('currentTime', 0)) + 10)})
        elif event.button.id == "volume-up-button":
            current_volume = int(self.lounge_controller.volume_state.get("volume", 50))
            await self.lounge_controller.set_volume(min(current_volume + 10, 100))
        elif event.button.id == "volume-down-button":
            current_volume = int(self.lounge_controller.volume_state.get("volume", 50))
            await self.lounge_controller.set_volume(max(current_volume - 10, 0))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "manual-screen-id-input":
            screen_id = event.value.strip()
            if not screen_id:
                return
            self.lounge_controller = ytlounge.YtLoungeApi(
                screen_id,
                self.config,
                self.api_helper,
                self.log,
            )
            await self.lounge_controller.change_web_session(self.web_session)
            self.query_one("#paired-device").update(f"Paired Device: {screen_id} (manual)")
            self.query_one("#manual-screen-id-input", Input).value = ""

        elif event.input.id == "search-input":
            if not self.config.apikey:
                list_widget: SelectionList = self.query_one("#search-results-list")
                list_widget.clear_options()
                list_widget.add_option(("API key not set. Please set it in the setup wizard.", "", False))
                self.query_one("#search-input").disabled = True
                return

            search_query = event.value
            if not search_query:
                return

            self.query_one("#search-input").disabled = True
            try:
                self.search_results = await self.api_helper.search_videos(search_query)
            except Exception:
                self.query_one("#search-input").disabled = False
                return

            list_widget: SelectionList = self.query_one("#search-results-list")
            list_widget.clear_options()
            if self.search_results:
                search_results_parsed = []
                for index, i in enumerate(self.search_results):
                    search_results_parsed.append(Selection(i[1], index, False))
                list_widget.add_options(search_results_parsed)
            else:
                list_widget.add_option(("No results found", "", False))
            self.query_one("#search-input").disabled = False

    async def on_selection_list_selection_changed(self, event: SelectionList.SelectionChanged) -> None:
        if event.selection_list.id == "devices-list":
            if not event.selection_list.selected:
                return
            selected_device_index = event.selection_list.selected[0]
            selected_device = self.devices_discovered_dial[selected_device_index]
            self.lounge_controller = ytlounge.YtLoungeApi(
                selected_device["screen_id"],
                self.config,
                self.api_helper,
                self.log,
            )
            await self.lounge_controller.change_web_session(self.web_session)
            self.query_one("#paired-device").update(f"Paired Device: {selected_device['name']}")
            self.lounge_controller.volume_state = await self.lounge_controller.get_now_playing()
        elif event.selection_list.id == "search-results-list":
            if not self.lounge_controller:
                return
            if not event.selection_list.selected:
                return

            selected_video_index = event.selection_list.selected[0]
            selected_video = self.search_results[selected_video_index]
            await self.lounge_controller.play_video(selected_video[0])

    def action_toggle_dark(self) -> None:
        """An action to toggle dark mode."""
        self.dark = not self.dark

if __name__ == "__main__":
    config = Config("data") # TODO: This should be passed from the cli
    app = iSponsorBlockTVTUI(config)
    app.run()
