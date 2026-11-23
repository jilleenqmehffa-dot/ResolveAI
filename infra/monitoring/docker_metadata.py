"""Expose Docker container names for joining with cAdvisor metrics."""

import http.client
import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer


class DockerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=5)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def prometheus_label(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        try:
            connection = DockerConnection()
            connection.request("GET", "/v1.44/containers/json?all=0")
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"Docker API returned {response.status}")
            containers = json.load(response)
            connection.close()
            lines = ["# HELP docker_container_info Docker container identity.",
                     "# TYPE docker_container_info gauge"]
            for container in containers:
                container_id = container["Id"]
                name = container.get("Names", [container_id[:12]])[0].lstrip("/")
                service = container.get("Labels", {}).get("com.docker.compose.service", "")
                labels = {"id": f"/docker/{container_id}", "name": name, "compose_service": service}
                formatted = ",".join(f'{key}="{prometheus_label(value)}"' for key, value in labels.items())
                lines.append(f"docker_container_info{{{formatted}}} 1")
            body = ("\n".join(lines) + "\n").encode()
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            self.send_error(503, str(error))
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9101), Handler).serve_forever()
