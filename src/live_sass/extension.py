from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from biscuit.extensions import Extension

if TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


def generate_sourcemap_v3(
    file_name: str,
    source_name: str,
    source_content: str,
    mappings_count: int,
) -> str:
    """Generate a standard compliant SourceMap v3 JSON string."""
    # AAAA maps col 0 line N to col 0 source 0 line N in VLQ encoding
    mappings = ";".join(["AAAA" for _ in range(mappings_count)])
    data = {
        "version": 3,
        "file": file_name,
        "sources": [source_name],
        "sourcesContent": [source_content],
        "names": [],
        "mappings": mappings,
    }
    return json.dumps(data, indent=2)


class PureSassCompiler:
    """Pure-Python SCSS parser handling variables, nested rules, and source maps."""

    @staticmethod
    def parse_blocks(css_text: str) -> list[tuple[str, list[str], list[Any]]]:
        """Parse CSS/SCSS text into a tree of (selector, declarations, children_blocks)."""
        root_children: list[Any] = []
        stack: list[tuple[str, list[str], list[Any]]] = [("", [], root_children)]

        buffer = ""
        in_string: str | None = None

        for ch in css_text:
            if in_string:
                buffer += ch
                if ch == in_string:
                    in_string = None
                continue

            if ch in ("'", '"'):
                in_string = ch
                buffer += ch
                continue

            if ch == "{":
                selector = buffer.strip()
                buffer = ""
                new_block: tuple[str, list[str], list[Any]] = (selector, [], [])
                stack[-1][2].append(new_block)
                stack.append(new_block)
            elif ch == "}":
                if buffer.strip():
                    stack[-1][1].append(buffer.strip())
                buffer = ""
                if len(stack) > 1:
                    stack.pop()
            elif ch == ";":
                decl = buffer.strip()
                if decl:
                    stack[-1][1].append(decl)
                buffer = ""
            else:
                buffer += ch

        return root_children

    @classmethod
    def flatten_tree(
        cls,
        blocks: list[tuple[str, list[str], list[Any]]],
        parent_selector: str = "",
    ) -> list[tuple[str, list[str]]]:
        """Recursively flatten nested selector blocks resolving '&' parent references."""
        flattened: list[tuple[str, list[str]]] = []

        for selector, decls, children in blocks:
            # Resolve parent selector
            if not parent_selector:
                current_selector = selector
            else:
                if "&" in selector:
                    current_selector = selector.replace("&", parent_selector)
                else:
                    current_selector = f"{parent_selector} {selector}"

            if decls:
                flattened.append((current_selector, decls))

            if children:
                flattened.extend(cls.flatten_tree(children, current_selector))

        return flattened

    @classmethod
    def compile(
        cls,
        scss_content: str,
        style: str = "expanded",
    ) -> str:
        """Compile SCSS content by substituting variables and flattening nesting."""
        variables: dict[str, str] = {}
        cleaned_lines: list[str] = []

        # 1. Variable extraction and substitution
        for raw_line in scss_content.splitlines():
            line = raw_line.strip()
            # Comments
            if line.startswith("//"):
                continue

            # Variable definition: $primary: #3b82f6;
            if line.startswith("$") and ":" in line and line.endswith(";"):
                var_def = line[:-1]
                var_name, var_val = var_def.split(":", 1)
                variables[var_name.strip()] = var_val.strip()
                continue

            # Variable replacement
            for var_name, var_val in variables.items():
                line = line.replace(var_name, var_val)

            cleaned_lines.append(line)

        text = " ".join(cleaned_lines)

        # 2. Block parsing and flattening
        blocks = cls.parse_blocks(text)
        flattened = cls.flatten_tree(blocks)

        # 3. Output generation
        output_rules: list[str] = []
        for selector, decls in flattened:
            if not selector or not decls:
                continue

            clean_decls = [d if d.endswith(";") else f"{d};" for d in decls]

            if style == "compressed":
                rule = f"{selector}{{{''.join(clean_decls)}}}"
            else:
                formatted_decls = "\n".join([f"  {d}" for d in clean_decls])
                rule = f"{selector} {{\n{formatted_decls}\n}}"

            output_rules.append(rule)

        delimiter = "" if style == "compressed" else "\n\n"
        result = delimiter.join(output_rules)
        if result and not result.endswith("\n"):
            result += "\n"
        return result


