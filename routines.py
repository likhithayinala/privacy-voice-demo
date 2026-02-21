import json
import os
import re
from datetime import datetime

ROUTINES_LOG_FILE = os.path.join(os.path.dirname(__file__), 'routine_history.json')
CUSTOM_ROUTINES_FILE = os.path.join(os.path.dirname(__file__), 'custom_routines.json')

# Predefined routines: trigger phrases -> actions + summary
ROUTINES = {
    "bedtime": {
        "triggers": ["going to bed", "goodnight", "good night", "bedtime", "heading to bed", "time to sleep", "going to sleep"],
        "actions": [
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.kitchen_light", "label": "kitchen light off"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.living_room_light", "label": "living room light off"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.bathroom_light", "label": "bathroom light off"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.office_light", "label": "office light off"},
            {"service": "input_boolean/turn_on", "entity_id": "input_boolean.bedroom_light", "label": "bedroom light on"},
        ],
        "summary": "Turning off all lights except the bedroom, just how you like it for bedtime.",
        "short_name": "bedtime routine"
    },
    "good_morning": {
        "triggers": ["good morning", "i'm awake", "wake up", "morning", "i woke up"],
        "actions": [
            {"service": "input_boolean/turn_on", "entity_id": "input_boolean.kitchen_light", "label": "kitchen light on"},
            {"service": "input_boolean/turn_on", "entity_id": "input_boolean.living_room_light", "label": "living room light on"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.bedroom_light", "label": "bedroom light off"},
        ],
        "summary": "Good morning! Turning on the kitchen and living room lights, and turning off the bedroom light.",
        "short_name": "morning routine"
    },
    "leaving_home": {
        "triggers": ["i'm leaving", "leaving home", "heading out", "going out", "bye bye", "i'm going out", "leaving the house"],
        "actions": [
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.kitchen_light", "label": "kitchen light off"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.bedroom_light", "label": "bedroom light off"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.living_room_light", "label": "living room light off"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.bathroom_light", "label": "bathroom light off"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.office_light", "label": "office light off"},
        ],
        "summary": "Turning off all the lights. Have a great time!",
        "short_name": "leaving home routine"
    },
    "movie_time": {
        "triggers": ["movie time", "watch a movie", "movie night", "watching a movie", "netflix time"],
        "actions": [
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.kitchen_light", "label": "kitchen light off"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.office_light", "label": "office light off"},
            {"service": "input_boolean/turn_on", "entity_id": "input_boolean.living_room_light", "label": "living room light on (dimmed)"},
        ],
        "summary": "Setting up for movie night! Dimming the lights in the living room and turning off the rest. Enjoy!",
        "short_name": "movie night routine"
    },
    "coming_home": {
        "triggers": ["i'm home", "i'm back", "coming home", "just got home", "back home"],
        "actions": [
            {"service": "input_boolean/turn_on", "entity_id": "input_boolean.living_room_light", "label": "living room light on"},
            {"service": "input_boolean/turn_on", "entity_id": "input_boolean.kitchen_light", "label": "kitchen light on"},
        ],
        "summary": "Welcome home! Turning on the living room and kitchen lights.",
        "short_name": "welcome home routine"
    },
    "focus_mode": {
        "triggers": ["focus mode", "i need to work", "study time", "working", "time to focus", "do not disturb"],
        "actions": [
            {"service": "input_boolean/turn_on", "entity_id": "input_boolean.office_light", "label": "office light on"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.living_room_light", "label": "living room light off"},
            {"service": "input_boolean/turn_off", "entity_id": "input_boolean.bedroom_light", "label": "bedroom light off"},
        ],
        "summary": "Focus mode activated! Office light is on, other lights are off. You've got this!",
        "short_name": "focus mode routine"
    }
}


def load_custom_routines():
    """Load user-created routines from file."""
    if os.path.exists(CUSTOM_ROUTINES_FILE):
        try:
            with open(CUSTOM_ROUTINES_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_custom_routines(custom):
    """Save user-created routines to file."""
    try:
        with open(CUSTOM_ROUTINES_FILE, 'w') as f:
            json.dump(custom, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save custom routines: {e}")


def get_all_routines():
    """Merge predefined and custom routines. Custom overrides predefined if same key."""
    merged = dict(ROUTINES)
    merged.update(load_custom_routines())
    return merged


def add_custom_routine(routine_key, routine_data):
    """Add or update a custom routine."""
    custom = load_custom_routines()
    custom[routine_key] = routine_data
    save_custom_routines(custom)
    return True


def remove_custom_routine(routine_key):
    """Remove a custom routine by key."""
    custom = load_custom_routines()
    if routine_key in custom:
        del custom[routine_key]
        save_custom_routines(custom)
        return True
    return False


def list_custom_routines():
    """List all custom routine keys and names."""
    custom = load_custom_routines()
    return {k: v.get('short_name', k) for k, v in custom.items()}


def detect_routine(text):
    """Check if user input matches any routine trigger. Returns routine key or None."""
    if not text:
        return None
    text_lower = text.lower().strip()
    text_clean = re.sub(r'[^\w\s]', '', text_lower)

    all_routines = get_all_routines()
    for routine_key, routine in all_routines.items():
        for trigger in routine["triggers"]:
            if trigger in text_clean:
                return routine_key
    return None


def get_routine(routine_key):
    """Get routine details by key (checks both predefined and custom)."""
    all_routines = get_all_routines()
    return all_routines.get(routine_key)


def log_routine_usage(routine_key):
    """Log when a routine is executed for pattern learning."""
    history = load_routine_history()

    entry = {
        "routine": routine_key,
        "timestamp": datetime.now().isoformat(),
        "hour": datetime.now().hour,
        "day_of_week": datetime.now().strftime("%A")
    }
    history.append(entry)

    # Keep last 100 entries
    history = history[-100:]

    try:
        with open(ROUTINES_LOG_FILE, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save routine history: {e}")


def load_routine_history():
    """Load routine usage history from file."""
    if os.path.exists(ROUTINES_LOG_FILE):
        try:
            with open(ROUTINES_LOG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def get_routine_context(routine_key):
    """Generate context string about past usage of this routine for Gemini."""
    history = load_routine_history()
    routine_entries = [e for e in history if e.get("routine") == routine_key]

    if not routine_entries:
        return None

    count = len(routine_entries)
    last_entry = routine_entries[-1]
    last_day = last_entry.get("day_of_week", "")
    last_hour = last_entry.get("hour", 0)

    # Find most common hour
    hours = [e.get("hour", 0) for e in routine_entries]
    most_common_hour = max(set(hours), key=hours.count) if hours else None

    # Format time nicely
    if most_common_hour is not None:
        if most_common_hour == 0:
            time_str = "midnight"
        elif most_common_hour < 12:
            time_str = f"{most_common_hour} AM"
        elif most_common_hour == 12:
            time_str = "noon"
        else:
            time_str = f"{most_common_hour - 12} PM"
    else:
        time_str = "this time"

    return {
        "times_used": count,
        "usual_time": time_str,
        "last_used_day": last_day,
        "last_used_hour": last_hour
    }
