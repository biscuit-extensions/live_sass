from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.live_sass import setup
from src.live_sass.extension import LiveSass


class TestLiveSassExtension:
    def test_extension_setup(self, app_instance) -> None:
        setup(app_instance.api)
        ext = LiveSass(app_instance.api)
        assert ext.get_output_css_path("styles/main.scss") == "styles/main.css"
        scss = "$primary: #3b82f6;\nbody { color: $primary; }"
        css = ext.compile_simple_scss(scss)
        assert "#3b82f6" in css
        assert "$primary" not in css
