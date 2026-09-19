"""Build user guides and API documentation from the installed project."""

from importlib.metadata import version as package_version

project = "Gemma Agents"
author = "dmttch"
copyright = "2026, dmttch"
release = package_version("gemma-agents")
version = release
language = "fr"
extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.napoleon",
              "sphinx.ext.viewcode", "sphinx_click"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
html_theme = "sphinx_rtd_theme"
html_title = f"Gemma Agents {release}"
autodoc_member_order = "bysource"
autodoc_typehints = "description"
napoleon_google_docstring = True
nitpicky = False
myst_heading_anchors = 3
linkcheck_ignore = [r"http://localhost.*", r"http://127\.0\.0\.1.*"]
linkcheck_timeout = 15
linkcheck_retries = 2
