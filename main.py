import sys
import time
import threading
from rich.console import Console

from nexus_ai.core.listener import NexusListener
from nexus_ai.core.speaker import NexusSpeaker
from nexus_ai.core.brain import NexusBrain
from nexus_ai.config import AGENT_NAME
from nexus_ai.ui.server import start_server
from nexus_ai.ui.state import state_manager

console = Console()


from nexus_ai.core.queues import COMMAND_QUEUE
import queue

def main():
    console.clear()
    console.print(f"[bold cyan]Initializing {AGENT_NAME}...[/bold cyan]")
    
    # Start UI Server in Background
    ui_thread = threading.Thread(target=start_server, daemon=True)
    ui_thread.start()
    console.print(f"[bold green]UI Server started at http://localhost:8000[/bold green]")
    
    # Initialize Core
    try:
        ear = NexusListener()
        mouth = NexusSpeaker()
        brain = NexusBrain()
    except Exception as e:
        console.print(f"[bold red]Critical Initialization Error:[/bold red] {e}")
        return

    state_manager.set_state("IDLE")
    console.print(f"[bold green]{AGENT_NAME} is Online and Listening...[/bold green]")
    console.print("[dim]Say 'Start Nexus' to wake up.[/dim]")

    # Synchronization Event to prevent listener from running while processing
    processing_lock = threading.Event()
    
    # --- VOICE LISTENER THREAD ---
    def voice_listener_loop():
        """Continuously listens and pushes voice commands to queue."""
        while True:
            try:
                # SPEECH LOCK 1: processing_lock
                # If the system is processing a command, we wait.
                # This prevents picking up the system's own response if there's latency.
                if processing_lock.is_set():
                    time.sleep(0.1)
                    continue

                # SPEECH LOCK 2: mouth.is_speaking
                if mouth.is_speaking():
                    time.sleep(0.1)
                    continue

                cmd = ear.listen() 
                if cmd:
                    # BLOCK LISTENER IMMEDIATELY
                    # We assume this command will be processed. 
                    # The main loop will clear this lock when done.
                    processing_lock.set()
                    
                    COMMAND_QUEUE.put({"text": cmd, "source": "voice"})
            except Exception as e:
                print(f"Listener Error: {e}")
                time.sleep(1)


    listener_thread = threading.Thread(target=voice_listener_loop, daemon=True)
    listener_thread.start()

    # Main Loop
    running = True
    
    while running:
        try:
            # 1. Check Queue (Blocking with timeout to check for exit signals or keep alive)
            try:
                command_data = COMMAND_QUEUE.get(timeout=0.1)
            except queue.Empty:
                continue

            command = command_data["text"]
            source = command_data["source"] # "voice" or "text"
            
            # If text command came in, we should ensuring processing_lock is set if it wasn't already?
            # Voice listener sets it. Text source doesn't. 
            # If Text command runs, we want Voice Listener to PAUSE too (so it doesn't pick up TTS if Text command triggers TTS).
            if source == "text":
                processing_lock.set()

            # 2. Think & Act
            if command:
                # Send Transcript to UI
                state_manager.send_transcript(f"[{source.upper()}] {command}")
                
                state_manager.set_state("THINKING")
                response, action, should_exit = brain.process_input(command)
                
                # 3. Determine if we should speak
                # Default: Speak if source is voice.
                # If source is text, Be Silent, UNLESS override phrase used.
                should_speak = (source == "voice")
                
                if source == "text":
                   # Check for override phrases
                   override_phrases = ["speak", "say", "voice", "reply with voice"]
                   if any(phrase in command.lower() for phrase in override_phrases):
                       should_speak = True

                # 4. Speak & Log
                if response:
                    # Send Response to UI
                    state_manager.send_response(response)
                    
                    console.print(f"[bold magenta]Nexus ({source}):[/bold magenta] {response}")
                    
                    if should_speak:
                        console.print(f"[dim](Debug: Sending to TTS engine...)[/dim]")
                        state_manager.set_state("SPEAKING")
                        mouth.speak(response)
                        
                        # BLOCKING WAIT: Ensure speech finishes
                        while mouth.is_speaking():
                            time.sleep(0.1)
                    else:
                        console.print(f"[dim](Silent Mode: Output to UI only)[/dim]")
                    
                    state_manager.set_state("IDLE")

                    # --- MEMORY: SAVE INTERACTION ---
                    # We save the interaction here, after responsiveness
                    brain.remember_interaction(command, response)
                
                # 3b. Execute Action (Voice Confirmation Protocol)
                if action:
                    time.sleep(1) # Small buffer 
                    console.print(f"[dim]Executing action...[/dim]")
                    action_response = action()
                    
                    # New: If action returns a string, it's a post-action feedback/error message
                    if action_response and isinstance(action_response, str) and action_response.strip() != (response or "").strip():
                        console.print(f"[bold magenta]Nexus (Action Feedback):[/bold magenta] {action_response}")
                        
                        # Apply same speaking logic to action feedback?
                        # Usually yes. If I typed "open spotify", I don't want it to say "Opening Spotify".
                        
                        if should_speak:
                            mouth.speak(action_response)
                             # BLOCKING WAIT
                            while mouth.is_speaking():
                                time.sleep(0.1)
                        else:
                             # Send to UI as a secondary response or log?
                             # state_manager.send_response(action_response)
                             pass
                             
            # RELEASE LOCK after processing is fully done (including speaking)
            processing_lock.clear()

            # 4. Handle Exit
            if 'should_exit' in locals() and should_exit:
                console.print("[yellow]Shutting down...[/yellow]")
                state_manager.set_state("IDLE")
                running = False
                
        except KeyboardInterrupt:
            console.print("\n[yellow]Force Exit.[/yellow]")
            running = False
        except Exception as e:
            console.print(f"[red]Runtime Error:[/red] {e}")
            state_manager.set_state("IDLE")

if __name__ == "__main__":
    main()
