"""
Access control and configuration matching module for HBlink4
"""

import re
from typing import Optional, Dict, Any, List, Tuple, Union, Literal
from dataclasses import dataclass, field

MatchType = Literal['specific_id', 'id_range', 'callsign']
PatternValue = Union[List[int], List[Tuple[int, int]], List[str]]

class InvalidPatternError(Exception):
    """Raised when a pattern configuration is invalid"""

def validate_pattern(match_type: Literal['specific_id', 'id_range', 'callsign'], pattern: Any) -> None:
    """Validate a pattern value against its declared type"""
    if not isinstance(pattern, list):
        raise InvalidPatternError(f"{match_type} pattern must be a list")

    if match_type == 'specific_id':
        if not all(isinstance(x, int) for x in pattern):
            raise InvalidPatternError("Specific ID patterns must contain only integers")
            
    elif match_type == 'id_range':
        for start, end in pattern:
            if not isinstance(start, int) or not isinstance(end, int):
                raise InvalidPatternError("Range bounds must be integers")
            if start > end:
                raise InvalidPatternError(f"Invalid range: start ({start}) > end ({end})")
            
    else:  # callsign
        if not all(isinstance(p, str) and re.match(r'^[A-Za-z0-9*]+$', p) for p in pattern):
            raise InvalidPatternError("Callsign patterns must contain only alphanumeric characters and *")

class BlacklistError(Exception):
    """Raised when a repeater matches a blacklist pattern"""
    def __init__(self, pattern_name: str, reason: str):
        self.pattern_name = pattern_name
        self.reason = reason
        super().__init__(f"Repeater blocked by {pattern_name}: {reason}")

@dataclass
class BlacklistMatch:
    """Represents a blacklist pattern"""
    name: str
    description: str
    match_type: Literal['specific_id', 'id_range', 'callsign']
    pattern: PatternValue
    reason: str

    def __post_init__(self):
        """Validate pattern matches the declared type"""
        validate_pattern(self.match_type, self.pattern)

@dataclass
class RepeaterConfig:
    """Configuration settings for a matched repeater"""
    passphrase: str
    slot1_talkgroups: List[int] = field(default_factory=list)
    slot2_talkgroups: List[int] = field(default_factory=list)

MatchType = Literal['specific_id', 'id_range', 'callsign']

@dataclass
class PatternMatch:
    """Represents a pattern matching rule for repeater configuration"""
    name: str
    config: RepeaterConfig
    match_type: Literal['specific_id', 'id_range', 'callsign']
    pattern: PatternValue

    def __post_init__(self):
        """Validate pattern matches the declared type"""
        validate_pattern(self.match_type, self.pattern)

def _extract_match_type(match_dict: Dict[str, Any]) -> Tuple[Literal['specific_id', 'id_range', 'callsign'], Any]:
    """Helper to extract match type and pattern from a match dictionary"""
    match_keys = [k for k in ['ids', 'id_ranges', 'callsigns'] if k in match_dict]
    if len(match_keys) != 1:
        raise InvalidPatternError(f"Must specify exactly one match type, found: {match_keys}")

    if 'ids' in match_dict:
        return 'specific_id', match_dict['ids']
    elif 'id_ranges' in match_dict:
        return 'id_range', [tuple(r) for r in match_dict['id_ranges']]
    else:  # callsigns
        return 'callsign', match_dict['callsigns']

class RepeaterMatcher:
    """
    Handles repeater identification and configuration matching
    """
    def __init__(self, config: Dict[str, Any]):
        self.blacklist = self._parse_blacklist(config.get('blacklist', {"patterns": []}))
        repeater_config = config.get('repeater_configurations', config.get('repeaters', {}))
        self.patterns = self._parse_patterns(repeater_config.get('patterns', []))
        self.default_config = RepeaterConfig(**repeater_config.get('default', {
            "passphrase": "passw0rd",
            "slot1_talkgroups": [8],
            "slot2_talkgroups": [8]
        }))

    def _parse_blacklist(self, blacklist_config: Dict[str, Any]) -> List[BlacklistMatch]:
        """Parse blacklist patterns from config"""
        result = []
        for pattern in blacklist_config['patterns']:
            match_type, pattern_value = _extract_match_type(pattern['match'])
            result.append(BlacklistMatch(
                name=pattern['name'],
                description=pattern['description'],
                match_type=match_type,
                pattern=pattern_value,
                reason=pattern['reason']
            ))
        return result

    def _parse_patterns(self, patterns: List[Dict[str, Any]]) -> List[PatternMatch]:
        """Parse pattern configurations from config file"""
        result = []
        for pattern in patterns:
            match_type, pattern_value = _extract_match_type(pattern['match'])
            config = RepeaterConfig(**pattern['config'])
            result.append(PatternMatch(
                name=pattern['name'],
                config=config,
                match_type=match_type,
                pattern=pattern_value
            ))
        
        # Sort by specificity: specific_id (0) -> id_range (1) -> callsign (2)
        result.sort(key=lambda p: {'specific_id': 0, 'id_range': 1, 'callsign': 2}[p.match_type])
        return result

    def _match_pattern(self, radio_id: int, callsign: Optional[str], pattern: Union[BlacklistMatch, PatternMatch]) -> bool:
        """Match a repeater against a pattern based on the pattern type"""
        if pattern.match_type == 'specific_id':
            return radio_id in pattern.pattern
            
        elif pattern.match_type == 'id_range':
            return any(start <= radio_id <= end for start, end in pattern.pattern)
            
        else:  # callsign
            if not callsign:  # Can't match callsign patterns without a callsign
                return False
                
            for p in pattern.pattern:
                pattern_regex = p.replace('*', '.*') if '*' in p else re.escape(p)
                if re.match(f"^{pattern_regex}$", callsign, re.IGNORECASE):
                    return True
            return False

    def _check_blacklist(self, radio_id: int, callsign: Optional[str] = None) -> None:
        """Check if a repeater matches any blacklist patterns"""
        for pattern in self.blacklist:
            if self._match_pattern(radio_id, callsign, pattern):
                raise BlacklistError(pattern.name, pattern.reason)

    def get_repeater_config(self, radio_id: int, callsign: Optional[str] = None) -> RepeaterConfig:
        """
        Get the configuration for a connecting repeater based on its ID and/or callsign.
        First checks blacklist, then follows strict priority:
        1. Specific IDs
        2. ID Ranges
        3. Callsign patterns
        4. Default config
        
        Raises:
            BlacklistError: If the repeater matches any blacklist pattern
        """
        # Check blacklist first
        self._check_blacklist(radio_id, callsign)
        
        # Patterns are already sorted by specificity in _parse_patterns
        for pattern in self.patterns:
            if self._match_pattern(radio_id, callsign, pattern):
                return pattern.config

        # If no patterns match, return default configuration
        return self.default_config
