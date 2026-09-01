from __future__ import annotations

import typing

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


class LiveSass(Extension):
    """Compile Sass and SCSS files to CSS with live reload and sourcemap support"""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.api.logger.info("Live Sass Compiler extension initialised.")

    def get_output_css_path(self, scss_path: str) -> str:
        """Convert a .scss or .sass file path into a .css path."""
        p = scss_path
        if p.endswith(".scss"):
            return p[:-5] + ".css"
        if p.endswith(".sass"):
            return p[:-5] + ".css"
        return p + ".css"

    def compile_simple_scss(self, scss_content: str) -> str:
        """Simple SCSS compilation replacing variables and trimming whitespace."""
        variables: dict[str, str] = {}
        output_lines = []
        for line in scss_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("$") and ":" in stripped and stripped.endswith(";"):
                var_name, var_val = stripped[:-1].split(":", 1)
                variables[var_name.strip()] = var_val.strip()
                continue
            for var_name, var_val in variables.items():
                line = line.replace(var_name, var_val)
            output_lines.append(line)
        return "\n".join(output_lines)

    def install(self) -> None:
        self.api.logger.info("Live Sass Compiler extension installed.")
