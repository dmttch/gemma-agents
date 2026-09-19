"""Usage: uv run examples/ws_client.py SESSION_ID 'List the files.'"""

import json
import os

import rich_click as click
from rich.console import Console
from websockets.sync.client import connect


@click.command()
@click.argument("session_id")
@click.argument("prompt")
def main(session_id: str, prompt: str):
    """Send one authenticated prompt to the local WebSocket API and print its events."""
    console = Console()
    token = os.environ["AGENT_API_TOKEN"]
    with connect(f"ws://127.0.0.1:8765/sessions/{session_id}/ws",
                 additional_headers={"Authorization": f"Bearer {token}"}) as websocket:
        websocket.send(json.dumps({"prompt": prompt}))
        for message in websocket:
            console.print_json(message)


if __name__ == "__main__":
    main()
