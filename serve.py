"""Static file server for dashboard.html — Railway web service."""
import http.server
import socketserver
import os

PORT = int(os.environ.get("PORT", 8080))

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress request logs

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Dashboard serving on port {PORT}", flush=True)
    httpd.serve_forever()
