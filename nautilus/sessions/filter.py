"""
sessions/filter.py — Trading session filtering.

Manages session state and determines if we're in a tradeable session
(e.g., New York RTH, London RTH, Tokyo RTH, or 24/7 for crypto).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Optional


@dataclass(slots=True)
class SessionState:
    """
    Current trading session state.

    Attributes
    ----------
    session_name : str
        Session identifier (e.g., "RTH", "ASIA", "CRYPTO", "ALWAYS").
    active : bool
        True if current time is within session trading hours.
    open_time : time
        Session opening time (HH:MM:SS UTC).
    close_time : time
        Session closing time (HH:MM:SS UTC).
    minutes_elapsed : float
        Minutes since session open (or -1 if not active).
    minutes_to_close : float
        Minutes until session close (or -1 if not active).
    """

    session_name: str
    active: bool
    open_time: time
    close_time: time
    minutes_elapsed: float = -1.0
    minutes_to_close: float = -1.0


class SessionFilter:
    """
    Filter to check if current time is within tradeable session windows.

    Supports:
      - Named sessions (RTH, ASIA, LONDON, etc.) with specific times
      - Custom session definitions
      - 24/7 "always active" mode for crypto

    Usage:
        filter = SessionFilter(sessions=[
            ("RTH", time(9, 30), time(16, 0)),
            ("LONDON", time(8, 0), time(16, 30)),
        ])
        current_session = filter.current_session(datetime.now(timezone.utc))
        if current_session.active:
            # Trade
    """

    def __init__(
        self,
        sessions: Optional[list[tuple[str, time, time]]] = None,
        always_active: bool = False,
    ) -> None:
        """
        Parameters
        ----------
        sessions : list[(str, time, time)], optional
            List of (name, open_time_utc, close_time_utc) tuples.
            If None and always_active=False, defaults to 24/7 crypto sessions.
        always_active : bool
            If True, always returns active=True (for crypto or backtesting).
        """
        self._always_active = always_active
        if always_active:
            self._sessions = []
        elif sessions is None:
            # Default: 24/7 crypto mode (all times are valid)
            self._sessions = [("CRYPTO", time(0, 0), time(23, 59, 59))]
        else:
            self._sessions = sessions

    @classmethod
    def always(cls) -> "SessionFilter":
        """Create a filter that always returns active=True."""
        return cls(always_active=True)

    @classmethod
    def rtc(cls) -> "SessionFilter":
        """Create a filter for US Regular Trading Hours (9:30-16:00 EST = 14:30-21:00 UTC)."""
        return cls(sessions=[("RTH", time(14, 30), time(21, 0))])

    @classmethod
    def crypto_24_7(cls) -> "SessionFilter":
        """Create a filter for 24/7 crypto trading."""
        return cls(sessions=[("CRYPTO", time(0, 0), time(23, 59, 59))])

    @classmethod
    def from_config(cls, config: Optional[dict]) -> "SessionFilter":
        """Load session configuration from a dict-like config object."""
        if config is None:
            return cls.always()

        always = config.get("always_active", False)
        if always:
            return cls(always_active=True)

        sessions_list = config.get("sessions", None)
        if sessions_list:
            # Parse if it's a list of dicts with name, open, close
            parsed = []
            for s in sessions_list:
                if isinstance(s, dict):
                    name = s.get("name", "session")
                    open_str = s.get("open", "00:00")
                    close_str = s.get("close", "23:59")
                    try:
                        open_t = datetime.strptime(open_str, "%H:%M").time()
                        close_t = datetime.strptime(close_str, "%H:%M").time()
                        parsed.append((name, open_t, close_t))
                    except ValueError:
                        pass
            if parsed:
                return cls(sessions=parsed)

        return cls.always()

    def current_session(self, dt: datetime) -> SessionState:
        """
        Get the current session state for a given datetime (assumed UTC).

        Parameters
        ----------
        dt : datetime
            Current time (should be in UTC).

        Returns
        -------
        SessionState
            Session info with active flag and time metrics.
        """
        if self._always_active:
            # For always-active mode, return large elapsed/remaining times
            return SessionState(
                session_name="ALWAYS",
                active=True,
                open_time=time(0, 0),
                close_time=time(23, 59, 59),
                minutes_elapsed=float('inf'),
                minutes_to_close=float('inf'),
            )

        current_time = dt.time()

        for session_name, open_t, close_t in self._sessions:
            # Handle overnight sessions (e.g., close < open)
            if close_t < open_t:
                # Session spans midnight
                active = current_time >= open_t or current_time <= close_t
            else:
                # Normal session within same day
                active = open_t <= current_time <= close_t

            if active:
                # Calculate minutes elapsed and remaining
                open_dt = dt.replace(hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0)
                close_dt = dt.replace(hour=close_t.hour, minute=close_t.minute, second=0, microsecond=0)
                
                # Handle overnight session close
                if close_t < open_t and current_time < open_t:
                    # We're in the "early morning" portion after midnight
                    close_dt += __import__('datetime').timedelta(days=1)
                elif close_t < open_t and current_time >= open_t:
                    # Normal case: after opening, before next midnight
                    close_dt += __import__('datetime').timedelta(days=1)
                
                elapsed = (dt - open_dt).total_seconds() / 60.0
                remaining = (close_dt - dt).total_seconds() / 60.0
                
                return SessionState(
                    session_name=session_name,
                    active=True,
                    open_time=open_t,
                    close_time=close_t,
                    minutes_elapsed=max(0, elapsed),
                    minutes_to_close=max(0, remaining),
                )

        # No matching session found — return default inactive state
        if self._sessions:
            first_name, first_open, first_close = self._sessions[0]
            return SessionState(
                session_name=first_name,
                active=False,
                open_time=first_open,
                close_time=first_close,
                minutes_elapsed=-1.0,
                minutes_to_close=-1.0,
            )

        # Fallback
        return SessionState(
            session_name="NONE",
            active=False,
            open_time=time(0, 0),
            close_time=time(0, 0),
            minutes_elapsed=-1.0,
            minutes_to_close=-1.0,
        )