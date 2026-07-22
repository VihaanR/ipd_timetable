from __future__ import annotations

import socket

from webapp.server import _choose_port


def test_choose_port_skips_occupied_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        occupied_port = busy.getsockname()[1]

        chosen_port = _choose_port("127.0.0.1", occupied_port)

    assert chosen_port > occupied_port