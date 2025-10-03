#!/usr/bin/env python3
"""
User Routing Cache for HBlink4

Tracks DMR users (radio IDs) and their last heard repeater for:
1. Dashboard "Last Heard" display
2. Private call routing optimization (avoids flooding all repeaters)

The cache is time-limited to prevent unbounded memory growth.
Default timeout: 10 minutes (configurable)

Copyright (C) 2025 Cort Buffington, N0MJS
License: GNU GPLv3
"""

import logging
from time import time
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field

LOGGER = logging.getLogger(__name__)


@dataclass
class UserEntry:
    """Cache entry for a heard user"""
    radio_id: int
    repeater_id: int
    callsign: str
    slot: int
    talkgroup: int
    last_heard: float = field(default_factory=time)
    talker_alias: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        return {
            'radio_id': self.radio_id,
            'repeater_id': self.repeater_id,
            'callsign': self.callsign,
            'slot': self.slot,
            'talkgroup': self.talkgroup,
            'last_heard': self.last_heard,
            'talker_alias': self.talker_alias
        }


class UserCache:
    """
    Routing cache for tracking users and their last heard repeater.
    
    This cache serves two purposes:
    1. Provides data for dashboard "Last Heard" display
    2. Enables efficient private call routing without flooding all repeaters
    
    Memory management: Entries expire after configurable timeout (default 10 minutes)
    to prevent unbounded growth.
    """
    
    def __init__(self, timeout_seconds: int = 600):
        """
        Initialize user cache.
        
        Args:
            timeout_seconds: How long to keep entries (default 600 = 10 minutes)
        """
        self._cache: Dict[int, UserEntry] = {}
        self._timeout = timeout_seconds
        LOGGER.info(f'User cache initialized with {timeout_seconds}s timeout')
    
    def update(self, radio_id: int, repeater_id: int, callsign: str, 
               slot: int, talkgroup: int, talker_alias: Optional[str] = None) -> None:
        """
        Update cache with user activity.
        
        Args:
            radio_id: Source radio ID (user)
            repeater_id: Repeater the user was heard on
            callsign: User's callsign
            slot: Timeslot (1 or 2)
            talkgroup: Talkgroup/destination ID
            talker_alias: Optional decoded talker alias
        """
        now = time()
        
        # Update or create entry
        if radio_id in self._cache:
            entry = self._cache[radio_id]
            entry.repeater_id = repeater_id
            entry.callsign = callsign
            entry.slot = slot
            entry.talkgroup = talkgroup
            entry.last_heard = now
            if talker_alias:
                entry.talker_alias = talker_alias
            LOGGER.debug(f'Updated cache: user {radio_id} ({callsign}) on repeater {repeater_id} slot {slot} TG {talkgroup}')
        else:
            self._cache[radio_id] = UserEntry(
                radio_id=radio_id,
                repeater_id=repeater_id,
                callsign=callsign,
                slot=slot,
                talkgroup=talkgroup,
                last_heard=now,
                talker_alias=talker_alias
            )
            LOGGER.debug(f'Added to cache: user {radio_id} ({callsign}) on repeater {repeater_id} slot {slot} TG {talkgroup}')
    
    def lookup(self, radio_id: int) -> Optional[UserEntry]:
        """
        Look up a user in the cache.
        
        Args:
            radio_id: Radio ID to look up
            
        Returns:
            UserEntry if found and not expired, None otherwise
        """
        if radio_id not in self._cache:
            return None
        
        entry = self._cache[radio_id]
        
        # Check if expired
        if time() - entry.last_heard > self._timeout:
            del self._cache[radio_id]
            LOGGER.debug(f'Removed expired entry for user {radio_id}')
            return None
        
        return entry
    
    def get_repeater_for_user(self, radio_id: int) -> Optional[int]:
        """
        Get the repeater ID where a user was last heard.
        
        This is the primary function for private call routing optimization.
        
        Args:
            radio_id: Target radio ID for private call
            
        Returns:
            Repeater ID if user found and not expired, None otherwise
        """
        entry = self.lookup(radio_id)
        return entry.repeater_id if entry else None
    
    def cleanup(self) -> int:
        """
        Remove expired entries from cache.
        
        This should be called periodically (e.g., once per minute) to prevent
        unbounded memory growth.
        
        Returns:
            Number of entries removed
        """
        now = time()
        expired = []
        
        for radio_id, entry in self._cache.items():
            if now - entry.last_heard > self._timeout:
                expired.append(radio_id)
        
        for radio_id in expired:
            del self._cache[radio_id]
        
        if expired:
            LOGGER.info(f'Cleaned up {len(expired)} expired user cache entries')
        
        return len(expired)
    
    def get_last_heard(self, limit: int = 50) -> List[dict]:
        """
        Get list of recently heard users, sorted by most recent first.
        
        Args:
            limit: Maximum number of entries to return
            
        Returns:
            List of user entries as dictionaries, sorted by last_heard descending
        """
        # Filter out expired entries
        now = time()
        valid_entries = [
            entry for entry in self._cache.values()
            if now - entry.last_heard <= self._timeout
        ]
        
        # Sort by last heard (most recent first)
        sorted_entries = sorted(valid_entries, key=lambda e: e.last_heard, reverse=True)
        
        # Limit results
        return [entry.to_dict() for entry in sorted_entries[:limit]]
    
    def get_stats(self) -> dict:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        now = time()
        valid = sum(1 for entry in self._cache.values() 
                   if now - entry.last_heard <= self._timeout)
        
        return {
            'total_entries': len(self._cache),
            'valid_entries': valid,
            'expired_entries': len(self._cache) - valid,
            'timeout_seconds': self._timeout
        }
    
    def clear(self) -> None:
        """Clear all entries from cache."""
        count = len(self._cache)
        self._cache.clear()
        LOGGER.info(f'Cleared {count} entries from user cache')
