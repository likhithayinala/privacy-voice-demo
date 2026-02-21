import pyaudio
import os
import asyncio
import websockets
import json
import wave
import threading
import struct
import math
import re
from google import genai
from urllib.parse import urlencode
from dotenv import load_dotenv
from io import BytesIO
from app import mask_sensitive_data
from routines import detect_routine, get_routine_context

load_dotenv()

SMALLEST_API_KEY = os.getenv('SMALLEST_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Init Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

class VoiceAssistant:
    def __init__(self):
        self.api_key = SMALLEST_API_KEY
        self.gemini_key = GEMINI_API_KEY
        self.sample_rate = 16000
        self.tts_sample_rate = 24000
        self.wake_word = "hey sunday"
        self.assistant_name = "Sunday"
        
        # Audio settings
        self.chunk_size = 1024
        self.silence_threshold = 300  # Lowered for better sensitivity
        self.silence_duration = 1.5   # Seconds of silence to stop recording
        
        # Initialize streaming TTS
        try:
            from smallestai.waves import TTSConfig, WavesStreamingTTS
            self.tts_config = TTSConfig(
                voice_id="aditi",
                api_key=self.api_key,
                sample_rate=self.tts_sample_rate,
                speed=1.0,
                max_buffer_flush_ms=100
            )
            self.streaming_tts = WavesStreamingTTS(self.tts_config)
            self.use_streaming_tts = True
            print("✓ Streaming TTS initialized")
        except ImportError:
            print("⚠️ smallestai package not found, using REST API for TTS")
            self.use_streaming_tts = False
        except Exception as e:
            print(f"⚠️ Streaming TTS init failed: {e}, using REST API")
            self.use_streaming_tts = False

    def get_audio_level(self, data):
        """Calculate RMS audio level from raw audio data"""
        try:
            count = len(data) // 2
            shorts = struct.unpack(f"{count}h", data)
            sum_squares = sum(s * s for s in shorts)
            rms = math.sqrt(sum_squares / count) if count > 0 else 0
            return rms
        except:
            return 0

    async def listen_for_wake_word(self, timeout_seconds=30):
        """Continuously listen for wake word with rolling buffer"""
        
        BASE_WS_URL = "wss://waves-api.smallest.ai/api/v1/pulse/get_text"
        params = {
            "language": "en",
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "word_timestamps": "false"
        }
        uri = f"{BASE_WS_URL}?{urlencode(params)}"
        
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size
        )
        
        wake_detected = False
        start_time = asyncio.get_event_loop().time()
        
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            
            async with websockets.connect(uri, additional_headers=headers) as websocket:
                recent_transcripts = []
                
                async def send_audio():
                    nonlocal wake_detected
                    try:
                        while not wake_detected:
                            elapsed = asyncio.get_event_loop().time() - start_time
                            if elapsed > timeout_seconds:
                                break
                            
                            data = stream.read(self.chunk_size, exception_on_overflow=False)
                            # Send all audio - let the STT service handle noise
                            await websocket.send(data)
                            await asyncio.sleep(self.chunk_size / self.sample_rate)
                        
                        await websocket.send(b'')
                    except Exception as e:
                        print(f"Send error: {e}")
                
                async def receive_transcription():
                    nonlocal wake_detected, recent_transcripts
                    try:
                        while not wake_detected:
                            try:
                                message = await asyncio.wait_for(
                                    websocket.recv(), 
                                    timeout=2.0
                                )
                                
                                data = json.loads(message)
                                transcript = data.get('transcript', '').lower().strip()
                                
                                if transcript:
                                    recent_transcripts.append(transcript)
                                    recent_transcripts = recent_transcripts[-5:]
                                    combined = ' '.join(recent_transcripts)
                                    
                                    print(f"   [Heard]: {transcript}")
                                    
                                    if self.check_wake_word(transcript) or self.check_wake_word(combined):
                                        wake_detected = True
                                        return
                                        
                            except asyncio.TimeoutError:
                                elapsed = asyncio.get_event_loop().time() - start_time
                                if elapsed > timeout_seconds:
                                    return
                            except json.JSONDecodeError:
                                pass
                                
                    except websockets.exceptions.ConnectionClosed:
                        pass
                    except Exception as e:
                        print(f"Receive error: {e}")
                
                await asyncio.gather(
                    send_audio(),
                    receive_transcription(),
                    return_exceptions=True
                )
                
        except websockets.exceptions.InvalidStatusCode as e:
            print(f"WebSocket auth error: {e.status_code}")
        except Exception as e:
            print(f"WebSocket connection error: {e}")
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
        
        return wake_detected

    async def listen_for_command(self, max_duration=5):
        """Listen for a command - simple version"""
        
        BASE_WS_URL = "wss://waves-api.smallest.ai/api/v1/pulse/get_text"
        params = {
            "language": "en",
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "word_timestamps": "false"
        }
        uri = f"{BASE_WS_URL}?{urlencode(params)}"
        
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.chunk_size
        )
        
        full_text = ""
        
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            
            async with websockets.connect(uri, additional_headers=headers) as websocket:
                audio_finished = asyncio.Event()
                
                async def send_audio():
                    try:
                        num_chunks = int(self.sample_rate / self.chunk_size * max_duration)
                        for _ in range(num_chunks):
                            data = stream.read(self.chunk_size, exception_on_overflow=False)
                            await websocket.send(data)
                            await asyncio.sleep(self.chunk_size / self.sample_rate)
                        await websocket.send(b'')
                        audio_finished.set()
                    except Exception as e:
                        print(f"Send error: {e}")
                        audio_finished.set()
                
                async def receive_transcription():
                    nonlocal full_text
                    try:
                        while True:
                            try:
                                timeout = 3.0 if audio_finished.is_set() else 10.0
                                message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                                data = json.loads(message)
                                transcript = data.get('transcript', '')
                                if transcript:
                                    full_text = transcript
                                    print(f"   [Command]: {transcript}")
                                if data.get('is_final', False):
                                    return
                            except asyncio.TimeoutError:
                                if audio_finished.is_set():
                                    return
                            except json.JSONDecodeError:
                                pass
                    except websockets.exceptions.ConnectionClosed:
                        pass
                    except Exception as e:
                        print(f"Receive error: {e}")
                
                await asyncio.gather(send_audio(), receive_transcription())
                
        except Exception as e:
            print(f"WebSocket error: {e}")
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
        
        return full_text.strip()

    def play_audio_stream(self, audio_chunks):
        """Play audio chunks in real-time"""
        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.tts_sample_rate,
            output=True
        )
        
        try:
            for chunk in audio_chunks:
                stream.write(chunk)
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

    def speak_streaming(self, text):
        """Speak using streaming TTS with real-time playback"""
        if self.use_streaming_tts:
            print(f"🔊 {self.assistant_name}: {text}")
            
            p = pyaudio.PyAudio()
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.tts_sample_rate,
                output=True
            )
            
            try:
                for chunk in self.streaming_tts.synthesize(text):
                    stream.write(chunk)
            except Exception as e:
                print(f"⚠️ Streaming TTS error: {e}")
                stream.stop_stream()
                stream.close()
                p.terminate()
                self._speak_rest_api_sync(text)
                return
            finally:
                stream.stop_stream()
                stream.close()
                p.terminate()
        else:
            self._speak_rest_api_sync(text)

    def _speak_rest_api_sync(self, text):
        """Fallback TTS using REST API (synchronous)"""
        import requests
        
        print(f"🔊 {self.assistant_name}: {text}")
        
        url = "https://waves-api.smallest.ai/api/v1/lightning/get_speech"
        
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'text': text,
            'voice_id': 'aditi',
            'sample_rate': self.tts_sample_rate,
            'add_wav_header': True
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, stream=True, timeout=10)
            
            if response.status_code == 200:
                with open('response.wav', 'wb') as f:
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:
                            f.write(chunk)
                self.play_audio('response.wav')
        except Exception as e:
            print(f"TTS Exception: {e}")

    def play_audio(self, audio_file):
        """Play audio file"""
        if os.name == 'nt':
            import winsound
            winsound.PlaySound(audio_file, winsound.SND_FILENAME)
        else:
            os.system(f'mpg123 {audio_file}')

    def play_chime(self, success=True):
        """Play a sound to indicate wake word detected or command status"""
        if os.name == 'nt':
            import winsound
            if success:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
            else:
                winsound.PlaySound("SystemHand", winsound.SND_ALIAS | winsound.SND_ASYNC)

    def check_wake_word(self, text):
        """Check if text contains wake word"""
        if not text:
            return False
        
        text_clean = re.sub(r'[^\w\s]', '', text.lower().strip())
        
        wake_variations = [
            "hey sunday", "hey sonday", "hey sundae", "hey sun day",
            "hey son day", "he sunday", "hay sunday", "hey sanday",
            "a sunday", "hey sunde", "hey sundy",
            "hey san day", "hey someday", "hey sandy",
            "hey", "hello", "hi"
        ]
        
        for wake in wake_variations:
            if wake in text_clean:
                print(f"   [Wake] ✓ Matched: '{wake}' in '{text_clean}'")
                return True
        
        if len(text_clean.split()) <= 3 and "sunday" in text_clean:
            print(f"   [Wake] ✓ Matched 'sunday' in short phrase: '{text_clean}'")
            return True
            
        return False

    def unmask_sensitive_data(self, text, original_rooms):
        """Replace [ROOM] placeholders back with original room names"""
        unmasked = text
        for room in original_rooms:
            # Replace one [ROOM] at a time with the corresponding original room
            unmasked = unmasked.replace('[ROOM]', room, 1)
        # If any [ROOM] tokens remain (e.g., Gemini echoed extras), replace with generic
        unmasked = unmasked.replace('[ROOM]', 'room')
        # Clean up other placeholder artifacts Gemini might echo back
        unmasked = unmasked.replace('[PERSON]', 'someone')
        unmasked = unmasked.replace('[TIME]', 'the scheduled time')
        return unmasked

    def is_routine_creation_request(self, text):
        """Check if user wants to create a new routine."""
        if not text:
            return False
        text_lower = text.lower()
        creation_phrases = [
            "create a routine", "new routine", "make a routine",
            "set up a routine", "add a routine", "create routine",
            "save a routine", "build a routine", "custom routine",
            "when i say", "whenever i say"
        ]
        return any(phrase in text_lower for phrase in creation_phrases)

    def is_routine_deletion_request(self, text):
        """Check if user wants to delete a routine."""
        if not text:
            return False
        text_lower = text.lower()
        deletion_phrases = [
            "delete routine", "remove routine", "delete the routine",
            "remove the routine", "get rid of routine"
        ]
        return any(phrase in text_lower for phrase in deletion_phrases)

    def generate_response(self, user_input, command_result=None):
        """Generate conversational response using Gemini"""
        
        # Mask sensitive data before sending to Gemini
        masked_input, original_rooms = mask_sensitive_data(user_input)
        masked_command_result = None
        if command_result:
            masked_command_result = dict(command_result)
            if masked_command_result.get('room'):
                masked_command_result['room'] = '[ROOM]'
        
        # Check if this was a routine creation
        if command_result and command_result.get('routine_created'):
            routine_info = command_result.get('routine', {})
            triggers = routine_info.get('triggers', [])
            trigger_hint = f"'{triggers[0]}'" if triggers else "the trigger phrase"
            return f"Done! I've created your {routine_info.get('short_name', 'new routine')}. Just say {trigger_hint} anytime to activate it."
        
        # Check if this was a routine execution
        if command_result and command_result.get('routine'):
            routine_key = command_result['routine']
            routine_summary = command_result.get('routine_summary', '')
            routine_actions = command_result.get('routine_actions', [])
            routine_context = command_result.get('routine_context')
            
            context_hint = ""
            if routine_context and routine_context.get('times_used', 0) > 1:
                context_hint = f"""
The user has triggered this routine {routine_context['times_used']} times before, 
usually around {routine_context['usual_time']}. 
Make your response feel personalized — mention that you remember their pattern, 
e.g., 'like you usually do around this time' or 'your usual routine'."""
            
            action_labels = [a['label'] for a in routine_actions if a.get('success')]
            
            prompt = f"""You are Sunday, a friendly, proactive smart home voice assistant.
Your responses should be brief, warm, and conversational (1-2 sentences max).
The user said: "{masked_input}"

You executed their routine which performed these actions: {', '.join(action_labels)}.
{context_hint}

Generate a brief, warm confirmation. Don't list every action mechanically — 
summarize naturally. Sound like a thoughtful assistant who knows their habits.
Use [ROOM] as a placeholder for room names if needed."""

            try:
                response = client.models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=prompt
                )
                return self.unmask_sensitive_data(response.text, original_rooms)
            except Exception as e:
                print(f"Gemini API Exception: {e}")
                return routine_summary
        
        # Non-routine: existing logic
        if command_result:
            prompt = f"""You are Sunday, a friendly and helpful smart home voice assistant. 
Your responses should be brief, warm, and conversational (1-2 sentences max).
The user said: "{masked_input}"
The command was processed with result: {json.dumps(masked_command_result)}

Generate a brief, friendly confirmation response. Don't mention technical details like "entity_id" or "service".
Just confirm what you did in natural language. Use [ROOM] as a placeholder for room names if needed."""
        else:
            prompt = f"""You are Sunday, a friendly and helpful smart home voice assistant.
Your responses should be brief, warm, and conversational (1-2 sentences max).
The user said: "{masked_input}"

If this seems like a greeting or casual conversation, respond warmly.
If it's a command you can't process, apologize briefly and ask them to try again.
Use [ROOM] as a placeholder for room names if needed."""

        try:
            response = client.models.generate_content(
                model="gemini-3-flash-preview",
                contents=prompt
            )
            return self.unmask_sensitive_data(response.text, original_rooms)
        except Exception as e:
            print(f"Gemini API Exception: {e}")
            return "I'm having trouble connecting right now."


