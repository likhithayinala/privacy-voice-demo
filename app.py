from flask import Flask, request, jsonify, render_template
import requests
import re
import os
from dotenv import load_dotenv
from datetime import datetime
from google import genai
import json as json_module

load_dotenv()

app = Flask(__name__)

HA_URL = os.getenv('HA_URL')
HA_TOKEN = os.getenv('HA_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Command log for dashboard
command_log = []

# Init Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

# Privacy masking function
def mask_sensitive_data(text):
    """Mask sensitive information in commands"""
    masked = text
    original_rooms = []
    
    # Mask room names
    rooms = ['bedroom', 'kitchen', 'living room', 'bathroom', 'dining room', 'office', 'garage']
    for room in rooms:
        if room in masked.lower():
            original_rooms.append(room)
            masked = re.sub(room, '[ROOM]', masked, flags=re.IGNORECASE)
    
    # Mask person names (simple pattern)
    masked = re.sub(r'\b([A-Z][a-z]+)\b', '[PERSON]', masked)
    
    # Mask times
    masked = re.sub(r'\d{1,2}:\d{2}', '[TIME]', masked)
    
    return masked, original_rooms

# Routing decision
def should_process_locally(text):
    """Decide if command is simple enough for local processing"""
    simple_patterns = [
        'turn on',
        'turn off', 
        'lights on',
        'lights off',
        'activate',
        'trigger',
        'switch on',
        'switch off'
    ]
    return any(pattern in text.lower() for pattern in simple_patterns)

# Call Home Assistant API
def call_home_assistant(service, entity_id=None, data=None):
    """Make API call to Home Assistant"""
    headers = {
        'Authorization': f'Bearer {HA_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    url = f"{HA_URL}/api/services/{service}"
    
    payload = {}
    if entity_id:
        payload['entity_id'] = entity_id
    if data:
        payload.update(data)
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        return response.json() if response.status_code == 200 else None
    except Exception as e:
        print(f"HA API Error: {e}")
        return None

# Get device state from Home Assistant
def get_device_state(entity_id):
    """Get current state of a device"""
    headers = {
        'Authorization': f'Bearer {HA_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    url = f"{HA_URL}/api/states/{entity_id}"
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"HA State API Error: {e}")
        return None

# Parse simple commands locally
def parse_local_command(text):
    """Parse simple on/off commands without LLM"""
    text_lower = text.lower()
    
    # Check for "all lights" command
    if 'all lights' in text_lower or 'all the lights' in text_lower:
        action = 'turn_on' if 'on' in text_lower else 'turn_off'
        return {
            'service': f'input_boolean/{action}',
            'entity_id': [
                'input_boolean.kitchen_light',
                'input_boolean.bedroom_light',
                'input_boolean.living_room_light',
                'input_boolean.bathroom_light',
                'input_boolean.office_light'
            ],
            'room': 'all rooms',
            'action': 'on' if action == 'turn_on' else 'off',
            'device': 'lights'
        }
    
    # Extract action
    action = 'turn_on' if 'on' in text_lower else 'turn_off'
    action_friendly = 'on' if action == 'turn_on' else 'off'
    
    # Extract room/entity - match your helper names
    room = None
    if 'kitchen' in text_lower:
        entity = 'input_boolean.kitchen_light'
        room = 'kitchen'
    elif 'bedroom' in text_lower:
        entity = 'input_boolean.bedroom_light'
        room = 'bedroom'
    elif 'living' in text_lower:
        entity = 'input_boolean.living_room_light'
        room = 'living room'
    elif 'bathroom' in text_lower:
        entity = 'input_boolean.bathroom_light'
        room = 'bathroom'
    elif 'office' in text_lower:
        entity = 'input_boolean.office_light'
        room = 'office'
    else:
        entity = 'input_boolean.kitchen_light'
        room = 'kitchen'
    
    return {
        'service': f'input_boolean/{action}',
        'entity_id': entity,
        'room': room,
        'action': action_friendly,
        'device': 'light'
    }

# Check if command is a query
def is_state_query(text):
    """Check if user is asking about device state"""
    query_patterns = ['is the', 'is my', 'are the', 'status', 'check']
    return any(p in text.lower() for p in query_patterns)

# Call Gemini API for complex commands
def call_gemini_api(text):
    """Use Gemini to parse complex commands"""
    
    prompt = f"""Parse this smart home command and return ONLY a JSON object with:
- service: the Home Assistant service (e.g., "light/turn_on", "scene/turn_on")
- entity_id: the entity to control (e.g., "light.kitchen", "scene.movie_mode")
- data: any additional parameters (optional)
- room: the room mentioned (e.g., "kitchen", "bedroom")
- action: what's being done (e.g., "on", "off", "dim", "brighten")
- device: what device type (e.g., "light", "fan", "thermostat")

Command: {text}

Respond with ONLY the JSON, no other text."""

    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt
        )
        
        content = response.text
        content = content.replace('```json', '').replace('```', '').strip()
        return json_module.loads(content)
    except Exception as e:
        print(f"Gemini API Exception: {e}")
        return None

# Main command processing endpoint
@app.route('/process_command', methods=['POST'])
def process_command():
    data = request.json
    original_text = data.get('text', '')
    
    # Check if this is a state query
    if is_state_query(original_text):
        # Extract room and get state
        text_lower = original_text.lower()
        entity = None
        room = None
        
        if 'kitchen' in text_lower:
            entity = 'input_boolean.kitchen_light'
            room = 'kitchen'
        elif 'bedroom' in text_lower:
            entity = 'input_boolean.bedroom_light'
            room = 'bedroom'
        elif 'living' in text_lower:
            entity = 'input_boolean.living_room_light'
            room = 'living room'
        
        if entity:
            state = get_device_state(entity)
            return jsonify({
                'success': True,
                'route': 'LOCAL',
                'result': 'State query',
                'room': room,
                'action': 'query',
                'device': 'light',
                'state': state.get('state') if state else 'unknown'
            })
    
    # Apply privacy masking
    masked_text, rooms = mask_sensitive_data(original_text)
    
    # Decide routing
    is_local = should_process_locally(original_text)
    route = 'LOCAL' if is_local else 'CLOUD'
    
    # Process command
    if is_local:
        parsed = parse_local_command(original_text)
        
        # Handle multiple entities (all lights)
        if isinstance(parsed.get('entity_id'), list):
            ha_response = True
            for eid in parsed['entity_id']:
                result = call_home_assistant(parsed['service'], eid)
                if not result:
                    ha_response = False
        else:
            ha_response = call_home_assistant(parsed['service'], parsed.get('entity_id'))
        
        result_text = f"Executed {parsed['service']} locally"
    else:
        parsed = call_gemini_api(masked_text)
        if parsed:
            ha_response = call_home_assistant(
                parsed.get('service'),
                parsed.get('entity_id'),
                parsed.get('data')
            )
            result_text = f"Executed via Gemini: {parsed.get('service')}"
        else:
            result_text = "Failed to parse command"
            ha_response = None
            parsed = {}
    
    # Log for dashboard
    log_entry = {
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'original': original_text,
        'masked': masked_text if not is_local else None,
        'route': route,
        'success': ha_response is not None
    }
    command_log.append(log_entry)
    
    if len(command_log) > 10:
        command_log.pop(0)
    
    return jsonify({
        'success': ha_response is not None,
        'route': route,
        'result': result_text,
        'room': parsed.get('room'),
        'action': parsed.get('action'),
        'device': parsed.get('device')
    })

# Dashboard endpoints
@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/logs')
def get_logs():
    return jsonify(command_log)

if __name__ == '__main__':
    app.run(debug=True, port=5000)