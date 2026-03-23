"""
Rate Limiting and Usage Tracking for Google Custom Search API

This module provides configurable rate limiting with:
- Daily and monthly query limits
- Per-run query caps
- Cooldown periods after consecutive runs
- Persistent usage tracking via JSON file
- Easy toggle to enable/disable all rate limiting
"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION - All limits are easily adjustable here
# ============================================================================

# Master toggle - set to False to disable all rate limiting
RATE_LIMITING_ENABLED = False

# Query limits
DAILY_QUERY_LIMIT = 500
MONTHLY_QUERY_LIMIT = 5000
MAX_QUERIES_PER_RUN = 100

# Cooldown configuration
FREE_RUNS_PER_DAY = 3  # First 3 runs per day are free (no cooldown)
COOLDOWN_HOURS = 3  # After 3 runs, require 3 hours cooldown between runs

# Storage
USAGE_FILE = "usage_data.json"


# ============================================================================
# UsageTracker Class
# ============================================================================

class UsageTracker:
    """
    Thread-safe usage tracker with persistent JSON storage.
    Automatically resets daily counters at midnight and monthly counters on the 1st.
    """
    
    def __init__(self, storage_path: str = USAGE_FILE):
        self.storage_path = storage_path
        self.lock = Lock()
        self._ensure_storage_exists()
        
    def _ensure_storage_exists(self):
        """Create storage file if it doesn't exist"""
        if not os.path.exists(self.storage_path):
            initial_data = {
                "daily": {
                    "queries": 0,
                    "runs": 0,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "last_run_time": None
                },
                "monthly": {
                    "queries": 0,
                    "month": datetime.now().strftime("%Y-%m")
                }
            }
            self._save_data(initial_data)
            logger.info(f"Created new usage tracking file: {self.storage_path}")
    
    def _load_data(self) -> Dict:
        """Load usage data from JSON file"""
        try:
            with open(self.storage_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading usage data: {e}")
            # Return default structure if file is corrupted
            return {
                "daily": {
                    "queries": 0,
                    "runs": 0,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "last_run_time": None
                },
                "monthly": {
                    "queries": 0,
                    "month": datetime.now().strftime("%Y-%m")
                }
            }
    
    def _save_data(self, data: Dict):
        """Save usage data to JSON file"""
        try:
            with open(self.storage_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving usage data: {e}")
    
    def _check_and_reset(self, data: Dict) -> Dict:
        """Check if daily or monthly reset is needed and perform it"""
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        current_month = now.strftime("%Y-%m")
        
        # Reset daily counters if date changed
        if data["daily"]["date"] != current_date:
            logger.info(f"Resetting daily counters (was {data['daily']['date']}, now {current_date})")
            data["daily"] = {
                "queries": 0,
                "runs": 0,
                "date": current_date,
                "last_run_time": None
            }
        
        # Reset monthly counters if month changed
        if data["monthly"]["month"] != current_month:
            logger.info(f"Resetting monthly counters (was {data['monthly']['month']}, now {current_month})")
            data["monthly"] = {
                "queries": 0,
                "month": current_month
            }
        
        return data
    
    def get_status(self) -> Dict:
        """
        Get current usage status including all limits and reset times.
        
        Returns:
            Dict with structure:
            {
                "rate_limiting_enabled": bool,
                "daily": {"used": int, "limit": int, "resets_at": str},
                "monthly": {"used": int, "limit": int, "resets_at": str},
                "runs": {
                    "used_today": int,
                    "free_limit": int,
                    "cooldown_active": bool,
                    "cooldown_remaining_seconds": int,
                    "cooldown_ends_at": str or None
                },
                "max_queries_per_run": int
            }
        """
        with self.lock:
            data = self._load_data()
            data = self._check_and_reset(data)
            
            cooldown_remaining = self.get_cooldown_remaining()
            cooldown_active = cooldown_remaining > 0
            
            cooldown_ends_at = None
            if cooldown_active and data["daily"]["last_run_time"]:
                last_run = datetime.fromisoformat(data["daily"]["last_run_time"])
                cooldown_ends_at = (last_run + timedelta(hours=COOLDOWN_HOURS)).isoformat()
            
            return {
                "rate_limiting_enabled": RATE_LIMITING_ENABLED,
                "daily": {
                    "used": data["daily"]["queries"],
                    "limit": DAILY_QUERY_LIMIT,
                    "resets_at": self.get_daily_reset_time().isoformat()
                },
                "monthly": {
                    "used": data["monthly"]["queries"],
                    "limit": MONTHLY_QUERY_LIMIT,
                    "resets_at": self.get_monthly_reset_time().isoformat()
                },
                "runs": {
                    "used_today": data["daily"]["runs"],
                    "free_limit": FREE_RUNS_PER_DAY,
                    "cooldown_active": cooldown_active,
                    "cooldown_remaining_seconds": cooldown_remaining,
                    "cooldown_ends_at": cooldown_ends_at
                },
                "max_queries_per_run": MAX_QUERIES_PER_RUN
            }
    
    def can_run(self, requested_queries: int) -> Tuple[bool, str]:
        """
        Check if a run with the requested number of queries is allowed.
        
        Args:
            requested_queries: Number of queries the user wants to run
            
        Returns:
            Tuple of (allowed: bool, reason: str)
            If allowed is False, reason contains the error message
        """
        # If rate limiting is disabled, only check per-run cap
        if not RATE_LIMITING_ENABLED:
            if requested_queries > MAX_QUERIES_PER_RUN:
                return False, f"Query count ({requested_queries}) exceeds maximum allowed per run ({MAX_QUERIES_PER_RUN}). Please reduce your file to {MAX_QUERIES_PER_RUN} queries or fewer."
            return True, ""
        
        with self.lock:
            data = self._load_data()
            data = self._check_and_reset(data)
            
            # Check 1: Per-run query cap
            if requested_queries > MAX_QUERIES_PER_RUN:
                return False, f"Query count ({requested_queries}) exceeds maximum allowed per run ({MAX_QUERIES_PER_RUN}). Please reduce your file to {MAX_QUERIES_PER_RUN} queries or fewer."
            
            # Check 2: Daily query limit
            if data["daily"]["queries"] + requested_queries > DAILY_QUERY_LIMIT:
                remaining = DAILY_QUERY_LIMIT - data["daily"]["queries"]
                reset_time = self.get_daily_reset_time()
                hours_until_reset = (reset_time - datetime.now()).total_seconds() / 3600
                return False, f"Daily query limit would be exceeded. You have {remaining} queries remaining today. Daily limit resets in {hours_until_reset:.1f} hours."
            
            # Check 3: Monthly query limit
            if data["monthly"]["queries"] + requested_queries > MONTHLY_QUERY_LIMIT:
                remaining = MONTHLY_QUERY_LIMIT - data["monthly"]["queries"]
                reset_time = self.get_monthly_reset_time()
                days_until_reset = (reset_time - datetime.now()).days
                return False, f"Monthly query limit would be exceeded. You have {remaining} queries remaining this month. Monthly limit resets in {days_until_reset} days."
            
            # Check 4: Cooldown (only applies after FREE_RUNS_PER_DAY)
            if data["daily"]["runs"] >= FREE_RUNS_PER_DAY:
                cooldown_remaining = self.get_cooldown_remaining()
                if cooldown_remaining > 0:
                    hours = int(cooldown_remaining // 3600)
                    minutes = int((cooldown_remaining % 3600) // 60)
                    return False, f"Cooldown active. You've used {data['daily']['runs']} of {FREE_RUNS_PER_DAY} free runs today. Next run available in {hours}h {minutes}m."
            
            return True, ""
    
    def record_queries(self, count: int):
        """Record that queries were executed"""
        with self.lock:
            data = self._load_data()
            data = self._check_and_reset(data)
            
            data["daily"]["queries"] += count
            data["monthly"]["queries"] += count
            
            self._save_data(data)
            logger.info(f"Recorded {count} queries. Daily: {data['daily']['queries']}/{DAILY_QUERY_LIMIT}, Monthly: {data['monthly']['queries']}/{MONTHLY_QUERY_LIMIT}")
    
    def record_run(self):
        """Record that a run was executed"""
        with self.lock:
            data = self._load_data()
            data = self._check_and_reset(data)
            
            data["daily"]["runs"] += 1
            data["daily"]["last_run_time"] = datetime.now().isoformat()
            
            self._save_data(data)
            logger.info(f"Recorded run #{data['daily']['runs']} at {data['daily']['last_run_time']}")
    
    def get_cooldown_remaining(self) -> int:
        """
        Get remaining cooldown time in seconds.
        Returns 0 if no cooldown is active.
        """
        data = self._load_data()
        data = self._check_and_reset(data)
        
        # No cooldown if we haven't exceeded free runs
        if data["daily"]["runs"] < FREE_RUNS_PER_DAY:
            return 0
        
        # No cooldown if we haven't run anything yet today
        if not data["daily"]["last_run_time"]:
            return 0
        
        try:
            last_run = datetime.fromisoformat(data["daily"]["last_run_time"])
            cooldown_end = last_run + timedelta(hours=COOLDOWN_HOURS)
            now = datetime.now()
            
            if now < cooldown_end:
                return int((cooldown_end - now).total_seconds())
            else:
                return 0
        except Exception as e:
            logger.error(f"Error calculating cooldown: {e}")
            return 0
    
    def get_daily_reset_time(self) -> datetime:
        """Get the datetime when daily counters will reset (next midnight)"""
        now = datetime.now()
        tomorrow = now + timedelta(days=1)
        return datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0)
    
    def get_monthly_reset_time(self) -> datetime:
        """Get the datetime when monthly counters will reset (1st of next month)"""
        now = datetime.now()
        if now.month == 12:
            return datetime(now.year + 1, 1, 1, 0, 0, 0)
        else:
            return datetime(now.year, now.month + 1, 1, 0, 0, 0)


# ============================================================================
# Global instance
# ============================================================================

# Create a global instance that can be imported and used throughout the app
usage_tracker = UsageTracker()

