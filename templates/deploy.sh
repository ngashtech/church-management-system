#!/bin/bash
echo "🚀 Preparing for deployment..."

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create necessary directories
mkdir -p static
mkdir -p templates

echo "✅ Ready for deployment!"
echo "📝 Next steps:"
echo "1. Upload all files to PythonAnywhere"
echo "2. Set up Web app with Python 3.10"
echo "3. Configure wsgi.py"