"""
This process serves an HTML UI and relays Topotest events to connected
browser clients in real time.
"""
# Flask primitives:
# - Flask: creates the web application object
# - render_template: renders templates/index.html for the root page
# - request: reads incoming HTTP request payloads (JSON in this case)
# flask_socketio adds WebSocket/event-style messaging on top of Flask.
# standard library logging module used to configure Flask/Werkzeug log output
from flask import Flask, render_template, request
from flask_socketio import SocketIO
import logging

# suppress standard Werkzeug request logs to focus on mobility/debug messages
log = logging.getLogger('werkzeug')

# only show ERROR and above from Werkzeug (hide INFO request-per-line logs).
log.setLevel(logging.ERROR)

# create the Flask web app instance.
app = Flask(__name__)

# create a Socket.IO server bound to this Flask app
# `cors_allowed_origins="*"` allows browser clients from any origin to connect
# this is convenient during local development and containerized test runs
socketio = SocketIO(app, cors_allowed_origins="*")

# HTTP GET / serves the visualizer page.
@app.route('/')
def index():
    # render templates/index.html and return it as the HTTP response
    return render_template('index.html')


@app.route('/packet-chart')
def packet_chart():
    """Serve the standalone live packet chart page."""
    return render_template('packet_chart.html')


@app.route('/health')
def health():
    """Basic liveness endpoint used by optional test-side startup checks."""
    return {"status": "ok"}, 200

# HTTP POST /event is called by the Topotest file to push (send) new events
@app.route('/event', methods=['POST'])
def receive_event():
    """Receive JSON from Topotest and broadcast it to all browser clients."""

    # parse request JSON body into a Python dict/list (or None if missing).
    data = request.json

    # emit one Socket.IO event named `network_event` to every connected client.
    # the frontend listens for this event and updates topology/animations.
    socketio.emit('network_event', data)

    # return a simple JSON success response and HTTP 200 status code.
    return {"status": "success"}, 200

# run this block only when the file is executed directly (not imported).
if __name__ == '__main__':
    # Print startup banner so users know where to open the UI.
    print("=========================================")
    print(" EVPN Visualizer starting on port 5000")
    print(" Open http://localhost:5000 in your browser")
    print("=========================================")

    # Start the Socket.IO development server.
    # - host='0.0.0.0' binds on all interfaces so Docker port publishing works.
    # - port=5000 is the externally mapped visualizer port.
    # - allow_unsafe_werkzeug=True permits Werkzeug in this non-production setup.
    socketio.run(app, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
    