from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.live_sass import setup
from src.live_sass.extension import LiveSass, PureSassCompiler, generate_sourcemap_v3


class TestLiveSassExtension:
    def test_extension_setup_and_registration(self, mock_api) -> None:
        setup(mock_api)
        assert "live_sass" in mock_api.registered_extensions
        ext = mock_api.registered_extensions["live_sass"]
        assert isinstance(ext, LiveSass)

        commands = mock_api.commands.commands
        assert "live_sass.compile_current" in commands
        assert "live_sass.toggle_watch" in commands

        settings = mock_api.settings.get_registered_settings()
        assert "live_sass.generateSourceMap" in settings
        assert "live_sass.style" in settings

        assert len(mock_api.statusbar.buttons) == 1
        assert "Watch Sass" in mock_api.statusbar.buttons[0].text
        assert mock_api.statusbar.buttons[0].visible is True

    def test_manifest_metadata_and_dependencies(self) -> None:
        import toml
        manifest_path = Path(__file__).parent.parent / "extension.toml"
        assert manifest_path.exists()
        manifest = toml.load(manifest_path)

        assert manifest["extension"]["author"] == "tomlin7"
        assert manifest["extension"]["version"] == "0.2.0"
        assert manifest["dependencies"]["tools"] == ["sass", "node"]
        assert any("libsass" in dep for dep in manifest["dependencies"]["python"])
        cmd_ids = [cmd["id"] for cmd in manifest["contributes"]["commands"]]
        assert "live_sass.compile_current" in cmd_ids

    def test_get_output_css_path(self, mock_api) -> None:
        ext = LiveSass(mock_api)
        assert ext.get_output_css_path("styles/main.scss") == "styles/main.css"
        assert ext.get_output_css_path("styles/theme.sass") == "styles/theme.css"
        assert ext.get_output_css_path("styles/custom.other") == "styles/custom.other.css"

    def test_compile_variables_and_nesting(self, mock_api) -> None:
        ext = LiveSass(mock_api)
        scss = (
            "$theme-color: #ef4444;\n"
            "$spacing: 8px;\n"
            ".container {\n"
            "    margin: $spacing;\n"
            "    .title {\n"
            "        color: $theme-color;\n"
            "    }\n"
            "    &:hover {\n"
            "        opacity: 0.9;\n"
            "    }\n"
            "}\n"
        )
        css = ext.compile_simple_scss(scss)
        assert "#ef4444" in css
        assert "8px" in css
        assert "$theme-color" not in css
        assert "$spacing" not in css
        # Check nested rules flattened
        assert ".container" in css
        assert ".container .title" in css
        assert ".container:hover" in css

    def test_sourcemap_v3_generation(self) -> None:
        sm = generate_sourcemap_v3("app.css", "app.scss", "body { margin: 0; }", 3)
        data = json.loads(sm)
        assert data["version"] == 3
        assert data["file"] == "app.css"
        assert data["sources"] == ["app.scss"]
        assert len(data["mappings"]) > 0

    def test_compile_string_with_sourcemap(self, mock_api) -> None:
        ext = LiveSass(mock_api)
        scss = "$color: #10b981;\na { color: $color; }"
        css, map_json = ext.compile_string(scss, filename="main.scss", source_map=True)
        assert "#10b981" in css
        assert "sourceMappingURL=main.css.map" in css
        assert map_json is not None
        map_data = json.loads(map_json)
        assert map_data["version"] == 3
        assert map_data["file"] == "main.css"

    def test_compile_file_on_disk(self, tmp_path: Path, mock_api) -> None:
        ext = LiveSass(mock_api)
        input_file = tmp_path / "app.scss"
        input_file.write_text("$bg: #000000;\nbody { background: $bg; }", encoding="utf-8")

        css_text, out_path, map_path = ext.compile_file(input_file, source_map=True)
        assert out_path.exists()
        assert out_path.name == "app.css"
        assert "#000000" in out_path.read_text(encoding="utf-8")
        assert map_path is not None
        assert map_path.exists()
        assert map_path.name == "app.css.map"

    def test_watch_mode_toggle(self, mock_api) -> None:
        ext = LiveSass(mock_api)
        assert ext.is_watching is False

        # Start watch
        ext.start_watch()
        assert ext.is_watching is True
        assert "Watching Sass..." in ext.status_button.text

        # Stop watch
        ext.stop_watch()
        assert ext.is_watching is False
        assert "Watch Sass" in ext.status_button.text

        # Toggle watch
        ext.toggle_watch()
        assert ext.is_watching is True
        ext.toggle_watch()
        assert ext.is_watching is False

    def test_compile_current_file_command_with_editor(self, tmp_path: Path, mock_api) -> None:
        ext = LiveSass(mock_api)
        scss_file = tmp_path / "style.scss"
        scss_file.write_text("$font: sans-serif;\nh1 { font-family: $font; }", encoding="utf-8")

        mock_editor = MagicMock()
        mock_editor.path = str(scss_file)
        mock_api.editors.active_editor = mock_editor

        res = ext.compile_current_file()
        assert res is True
        css_file = tmp_path / "style.css"
        assert css_file.exists()
        assert "sans-serif" in css_file.read_text(encoding="utf-8")
        mock_api.notifications.info.assert_called_once()

    def test_compile_current_file_no_editor(self, mock_api) -> None:
        ext = LiveSass(mock_api)
        mock_api.editors.active_editor = None
        res = ext.compile_current_file()
        assert res is False
        mock_api.notifications.warning.assert_called_once()

    def test_app_instance_compatibility(self, app_instance) -> None:
        ext = LiveSass(app_instance.api)
        res = ext.compile_simple_scss("$c: red;\np { color: $c; }")
        assert "red" in res
