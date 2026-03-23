#!/bin/bash

echo "🚀 Document Data Generation Frontend Setup"
echo "=========================================="

# Get the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$DIR"

echo "📁 Working directory: $DIR"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "🔧 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔄 Activating virtual environment..."
source venv/bin/activate

# Install/upgrade pip
echo "📦 Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "📦 Installing requirements..."
pip install -r requirements.txt

# Check if required files exist
if [ ! -f "config.json" ]; then
    echo "⚠️  Warning: config.json not found. A default one will be created on first run."
fi

if [ ! -f "curl_input.json" ]; then
    echo "⚠️  Warning: curl_input.json not found. Please paste a curl command in the frontend to create it."
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "🌐 Starting the server..."
echo "   Frontend will be available at: http://localhost:5050"
echo "   Press Ctrl+C to stop the server"
echo ""

# Start the Flask server
python server.py\