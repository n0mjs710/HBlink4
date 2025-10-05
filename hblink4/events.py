"""
Event emitter for dashboard with transport abstraction
Supports TCP (remote) and Unix socket (local)

Performance: TCP ~5-15μs, Unix socket ~0.5-1μs
Dashboard connection state tracked for both transports
"""
import socket
import json
import logging
import os
import ipaddress
from time import time
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class EventEmitter:
    """
    Event emitter with pluggable transport layer.
    Supports TCP (remote) and Unix socket (local).
    """
    
    def __init__(self, enabled: bool = True, transport: str = 'unix', 
                 host_ipv4: str = '127.0.0.1', host_ipv6: str = '::1',
                 port: int = 8765,
                 unix_socket: str = '/tmp/hblink4.sock',
                 disable_ipv6: bool = False,
                 buffer_size: int = 65536):
        """
        Initialize event emitter with transport abstraction
        
        Args:
            enabled: Whether to emit events (False = zero overhead)
            transport: 'tcp' or 'unix'
            host_ipv4: Dashboard host IPv4 address (for TCP)
            host_ipv6: Dashboard host IPv6 address (for TCP)
            port: Dashboard port (for TCP)
            unix_socket: Unix socket path (for Unix transport)
            disable_ipv6: Disable IPv6 (for networks with broken IPv6)
            buffer_size: Socket send buffer size
        """
        self.enabled = enabled
        self.transport = transport.lower()
        self.host_ipv4 = host_ipv4
        self.host_ipv6 = host_ipv6 if not disable_ipv6 else None
        self.port = port
        self.unix_socket = unix_socket
        self.disable_ipv6 = disable_ipv6
        self.buffer_size = buffer_size
        self.sock = None
        self.connected = False
        self.last_connect_attempt = 0
        self.connect_retry_interval = 10.0  # Retry every 10 seconds
        self.using_ipv6 = False  # Track which protocol connected
        
        if disable_ipv6 and transport == 'tcp':
            logger.warning('⚠️  IPv6 disabled for dashboard connection - using IPv4 only')
        
        if not enabled:
            return
        
        # Initialize transport
        if self.transport == 'tcp':
            self._init_tcp()
        elif self.transport == 'unix':
            self._init_unix()
        else:
            logger.error(f"Unknown transport: {transport} (valid options: 'tcp', 'unix'), dashboard disabled")
            self.enabled = False
    
    def _init_tcp(self):
        """Initialize TCP socket (connection-oriented) - tries IPv6 first, falls back to IPv4"""
        # Try IPv6 first if configured
        if self.host_ipv6:
            try:
                self.sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                self.sock.setblocking(False)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.buffer_size)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                self.using_ipv6 = True
                self._try_connect()
                logger.info(f"📡 TCP event emitter initialized for [{self.host_ipv6}]:{self.port} (IPv6)")
                return
            except Exception as e:
                logger.warning(f"IPv6 connection failed: {e}, trying IPv4...")
                self.sock = None
                self.using_ipv6 = False
        
        # Fall back to IPv4
        if self.host_ipv4:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.setblocking(False)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.buffer_size)
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                self.using_ipv6 = False
                self._try_connect()
                logger.info(f"📡 TCP event emitter initialized for {self.host_ipv4}:{self.port} (IPv4)")
                return
            except Exception as e:
                logger.error(f"Failed to initialize TCP socket (IPv4): {e}")
                self.enabled = False
        else:
            logger.error("No host configured for TCP transport")
            self.enabled = False
    
    def _init_unix(self):
        """Initialize Unix domain socket (connection-oriented, local only)"""
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.setblocking(False)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self.buffer_size)
            
            # Attempt connection (non-blocking)
            self._try_connect()
            
            logger.info(f"📡 Unix socket event emitter initialized at {self.unix_socket}")
        except Exception as e:
            logger.error(f"Failed to initialize Unix socket: {e}")
            self.enabled = False
    
    def _try_connect(self):
        """Attempt non-blocking connection (TCP/Unix only)"""
        now = time()
        if now - self.last_connect_attempt < self.connect_retry_interval:
            return  # Don't retry too often
        
        self.last_connect_attempt = now
        
        try:
            if self.transport == 'tcp':
                host = self.host_ipv6 if self.using_ipv6 else self.host_ipv4
                self.sock.connect((host, self.port))
            elif self.transport == 'unix':
                self.sock.connect(self.unix_socket)
            
            self.connected = True
            logger.info(f"✅ Connected to dashboard ({self.transport})")
        except BlockingIOError:
            # Connection in progress (non-blocking), will complete later
            pass
        except (ConnectionRefusedError, FileNotFoundError) as e:
            # Dashboard not running yet (expected)
            if not self.connected:  # Only log once
                logger.debug(f"Dashboard not available yet ({self.transport}): {e}")
            self.connected = False
        except Exception as e:
            logger.warning(f"Connection attempt failed ({self.transport}): {e}")
            self.connected = False
    
    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Send event to dashboard (non-blocking, never blocks HBlink)
        
        Args:
            event_type: Type of event (e.g., 'stream_start', 'repeater_connected')
            data: Event data dictionary
        """
        if not self.enabled:
            return
        
        try:
            message = json.dumps({
                'type': event_type,
                'timestamp': time(),
                'data': data
            }, separators=(',', ':'))  # Compact JSON
            
            message_bytes = message.encode('utf-8')
            
            # Send via stream transport (TCP or Unix socket)
            self._send_stream(message_bytes)
                
        except Exception as e:
            # Never raise - dashboard is optional
            logger.debug(f"Event emit failed: {e}")
    
    def _send_stream(self, data: bytes):
        """Send via TCP or Unix socket (connection-oriented)"""
        # Try to reconnect if disconnected
        was_disconnected = not self.connected
        if not self.connected:
            self._try_connect()
        
        if not self.connected:
            return  # Still not connected, drop event
        
        try:
            # Frame message with length prefix (4 bytes, big-endian)
            length = len(data)
            frame = length.to_bytes(4, byteorder='big') + data
            
            # Non-blocking send
            self.sock.sendall(frame)
            
            # If we just reconnected, trigger state sync callback
            if was_disconnected and hasattr(self, 'on_reconnect'):
                try:
                    self.on_reconnect()
                except Exception as e:
                    logger.debug(f"Reconnect callback failed: {e}")
            
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            # Connection lost
            logger.warning(f"Dashboard connection lost: {e}")
            self.connected = False
            self._close_socket()
            
            # Recreate socket for next connection attempt
            if self.transport == 'tcp':
                self._init_tcp()
            elif self.transport == 'unix':
                self._init_unix()
        except BlockingIOError:
            # Send buffer full, drop event (prevents blocking)
            logger.debug("Send buffer full, dropping event")
        except Exception as e:
            logger.debug(f"Send failed: {e}")
    
    def _close_socket(self):
        """Close socket safely"""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
    
    def close(self):
        """Close connection and cleanup"""
        if self.enabled:
            self._close_socket()
            self.connected = False
            logger.info(f"Event emitter closed ({self.transport})")
