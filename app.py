from flask import Flask, request, jsonify, render_template
import requests
import re
import os
from dotenv import load_dotenv
from datetime import datetime
from google import genai
import json as json_module
from routines import detect_routine, get_routine, log_routine_usage, get_routine_context, add_custom_routine, remove_custom_routine, list_custom_routines, get_all_routines
import random
import copy

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

# Known entities for Gemini context
KNOWN_ENTITIES = {
    "kitchen light": "input_boolean.kitchen_light",
    "bedroom light": "input_boolean.bedroom_light",
    "living room light": "input_boolean.living_room_light",
    "bathroom light": "input_boolean.bathroom_light",
    "office light": "input_boolean.office_light"
}

# Codename pool for privacy-safe room substitution
ROOM_CODENAMES = [
    "alpha", "bravo", "charlie", "delta", "echo",
    "foxtrot", "golf", "hotel", "india", "juliet",
    "kilo", "lima", "mike", "november", "oscar"
]

def mask_with_codenames(text):
    """Replace room names with random codenames. Returns masked text, codename->room map, and room->codename map."""
    masked = text
    rooms_found = []
    
    rooms = ['living room', 'dining room', 'bedroom', 'kitchen', 'bathroom', 'office', 'garage']
    for room in rooms:
        if room in masked.lower():
            rooms_found.append(room)
    
    # Assign random unique codenames
    available = list(ROOM_CODENAMES)
    random.shuffle(available)
    
    codename_to_room = {}
    room_to_codename = {}
    for i, room in enumerate(rooms_found):
        codename = available[i]
        codename_to_room[codename] = room
        room_to_codename[room] = codename
        masked = re.sub(re.escape(room), codename, masked, flags=re.IGNORECASE)
    
    # Also mask person names and times
    masked = re.sub(r'\b([A-Z][a-z]+)\b', '[PERSON]', masked)
    masked = re.sub(r'\d{1,2}:\d{2}', '[TIME]', masked)
    
    return masked, codename_to_room, room_to_codename


def build_codename_entities(room_to_codename):
    """Build a fake entity mapping using codenames instead of real room names."""
    # Map real entity keys to codename versions
    room_entity_map = {
        "kitchen": ("kitchen light", "input_boolean.kitchen_light"),
        "bedroom": ("bedroom light", "input_boolean.bedroom_light"),
        "living room": ("living room light", "input_boolean.living_room_light"),
        "bathroom": ("bathroom light", "input_boolean.bathroom_light"),
        "office": ("office light", "input_boolean.office_light"),
    }
    
    codename_entities = {}
    codename_to_entity = {}
    
    for room, codename in room_to_codename.items():
        if room in room_entity_map:
            real_label, entity_id = room_entity_map[room]
            fake_label = f"{codename} light"
            codename_entities[fake_label] = entity_id
            codename_to_entity[codename] = entity_id
    
    # Include unmapped entities with their real names (rooms not mentioned by user)
    for room, (label, entity_id) in room_entity_map.items():
        if room not in room_to_codename:
            codename_entities[label] = entity_id
    
    return codename_entities


def unmask_routine_data(parsed, codename_to_room):
    """Replace codename placeholders back to real room names in routine data."""
    result = copy.deepcopy(parsed)
    
    def unmask_text(text):
        for codename, room in codename_to_room.items():
            text = re.sub(re.escape(codename), room, text, flags=re.IGNORECASE)
        text = text.replace('[PERSON]', 'someone')
        text = text.replace('[TIME]', 'the scheduled time')
        return text
    
    # Unmask triggers
    result['triggers'] = [unmask_text(t) for t in result.get('triggers', [])]
    
    # Unmask summary
    if 'summary' in result:
        result['summary'] = unmask_text(result['summary'])
    
    # Unmask short_name
    if 'short_name' in result:
        result['short_name'] = unmask_text(result['short_name'])
    
    # Unmask routine_key
    if 'routine_key' in result:
        result['routine_key'] = unmask_text(result['routine_key'])
    
    # Unmask action labels
    for action in result.get('actions', []):
        if 'label' in action:
            action['label'] = unmask_text(action['label'])
    
    return result