class LiveSass(Extension):
    """Compile Sass and SCSS files to CSS with live reload and sourcemap support."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.is_watching = False
        self.generate_sourcemap = True
        self.output_style = "expanded"
        self.status_button = None

        self._load_settings()
        self.install()
        self.api.logger.info("Live Sass Compiler extension initialised.")

    def _load_settings(self) -> None:
        if hasattr(self.api, "settings") and self.api.settings:
            self.generate_sourcemap = self.api.settings.get("live_sass.generateSourceMap", self.generate_sourcemap)
            self.output_style = self.api.settings.get("live_sass.style", self.output_style)

    def install(self) -> None:
        """Register commands, settings, and UI controls."""
        if hasattr(self.api, "settings") and self.api.settings:
            self.api.settings.register_setting(
                "live_sass.generateSourceMap",
                type="boolean",
                default=True,
                description="Generate CSS source map file (.map)",
            )
            self.api.settings.register_setting(
                "live_sass.style",
                type="string",
                default="expanded",
                description="Output CSS style: expanded or compressed",
            )

        if hasattr(self.api, "commands") and self.api.commands:
            self.api.commands.register_command("live_sass.compile_current", self.compile_current_file)
            self.api.commands.register_command("live_sass.toggle_watch", self.toggle_watch)

        if hasattr(self.api, "statusbar") and self.api.statusbar:
            if not self.status_button:
                self.status_button = self.api.statusbar.add_button(
                    text="Watch Sass",
                    icon="eye",
                    callback=self.toggle_watch,
                    description="Live Sass watcher (click to toggle)",
                )
                if hasattr(self.status_button, "show"):
                    self.status_button.show()

        self.api.logger.info("Live Sass Compiler extension installed.")

    def get_output_css_path(self, scss_path: str) -> str:
        """Convert a .scss or .sass file path into a .css path."""
        p = scss_path
        if p.endswith(".scss"):
            return p[:-5] + ".css"
        if p.endswith(".sass"):
            return p[:-5] + ".css"
        return p + ".css"

    def compile_simple_scss(self, scss_content: str) -> str:
        """Simple SCSS compilation replacing variables and flattening nested rules."""
        return PureSassCompiler.compile(scss_content, style=self.output_style)

    def _compile_with_libsass(
        self,
        content: str,
        filename: str = "style.scss",
        style: str = "expanded",
        source_map: bool = True,
    ) -> tuple[bool, str, str | None]:
        """Attempt compilation via python libsass module if installed."""
        try:
            import sass
            css_style = "compressed" if style == "compressed" else "expanded"
            if source_map:
                css, map_json = sass.compile(
                    string=content,
                    output_style=css_style,
                    source_comments=False,
                    source_map_filename=f"{filename}.map",
                )
                return True, css, map_json
            css = sass.compile(string=content, output_style=css_style)
            return True, css, None
        except Exception:
            return False, "", None

    def _compile_with_cli(
        self,
        input_path: Path,
        output_path: Path,
        style: str = "expanded",
        source_map: bool = True,
    ) -> bool:
        """Attempt compilation via sass CLI runner."""
        sass_bin = shutil.which("sass")
        if not sass_bin:
            return False

        cmd = [sass_bin, str(input_path), str(output_path)]
        cmd.extend(["--style", "compressed" if style == "compressed" else "expanded"])
        if source_map:
            cmd.append("--source-map")
        else:
            cmd.append("--no-source-map")

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            return proc.returncode == 0
        except Exception:
            return False

    def compile_string(
        self,
        content: str,
        filename: str = "style.scss",
        style: str = "expanded",
        source_map: bool = True,
    ) -> tuple[str, str | None]:
        """Compile SCSS string to CSS and optional SourceMap v3 JSON."""
        # 1. Try libsass
        ok, css, sm = self._compile_with_libsass(content, filename, style, source_map)
        if ok:
            return css, sm

        # 2. Pure Python compilation fallback
        compiled_css = PureSassCompiler.compile(content, style=style)
        map_json = None

        if source_map:
            css_filename = self.get_output_css_path(filename)
            map_filename = f"{css_filename}.map"
            line_count = len(compiled_css.splitlines())
            map_json = generate_sourcemap_v3(
                file_name=Path(css_filename).name,
                source_name=Path(filename).name,
                source_content=content,
                mappings_count=max(1, line_count),
            )
            compiled_css += f"\n/*# sourceMappingURL={Path(map_filename).name} */\n"

        return compiled_css, map_json

    def compile_file(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        style: str = "expanded",
        source_map: bool = True,
    ) -> tuple[str, Path, Path | None] | str:
        """Compile a Sass/SCSS file on disk, writing CSS and source maps."""
        # If input_path is a raw SCSS code string rather than a file on disk
        is_string_source = False
        if isinstance(input_path, str):
            if "\n" in input_path or "{" in input_path or ";" in input_path or "$" in input_path:
                is_string_source = True
            elif not Path(input_path).exists():
                is_string_source = True

        if is_string_source:
            try:
                inp_check = Path(input_path).resolve()
                if inp_check.exists() and inp_check.is_file():
                    is_string_source = False
            except Exception:
                is_string_source = True

        if is_string_source:
            css_text, _ = self.compile_string(str(input_path), filename="input.scss", style=style, source_map=source_map)
            return css_text

        inp = Path(input_path).resolve()
        if not inp.exists():
            raise FileNotFoundError(f"Source file not found: {inp}")

        out = Path(output_path).resolve() if output_path else Path(self.get_output_css_path(str(inp)))
        out.parent.mkdir(parents=True, exist_ok=True)

        # Try CLI if available
        if self._compile_with_cli(inp, out, style, source_map):
            css_text = out.read_text(encoding="utf-8")
            map_path = Path(f"{out}.map") if source_map and Path(f"{out}.map").exists() else None
            return css_text, out, map_path

        # Pure / libsass fallback
        content = inp.read_text(encoding="utf-8")
        css_text, map_json = self.compile_string(content, filename=inp.name, style=style, source_map=source_map)

        out.write_text(css_text, encoding="utf-8")
        map_path: Path | None = None
        if source_map and map_json:
            map_path = Path(f"{out}.map")
            map_path.write_text(map_json, encoding="utf-8")

        return css_text, out, map_path

    def start_watch(self, directory: str | None = None) -> None:
        """Enable live watch mode."""
        self.is_watching = True
        if self.status_button:
            self.status_button.change_text("Watching Sass...")
            self.status_button.change_icon("radio-tower")
        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info("Live Sass: Watch mode enabled.")

    def stop_watch(self) -> None:
        """Disable live watch mode."""
        self.is_watching = False
        if self.status_button:
            self.status_button.change_text("Watch Sass")
            self.status_button.change_icon("eye")
        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info("Live Sass: Watch mode stopped.")

    def toggle_watch(self) -> bool:
        """Biscuit Command: Toggle Sass watch mode."""
        if self.is_watching:
            self.stop_watch()
        else:
            self.start_watch()
        return self.is_watching

    def _get_active_editor(self) -> Any:
        if not hasattr(self.api, "editors") or not self.api.editors:
            return None
        return getattr(self.api.editors, "active_editor", None)

    def compile_current_file(self) -> bool:
        """Biscuit Command: Compile active SCSS/Sass editor file."""
        active = self._get_active_editor()
        if not active:
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.warning("Live Sass: No active file to compile.")
            return False

        file_path = getattr(active, "path", None)
        text_widget = getattr(active, "text", None) or getattr(getattr(active, "content", None), "text", None)

        if file_path and (file_path.endswith(".scss") or file_path.endswith(".sass")):
            try:
                css_text, out_path, map_path = self.compile_file(
                    file_path,
                    style=self.output_style,
                    source_map=self.generate_sourcemap,
                )
                if hasattr(self.api, "notifications") and self.api.notifications:
                    self.api.notifications.info(f"Live Sass: Compiled to {out_path.name}")
                return True
            except Exception as e:
                if hasattr(self.api, "notifications") and self.api.notifications:
                    self.api.notifications.error(f"Live Sass compilation failed: {e}")
                return False

        # Fallback if unsaved buffer
        if text_widget:
            content = text_widget.get("1.0", "end-1c")
            if content:
                css_text, _ = self.compile_string(content)
                if hasattr(self.api, "notifications") and self.api.notifications:
                    self.api.notifications.info("Live Sass: Compiled SCSS buffer.")
                return True

        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.warning("Live Sass: Active document is not an SCSS or Sass file.")
        return False
