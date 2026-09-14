"""Local-only alert delivery sink. External recipients require operator setup."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from services.observability import configure_logging, event


class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if self.path != "/alerts" or not 0 < size <= 262144:
            self.send_error(400)
            return
        try:
            payload = json.loads(self.rfile.read(size))
            for alert in payload.get("alerts", []):
                event("monitoring_alert", status=alert.get("status"),
                      error_code=alert.get("labels", {}).get("alertname"))
        except (ValueError, TypeError, AttributeError):
            self.send_error(400)
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    configure_logging()
    HTTPServer(("0.0.0.0", 9102), Receiver).serve_forever()
