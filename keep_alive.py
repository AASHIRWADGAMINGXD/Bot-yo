"""
Keep-alive server for 24/7 hosting on Render
This creates a simple web server that responds to HTTP requests
to prevent the bot from going to sleep on free hosting services.
"""

from flask import Flask
from threading import Thread
import logging

logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    """Home endpoint - returns bot status"""
    return "Bot is online and running 24/7!"

@app.route('/health')
def health():
    """Health check endpoint for monitoring"""
    return "OK", 200

@app.route('/ping')
def ping():
    """Ping endpoint for uptime monitors"""
    return "Pong!", 200

def run():
    """Run the Flask server"""
    app.run(host='0.0.0.0', port=8080, debug=False)

def keep_alive():
    """Start the keep-alive server in a separate thread"""
    server = Thread(target=run)
    server.daemon = True
    server.start()
    logger.info("Keep-alive server started on port 8080")

if __name__ == "__main__":
    keep_alive()
