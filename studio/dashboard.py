"""Cell 8: table of every running tool and its public link."""
from .lib import *


def show():
    from IPython.display import HTML, display
    start_cell("Cell 8 · Dashboard")

    if not os.path.exists(LINKS_FILE):
        finish_cell(False, None, ["No links registered yet. Run the tool cells (3-7) first."])
    else:
        with open(LINKS_FILE) as f:
            links = json.load(f)

        rows = ""
        for name, info in links.items():
            url, port, usage = info.get("url", ""), info.get("port", ""), info.get("usage", "")
            if url.startswith("https://"):
                button = (f'<a href="{url}" target="_blank" style="background:#2563eb;color:white;padding:6px 12px;'
                          f'border-radius:6px;text-decoration:none;font-weight:bold;display:inline-block;">🔗 Abrir</a>')
            else:
                button = f"<span>{url}</span>"
            rows += f"""
            <tr style="border-bottom:1px solid #374151;">
                <td style="padding:10px;font-weight:bold;">{name}</td>
                <td style="padding:10px;color:#9ca3af;">:{port}</td>
                <td style="padding:10px;">{usage}</td>
                <td style="padding:10px;">{button}<br><small style="color:#6b7280;">{url}</small></td>
            </tr>"""

        display(HTML(f"""
        <div style="font-family:sans-serif;margin-top:10px;">
            <h3 style="margin-bottom:8px;">🎛️ YT-Channel-Studio · Links activos</h3>
            <table style="width:100%;border-collapse:collapse;text-align:left;font-size:14px;">
                <thead><tr style="background:#1f2937;color:#f9fafb;">
                    <th style="padding:10px;">Herramienta</th><th style="padding:10px;">Puerto</th>
                    <th style="padding:10px;">Uso</th><th style="padding:10px;">Acceso</th>
                </tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>"""))

        finish_cell(True, [f"{len(links)} tools registered"])
