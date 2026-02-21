import requests
import json
from app import process_command_logic
import os

# Smallest.ai configuration
SMALLEST_API_KEY = os.getenv('SMALLEST_API_KEY', '')
SMALLEST_STT_URL = 'https://api.smallest.ai/v1/stt'  # Adjust to actual endpoint
SMALLEST_TTS_URL = 'https://api.smallest.ai/v1/tts'  # Adjust to actual endpoint

def transcribe_audio(audio_file_path):
    """Convert audio to text using Smallest.ai"""
    # TODO: Update with actual Smallest.ai API call
    headers = {
        'Authorization': f'Bearer {SMALLEST_API_KEY}'
    }
    
    with open(audio_file_path, 'rb') as audio_file:
        files = {'audio': audio_file}
        response = requests.post(SMALLEST_TTS_URL, headers=headers, files=files)
        
        if response.status_code == 200:
            return response.json()['text']
    return None

def text_to_speech(text):
    """Convert text to speech using Smallest.ai"""
    # TODO: Update with actual Smallest.ai API call
    headers = {
        'Authorization': f'Bearer {SMALLEST_API_KEY}',
        'Content-Type': 'application/json'
    }
    
    payload = {'text': text}
    response = requests.post(SMALLEST_TTS_URL, headers=headers, json=payload)
    
    if response.status_code == 200:
        return response.content  # Audio bytes
    return None

def voice_loop():
    """Main voice interaction loop"""
    print("🎤 Voice assistant ready. Say a command...")
    
    while True:
        # Record audio (implement based on your setup)
        # audio_path = record_audio()
        
        # Transcribe
        # text = transcribe_audio(audio_path)
        # print(f"Heard: {text}")
        
        # For now, use text input
        text = input("Command: ")
        
        if text.lower() in ['quit', 'exit']:
            break
        
        # Process through your privacy layer
        from app import mask_sensitive_data, should_process_locally, parse_local_command, call_claude_api, call_home_assistant
        
        masked, _ = mask_sensitive_data(text)
        is_local = should_process_locally(text)
        
        if is_local:
            result = "Command processed locally"
        else:
            result = "Command processed via cloud with privacy masking"
        
        print(f"Result: {result}")
        
        # Convert to speech
        # audio = text_to_speech(result)
        # play_audio(audio)

if __name__ == '__main__':
    voice_loop()
