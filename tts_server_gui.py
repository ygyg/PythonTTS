from http.server import HTTPServer, BaseHTTPRequestHandler
import socket
import pyttsx3
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime
import os
import queue
import threading
from threading import Event, Lock
from queue import Queue, Empty
from typing import Any, Optional 
import select
import socket
import ipaddress
import string


"""
How to Run:

Install dependencies:

    bash
    pip install pyttsx3 tkinter

Run the script:

    bash
    python tts_server.py

Send messages:

    - Via TCP: Connect to localhost:5000 and send a text message.
    - Via HTTP: Send a POST request to http://localhost:5001/tts with the message in the body.
    
"""

class SafeQueue(Queue):
    """Thread-safe queue with additional safety measures"""
    def __init__(self):
        super().__init__()
        self._lock = Lock()
        
    def safe_put(self, item: Any) -> None:
        """Thread-safe put operation"""
        with self._lock:
            self.put(item)
            
    def safe_get(self) -> Optional[Any]:
        """Thread-safe get operation"""
        with self._lock:
            try:
                return self.get_nowait()
            except Empty:
                return None
                
    def safe_qsize(self) -> int:
        """Thread-safe qsize operation"""
        with self._lock:
            return self.qsize()                

class TTSRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/tts':
            content_length = int(self.headers['Content-Length'])
            message = self.rfile.read(content_length).decode('utf-8')
            
            # Clean the message before processing
            cleaned_message = self.server.gui.clean_text(message)
            
            if cleaned_message:  # Only process non-empty messages
                # Log the message and add to queue
                self.server.gui.add_message_log(cleaned_message)
                self.server.gui.update_queue_status()
                self.server.message_queue.safe_put((cleaned_message, None))
                
                # Send response
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Message received")
            else:
                # Send error response for empty or invalid messages
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Empty or invalid message")
                
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Override to prevent console logging
        pass

class TTSServerGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("TTS Server Log")
        self.root.geometry("600x500")
        
        # Initialize message queue
        self.message_queue = SafeQueue()
        
        # Event to signal thread termination
        self.shutdown_event = Event()        
        
        # Setup logging directory
        self.log_directory = "server_logs"
        if not os.path.exists(self.log_directory):
            os.makedirs(self.log_directory)
            
        # Create separate logs for system events and messages
        self.system_log_file = os.path.join(
            self.log_directory, 
            f"server_system_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        self.message_log_file = os.path.join(
            self.log_directory, 
            f"server_messages_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        
        # Debug: Print log file paths
        print(f"System log file: {self.system_log_file}")
        print(f"Message log file: {self.message_log_file}")
        
        # Define the port as a class attribute
        self.tcp_port = 5000  # Default TCP port   
        self.http_port = 5001
        
        # Define allowed private IP ranges
        self.allowed_networks = [
            ipaddress.ip_network('127.0.0.0/8'),      # Localhost
            ipaddress.ip_network('192.168.0.0/16'),   # 192.168.x.x
            ipaddress.ip_network('10.0.0.0/8'),       # 10.x.x.x
            ipaddress.ip_network('172.16.0.0/12')     # 172.16.x.x to 172.31.x.x
        ]
        
        # Setup GUI
        self.setup_gui()  # <-- Now system_log_file and message_log_file are initialized before this call
        
        # Initialize TTS engine with voice settings
        self.setup_tts_engine()        
        
        # Initialize server attributes
        self.tcp_socket = None
        self.httpd = None
        
        # Start servers and message processor
        self.start_message_processor()
        self.start_servers()
        
        # Ensure graceful shutdown
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)        

    def clean_text(self, text: str) -> str:
        """
        Remove unreadable or unwanted characters from the text.
        Only allow printable ASCII characters and basic punctuation.
        """
        # Define allowed characters (printable ASCII + basic punctuation)
        allowed_chars = set(string.printable)  # Includes letters, digits, punctuation, and whitespace
        allowed_chars -= set("\x0b\x0c")  # Remove vertical tab and form feed (non-printable)
        
        # Filter out unwanted characters
        cleaned_text = "".join(char for char in text if char in allowed_chars)
        
        # Remove leading/trailing whitespace
        cleaned_text = cleaned_text.strip()
        
        return cleaned_text


    def setup_tts_engine_good(self):
        self.engine = pyttsx3.init()
        voices = self.engine.getProperty('voices')
        if voices:
            self.engine.setProperty('voice', voices[0].id)  # Use first voice
        self.engine.setProperty('rate', 150)  # Set speech rate
        
    def setup_tts_engine(self):
        try:
            self.engine = pyttsx3.init()
            voices = self.engine.getProperty('voices')
            
            # Debug: Print all available voices
            print("Available Voices:")
            for i, voice in enumerate(voices):
                print(f"Voice {i}: Name='{voice.name}', ID='{voice.id}'")
            
            # Find the first female voice (preferably named "Zira")
            female_voice = None
            for voice in voices:
                # Check if the voice is female (gender is usually indicated in the voice ID or name)
                if "female" in voice.name.lower() or "zira" in voice.name.lower() or "woman" in voice.name.lower():
                    female_voice = voice
                    break
            
            # If no female voice is found, fall back to the first available voice
            if female_voice:
                self.engine.setProperty('voice', female_voice.id)
                self.add_system_log(f"Selected voice: {female_voice.name}")
            else:
                self.engine.setProperty('voice', voices[0].id)  # Use the first voice as a fallback
                self.add_system_log(f"No female voice found. Using default voice: {voices[0].name}")
            
            # Set speech rate (optional)
            self.engine.setProperty('rate', 150)  # Adjust the speech rate as needed
        except Exception as e:
            self.add_system_log(f"Error initializing TTS engine: {e}")
            raise  # Re-raise the exception to stop the server if TTS initialization fails        
        

    def setup_gui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Server status (single line)
        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # TCP Status
        self.tcp_status_var = tk.StringVar(value="TCP:{self.tcp_port} Starting")
        ttk.Label(status_frame, textvariable=self.tcp_status_var).grid(row=0, column=0, padx=(0,10))
        
        # HTTP Status
        self.http_status_var = tk.StringVar(value="HTTP:{self.http_port} Starting")
        ttk.Label(status_frame, textvariable=self.http_status_var).grid(row=0, column=1)
        
        # Queue status
        self.queue_status = ttk.Label(main_frame, text="Messages in queue: 0")
        self.queue_status.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 5))
        
        # Log display
        ttk.Label(main_frame, text="Log:").grid(row=2, column=0, sticky=tk.W)
        self.log_text = scrolledtext.ScrolledText(main_frame, height=15)
        self.log_text.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(3, weight=1)

    def add_system_log(self, message):
        now = datetime.now()
        screen_time = now.strftime("%H:%M:%S")
        file_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        
        # Display time only on screen
        screen_entry = f"[{screen_time}] {message}\n"
        self.log_text.insert(tk.END, screen_entry)
        self.log_text.see(tk.END)
        
        # Write full timestamp to file
        try:
            file_entry = f"[{file_timestamp}] {message}\n"
            with open(self.system_log_file, 'a', encoding='utf-8') as f:
                f.write(file_entry)
        except Exception as e:
            error_time = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{error_time}] Error writing to system log file: {e}\n")
            self.log_text.see(tk.END)

    def add_message_log(self, message):
        now = datetime.now()
        screen_time = now.strftime("%H:%M:%S")
        file_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        
        # Display time only on screen
        screen_entry = f"[{screen_time}] {message}\n"
        self.log_text.insert(tk.END, screen_entry)
        self.log_text.see(tk.END)
        
        # Write full timestamp to file
        try:
            file_entry = f"[{file_timestamp}] {message}\n"
            with open(self.message_log_file, 'a', encoding='utf-8') as f:
                f.write(file_entry)
        except Exception as e:
            self.add_system_log(f"Error writing to message log file: {e}")

    def update_status(self, server_type, status):
        status_var = self.tcp_status_var if server_type == "TCP" else self.http_status_var
        port = self.tcp_port if server_type == "TCP" else self.http_port
        status_var.set(f"{server_type}:{port} {status}")
        self.add_system_log(f"{server_type} Server {status}")

    def start_message_processor(self):
        def process_messages():
            while not self.shutdown_event.is_set():  # Check for shutdown signal
                try:
                    message = self.message_queue.safe_get()
                    if message:
                        self.engine.say(message[0])
                        self.engine.runAndWait()
                        self.update_queue_status()  
                except Exception as e:
                    self.add_system_log(f"Error processing message: {e}")
                    
        self.processor_thread = threading.Thread(target=process_messages, daemon=True, name="MessageProcessor")
        self.processor_thread.start()
        
    def stop_message_processor(self):
        self.shutdown_event.set()  # Signal the thread to stop
        if self.processor_thread:
            self.processor_thread.join(timeout=1)  # Wait for the thread to finish
            self.add_system_log("Message processor stopped.")        

    def update_queue_status(self):
        def update():
            queue_size = self.message_queue.safe_qsize()
            self.queue_status.config(text=f"Messages in queue: {queue_size}")
        # Ensure we update on the main thread
        self.root.after(0, update)

    def is_ip_allowed(self, ip: str) -> bool:
        """Check if the IP address is in the allowed private networks."""
        try:
            ip_addr = ipaddress.ip_address(ip)
            for network in self.allowed_networks:
                if ip_addr in network:
                    return True
            return False
        except ValueError:
            # Invalid IP address
            return False

    def handle_tcp_client(self, client_socket, addr):
        client_ip = addr[0]  # Get the client's IP address
        if not self.is_ip_allowed(client_ip):  # Check if IP is allowed
            self.add_system_log(f"Rejected connection from unauthorized IP: {client_ip}")
            client_socket.close()
            return       
        
        try:
            data = b""
            while True:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break
                data += chunk
                
                try:
                    message = data.decode('utf-8')
                    
                    # Clean the message before processing
                    cleaned_message = self.clean_text(message)                    
                    if cleaned_message:  # Only process non-empty messages
                        self.add_message_log(cleaned_message)
                        self.message_queue.safe_put((cleaned_message, None))
                        self.update_queue_status()
                    else:
                        self.add_system_log(f"Received empty or invalid message from {client_ip}")

                    data = b""
                except UnicodeDecodeError:
                    continue
        except Exception as e:
            self.add_system_log(f"Error handling client connection: {e}")
        finally:
            client_socket.close()

    def is_port_in_use(self, port: int) -> bool:  # Add self as the first parameter
        """Check if a port is already in use."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('0.0.0.0', port))
                return False  # Port is available
            except socket.error as e:
                if e.errno == 10048:  # Port already in use
                    return True
                raise  # Re-raise other errors

    def start_tcp_server(self):
        if self.is_port_in_use(self.tcp_port):  # Check if port is in use
            self.update_status("TCP", "Port {self.tcp_port} in use, not running")  
            self.add_system_log(f"TCP port {self.tcp_port} is already in use. TCP server will not start.")
            return  # Do not start the server        
        
        def run_tcp_server():
            try:
                self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Allow reuse
                self.tcp_socket.bind(('0.0.0.0', self.tcp_port))
                self.tcp_socket.listen(5)
                self.update_status("TCP", f"Ready on port {self.tcp_port}")

                # Use select to wait for incoming connections
                while not self.shutdown_event.is_set():
                    # Use select to wait for readable sockets (incoming connections)
                    readable, _, _ = select.select([self.tcp_socket], [], [], 1.0)
                    
                    if readable:
                        # Accept the incoming connection
                        client_socket, addr = self.tcp_socket.accept()
                        self.add_system_log(f"Accepted connection from {addr}")
                        
                        # Handle the client in a separate thread
                        client_thread = threading.Thread(
                            target=self.handle_tcp_client, 
                            args=(client_socket, addr)
                        )
                        client_thread.daemon = True
                        client_thread.start()
                    
                    # Check for shutdown signal
                    if self.shutdown_event.is_set():
                        break

            except Exception as e:
                self.add_system_log(f"TCP Server error: {e}")
            finally:
                if self.tcp_socket:
                    self.tcp_socket.close()
                    self.add_system_log("TCP Server socket closed.")

        self.tcp_thread = threading.Thread(target=run_tcp_server, daemon=True)
        self.tcp_thread.start()

    def start_http_server(self):
        if self.is_port_in_use(self.http_port):  # Check if the HTTP port is in use
            self.update_status("HTTP", "Port {self.http_port} in use, not running")  
            self.add_system_log(f"HTTP port {self.http_port} is already in use. HTTP server will not start.")
            return  # Skip starting the HTTP server    
        
        def run_http_server():
            try:
                server_address = ('', self.http_port)
                self.httpd = HTTPServer(server_address, TTSRequestHandler)
                self.httpd.message_queue = self.message_queue
                self.httpd.gui = self
                self.update_status("HTTP", f"Ready on port {self.http_port}")
                self.httpd.serve_forever()
            except Exception as e:
                self.add_system_log(f"HTTP Server error: {e}")

        self.http_thread = threading.Thread(target=run_http_server, daemon=True)
        self.http_thread.start()

    def start_servers(self):
        self.start_tcp_server()
        self.start_http_server()

    def on_close(self):
        # Stop the HTTP server if it is running
        if hasattr(self, 'httpd') and self.httpd:
            self.add_system_log("Shutting down HTTP server...")
            
            # Set a timeout for shutdown (e.g., 5 seconds)
            shutdown_timeout = 5  # Timeout in seconds
            
            # Start the shutdown process
            self.httpd.shutdown()
            
            # Wait for the HTTP server thread to finish
            if hasattr(self, 'http_thread') and self.http_thread:
                self.http_thread.join(timeout=shutdown_timeout)
                
                if self.http_thread.is_alive():
                    # If the thread is still alive after the timeout, forcefully terminate it
                    self.add_system_log("HTTP server did not shut down gracefully. Forcefully terminating...")
                    self.httpd.server_close()  # Close the server socket
                    self.http_thread.join(timeout=1)  # Give it one more second to terminate
                    
                    if self.http_thread.is_alive():
                        self.add_system_log("HTTP server thread could not be terminated.")
                else:
                    self.add_system_log("HTTP server shut down gracefully.")
            else:
                self.add_system_log("HTTP server thread not found.")
        else:
            self.add_system_log("HTTP server is not running.")
            
        # Stop the message processor
        self.stop_message_processor()
        
        # Close TCP server socket
        if hasattr(self, 'tcp_socket') and self.tcp_socket:
            self.tcp_socket.close()
            self.add_system_log("TCP Server socket closed.")
        
        # Close the Tkinter application
        self.root.destroy()
        
    def start(self):
        self.root.mainloop()

def main():
    server = TTSServerGUI()
    server.start()

if __name__ == "__main__":
    main()