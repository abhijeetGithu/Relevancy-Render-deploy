#!/usr/bin/env python3
"""
Startup script for Document Data Generation Frontend
This script handles dependency installation and server startup
"""

import os
import sys
import subprocess
import json

def check_and_install_requirements():
    """Check if Flask and flask-cors are installed, install if not"""
    try:
        import flask
        import flask_cors
        print("✅ Flask dependencies are already installed")
        return True
    except ImportError:
        print("📦 Installing Flask dependencies...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "flask-cors"])
            print("✅ Flask dependencies installed successfully")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Error installing dependencies: {e}")
            return False

def check_config_files():
    """Check if required config files exist and create defaults if needed"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Check curl_input.json
    curl_input_path = os.path.join(base_dir, 'curl_input.json')
    if not os.path.exists(curl_input_path):
        print("⚠️  curl_input.json not found. You'll need to paste a curl command in the frontend.")
    
    # Check config.json
    config_path = os.path.join(base_dir, 'config.json')
    if not os.path.exists(config_path):
        print("⚠️  config.json not found. Creating a default one...")
        default_config = {
            "REGENERATE_QUERIES": "yes",
            "APPEND_TO_CSV": True,
            "MULTI_SOURCE_CONFIG": {}
        }
        try:
            with open(config_path, 'w') as f:
                json.dump(default_config, f, indent=2)
            print("✅ Default config.json created")
        except Exception as e:
            print(f"❌ Error creating config.json: {e}")

def start_server():
    """Start the Flask server"""
    print("\n🚀 Starting Document Data Generation Frontend...")
    print("=" * 50)
    print("🌐 Frontend URL: http://localhost:5050")
    print("📁 Working directory:", os.path.dirname(os.path.abspath(__file__)))
    print("💡 Press Ctrl+C to stop the server")
    print("=" * 50)
    
    try:
        # Import and run the server
        from server import app
        app.run(debug=True, host='0.0.0.0', port=5050)
    except ImportError:
        print("❌ Error: server.py not found or has import errors")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user")
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        sys.exit(1)

def main():
    """Main startup function"""
    print("🚀 Document Data Generation Frontend Setup")
    print("=" * 50)
    
    # Check and install dependencies
    if not check_and_install_requirements():
        print("❌ Failed to install dependencies. Please install Flask manually:")
        print("   pip install flask flask-cors")
        sys.exit(1)
    
    # Check config files
    check_config_files()
    
    # Start the server
    start_server()

if __name__ == "__main__":
    main()