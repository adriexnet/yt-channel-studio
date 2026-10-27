"""YT-Channel-Studio: produce, assemble and publish short vertical videos from Google Colab.

The notebook only calls functions from this package; see README.md for the cell-by-cell guide.
"""
__version__ = "2.2.0"

MODULES = ["studio.lib", "studio.pipeline", "studio.setup", "studio.tools.ollama", "studio.tools.forge",
           "studio.tools.comfyui", "studio.tools.audio", "studio.control_center", "studio.dashboard"]


def reload():
    """Re-import every loaded module after a `git pull`, in dependency order, without restarting Colab."""
    import importlib, sys
    for name in MODULES:
        if name in sys.modules:
            importlib.reload(sys.modules[name])
