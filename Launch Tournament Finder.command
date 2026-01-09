#!/bin/bash

# Clash Royale Tournament Finder Launcher
# Double-click this file to start the app

cd "$(dirname "$0")"

echo "🏆 Starting Tournament Finder..."

# Check if Flask is installed
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Installing dependencies..."
    pip3 install -r requirements.txt
fi

# Kill any existing instance on port 5050
lsof -ti:5050 | xargs kill -9 2>/dev/null

# Start the server in background
python3 app.py &
SERVER_PID=$!

# Wait for server to start
sleep 2

# Open browser
open http://localhost:5050

echo "Server running (PID: $SERVER_PID)"
echo "Press Enter to stop the server..."
read

# Cleanup
kill $SERVER_PID 2>/dev/null
echo "Server stopped."