def parse_routine_with_gemini(text):
    """Use Gemini to parse a natural language routine description into structured data."""
    
    # Mask with codenames so Gemini can still distinguish rooms
    masked_text, codename_to_room, room_to_codename = mask_with_codenames(text)
    
    # Build entity list using codenames
    codename_entities = build_codename_entities(room_to_codename)
    entities_str = json_module.dumps(codename_entities, indent=2)
    
    prompt = f"""You are a smart home assistant that creates automation routines.
The user wants to create a new routine. Parse their description and return ONLY a JSON object.

Available devices and their entity_ids:
{entities_str}

The JSON must have this exact structure:
{{
    "routine_key": "a_snake_case_id",
    "short_name": "human readable name",
    "triggers": ["trigger phrase 1", "trigger phrase 2", "trigger phrase 3"],
    "actions": [
        {{"service": "input_boolean/turn_on", "entity_id": "input_boolean.kitchen_light", "label": "alpha light on"}},
        {{"service": "input_boolean/turn_off", "entity_id": "input_boolean.bedroom_light", "label": "bravo light off"}}
    ],
    "summary": "A brief spoken summary of what this routine does."
}}

Rules:
- "triggers" should include the phrase the user wants to say, plus 2-3 natural variations
- "service" must be either "input_boolean/turn_on" or "input_boolean/turn_off"
- "entity_id" must be one of the known entities listed above
- "label" should be a short human-readable description using the room codenames shown above
- "summary" should be a friendly 1-sentence description using room codenames
- "routine_key" should be a unique snake_case identifier derived from the name

User's description: "{masked_text}"

Respond with ONLY the JSON, no other text."""

    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt
        )
        
        content = response.text
        content = content.replace('```json', '').replace('```', '').strip()
        parsed = json_module.loads(content)
        
        # Validate required fields
        required = ['routine_key', 'short_name', 'triggers', 'actions', 'summary']
        if not all(k in parsed for k in required):
            print(f"Missing fields in Gemini response: {parsed.keys()}")
            return None
        
        # Validate actions have valid entity_ids
        valid_entities = set(KNOWN_ENTITIES.values())
        for action in parsed['actions']:
            if action.get('entity_id') not in valid_entities:
                print(f"⚠️ Unknown entity: {action.get('entity_id')}, skipping validation")
            if action.get('service') not in ['input_boolean/turn_on', 'input_boolean/turn_off']:
                print(f"⚠️ Unknown service: {action.get('service')}")
                return None
        
        # Unmask codenames back to real room names
        parsed = unmask_routine_data(parsed, codename_to_room)
        
        return parsed
    except json_module.JSONDecodeError as e:
        print(f"Gemini routine parse JSON error: {e}")
        return None
    except Exception as e:
        print(f"Gemini routine parse error: {e}")
        return None


@app.route('/create_routine', methods=['POST'])
def create_routine():
    """Create a new routine from natural language description."""
    data = request.json
    description = data.get('text', '')
    
    if not description:
        return jsonify({'success': False, 'error': 'No description provided'}), 400
    
    # Parse with Gemini
    parsed = parse_routine_with_gemini(description)
    
    if not parsed:
        return jsonify({
            'success': False,
            'error': 'Could not understand the routine description. Try being more specific.'
        }), 400
    
    routine_key = parsed.pop('routine_key')
    
    # Check for conflicts with existing triggers
    all_routines = get_all_routines()
    existing_triggers = {}
    for rk, rv in all_routines.items():
        for t in rv.get('triggers', []):
            existing_triggers[t.lower()] = rk
    
    conflicts = [t for t in parsed['triggers'] if t.lower() in existing_triggers]
    if conflicts:
        conflicting_routine = existing_triggers[conflicts[0].lower()]
        return jsonify({
            'success': False,
            'error': f"Trigger '{conflicts[0]}' conflicts with existing routine '{conflicting_routine}'."
        }), 409
    
    # Save the routine
    add_custom_routine(routine_key, parsed)
    
    # Log for dashboard
    log_entry = {
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'original': f"[ROUTINE CREATED] {description}",
        'masked': None,
        'route': 'CLOUD (ROUTINE CREATION)',
        'success': True
    }
    command_log.append(log_entry)
    if len(command_log) > 10:
        command_log.pop(0)
    
    return jsonify({
        'success': True,
        'routine_key': routine_key,
        'routine': parsed,
        'message': f"Created routine '{parsed['short_name']}'. Say '{parsed['triggers'][0]}' to activate it!"
    })


@app.route('/delete_routine', methods=['POST'])
def delete_routine():
    """Delete a custom routine."""
    data = request.json
    routine_key = data.get('routine_key', '')
    
    if not routine_key:
        return jsonify({'success': False, 'error': 'No routine key provided'}), 400
    
    # Prevent deleting built-in routines
    from routines import ROUTINES
    if routine_key in ROUTINES:
        return jsonify({'success': False, 'error': 'Cannot delete built-in routines'}), 403
    
    removed = remove_custom_routine(routine_key)
    if removed:
        return jsonify({'success': True, 'message': f"Deleted routine '{routine_key}'"})
    else:
        return jsonify({'success': False, 'error': 'Routine not found'}), 404


# Routine info endpoint for dashboard
@app.route('/api/routines')
def get_routines_api():
    from routines import get_all_routines, get_routine_context, ROUTINES
    all_r = get_all_routines()
    routine_list = []
    for key, routine in all_r.items():
        ctx = get_routine_context(key)
        routine_list.append({
            'key': key,
            'name': routine['short_name'],
            'triggers': routine['triggers'][:3],
            'action_count': len(routine['actions']),
            'context': ctx,
            'is_custom': key not in ROUTINES
        })
    return jsonify(routine_list)

# Dashboard endpoints
@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/logs')
def get_logs():
    return jsonify(command_log)

if __name__ == '__main__':
    app.run(debug=True, port=5000)