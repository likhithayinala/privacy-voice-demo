from flask import Flask, request, jsonify, render_template
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

SMALLEST_API_KEY = os.getenv('SMALLEST_API_KEY')
SMALLEST_API_BASE = 'https://waves.smallest.ai/api/v4.0'

@app.route('/')
def voice_interface():
    return render_template('voice.html')

@app.route('/api/process_voice', methods=['POST'])
def process_voice():
    """Process text as if it came from voice"""
    data = request.json
    text = data.get('text', '')
    
    # Send to main Flask app
    response = requests.post('http://localhost:5000/process_command', 
                            json={'text': text})
    
    result = response.json()
    
    # Generate TTS response
    tts_text = f"Command processed. Route: {result['route']}"
    
    # Call Smallest.ai TTS
    tts_url = f"{SMALLEST_API_BASE}/tts/stream"
    headers = {
        'Authorization': f'Bearer {SMALLEST_API_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {
        'text': tts_text,
        'voice': 'en-US-Neural2-A'
    }
    
    tts_response = requests.post(tts_url, headers=headers, json=payload)
    
    return jsonify({
        'success': result['success'],
        'route': result['route'],
        'tts_available': tts_response.status_code == 200
    })

if __name__ == '__main__':
    app.run(debug=True, port=5001)