async def main_async():
    """Main conversational voice loop"""
    import requests
    
    assistant = VoiceAssistant()
    
    print("=" * 50)
    print("☀️  SUNDAY - Privacy-First Voice Assistant")
    print("=" * 50)
    print(f"Say '{assistant.wake_word}' to wake me up!")
    print("Say 'create a routine' to teach me new automations!")
    print("Press Ctrl+C to quit\n")
    
    # Check if Flask app is running
    flask_available = False
    try:
        r = requests.get('http://localhost:5000/', timeout=2)
        flask_available = True
        print("✓ Connected to Home Assistant backend\n")
    except requests.exceptions.ConnectionError:
        print("⚠️  Flask app not running - commands won't be processed")
        print("   Start it with: python app.py\n")
    
    try:
        while True:
            print("💤 Listening for 'Hey Sunday'...")
            
            wake_detected = await assistant.listen_for_wake_word(timeout_seconds=60)
            
            if not wake_detected:
                print("   (timeout, restarting listener)")
                continue
            
            print(f"✨ Wake word detected!")
            assistant.play_chime(success=True)
            
            greeting = "Hi! How can I help you?"
            assistant.speak_streaming(greeting)
            
            await asyncio.sleep(0.3)
            
            print("🎤 Listening for your command...")
            
            command_text = await assistant.listen_for_command(max_duration=7)
            
            if not command_text or not command_text.strip():
                response = "I didn't catch that. Just say 'Hey Sunday' when you need me!"
                assistant.speak_streaming(response)
                print()
                continue
            
            print(f"📢 You said: {command_text}")
            
            # Check if user wants to create a routine
            if assistant.is_routine_creation_request(command_text):
                print("🛠️  Routine creation mode!")
                
                # Check if the description is already in the command
                # e.g., "create a routine when I say party mode turn on all lights"
                has_inline_description = any(
                    kw in command_text.lower() 
                    for kw in ["when i say", "whenever i say", "called"]
                )
                
                if has_inline_description:
                    description = command_text
                else:
                    assistant.speak_streaming("Sure! Describe your routine. For example, say: when I say party mode, turn on the living room and kitchen lights.")
                    await asyncio.sleep(0.3)
                    
                    print("🎤 Listening for routine description...")
                    description = await assistant.listen_for_command(max_duration=10)
                    
                    if not description or not description.strip():
                        assistant.speak_streaming("I didn't catch that. Let's try again later.")
                        print()
                        continue
                    
                    print(f"📝 Routine description: {description}")
                
                # Send to Flask for Gemini-powered creation
                if flask_available:
                    try:
                        resp = requests.post(
                            'http://localhost:5000/create_routine',
                            json={'text': description},
                            timeout=15
                        )
                        
                        if resp.status_code == 200:
                            result = resp.json()
                            command_result = {
                                'routine_created': True,
                                'routine': result.get('routine', {}),
                                'message': result.get('message', '')
                            }
                            response_text = assistant.generate_response(command_text, command_result)
                            print(f"✅ {result.get('message')}")
                        elif resp.status_code == 409:
                            result = resp.json()
                            response_text = f"Oops, {result.get('error', 'that trigger conflicts with an existing routine.')}. Try a different trigger phrase."
                        else:
                            result = resp.json()
                            response_text = result.get('error', "I couldn't create that routine. Could you try describing it differently?")
                    except Exception as e:
                        print(f"Error creating routine: {e}")
                        response_text = "I had trouble creating that routine. Please try again."
                else:
                    response_text = "I need the backend running to create routines. Start it with python app.py."
                
                assistant.speak_streaming(response_text)
                print()
                continue
            
            # Normal command flow
            command_result = None
            if flask_available:
                try:
                    resp = requests.post(
                        'http://localhost:5000/process_command',
                        json={'text': command_text},
                        timeout=10
                    )
                    
                    if resp.status_code == 200:
                        command_result = resp.json()
                        print(f"🔒 Route: {command_result.get('route', 'UNKNOWN')}")
                    
                except requests.exceptions.ConnectionError:
                    print("⚠️ Could not connect to Flask backend")
                except Exception as e:
                    print(f"Error: {e}")
            
            response_text = assistant.generate_response(command_text, command_result)
            assistant.speak_streaming(response_text)
            
            print()
            
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye! Have a great day!")


if __name__ == '__main__':
    asyncio.run(main_async())