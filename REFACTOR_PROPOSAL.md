# Unified Connection Architecture Proposal

## Current Problems
- Duplicate code for repeaters vs outbound connections
- Multiple stream event emission patterns
- Separate timeout checking methods
- Hard to add new connection types
- Performance impact from code duplication

## Proposed Solution: Connection Interface Pattern

### 1. Abstract Connection Interface
```python
from abc import ABC, abstractmethod

class ConnectionInterface(ABC):
    """Abstract interface for all connection types"""
    
    @abstractmethod
    def get_connection_type(self) -> str:
        """Return 'repeater', 'outbound', or future type"""
        pass
        
    @abstractmethod 
    def get_connection_name(self) -> str:
        """Return connection identifier for events"""
        pass
        
    @abstractmethod
    def get_slot_stream(self, slot: int) -> StreamState:
        """Get current stream on slot"""
        pass
        
    @abstractmethod
    def set_slot_stream(self, slot: int, stream: StreamState):
        """Set stream on slot"""
        pass
        
    @abstractmethod
    def get_radio_id(self) -> bytes:
        """Get connection's radio ID"""
        pass
        
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connection is active"""
        pass
```

### 2. Concrete Implementations
```python
class RepeaterConnection(ConnectionInterface):
    def __init__(self, repeater_state: RepeaterState):
        self._repeater = repeater_state
        
    def get_connection_type(self) -> str:
        return 'repeater'
        
    def get_connection_name(self) -> str:
        return str(self._repeater.radio_id)
        
    # ... implement other methods

class OutboundConnection(ConnectionInterface):
    def __init__(self, outbound_state: OutboundState):
        self._outbound = outbound_state
        
    def get_connection_type(self) -> str:
        return 'outbound'
        
    def get_connection_name(self) -> str:
        return self._outbound.config.name
        
    # ... implement other methods
```

### 3. Unified Stream Management
```python
class HBLinkProtocol:
    def _emit_stream_start(self, connection: ConnectionInterface, slot: int, 
                          src_id: int, dst_id: int, stream_id: str, 
                          call_type: str, is_assumed: bool = False):
        """Unified stream_start emission for all connection types"""
        
        event_data = {
            'connection_type': connection.get_connection_type(),
            'slot': slot,
            'src_id': src_id,
            'dst_id': dst_id,
            'stream_id': stream_id,
            'call_type': call_type,
            'assumed': is_assumed
        }
        
        # Add connection-specific identifier
        if connection.get_connection_type() == 'repeater':
            event_data['repeater_id'] = connection.get_connection_name()
        else:
            event_data['connection_name'] = connection.get_connection_name()
            
        self._events.emit('stream_start', event_data)
    
    def _emit_stream_end(self, connection: ConnectionInterface, slot: int,
                        stream: StreamState, end_reason: str):
        """Unified stream_end emission for all connection types"""
        
        duration = time() - stream.start_time
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
        
        event_data = {
            'connection_type': connection.get_connection_type(),
            'slot': slot,
            'src_id': int.from_bytes(stream.rf_src, 'big'),
            'dst_id': int.from_bytes(stream.dst_id, 'big'), 
            'stream_id': stream.stream_id.hex(),
            'duration': round(duration, 2),
            'packet_count': stream.packet_count,
            'end_reason': end_reason,
            'hang_time': hang_time,
            'call_type': stream.call_type,
            'assumed': stream.is_assumed
        }
        
        # Add connection-specific identifier
        if connection.get_connection_type() == 'repeater':
            event_data['repeater_id'] = connection.get_connection_name()
        else:
            event_data['connection_name'] = connection.get_connection_name()
            
        self._events.emit('stream_end', event_data)
    
    def _update_stream(self, connection: ConnectionInterface, slot: int,
                      rf_src: bytes, dst_id: bytes, stream_id: bytes,
                      is_terminator: bool, is_assumed: bool = False):
        """Unified stream update for all connection types"""
        
        current_stream = connection.get_slot_stream(slot)
        current_time = time()
        
        if not current_stream or current_stream.stream_id != stream_id:
            # New stream
            new_stream = StreamState(
                repeater_id=connection.get_radio_id(),
                rf_src=rf_src,
                dst_id=dst_id,
                slot=slot,
                start_time=current_time,
                last_seen=current_time,
                stream_id=stream_id,
                packet_count=1,
                call_type="private" if (dst_id[2] & 0x40) else "group",
                is_assumed=is_assumed
            )
            connection.set_slot_stream(slot, new_stream)
            
            # Emit unified stream_start
            self._emit_stream_start(
                connection, slot,
                int.from_bytes(rf_src, 'big'),
                int.from_bytes(dst_id, 'big'),
                stream_id.hex(),
                new_stream.call_type,
                is_assumed
            )
            
        else:
            # Update existing stream
            current_stream.last_seen = current_time
            current_stream.packet_count += 1
            
        # Handle terminator
        if is_terminator and current_stream:
            self._end_stream_unified(connection, slot, current_stream, 'terminator')
    
    def _end_stream_unified(self, connection: ConnectionInterface, slot: int,
                           stream: StreamState, end_reason: str):
        """Unified stream ending for all connection types"""
        
        if stream.ended:
            return
            
        # Mark stream as ended
        stream.ended = True
        stream.end_time = time()
        
        # Emit unified stream_end
        self._emit_stream_end(connection, slot, stream, end_reason)
        
        # Update counters
        if stream.is_assumed:
            self._active_calls -= 1
    
    def _check_connection_timeouts(self, connection: ConnectionInterface):
        """Unified timeout checking for all connection types"""
        
        if not connection.is_connected():
            return
            
        current_time = time()
        stream_timeout = CONFIG.get('global', {}).get('stream_timeout', 360.0)
        hang_time = CONFIG.get('global', {}).get('stream_hang_time', 10.0)
        
        for slot in [1, 2]:
            stream = connection.get_slot_stream(slot)
            if stream:
                self._check_stream_timeout_unified(
                    connection, slot, stream, current_time, 
                    stream_timeout, hang_time
                )
```

## Benefits

### 1. **Maintainability**
- Single source of truth for stream logic
- Easy to add new connection types
- Consistent behavior across all connection types

### 2. **Performance** 
- Eliminate code duplication
- Reduce method calls
- Single event emission path

### 3. **Extensibility**
- New connection types just implement the interface
- Automatic dashboard support
- Consistent API

### 4. **Testing**
- Mock the interface for unit tests
- Test all connection types with same test suite
- Easier to verify consistency

## Migration Strategy

### Phase 1: Create Interface & Wrappers
- Define ConnectionInterface
- Create RepeaterConnection and OutboundConnection wrappers
- Keep existing methods working

### Phase 2: Unified Methods
- Implement _emit_stream_start/_emit_stream_end
- Add _update_stream_unified
- Add _check_connection_timeouts

### Phase 3: Migration
- Replace existing calls one by one
- Remove duplicate methods
- Update tests

### Phase 4: New Connection Type
- Just implement ConnectionInterface
- Automatically gets all stream functionality

## Example: Adding New Connection Type
```python
class PeerConnection(ConnectionInterface):
    def get_connection_type(self) -> str:
        return 'peer'
        
    def get_connection_name(self) -> str:
        return self._peer_config.name
        
    # ... implement interface
```

That's it! No need to duplicate stream handling logic.