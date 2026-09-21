from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from cabrita.bootstrap.range_server import RangeHandler


def test_media_server_reads_ranges_and_rejects_out_of_bounds(tmp_path: Path) -> None:
    (tmp_path / "media.iso").write_bytes(b"0123456789")
    with ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(RangeHandler, directory=str(tmp_path))
    ) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/media.iso"
        try:
            with urlopen(
                Request(url, headers={"Range": "bytes=2-5"}), timeout=5
            ) as response:
                assert response.status == 206
                assert response.headers["Content-Range"] == "bytes 2-5/10"
                assert response.read() == b"2345"
            with pytest.raises(HTTPError) as error:
                urlopen(Request(url, headers={"Range": "bytes=20-"}), timeout=5)
            assert error.value.code == 416
            error.value.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_media_server_stops_when_its_session_ends(tmp_path: Path) -> None:
    import socket

    from cabrita.providers.helvetios.media_server import EphemeralRangeHTTPServer

    server = EphemeralRangeHTTPServer(
        port=0,
        bind_ip="127.0.0.1",
        bastion_ssh_host="unused",
        bastion_hostname=socket.gethostname(),
        remote_serve_dir=tmp_path,
    )
    with server:
        process = server.process
        assert process is not None
        assert process.poll() is None
    assert process.returncode == 0
    assert server.process is None
